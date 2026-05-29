import json
import uuid
import pytest
from agent.claude_identity import (
    ClaudeCodeIdentity,
    select_betas,
    build_headers,
    apply_metadata,
    order_body,
    stainless_os,
    stainless_arch,
    BODY_FIELD_ORDER,
    CLAUDE_CODE_FULL_AGENT_BETAS,
    CLAUDE_CODE_BASE_BETAS,
)


class TestClaudeCodeIdentity:
    def test_device_id_is_64_hex_chars(self):
        identity = ClaudeCodeIdentity()
        assert len(identity.device_id) == 64
        assert all(c in '0123456789abcdef' for c in identity.device_id)

    def test_session_id_is_uuid(self):
        identity = ClaudeCodeIdentity()
        uuid.UUID(identity.session_id)

    def test_two_identities_differ(self):
        a = ClaudeCodeIdentity()
        b = ClaudeCodeIdentity()
        assert a.device_id != b.device_id
        assert a.session_id != b.session_id


class TestSelectBetas:
    def test_no_body_returns_base(self):
        result = select_betas(None)
        assert 'oauth-2025-04-20' in result
        assert 'claude-code-20250219' not in result

    def test_full_agent_shape(self):
        body = {
            'tools': [{'name': 'test'}],
            'system': [{'type': 'text', 'text': 'hi'}],
            'thinking': {'type': 'adaptive'},
            'context_management': {'type': 'auto'},
            'output_config': {'effort': 'high'},
            'diagnostics': {'enabled': True},
        }
        result = select_betas(body)
        assert 'claude-code-20250219' in result
        # context-1m excluded: default on 4.6+, triggers credit check on Max
        assert 'context-1m-2025-08-07' not in result

    def test_fast_mode_adds_beta(self):
        body = {'speed': 'fast'}
        result = select_betas(body)
        assert 'fast-mode-2026-02-01' in result

    def test_no_duplicates(self):
        body = {'speed': 'fast'}
        result = select_betas(body, extra_betas=['oauth-2025-04-20'])
        betas = result.split(',')
        assert len(betas) == len(set(betas))

    def test_structured_output_betas(self):
        body = {'output_config': {'format': {'type': 'json_schema'}}}
        result = select_betas(body)
        assert 'structured-outputs-2025-12-15' in result
        assert 'claude-code-20250219' not in result

    def test_excluded_beta_filtered_from_extra(self):
        # context-1m introduced via extra_betas must be denied, not merged in.
        result = select_betas(None, extra_betas=['context-1m-2025-08-07'])
        assert 'context-1m-2025-08-07' not in result
        # other betas still present
        assert 'oauth-2025-04-20' in result

    def test_excluded_beta_filtered_from_full_agent(self):
        body = {
            'tools': [{'name': 'test'}],
            'system': [{'type': 'text', 'text': 'hi'}],
            'thinking': {'type': 'adaptive'},
            'context_management': {'type': 'auto'},
            'output_config': {'effort': 'high'},
            'diagnostics': {'enabled': True},
        }
        result = select_betas(body, extra_betas=['context-1m-2025-08-07'])
        assert 'context-1m-2025-08-07' not in result


class TestBuildHeaders:
    def test_sets_bearer_auth(self):
        identity = ClaudeCodeIdentity()
        headers = build_headers('sk-ant-oat01-test', identity)
        assert headers['authorization'] == 'Bearer sk-ant-oat01-test'

    def test_removes_api_key(self):
        identity = ClaudeCodeIdentity()
        headers = build_headers('sk-ant-oat01-test', identity, existing_headers={'x-api-key': 'old'})
        assert 'x-api-key' not in headers

    def test_sets_user_agent(self):
        identity = ClaudeCodeIdentity()
        headers = build_headers('sk-ant-oat01-test', identity)
        assert headers['user-agent'] == 'claude-cli/2.1.141 (external, sdk-cli)'

    def test_sets_stainless_headers(self):
        identity = ClaudeCodeIdentity()
        headers = build_headers('sk-ant-oat01-test', identity)
        assert headers['x-stainless-lang'] == 'js'
        assert headers['x-stainless-runtime'] == 'node'
        assert headers['x-stainless-package-version'] == '0.94.0'
        assert headers['x-stainless-runtime-version'] == 'v24.3.0'

    def test_sets_session_id(self):
        identity = ClaudeCodeIdentity()
        headers = build_headers('sk-ant-oat01-test', identity)
        assert headers['x-claude-code-session-id'] == identity.session_id

    def test_sets_x_app(self):
        identity = ClaudeCodeIdentity()
        headers = build_headers('sk-ant-oat01-test', identity)
        assert headers['x-app'] == 'cli'


class TestApplyMetadata:
    def test_injects_user_id(self):
        identity = ClaudeCodeIdentity()
        identity.account_uuid = 'test-uuid-123'
        body = {}
        apply_metadata(body, identity)
        assert 'metadata' in body
        user_id = json.loads(body['metadata']['user_id'])
        assert user_id['device_id'] == identity.device_id
        assert user_id['account_uuid'] == 'test-uuid-123'
        assert user_id['session_id'] == identity.session_id

    def test_no_account_uuid_removes_user_id(self):
        identity = ClaudeCodeIdentity()
        body = {'metadata': {'user_id': 'old'}}
        apply_metadata(body, identity)
        assert 'user_id' not in body['metadata']


class TestOrderBody:
    def test_canonical_order(self):
        body = {
            'stream': True, 'model': 'claude-opus-4-6',
            'max_tokens': 4096, 'messages': [], 'system': [], 'tools': [],
        }
        ordered = order_body(body)
        keys = list(ordered.keys())
        assert keys.index('model') < keys.index('messages')
        assert keys.index('messages') < keys.index('system')
        assert keys.index('system') < keys.index('tools')
        assert keys.index('max_tokens') < keys.index('stream')

    def test_unknown_keys_appended(self):
        body = {'model': 'test', 'custom_field': 'value', 'messages': []}
        ordered = order_body(body)
        keys = list(ordered.keys())
        assert keys[-1] == 'custom_field'

    def test_preserves_all_keys(self):
        body = {'a': 1, 'model': 2, 'b': 3, 'messages': 4}
        ordered = order_body(body)
        assert set(ordered.keys()) == set(body.keys())
