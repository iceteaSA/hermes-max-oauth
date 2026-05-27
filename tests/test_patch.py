"""Tests for the runtime patcher (hermes_max_patch.py).

Verifies monkey-patching logic without needing the anthropic SDK
or a live API — mocks the adapter module.
"""

import json
import sys
import types
import pytest
from unittest.mock import MagicMock, patch

# Make repo root importable (agent/ and hermes_max_patch.py)
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, os.path.dirname(__file__))


@pytest.fixture
def mock_adapter():
    """Create a mock agent.anthropic_adapter with the functions we patch."""
    adapter = types.ModuleType('agent.anthropic_adapter')

    # Mock the original functions
    adapter.build_anthropic_client = MagicMock(return_value='original_client')
    adapter.build_anthropic_kwargs = MagicMock(return_value={
        'model': 'claude-sonnet-4-6',
        'messages': [{'role': 'user', 'content': 'hi'}],
        'system': 'You are a helpful assistant.',
        'max_tokens': 100,
    })

    # Mock internal functions the patcher uses
    adapter._is_oauth_token = lambda k: k.startswith('sk-ant-oat')
    adapter._is_third_party_anthropic_endpoint = lambda u: False
    adapter._is_kimi_coding_endpoint = lambda u: False
    adapter._requires_bearer_auth = lambda u: False
    adapter._normalize_base_url_text = lambda u: u or ''
    adapter._get_anthropic_sdk = MagicMock(return_value=None)
    adapter._common_betas_for_base_url = lambda u, **kw: [
        'interleaved-thinking-2025-05-14',
    ]
    adapter._OAUTH_ONLY_BETAS = [
        'claude-code-20250219',
        'oauth-2025-04-20',
    ]
    adapter.normalize_proxy_env_vars = lambda: None

    # Register in sys.modules so patcher can import it
    sys.modules['agent.anthropic_adapter'] = adapter
    yield adapter
    del sys.modules['agent.anthropic_adapter']


class TestPatchBuildKwargs:
    """Test that build_anthropic_kwargs wrapper applies OAuth transforms."""

    def test_non_oauth_passes_through(self, mock_adapter):
        from hermes_max_patch import _patch_build_kwargs
        _patch_build_kwargs(mock_adapter)

        result = mock_adapter.build_anthropic_kwargs(
            'claude-sonnet-4-6', [], is_oauth=False,
        )
        # Original was called with is_oauth=False
        mock_adapter.build_anthropic_kwargs.__wrapped__  # has wrapper
        assert result['model'] == 'claude-sonnet-4-6'

    def test_oauth_adds_system_blocks(self, mock_adapter):
        from hermes_max_patch import _patch_build_kwargs
        _patch_build_kwargs(mock_adapter)

        result = mock_adapter.build_anthropic_kwargs(
            'claude-sonnet-4-6', [], is_oauth=True,
        )

        system = result['system']
        assert isinstance(system, list)
        assert len(system) >= 3
        # Block 0: billing header
        assert 'x-anthropic-billing-header' in system[0]['text']
        # Block 1: identity prefix
        assert 'Claude agent' in system[1]['text']
        # Block 2: sanitized body with cache_control
        assert system[2].get('cache_control') == {'type': 'ephemeral'}

    def test_oauth_prefixes_tool_names(self, mock_adapter):
        from hermes_max_patch import _patch_build_kwargs

        mock_adapter.build_anthropic_kwargs = MagicMock(return_value={
            'model': 'claude-sonnet-4-6',
            'messages': [],
            'system': 'test',
            'tools': [{'name': 'read_file'}, {'name': 'terminal'}],
            'max_tokens': 100,
        })
        _patch_build_kwargs(mock_adapter)

        result = mock_adapter.build_anthropic_kwargs(
            'claude-sonnet-4-6', [], is_oauth=True,
        )
        assert result['tools'][0]['name'] == 'mcp_ReadFile'
        assert result['tools'][1]['name'] == 'mcp_Terminal'

    def test_oauth_skips_already_prefixed_tools(self, mock_adapter):
        from hermes_max_patch import _patch_build_kwargs

        mock_adapter.build_anthropic_kwargs = MagicMock(return_value={
            'model': 'claude-sonnet-4-6',
            'messages': [],
            'system': 'test',
            'tools': [{'name': 'mcp_read_file'}],
            'max_tokens': 100,
        })
        _patch_build_kwargs(mock_adapter)

        result = mock_adapter.build_anthropic_kwargs(
            'claude-sonnet-4-6', [], is_oauth=True,
        )
        assert result['tools'][0]['name'] == 'mcp_read_file'  # already prefixed, untouched

    def test_to_pascal_case(self, mock_adapter):
        from hermes_max_patch import _to_pascal_case

        assert _to_pascal_case('read_file') == 'ReadFile'
        assert _to_pascal_case('terminal') == 'Terminal'
        assert _to_pascal_case('bash') == 'Bash'
        assert _to_pascal_case('todo_write') == 'TodoWrite'
        assert _to_pascal_case('web_search') == 'WebSearch'
        assert _to_pascal_case('') == ''
        assert _to_pascal_case('Read') == 'Read'  # already PascalCase

    def test_oauth_sanitizes_hermes_references(self, mock_adapter):
        from hermes_max_patch import _patch_build_kwargs

        mock_adapter.build_anthropic_kwargs = MagicMock(return_value={
            'model': 'claude-sonnet-4-6',
            'messages': [],
            'system': 'You are Hermes Agent by Nous Research.',
            'max_tokens': 100,
        })
        _patch_build_kwargs(mock_adapter)

        result = mock_adapter.build_anthropic_kwargs(
            'claude-sonnet-4-6', [], is_oauth=True,
        )
        full_text = ' '.join(b['text'] for b in result['system'])
        assert 'Hermes Agent' not in full_text
        assert 'Nous Research' not in full_text


class TestFromPascalCase:
    """Test PascalCase → snake_case conversion for response unwrapping."""

    def test_basic_conversions(self):
        from hermes_max_patch import _from_pascal_case

        assert _from_pascal_case('ReadFile') == 'read_file'
        assert _from_pascal_case('Terminal') == 'terminal'
        assert _from_pascal_case('Bash') == 'bash'
        assert _from_pascal_case('TodoWrite') == 'todo_write'
        assert _from_pascal_case('WebSearch') == 'web_search'

    def test_already_snake_case(self):
        from hermes_max_patch import _from_pascal_case

        assert _from_pascal_case('read_file') == 'read_file'
        assert _from_pascal_case('terminal') == 'terminal'

    def test_empty_string(self):
        from hermes_max_patch import _from_pascal_case

        assert _from_pascal_case('') == ''

    def test_round_trip(self):
        """PascalCase → snake_case should reverse snake_case → PascalCase."""
        from hermes_max_patch import _to_pascal_case, _from_pascal_case

        names = ['read_file', 'terminal', 'bash', 'todo_write', 'web_search']
        for name in names:
            assert _from_pascal_case(_to_pascal_case(name)) == name


class TestNormalizeResponsePatch:
    """Test that normalize_response unwraps PascalCase tool names."""

    def test_unwraps_pascal_case_when_strip_prefix(self):
        from hermes_max_patch import _from_pascal_case, _patch_normalize_response
        from unittest.mock import MagicMock, patch
        from types import SimpleNamespace

        # Mock ToolCall with mutable name
        class MockToolCall:
            def __init__(self, name):
                self.name = name

        # Mock NormalizedResponse
        mock_result = SimpleNamespace(
            tool_calls=[MockToolCall('ReadFile'), MockToolCall('Terminal')],
        )

        # Mock AnthropicTransport class
        mock_transport_cls = MagicMock()
        mock_transport_cls.normalize_response = MagicMock(return_value=mock_result)
        original_normalize = mock_transport_cls.normalize_response

        with patch.dict('sys.modules', {
            'agent': MagicMock(),
            'agent.transports': MagicMock(),
            'agent.transports.anthropic': MagicMock(
                AnthropicTransport=mock_transport_cls,
            ),
        }):
            _patch_normalize_response()

        # Call the patched method with strip_tool_prefix=True
        result = mock_transport_cls.normalize_response(
            MagicMock(), MagicMock(), strip_tool_prefix=True,
        )
        assert result.tool_calls[0].name == 'read_file'
        assert result.tool_calls[1].name == 'terminal'

    def test_no_unwrap_without_strip_prefix(self):
        from hermes_max_patch import _patch_normalize_response
        from unittest.mock import MagicMock, patch
        from types import SimpleNamespace

        class MockToolCall:
            def __init__(self, name):
                self.name = name

        mock_result = SimpleNamespace(
            tool_calls=[MockToolCall('mcp_ReadFile')],
        )

        mock_transport_cls = MagicMock()
        mock_transport_cls.normalize_response = MagicMock(return_value=mock_result)

        with patch.dict('sys.modules', {
            'agent': MagicMock(),
            'agent.transports': MagicMock(),
            'agent.transports.anthropic': MagicMock(
                AnthropicTransport=mock_transport_cls,
            ),
        }):
            _patch_normalize_response()

        # Call without strip_tool_prefix — names should stay as-is
        result = mock_transport_cls.normalize_response(
            MagicMock(), MagicMock(),
        )
        assert result.tool_calls[0].name == 'mcp_ReadFile'


class TestPatchBuildClient:
    """Test that build_anthropic_client wrapper handles OAuth tokens."""

    def test_non_oauth_passes_through(self, mock_adapter):
        from hermes_max_patch import _patch_build_client
        _patch_build_client(mock_adapter)

        result = mock_adapter.build_anthropic_client('sk-ant-api03-regular')
        # Non-OAuth token — should have called original
        assert result == 'original_client'

    def test_oauth_without_sdk_raises(self, mock_adapter):
        from hermes_max_patch import _patch_build_client
        mock_adapter._get_anthropic_sdk.return_value = None
        _patch_build_client(mock_adapter)

        with pytest.raises(ImportError, match='anthropic'):
            mock_adapter.build_anthropic_client('sk-ant-oat01-test')


class TestActivate:
    """Test the activation mechanism."""

    def test_activate_registers_finder(self):
        from hermes_max_patch import activate, _HermesMaxFinder
        import hermes_max_patch

        # Reset state
        hermes_max_patch._PATCHED = False
        sys.meta_path[:] = [
            f for f in sys.meta_path
            if not isinstance(f, _HermesMaxFinder)
        ]

        activate()
        assert any(isinstance(f, _HermesMaxFinder) for f in sys.meta_path)

        # Cleanup
        sys.meta_path[:] = [
            f for f in sys.meta_path
            if not isinstance(f, _HermesMaxFinder)
        ]

    def test_activate_patches_if_already_imported(self, mock_adapter):
        import hermes_max_patch

        hermes_max_patch._PATCHED = False
        hermes_max_patch.activate()
        assert hermes_max_patch._PATCHED
