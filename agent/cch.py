"""
Claude Code billing header (cch) -- xxHash64 full-body signing.

Signs the entire serialized request body, masks to 20 bits, and writes
the 5-char hex hash into the billing header.

Two-pass approach:
1. Build billing header with cch=00000 placeholder
2. Serialize full request body (including placeholder)
3. Compute xxHash64 of serialized body
4. Replace placeholder with computed hash
"""

import hashlib
import re
import xxhash

CCH_SEED: int = 0x6E52736AC806831E
CCH_PATTERN = re.compile(r'\bcch=([0-9a-f]{5});')
CCH_PLACEHOLDER = '00000'

CLAUDE_CODE_VERSION = '2.1.141'
CLAUDE_CODE_BUILD_HASH = '67b'
ENTRYPOINT = 'sdk-cli'


def compute_cch(body_bytes: bytes) -> str:
    """xxHash64 of body bytes, masked to 20 bits, as 5-char hex."""
    h = xxhash.xxh64(body_bytes, seed=CCH_SEED).intdigest()
    return format(h & 0xFFFFF, '05x')


def sign_request_body(body_string: str) -> str:
    """Sign the body by writing the xxHash64 token into the cch field.

    Idempotent across retries: any existing cch value is first reset to the
    placeholder so the hash is always computed over the placeholder body.
    Without this, re-signing an already-signed body (e.g. on an SDK retry)
    would hash over the previous token and produce a different, wrong cch.
    """
    if not CCH_PATTERN.search(body_string):
        return body_string
    # Normalize to the placeholder before hashing for retry-stability.
    normalized = CCH_PATTERN.sub(f'cch={CCH_PLACEHOLDER};', body_string)
    token = compute_cch(normalized.encode('utf-8'))
    return CCH_PATTERN.sub(f'cch={token};', normalized)


def compute_version_suffix(version: str = CLAUDE_CODE_VERSION, date=None) -> str:
    """Day-stable version suffix. Pinned version returns build hash."""
    if version == CLAUDE_CODE_VERSION:
        return CLAUDE_CODE_BUILD_HASH
    if date is None:
        from datetime import datetime, timezone
        date = datetime.now(timezone.utc)
    day_stamp = date.strftime('%Y-%m-%d')
    return hashlib.sha256(f'{day_stamp}{version}'.encode()).hexdigest()[:3]


def build_billing_header(
    version: str = CLAUDE_CODE_VERSION,
    entrypoint: str = ENTRYPOINT,
    date=None,
) -> str:
    """Build billing header string with cch=00000 placeholder."""
    suffix = compute_version_suffix(version, date)
    return (
        f'x-anthropic-billing-header: '
        f'cc_version={version}.{suffix}; '
        f'cc_entrypoint={entrypoint}; '
        f'cch=00000;'
    )
