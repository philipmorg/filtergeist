# MCP & Provider Setup (Exa + Optional Apify)

## 1. Exa MCP (Required for Agent Curation)

`web_search_advanced_exa` is disabled unless explicitly listed in the `tools=` query parameter. Use this exact hosted URL so all four tools (`web_search_exa`, `web_search_advanced_exa`, `web_fetch_exa`, `agent_run`) are enabled:

```text
https://mcp.exa.ai/mcp?tools=web_search_exa,web_search_advanced_exa,web_fetch_exa,agent_run
```

### Claude Code

```bash
# OAuth sign-in:
claude mcp add --transport http exa "https://mcp.exa.ai/mcp?tools=web_search_exa,web_search_advanced_exa,web_fetch_exa,agent_run&login"

# Or API key header:
claude mcp add --transport http exa "https://mcp.exa.ai/mcp?tools=web_search_exa,web_search_advanced_exa,web_fetch_exa,agent_run" \
  --header "x-api-key: ${EXA_API_KEY}"
```

### Codex CLI

```bash
codex mcp add exa --url "https://mcp.exa.ai/mcp?tools=web_search_exa,web_search_advanced_exa,web_fetch_exa,agent_run"
```

### Cursor (`~/.cursor/mcp.json`)

```json
{
  "mcpServers": {
    "exa": {
      "type": "streamable-http",
      "url": "https://mcp.exa.ai/mcp?tools=web_search_exa,web_search_advanced_exa,web_fetch_exa,agent_run",
      "headers": {
        "x-api-key": "${env:EXA_API_KEY}"
      }
    }
  }
}
```

### Verified Exa MCP Parameter Rules

- **`web_search_advanced_exa`:**
  - `type`: always pass `"auto"` (supports every filter).
  - `category`: valid values are `"personal site"`, `"research paper"`, `"news"`, `"company"`, `"github"`, `"pdf"`, `"people"`, `"financial report"`. Omit `category` for general blog/engineering searches.
  - `includeText`: requires **all** strings to appear on the page — pass at most 1 short anchor string, and put alternative phrasings in `additionalQueries`.
  - `excludeDomains`: omit when `includeDomains` is set.
- **`web_fetch_exa`:** batch 5–10 URLs in `urls` and pass `maxCharacters: 12000` for articles (`100000` for `exa.ai/library/podcast` transcripts; default `3000` truncates body text).
- **`agent_run`:** pass `query` OR `runId`, never both. If a long run returns `status: "running"` with `runId: "agent_run_..."`, poll that same run with `agent_run({ runId })`.

---

## 2. Optional Apify Integration (LinkedIn, X, YouTube, JS Web)

Apify is **off by default** (`apify.enabled: false`). Passing `--noapify` or setting `FILTERGEIST_NOAPIFY=1` blocks all Apify execution regardless of stream config.

When enabled for a stream, Filtergeist supports two execution paths:

1. **Direct REST execution via `APIFY_TOKEN`:**
   ```bash
   export APIFY_TOKEN="apify_api_..."
   python3 scripts/filtergeist.py --stream <slug> poll-apify --out /tmp/apify_raw.json
   ```
   Enforces `apify.max_usd_per_run` by tracking cumulative actor `usageTotalUsd` and stopping before launching subsequent actors if the cap is reached.

2. **Apify Official MCP Server (`https://mcp.apify.com`):**
   ```bash
   claude mcp add --transport http apify "https://mcp.apify.com" --header "Authorization: Bearer ${APIFY_TOKEN}"
   python3 scripts/filtergeist.py --stream <slug> poll-apify --emit-mcp-plan --out /tmp/apify_plan.json
   ```
   Your coding agent reads `mcp_calls` from `/tmp/apify_plan.json` and invokes the listed Apify actors through MCP.
