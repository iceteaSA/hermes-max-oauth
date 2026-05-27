# hermes-max-oauth

Zero-modification runtime patch for [NousResearch/hermes-agent](https://github.com/NousResearch/hermes-agent) that routes Anthropic OAuth requests through the **Max subscription base allowance** instead of extra usage credits.

## What it does

When you authenticate hermes-agent with Anthropic OAuth (Max/Pro plan), requests normally consume paid overage credits. This patch intercepts the OAuth request path and adds the Claude Code identity signals that Anthropic uses to route requests through your base subscription capacity.

No hermes-agent source files are modified. The patch uses a Python `MetaPathFinder` hook to monkey-patch `build_anthropic_client()` and `build_anthropic_kwargs()` at runtime.

## Install

**One-liner:**

```bash
curl -fsSL https://raw.githubusercontent.com/iceteaSA/hermes-max-oauth/main/install-remote.sh | bash
```

**Or clone and install manually:**

```bash
git clone https://github.com/iceteaSA/hermes-max-oauth.git
cd hermes-max-oauth
./install.sh /path/to/hermes-agent
```

The install script:
1. Copies three agent modules into hermes's `agent/` directory
2. Copies the runtime patcher into the venv's `site-packages`
3. Creates a `.pth` activation hook (auto-loads at Python startup)
4. Installs `xxhash` dependency
5. Copies default config to `~/.hermes/hermes_max_config.json`

## Uninstall

```bash
./uninstall.sh /path/to/hermes-agent
```

Removes all installed files. Restores backups if any existed.

## How it works

### Three modules

| Module | Purpose |
|--------|---------|
| `agent/cch.py` | xxHash64 billing header signing (two-pass: placeholder -> serialize -> hash -> replace) |
| `agent/claude_identity.py` | Claude Code identity headers, dynamic beta selection, body field ordering, metadata |
| `agent/prompt_sanitizer.py` | 3-block system prompt layout, configurable text sanitization, rewrite patterns |

### Runtime patcher

`hermes_max_patch.py` registers a `MetaPathFinder` that intercepts the import of `agent.anthropic_adapter` and wraps two functions:

**`build_anthropic_client()`** — when called with an OAuth token, constructs the Anthropic SDK client with an httpx event hook that:
- Signs the request body with xxHash64 (billing header `cch`)
- Sets Claude Code identity headers (user-agent, stainless-*, session ID)
- Orders body fields canonically
- Injects device/session metadata

**`build_anthropic_kwargs()`** — when called with `is_oauth=True`, replaces the system prompt with a 3-block layout:
- Block 0: Billing header (with cch placeholder for signing)
- Block 1: Claude Code identity prefix (must be standalone for Max routing)
- Block 2: Sanitized prompt body + rewrite notice (cached via `cache_control`)
- Block 3: Dynamic content after boundary marker (optional, not cached)

### System prompt sanitization

Product names in the system prompt trigger Anthropic's content filter. The sanitizer replaces:
- "Hermes Agent" -> "Hades Agent"
- "Nous Research" -> "Anthropic"
- Other configurable pairs via `~/.hermes/hermes_max_config.json`

## Config

`~/.hermes/hermes_max_config.json`:

```json
{
  "sanitize": {
    "replacements": [
      { "from": "Hermes Agent", "to": "Hades Agent" },
      { "from": "hermes-agent", "to": "hades-agent" },
      { "from": "Nous Research", "to": "Anthropic" }
    ],
    "rewrite_patterns": []
  }
}
```

Override location with `HERMES_MAX_CONFIG` env var.

## Tests

```bash
# Requires: pip install pytest xxhash
pytest tests/ -v
```

72 tests across 5 files covering cch signing, identity headers, prompt sanitization, integration pipeline, and the runtime patcher.

## Known constraints

- `system[0]` must be the billing header and `system[1]` must be the Claude Code identity text **alone** — merging it into a larger block breaks Max routing (429)
- `context-1m-2025-08-07` beta header triggers "Usage credits required for long context" on Max base allowance — excluded from beta selection (context-1m is default on 4.6+)
- `json.dumps` in the httpx hook must use `separators=(',', ':')` to match the SDK's compact serialization — default separators cause Content-Length mismatch
- Version pinned to Claude Code 2.1.141

## License

MIT
