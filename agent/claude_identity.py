"""
Claude Code identity signals for Max OAuth routing.

Sets exact headers, body ordering, metadata, and beta selection
that Anthropic uses to identify requests as Claude Code.
"""

import json
import os
import platform
import uuid
from typing import Optional

# --- Version constants (pinned to Claude Code 2.1.141) ---

CLAUDE_CODE_VERSION = '2.1.141'
USER_AGENT = f'claude-cli/{CLAUDE_CODE_VERSION} (external, sdk-cli)'
STAINLESS_PACKAGE_VERSION = '0.94.0'
STAINLESS_RUNTIME_VERSION = 'v24.3.0'
FAST_MODE_BETA = 'fast-mode-2026-02-01'

CLAUDE_CODE_IDENTITY_TEXT = (
    "You are a Claude agent, built on Anthropic's Claude Agent SDK."
)

# --- Beta header sets ---

CLAUDE_CODE_FULL_AGENT_BETAS = [
    'claude-code-20250219',
    'oauth-2025-04-20',
    'interleaved-thinking-2025-05-14',
    'context-management-2025-06-27',
    'prompt-caching-scope-2026-01-05',
    'advisor-tool-2026-03-01',
    'advanced-tool-use-2025-11-20',
    # NOTE: context-1m-2025-08-07 deliberately excluded — Max base allowance
    # rejects it with "Usage credits are required for long context requests."
    # The adapter manages _CONTEXT_1M_BETA separately for Bedrock/Azure.
    'effort-2025-11-24',
    'extended-cache-ttl-2025-04-11',
    'cache-diagnosis-2026-04-07',
]

CLAUDE_CODE_STRUCTURED_OUTPUT_BETAS = [
    'oauth-2025-04-20',
    'interleaved-thinking-2025-05-14',
    'context-management-2025-06-27',
    'prompt-caching-scope-2026-01-05',
    'advisor-tool-2026-03-01',
    'structured-outputs-2025-12-15',
    'cache-diagnosis-2026-04-07',
]

CLAUDE_CODE_BASE_BETAS = [
    'oauth-2025-04-20',
    'interleaved-thinking-2025-05-14',
    'context-management-2025-06-27',
    'prompt-caching-scope-2026-01-05',
    'advisor-tool-2026-03-01',
    'cache-diagnosis-2026-04-07',
]

# --- Body field ordering ---

BODY_FIELD_ORDER = [
    'model', 'messages', 'system', 'tools', 'tool_choice',
    'metadata', 'max_tokens', 'temperature', 'thinking',
    'context_management', 'output_config', 'diagnostics', 'stream', 'speed',
]


class ClaudeCodeIdentity:
    """Per-session identity. Create once at client init, reuse across requests."""

    def __init__(self):
        self.device_id: str = os.urandom(32).hex()
        self.session_id: str = str(uuid.uuid4())
        self.account_uuid: Optional[str] = None


def _has_full_agent_shape(body: dict) -> bool:
    return (
        isinstance(body.get('tools'), list) and len(body['tools']) > 0
        and isinstance(body.get('system'), list)
        and isinstance(body.get('thinking'), dict)
        and isinstance(body.get('context_management'), dict)
        and isinstance(body.get('output_config'), dict)
        and isinstance(body.get('diagnostics'), dict)
    )


def _has_structured_output(body: dict) -> bool:
    output_config = body.get('output_config')
    if not isinstance(output_config, dict):
        return False
    fmt = output_config.get('format')
    return isinstance(fmt, dict) and fmt.get('type') == 'json_schema'


def select_betas(
    body: Optional[dict] = None,
    extra_betas: Optional[list[str]] = None,
) -> str:
    """Select beta header set based on request body shape."""
    if body is not None:
        if _has_full_agent_shape(body):
            selected = list(CLAUDE_CODE_FULL_AGENT_BETAS)
        elif _has_structured_output(body):
            selected = list(CLAUDE_CODE_STRUCTURED_OUTPUT_BETAS)
        else:
            selected = list(CLAUDE_CODE_BASE_BETAS)
    else:
        selected = list(CLAUDE_CODE_BASE_BETAS)

    if body and body.get('speed') == 'fast':
        selected.append(FAST_MODE_BETA)

    if extra_betas:
        for beta in extra_betas:
            trimmed = beta.strip()
            if trimmed:
                selected.append(trimmed)

    return ','.join(dict.fromkeys(selected))  # deduplicate, preserve order


def stainless_os() -> str:
    system = platform.system()
    return {
        'Darwin': 'MacOS', 'Windows': 'Windows',
        'Linux': 'Linux', 'FreeBSD': 'FreeBSD',
    }.get(system, 'Unknown')


def stainless_arch() -> str:
    machine = platform.machine().lower()
    if machine in ('arm64', 'aarch64'):
        return 'arm64'
    if machine in ('x86_64', 'amd64'):
        return 'x64'
    if machine == 'i686':
        return 'x32'
    return machine


def build_headers(
    access_token: str,
    identity: ClaudeCodeIdentity,
    body: Optional[dict] = None,
    extra_betas: Optional[list[str]] = None,
    existing_headers: Optional[dict] = None,
) -> dict:
    """Build complete Claude Code identity headers."""
    incoming_betas = []
    if existing_headers and 'anthropic-beta' in existing_headers:
        incoming_betas = [
            b.strip() for b in existing_headers['anthropic-beta'].split(',')
            if b.strip()
        ]

    all_extra = incoming_betas + (extra_betas or [])

    headers = dict(existing_headers or {})
    headers['accept'] = 'application/json'
    headers['authorization'] = f'Bearer {access_token}'
    headers['content-type'] = 'application/json'
    headers['user-agent'] = USER_AGENT
    headers['anthropic-beta'] = select_betas(body, all_extra)
    headers['anthropic-dangerous-direct-browser-access'] = 'true'
    headers['anthropic-version'] = '2023-06-01'
    headers['x-app'] = 'cli'
    headers['x-client-request-id'] = str(uuid.uuid4())
    headers['x-claude-code-session-id'] = identity.session_id
    headers['x-stainless-arch'] = stainless_arch()
    headers['x-stainless-lang'] = 'js'
    headers['x-stainless-os'] = stainless_os()
    headers['x-stainless-package-version'] = STAINLESS_PACKAGE_VERSION
    headers['x-stainless-retry-count'] = '0'
    headers['x-stainless-runtime'] = 'node'
    headers['x-stainless-runtime-version'] = STAINLESS_RUNTIME_VERSION
    headers['x-stainless-timeout'] = '600'
    headers.pop('x-api-key', None)
    return headers


def apply_metadata(body: dict, identity: ClaudeCodeIdentity) -> None:
    """Inject user_id metadata with device/session UUIDs."""
    if identity.account_uuid:
        if 'metadata' not in body:
            body['metadata'] = {}
        body['metadata']['user_id'] = json.dumps({
            'device_id': identity.device_id,
            'account_uuid': identity.account_uuid,
            'session_id': identity.session_id,
        })
    else:
        metadata = body.get('metadata')
        if isinstance(metadata, dict):
            metadata.pop('user_id', None)


def order_body(body: dict) -> dict:
    """Reorder body fields to canonical Claude Code order."""
    ordered = {}
    for key in BODY_FIELD_ORDER:
        if key in body:
            ordered[key] = body[key]
    for key in body:
        if key not in ordered:
            ordered[key] = body[key]
    return ordered
