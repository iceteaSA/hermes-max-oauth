"""Integration tests for the full Max OAuth request transformation pipeline."""

import json
import re
import pytest
from agent.prompt_sanitizer import build_system_blocks
from agent.cch import build_billing_header, sign_request_body
from agent.claude_identity import (
    ClaudeCodeIdentity, build_headers, apply_metadata,
    order_body, CLAUDE_CODE_IDENTITY_TEXT,
)


class TestFullRequestPipeline:
    """Test the complete request transformation pipeline."""

    def test_system_blocks_in_signed_body(self):
        billing = build_billing_header()
        blocks = build_system_blocks(
            'You are Hermes Agent.\n## Current Date\n2026-05-22',
            billing,
        )
        body = {
            'model': 'claude-opus-4-6',
            'system': blocks,
            'messages': [{'role': 'user', 'content': [{'type': 'text', 'text': 'hi'}]}],
            'tools': [],
            'max_tokens': 4096,
            'stream': True,
        }
        body = order_body(body)
        body_str = json.dumps(body)
        signed = sign_request_body(body_str)

        # cch replaced
        assert 'cch=00000;' not in signed
        assert re.search(r'cch=[0-9a-f]{5};', signed)

        # body structure
        parsed = json.loads(signed)
        assert parsed['system'][0]['text'].startswith('x-anthropic-billing-header:')
        assert parsed['system'][1]['text'] == CLAUDE_CODE_IDENTITY_TEXT
        assert 'Hermes Agent' not in parsed['system'][2]['text']
        assert 'Hades Agent' in parsed['system'][2]['text']
        assert parsed['system'][3]['text'].startswith('\n## Current Date')

    def test_field_order_is_canonical(self):
        body = {
            'stream': True,
            'model': 'claude-opus-4-6',
            'tools': [],
            'messages': [],
            'system': [],
            'max_tokens': 4096,
        }
        ordered = order_body(body)
        keys = list(ordered.keys())
        assert keys[0] == 'model'
        assert keys[1] == 'messages'
        assert keys[2] == 'system'
        assert keys[3] == 'tools'

    def test_headers_complete_for_full_agent(self):
        identity = ClaudeCodeIdentity()
        body = {
            'tools': [{'name': 'test'}],
            'system': [{'type': 'text', 'text': 'hi'}],
            'thinking': {'type': 'adaptive'},
            'context_management': {'type': 'auto'},
            'output_config': {'effort': 'high'},
            'diagnostics': {'enabled': True},
        }
        headers = build_headers('sk-ant-oat01-test', identity, body=body)
        required = [
            'authorization', 'user-agent', 'anthropic-beta', 'x-app',
            'x-stainless-lang', 'x-stainless-os', 'x-stainless-arch',
            'x-claude-code-session-id', 'x-client-request-id',
        ]
        for key in required:
            assert key in headers, f'Missing header: {key}'
        assert 'claude-code-20250219' in headers['anthropic-beta']

    def test_hook_simulation(self):
        """Simulate the full httpx hook pipeline end-to-end."""
        identity = ClaudeCodeIdentity()
        billing = build_billing_header()
        blocks = build_system_blocks('Test prompt', billing)
        body = {
            'model': 'claude-opus-4-6',
            'system': blocks,
            'messages': [{'role': 'user', 'content': 'hi'}],
            'max_tokens': 4096,
            'stream': True,
        }
        # Simulate hook steps
        apply_metadata(body, identity)
        body = order_body(body)
        body_str = json.dumps(body, ensure_ascii=False)
        signed = sign_request_body(body_str)

        # Valid JSON after signing
        parsed = json.loads(signed)
        assert parsed['model'] == 'claude-opus-4-6'
        # cch signed
        assert 'cch=00000;' not in signed
        assert re.search(r'cch=[0-9a-f]{5};', signed)
        # field order
        keys = list(parsed.keys())
        assert keys[0] == 'model'
