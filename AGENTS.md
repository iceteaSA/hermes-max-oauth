# hermes-max-oauth — Development Guide

Runtime patch for hermes-agent Max OAuth routing. Zero source modifications.

## Architecture

```
hermes-max-oauth/
├── hermes_max_patch.py       # Runtime monkey-patcher (MetaPathFinder hook)
├── install.sh                # Install to any hermes-agent venv
├── uninstall.sh              # Clean removal
├── hermes_max_config.json    # Sanitization config (copied to ~/.hermes/)
├── agent/                    # Modules copied into hermes's agent/ dir
│   ├── cch.py                # xxHash64 billing header signing
│   ├── claude_identity.py    # Claude Code identity headers + beta selection
│   └── prompt_sanitizer.py   # 3-block system prompt layout + sanitization
└── tests/                    # 65 tests
    ├── test_cch.py
    ├── test_claude_identity.py
    ├── test_prompt_sanitizer.py
    ├── test_max_integration.py
    └── test_patch.py
```

## How the patcher works

`hermes_max_patch.py` uses Python's `MetaPathFinder` to intercept the import of
`agent.anthropic_adapter`. When loaded, it wraps two functions:

1. **`build_anthropic_client()`** — OAuth tokens get an httpx event hook that
   signs the body (cch), sets identity headers, orders fields, injects metadata.
   Non-OAuth tokens pass through to the original.

2. **`build_anthropic_kwargs()`** — `is_oauth=True` calls get the system prompt
   replaced with the 3-block layout (billing header, identity prefix, sanitized body).
   `is_oauth=False` passes through to the original.

Activation: `.pth` file in site-packages runs `hermes_max_patch.activate()` at
Python startup. If `agent.anthropic_adapter` is already imported, patches directly.
Otherwise, registers the MetaPathFinder to patch on first import.

## Key constants (pinned to Claude Code 2.1.141)

| Constant | Value |
|----------|-------|
| `CCH_SEED` | `0x6E52736AC806831E` |
| `CLAUDE_CODE_VERSION` | `2.1.141` |
| `CLAUDE_CODE_BUILD_HASH` | `67b` |
| `USER_AGENT` | `claude-cli/2.1.141 (external, sdk-cli)` |
| `STAINLESS_PACKAGE_VERSION` | `0.94.0` |
| `STAINLESS_RUNTIME_VERSION` | `v24.3.0` |
| `ENTRYPOINT` | `sdk-cli` |

## Known constraints

- `context-1m-2025-08-07` beta excluded — triggers "Usage credits required" on Max
  base allowance. Context-1m is default on 4.6+, header unnecessary.
- `json.dumps` separators must be `(',', ':')` in the httpx hook to match SDK compact
  serialization. Default separators cause Content-Length mismatch → HTTP/1.1 error.
- System block 1 (identity prefix) must be ALONE — merging into larger block breaks
  Max routing with 429.
- Product name strings in system prompt trigger content filter — sanitizer handles this.

## Running tests

```bash
# From repo root, using any Python 3.11+ with xxhash + pytest:
pytest tests/ -v

# Or with a hermes-agent venv that has the deps:
/path/to/hermes-agent/.venv/bin/pytest tests/ -v
```


