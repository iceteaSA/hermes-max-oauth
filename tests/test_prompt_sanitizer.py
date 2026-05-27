import pytest
from agent.prompt_sanitizer import (
    sanitize_system_text,
    split_system_prompt,
    build_system_blocks,
    load_config,
    CLAUDE_CODE_IDENTITY_TEXT,
    DEFAULT_REPLACEMENTS,
    REWRITE_NOTICE,
)


class TestSanitizeSystemText:
    def test_replaces_hermes_agent(self):
        result = sanitize_system_text('You are Hermes Agent, a coding assistant.')
        assert 'Hermes Agent' not in result
        assert 'Hades Agent' in result

    def test_replaces_lowercase(self):
        result = sanitize_system_text('the hermes-agent tool')
        assert 'hermes-agent' not in result
        assert 'hades-agent' in result

    def test_replaces_nous_research(self):
        result = sanitize_system_text('Built by Nous Research')
        assert 'Nous Research' not in result
        assert 'Anthropic' in result

    def test_preserves_unrelated_text(self):
        text = 'Use Claude to write Python code'
        assert sanitize_system_text(text) == text

    def test_multiple_replacements_in_one_string(self):
        text = 'Hermes Agent by Nous Research uses hermes-agent'
        result = sanitize_system_text(text)
        assert 'Hermes Agent' not in result
        assert 'Nous Research' not in result
        assert 'hermes-agent' not in result

    def test_custom_config_replacements(self):
        config = {'sanitize': {'replacements': [{'from': 'Foo', 'to': 'Bar'}]}}
        result = sanitize_system_text('Hello Foo world', config)
        assert result == 'Hello Bar world'

    def test_rewrite_patterns(self):
        config = {'sanitize': {
            'replacements': [],
            'rewrite_patterns': [{'match': 'Hello \\w+', 'replace': 'Hi'}],
        }}
        result = sanitize_system_text('Hello World', config)
        assert result == 'Hi'


class TestSplitSystemPrompt:
    def test_splits_at_current_date(self):
        prompt = 'Static instructions here.\n## Current Date\n2026-05-22'
        static, dynamic = split_system_prompt(prompt)
        assert static == 'Static instructions here.'
        assert dynamic == '\n## Current Date\n2026-05-22'

    def test_splits_at_project_context(self):
        prompt = 'Static.\n# Project Context\nDetails here'
        static, dynamic = split_system_prompt(prompt)
        assert static == 'Static.'
        assert dynamic.startswith('\n# Project Context')

    def test_no_boundary_returns_none(self):
        prompt = 'Just a plain system prompt with no markers'
        static, dynamic = split_system_prompt(prompt)
        assert static == prompt
        assert dynamic is None

    def test_first_marker_wins(self):
        prompt = 'A\n## Current Date\nB\n# Project Context\nC'
        static, dynamic = split_system_prompt(prompt)
        assert static == 'A'
        assert '## Current Date' in dynamic


class TestBuildSystemBlocks:
    def test_returns_at_least_3_blocks(self):
        blocks = build_system_blocks('Hello world', 'x-anthropic-billing-header: cch=00000;')
        assert len(blocks) >= 3

    def test_block_0_is_billing_header(self):
        blocks = build_system_blocks('Hello', 'billing-header-text')
        assert blocks[0]['text'] == 'billing-header-text'

    def test_block_1_is_identity_standalone(self):
        blocks = build_system_blocks('Hello', 'billing')
        assert blocks[1]['text'] == CLAUDE_CODE_IDENTITY_TEXT

    def test_block_2_has_cache_control(self):
        blocks = build_system_blocks('Hello', 'billing')
        assert blocks[2].get('cache_control') == {'type': 'ephemeral'}

    def test_block_2_contains_rewrite_notice(self):
        blocks = build_system_blocks('Hello', 'billing')
        assert REWRITE_NOTICE in blocks[2]['text']

    def test_with_dynamic_content(self):
        prompt = 'Static part\n## Current Date\n2026-05-22'
        blocks = build_system_blocks(prompt, 'billing')
        assert len(blocks) == 4
        assert '## Current Date' in blocks[3]['text']
        assert 'cache_control' not in blocks[3]

    def test_sanitizes_hermes_references(self):
        prompt = 'You are Hermes Agent by Nous Research'
        blocks = build_system_blocks(prompt, 'billing')
        full_text = ' '.join(b['text'] for b in blocks)
        assert 'Hermes Agent' not in full_text
        assert 'Nous Research' not in full_text
