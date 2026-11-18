---
name: siftpress
description: Curate ranked editorial research guides and feeds (Markdown, Text RSS XML, Podcast RSS XML, Audit Review Markdown, and JSON) from curated RSS/podcast feeds, Exa MCP search and Exa Agent lenses, and optional Apify social/video actors, configured via JSON or the local Siftpress Studio web app.
---

# Siftpress — Editorial Curation Skill

Siftpress turns noisy RSS feeds, open-web searches, podcast transcripts, and optional social/video streams into ranked, source-verified research guides and RSS feeds.

## What This Does, In Plain Language

Nothing is emailed or auto-published to subscribers. Every run produces clean local files (`out/<stream>/`) that a human editor reads in Markdown or subscribes to in an RSS or podcast app:

1. **Collect candidates** from your stream's curated RSS/podcast feeds, fresh web and podcast-transcript searches via Exa MCP, and (only when enabled) Apify social/video scrapers.
2. **Screen out obvious noise** — links already covered in past builds, blocked domains, unresolved link shorteners, job ads, and stubs too short to judge.
3. **Fetch real full text** for items that arrived as short summaries.
4. **Review each survivor with an LLM** — producing a one-sentence thesis, a "why this matters" takeaway, up to three exact verbatim quotes, and five `0–5` quality scores.
5. **Score, verify quotes, and write your chosen output formats** (`issue_content.md`, `feed.xml` Text RSS, `podcast_feed.xml` Podcast RSS, `review_content.md`, and/or `issue-YYYY-MM-DD.json`).

### Vocabulary You Will See

| Term | Plain meaning |
| --- | --- |
| **stream** | One publication or topic beat, configured in `streams/<slug>.json` (default starter: `systems-and-craft`). |
| **candidate** | One article, social post, video, or podcast episode discovered during a run. |
| **lane** | Which channel found a candidate (`push_rss`, `push_podcast`, `exa_search_<cat>`, `exa_agent_<lens>`, `exa_podcasts`, `apify_<kind>`, or `editorial_must_include`). |
| **cycle / build** | One issue in progress, named by an ISO date (`2026-11-18`). Builds stay open and accumulate new picks across runs until you start a new date. |
| **hydrate** | Fetch full page text when an RSS entry, search hit, or Agent result supplied only a headline or short summary. |
| **prefilter** | Fast, zero-LLM checks (word count, blocked domains, promo/job regex, paywall stubs) run before editorial review. |
| **triage** | Quick `0–4` relevance screen so off-brief items stop before consuming a full review. |
| **spine** | The full structured LLM review of one candidate: `thesis`, `novelty_claim`, `best_quotes`, `topic_tags`, `slop` rating, and five `0–5` `axes`. Stored under the JSON key `"spine"`. |
| **native lens gate** | Quality bar (`0–10`, default `>= 7.5`) applied to Exa Agent discoveries using the rubric for the lens that found them (`fact`, `pov`, or `howto`). |
| **must-include pin** | A specific URL pinned by the editor for a build date; bypasses length floors, vendor/hype gates, and shortlist caps. |
| **what-if** | Instant zero-cost re-scoring of an already-saved build after tweaking weights, hype suppressors, vendor penalties, or shortlist quotas. |

---

## Files in This Repository

- `streams/<slug>.json` — Stream configuration (starter example: `streams/systems-and-craft.json`).
- `scripts/siftpress.py` — Zero-dependency Python 3.9+ CLI (`python3` or `uv run`) for feed polling, optional Apify execution, Exa plan generation, pooling, quote-verified rendering, `what-if` simulation, and launching **Siftpress Studio**.
- `references/mcp-setup.md` — One-line Exa MCP and optional Apify setup for Claude Code, Codex, and Cursor, plus verified tool parameter rules.
- `references/schema-and-rubric.md` — Complete `spine` JSON schema, scoring equations, publisher down-weighting, hype-topic suppressors, and output format specs.

---

## Run Parameters & Modes

Every parameter has a working default in `streams/<slug>.json` (editable visually via `python3 scripts/siftpress.py studio`).

1. **`stream` — which publication config to run** (default `systems-and-craft`). Pass `--stream <slug>` on every CLI command.
2. **`mode` — how much Exa discovery firepower to use** (`deep` | `standard`):
   - **`deep`** (default when `agent_run` is available): runs the stream's Exa Agent researcher lenses (`fact`, `pov`, `howto` at ~$0.025/lens on `low` effort) plus complementary `web_search_advanced_exa` category and podcast-transcript searches.
   - **`standard`** (fast sweep, or when `agent_run` is unavailable): uses regular Exa searches across all categories, the high-signal domain allowlist, and podcast transcripts.
3. **`apify` — optional social & video scraping** (`enabled: false` by default): when enabled in `streams/<slug>.json` (and neither `--noapify` nor `SIFTPRESS_NOAPIFY=1` is set), polls configured LinkedIn, X/Twitter, YouTube, or JS-web sources up to `apify.max_usd_per_run`.
4. **`cycle` — which issue build you are adding to** (`YYYY-MM-DD`, optional): omit to continue `out/<stream>/CURRENT_BUILD` (or start today's UTC date); pass a new date to start a fresh issue.
5. **`lookback_days` — how far back to search** (default `14`; editor-owned feeds use `owned_source_lookback_days`, default `90`).
6. **`max_spines` — how many candidates receive a full LLM review (`spine`) per run** (default `40`). Overflow candidates are still logged when `review_markdown` is enabled.
7. **`outputs.formats` — which files to write**: configured in the GUI or `streams/<slug>.json` (and overridable per run via `--formats markdown,text_xml,podcast_xml,review_markdown,json`):
   - `markdown` -> `out/<stream>/issue_content.md`
   - `text_xml` -> `out/<stream>/feed.xml` (RSS 2.0 reading feed)
   - `podcast_xml` -> `out/<stream>/podcast_feed.xml` (iTunes podcast RSS with audio `<enclosure>` tags)
   - `review_markdown` -> `out/<stream>/review_content.md` (full audit pile)
   - `json` -> `out/<stream>/issue-YYYY-MM-DD.json`

---

## End-to-End Curation Workflow

Set `WORKDIR="$(mktemp -d /tmp/siftpress.XXXXXX)"`, `<REPO>` to this folder, and `<STREAM>` to the target stream slug.

### Step 1 — Poll Curated Feeds (and Optional Apify Sources)

```bash
python3 "<REPO>/scripts/siftpress.py" --stream "<STREAM>" poll-feeds \
  --out "$WORKDIR/rss_raw.json"
```

- If HTTP feed access is restricted, pass `--mcp-only` to emit Exa domain/show batches for every enabled source.
- Check `exa_fallback_batches` in `$WORKDIR/rss_raw.json` and run each batch via `web_search_advanced_exa` so non-XML sources are covered.
- **If `apify.enabled` is `true` in the stream config:** run `poll-apify --out "$WORKDIR/apify_raw.json"` (uses `APIFY_TOKEN` with `max_usd_per_run` enforcement, or `--emit-mcp-plan` to execute via Apify MCP).

### Step 2 — Run Exa MCP Discovery (`deep` or `standard`)

```bash
python3 "<REPO>/scripts/siftpress.py" --stream "<STREAM>" search-plan \
  --mode deep > "$WORKDIR/search_plan.json"
```

Follow `references/mcp-setup.md` to execute the planned calls concurrently:

- **`agent_calls` (`deep` mode):** call `agent_run` with each entry's `arguments` (`query`, `effort`, `outputSchema`, `input.exclusion`). If `agent_run` returns `status: "running"` with a `runId`, resume with `agent_run({ runId })` rather than starting a duplicate run.
- **`search_calls`:** call `web_search_advanced_exa` (or `web_search_exa` fallback) for each category, allowlist, and `exa.ai/library/podcast` transcript query.

Save all Exa results to `$WORKDIR/exa_raw.json`, preserving `lane`, `content_lens`, `title`, `url`, `author`, `publishedDate`, and `text`.

### Step 3 — Merge, Deduplicate, Fetch Missing Text, and Screen Out Junk

```bash
python3 "<REPO>/scripts/siftpress.py" --stream "<STREAM>" prepare-pool \
  --rss "$WORKDIR/rss_raw.json" \
  --exa "$WORKDIR/exa_raw.json" \
  --pool-out "$WORKDIR/pool.json"
```

(Include `--apify "$WORKDIR/apify_raw.json"` when Apify was run.)

**Hydration check:** inspect `needs_exa_fetch` in `$WORKDIR/pool.json` and confirm every `exa_agent_*` candidate has full page text (`word_count >= min_words_article`). Batch-fetch missing URLs via `web_fetch_exa` (`maxCharacters: 12000` for articles, `100000` for podcast transcripts), save to `$WORKDIR/hydrated.json`, and re-run `prepare-pool` with `--hydrated "$WORKDIR/hydrated.json" --no-http-hydrate`.

### Step 4 — Review Survivors (Quick Screen, Lens Check, Full Review)

Read the stream's `audience`, `categories`, and `agent_lenses` alongside `references/schema-and-rubric.md`, then evaluate `passed` candidates in `$WORKDIR/pool.json`:

1. **Quick relevance screen (`0–4`):** advance candidates meeting `audience.triage_min_relevance` (default `3`), `"uncertain"` items, `must_include` pins, and any low-scoring item marked `"explore_probe": true` (10% false-negative check), up to `max_spines`. Record skipped items with `"jev": {"relevance": <0-2>, "decision": "likely_reject"}`.
2. **Lens check (`deep` mode `exa_agent_*` items):** score fetched article text `0–10` against the lens's `gate_rubric`. Items below `min_gate_score_10` receive `"spine": {"reject": true, "reject_reason": "Failed native <lens> gate (<score>/10): ...", ...}`.
3. **Full review (`spine`):** read the candidate's `text` and emit the JSON object in `references/schema-and-rubric.md` (`reject`, `reject_reason`, `slop`, `category`, `thesis`, `novelty_claim`, up to 3 **exact verbatim** `best_quotes`, `topic_tags`, and `0–5` `axes`).

Write the list of evaluation objects to `$WORKDIR/evaluated.json`.

### Step 5 — Verify Quotes, Score, and Render Configured Outputs

```bash
python3 "<REPO>/scripts/siftpress.py" --stream "<STREAM>" render-guide \
  --pool "$WORKDIR/pool.json" \
  --evaluated "$WORKDIR/evaluated.json" \
  --model "<active-agent-model>"
```

`render-guide` strips any quote that does not appear verbatim in source text, checks live HTTP status codes, applies focus boosts, publisher down-weighting, hype suppressors, and cross-issue diversity memory, updates `out/<STREAM>/cycle-<YYYY-MM-DD>.json` + `author_leaderboard.json`, and writes every format enabled under `outputs.formats`.

---

## Configuring Visually with Siftpress Studio

Launch the local web GUI anytime to configure streams, test RSS feeds live, toggle output formats (`Markdown`, `Text XML`, `Podcast XML`, `Review Markdown`, `JSON`), manage Apify sources, and run instant `what-if` scoring simulations:

```bash
python3 "<REPO>/scripts/siftpress.py" studio --port 8765
```
