import re
import pytest
from datetime import datetime, timezone
from agent.cch import compute_cch, sign_request_body, build_billing_header, compute_version_suffix


class TestComputeCCH:
    def test_returns_5_char_hex(self):
        result = compute_cch(b'{"model":"claude-opus-4-6","messages":[]}')
        assert len(result) == 5
        assert all(c in '0123456789abcdef' for c in result)

    def test_deterministic(self):
        body = b'{"model":"claude-opus-4-6","messages":[]}'
        assert compute_cch(body) == compute_cch(body)

    def test_different_bodies_different_hashes(self):
        a = compute_cch(b'{"model":"claude-opus-4-6"}')
        b = compute_cch(b'{"model":"claude-sonnet-4-6"}')
        assert a != b

    def test_empty_body(self):
        result = compute_cch(b'')
        assert len(result) == 5


class TestSignRequestBody:
    def test_replaces_placeholder(self):
        body = '{"system":[{"type":"text","text":"x-anthropic-billing-header: cc_version=2.1.141.67b; cc_entrypoint=sdk-cli; cch=00000;"}]}'
        signed = sign_request_body(body)
        assert 'cch=00000;' not in signed
        assert 'cch=' in signed

    def test_no_placeholder_passthrough(self):
        body = '{"model":"claude-opus-4-6"}'
        assert sign_request_body(body) == body

    def test_signed_body_has_valid_hash(self):
        body = '{"system":[{"type":"text","text":"x-anthropic-billing-header: cc_version=2.1.141.67b; cc_entrypoint=sdk-cli; cch=00000;"}]}'
        signed = sign_request_body(body)
        match = re.search(r'cch=([0-9a-f]{5});', signed)
        assert match is not None
        assert match.group(1) != '00000'

    def test_idempotent_across_retries(self):
        # Re-signing an already-signed body (SDK retry) must produce the
        # same token, because hashing always runs over the placeholder.
        body = '{"system":[{"type":"text","text":"x-anthropic-billing-header: cc_version=2.1.141.67b; cc_entrypoint=sdk-cli; cch=00000;"}]}'
        once = sign_request_body(body)
        twice = sign_request_body(once)
        assert once == twice

    def test_resign_matches_fresh_sign(self):
        # Signing a body whose placeholder was pre-filled with a junk hash
        # yields the same result as signing the clean placeholder body.
        body = '{"x":"cch=00000;"}'
        fresh = sign_request_body(body)
        prefilled = sign_request_body(body.replace('cch=00000;', 'cch=abcde;'))
        assert fresh == prefilled


class TestComputeVersionSuffix:
    def test_pinned_version_returns_build_hash(self):
        assert compute_version_suffix('2.1.141') == '67b'

    def test_other_version_returns_3_char_hex(self):
        result = compute_version_suffix('9.9.9', datetime(2026, 5, 22, tzinfo=timezone.utc))
        assert len(result) == 3
        assert all(c in '0123456789abcdef' for c in result)

    def test_day_stable(self):
        d = datetime(2026, 5, 22, 10, 0, tzinfo=timezone.utc)
        d2 = datetime(2026, 5, 22, 23, 59, tzinfo=timezone.utc)
        assert compute_version_suffix('9.9.9', d) == compute_version_suffix('9.9.9', d2)

    def test_different_days_different_suffix(self):
        d1 = datetime(2026, 5, 22, tzinfo=timezone.utc)
        d2 = datetime(2026, 5, 23, tzinfo=timezone.utc)
        assert compute_version_suffix('9.9.9', d1) != compute_version_suffix('9.9.9', d2)


class TestBuildBillingHeader:
    def test_contains_placeholder(self):
        header = build_billing_header()
        assert 'cch=00000;' in header

    def test_contains_version(self):
        header = build_billing_header()
        assert 'cc_version=2.1.141.67b;' in header

    def test_contains_entrypoint(self):
        header = build_billing_header()
        assert 'cc_entrypoint=sdk-cli;' in header

    def test_starts_with_header_name(self):
        header = build_billing_header()
        assert header.startswith('x-anthropic-billing-header:')
