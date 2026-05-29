"""
Hermes Max OAuth runtime patcher.

Monkey-patches agent.anthropic_adapter to route OAuth requests through
the Max subscription base allowance instead of extra usage credits.

Patches three functions:
1. build_anthropic_client() — adds httpx event hook for cch signing,
   identity headers, body ordering, and metadata injection.
2. build_anthropic_kwargs() — replaces system prompt with 3-block layout
   (billing header, identity prefix, sanitized body) for Max routing.
3. AnthropicTransport.normalize_response() — unwraps PascalCase tool names
   back to snake_case so hermes's tool dispatcher resolves them correctly.

Applied automatically via hermes-max-oauth.pth at Python startup.
Does NOT modify any hermes-agent source files.
"""

import functools
import importlib
import importlib.util
import logging
import sys
from importlib.abc import Loader, MetaPathFinder

logger = logging.getLogger(__name__)

_PATCHED = False
_MCP_TOOL_PREFIX = 'mcp_'


def _to_pascal_case(name: str) -> str:
    """Convert snake_case tool name to PascalCase.

    Claude Code uses PascalCase tool names (e.g. mcp_Bash, mcp_Read).
    Lowercase names (mcp_bash, mcp_read) are flagged as non-Claude-Code.
    """
    return ''.join(part.capitalize() for part in name.split('_') if part)


def _from_pascal_case(name: str) -> str:
    """Convert PascalCase back to snake_case for response unwrapping.

    Reverses _to_pascal_case: ReadFile → read_file, Terminal → terminal.
    Used when Claude returns tool names we PascalCased on the outbound side.
    """
    import re
    # Insert underscore before each uppercase letter that follows a lowercase
    # letter or is followed by a lowercase letter (handles acronyms).
    s = re.sub(r'([a-z0-9])([A-Z])', r'\1_\2', name)
    s = re.sub(r'([A-Z]+)([A-Z][a-z])', r'\1_\2', s)
    return s.lower()


def _patch_build_client(adapter):
    """Wrap build_anthropic_client to add httpx hook for OAuth tokens."""
    import json
    from agent.cch import sign_request_body
    from agent.claude_identity import (
        ClaudeCodeIdentity, build_headers, apply_metadata, order_body,
    )

    _original = adapter.build_anthropic_client

    @functools.wraps(_original)
    def _patched(api_key, base_url=None, timeout=None, *,
                 drop_context_1m_beta=False):
        # Non-OAuth, non-string, or callable (e.g. Entra ID bearer):
        # use original. A str is never callable, so the callable case is
        # already covered by the isinstance check.
        if not isinstance(api_key, str) or not adapter._is_oauth_token(api_key):
            return _original(
                api_key, base_url, timeout,
                drop_context_1m_beta=drop_context_1m_beta,
            )

        # Third-party endpoint: use original (they have own auth)
        if adapter._is_third_party_anthropic_endpoint(base_url):
            return _original(
                api_key, base_url, timeout,
                drop_context_1m_beta=drop_context_1m_beta,
            )

        # Kimi coding endpoint: use original (needs own UA)
        if adapter._is_kimi_coding_endpoint(base_url):
            return _original(
                api_key, base_url, timeout,
                drop_context_1m_beta=drop_context_1m_beta,
            )

        # Bearer-auth third party (MiniMax, Azure): use original
        if adapter._requires_bearer_auth(
            adapter._normalize_base_url_text(base_url),
        ):
            return _original(
                api_key, base_url, timeout,
                drop_context_1m_beta=drop_context_1m_beta,
            )

        # ── OAuth on Anthropic endpoint → Max routing ──────────────
        # Fail fast on a missing SDK before importing httpx, so the failure
        # mode is the actionable "install anthropic" message rather than an
        # incidental httpx ImportError.
        _sdk = adapter._get_anthropic_sdk()
        if _sdk is None:
            raise ImportError(
                "The 'anthropic' package is required for OAuth. "
                "Install with: pip install 'anthropic>=0.39.0'"
            )

        import httpx as _httpx

        adapter.normalize_proxy_env_vars()

        _max_identity = ClaudeCodeIdentity()

        def _max_request_hook(request: _httpx.Request) -> None:
            """Rewrite outgoing request with Claude Code identity."""
            try:
                body_dict = json.loads(request.content)
            except (json.JSONDecodeError, UnicodeDecodeError):
                return

            apply_metadata(body_dict, _max_identity)
            body_dict = order_body(body_dict)

            # Must use compact separators to match SDK serialization,
            # otherwise Content-Length mismatch breaks HTTP/1.1.
            body_str = json.dumps(
                body_dict, ensure_ascii=False, separators=(',', ':'),
            )
            signed = sign_request_body(body_str)
            body_bytes = signed.encode('utf-8')

            request._content = body_bytes
            request.headers['content-length'] = str(len(body_bytes))

            new_headers = build_headers(
                api_key, _max_identity,
                body=body_dict,
                existing_headers=dict(request.headers),
            )
            for k, v in new_headers.items():
                request.headers[k] = v

        normalized = adapter._normalize_base_url_text(base_url)
        _read_timeout = (
            timeout
            if isinstance(timeout, (int, float)) and timeout > 0
            else 900.0
        )

        # NOTE: We intentionally do NOT seed default_headers['anthropic-beta'].
        # The request hook (build_headers/select_betas) is the single source of
        # truth for the beta set on Max OAuth requests. Seeding it here caused
        # the hook to read those betas back as "incoming" and merge them in,
        # which could reintroduce context-1m-2025-08-07 (rejected by the Max
        # base allowance). select_betas owns the curated, deny-filtered list.
        client_kwargs = {
            'timeout': _httpx.Timeout(
                timeout=float(_read_timeout), connect=10.0,
            ),
            'auth_token': api_key,
            'http_client': _httpx.Client(
                event_hooks={'request': [_max_request_hook]},
                timeout=_httpx.Timeout(600.0, connect=30.0),
            ),
        }
        if normalized:
            client_kwargs['base_url'] = normalized

        return _sdk.Anthropic(**client_kwargs)

    adapter.build_anthropic_client = _patched


def _patch_build_kwargs(adapter):
    """Wrap build_anthropic_kwargs to add prompt sanitizer for OAuth."""
    from agent.cch import build_billing_header
    from agent.prompt_sanitizer import build_system_blocks, load_config

    _original = adapter.build_anthropic_kwargs

    @functools.wraps(_original)
    def _patched(model, messages, tools=None, max_tokens=None,
                 reasoning_config=None, **kwargs):
        is_oauth = kwargs.pop('is_oauth', False)

        if not is_oauth:
            return _original(
                model, messages, tools, max_tokens, reasoning_config,
                is_oauth=False, **kwargs,
            )

        # Call original with is_oauth=False to get base kwargs
        # (skips hermes's built-in OAuth transforms)
        result = _original(
            model, messages, tools, max_tokens, reasoning_config,
            is_oauth=False, **kwargs,
        )

        # ── Apply Max OAuth system prompt layout ───────────────────
        _config = load_config()
        _billing = build_billing_header()

        system = result.get('system', '')
        if isinstance(system, str):
            raw = system
        elif isinstance(system, list):
            raw = '\n\n'.join(
                b.get('text', '') if isinstance(b, dict) else str(b)
                for b in system
            )
        else:
            raw = str(system) if system else ''

        result['system'] = build_system_blocks(raw, _billing, _config)

        # ── Prefix tool names with mcp_ + PascalCase ─────────────
        if result.get('tools'):
            for tool in result['tools']:
                if 'name' in tool and not tool['name'].startswith(
                    _MCP_TOOL_PREFIX
                ):
                    tool['name'] = (
                        _MCP_TOOL_PREFIX + _to_pascal_case(tool['name'])
                    )

        # ── Prefix tool names in message history ──────────────────
        for msg in result.get('messages', []):
            content = msg.get('content')
            if isinstance(content, list):
                for block in content:
                    if not isinstance(block, dict):
                        continue
                    if (
                        block.get('type') == 'tool_use'
                        and 'name' in block
                        and not block['name'].startswith(_MCP_TOOL_PREFIX)
                    ):
                        block['name'] = (
                            _MCP_TOOL_PREFIX
                            + _to_pascal_case(block['name'])
                        )

        return result

    adapter.build_anthropic_kwargs = _patched


def _patch_normalize_response():
    """Wrap AnthropicTransport.normalize_response to unwrap PascalCase tool names.

    When strip_tool_prefix=True (OAuth mode), hermes strips the mcp_ prefix
    but leaves PascalCase intact (e.g. ReadFile). We added PascalCase on the
    outbound side, so we must reverse it: ReadFile → read_file.
    """
    try:
        from agent.transports.anthropic import AnthropicTransport
    except ImportError:
        logger.debug('hermes-max-oauth: AnthropicTransport not found, '
                      'skipping response unwrap patch')
        return

    _original = AnthropicTransport.normalize_response

    @functools.wraps(_original)
    def _patched(self, response, **kwargs):
        result = _original(self, response, **kwargs)
        if kwargs.get('strip_tool_prefix', False) and result.tool_calls:
            for tc in result.tool_calls:
                tc.name = _from_pascal_case(tc.name)
        return result

    AnthropicTransport.normalize_response = _patched


def apply_patches(adapter=None):
    """Apply all patches to the adapter module."""
    global _PATCHED
    if _PATCHED:
        return

    try:
        if adapter is None:
            import agent.anthropic_adapter as adapter
        _patch_build_client(adapter)
        _patch_build_kwargs(adapter)
        _patch_normalize_response()
        _PATCHED = True
        logger.debug('hermes-max-oauth: patches applied')
    except Exception:
        logger.debug('hermes-max-oauth: patch failed', exc_info=True)


class _PatchingLoader(Loader):
    """Wraps the real loader; applies patches after the module executes."""

    def __init__(self, loader):
        self._loader = loader

    def create_module(self, spec):
        # Defer to the real loader's module creation (or default machinery).
        if hasattr(self._loader, 'create_module'):
            return self._loader.create_module(spec)
        return None

    def exec_module(self, module):
        # Run the real module body first, then patch the populated module.
        self._loader.exec_module(module)
        apply_patches(module)

    def __getattr__(self, name):
        # Proxy everything else (get_source, is_package, etc.) to the real loader.
        return getattr(self._loader, name)


class _HermesMaxFinder(MetaPathFinder):
    """Intercepts agent.anthropic_adapter import to apply patches.

    Uses the modern find_spec API (PEP 451). The legacy find_module/
    load_module protocol was removed from the import system in Python 3.12,
    so it must not be relied upon.
    """

    _TARGET = 'agent.anthropic_adapter'

    def find_spec(self, fullname, path=None, target=None):
        if fullname != self._TARGET:
            return None

        # Temporarily remove ourselves so downstream finders resolve the
        # real module without re-entering this hook.
        saved = [f for f in sys.meta_path if isinstance(f, _HermesMaxFinder)]
        sys.meta_path[:] = [
            f for f in sys.meta_path if not isinstance(f, _HermesMaxFinder)
        ]
        try:
            spec = importlib.util.find_spec(fullname)
        except Exception:
            spec = None
        finally:
            # Re-register for reload scenarios.
            for f in saved:
                sys.meta_path.insert(0, f)

        if spec is None or spec.loader is None:
            return None

        spec.loader = _PatchingLoader(spec.loader)
        return spec


def activate():
    """Register the import hook. Called from .pth file at startup."""
    if 'agent.anthropic_adapter' in sys.modules:
        # Already imported — patch directly
        apply_patches()
    elif not any(isinstance(f, _HermesMaxFinder) for f in sys.meta_path):
        sys.meta_path.insert(0, _HermesMaxFinder())
