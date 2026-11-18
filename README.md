# Siftpress

**Siftpress** is an open-source editorial curation skill, CLI, and local configuration studio for turning RSS feeds, Exa web & podcast-transcript searches, Exa Agent research lenses, and optional Apify social/video streams into ranked, quote-verified research guides and RSS feeds.

Zero third-party Python or Node packages are required: Siftpress runs on stock **Python 3.9+** (or `uv run`) using only the standard library.

## Highlights

- **Multi-channel discovery:** Parallel RSS/Atom & podcast polling (with automatic Exa domain fallback), Exa MCP (`Standard` search mode or `Deep` multi-lens `agent_run` mode), full podcast transcript search, and **opt-in Apify actors** (LinkedIn, X/Twitter, YouTube + transcripts, JS web) guarded by a hard per-run USD cap.
- **Editorial anti-slop & scoring controls:** Verbatim quote substring verification, 5-axis scoring, **Publisher / Vendor Blog down-weighting & hard floors**, **Hype-Topic suppressors**, **Focus-Topic boosters**, **Cross-Issue Diversity & duplicate-idea memory**, and **Must-Include editorial pins**.
- **Selectable output formats:** Toggle any combination of **Markdown Research Guide** (`issue_content.md`), **Text RSS 2.0 XML** (`feed.xml`), **Podcast RSS 2.0 + iTunes XML** (`podcast_feed.xml`), **Audit Review Markdown** (`review_content.md`), and **Structured Issue JSON** (`issue-YYYY-MM-DD.json`).
- **Siftpress Studio Web GUI:** Built-in local web app (`127.0.0.1`) to configure streams, test RSS feeds live, edit rubrics and Exa Agent lenses, toggle output formats, and run **zero-cost What-If re-scoring** against saved builds.

---

## 60-Second Quickstart

### Explore the Starter Config in Siftpress Studio

Siftpress ships with an example stream (`streams/systems-and-craft.json` — *Systems & Craft Weekly*) preloaded with 8 live public engineering & podcast feeds so you can try everything right away with zero API keys:

```bash
python3 scripts/siftpress.py studio --port 8765
```

Open <http://127.0.0.1:8765> to:
- Toggle output formats (**Markdown**, **Text RSS XML**, **Podcast RSS XML**, **Review Pile**, **JSON**).
- Click **Poll Enabled Feeds Live** under *RSS & Web Sources* to pull live articles & podcast episodes.
- Open **Pins & What-If Lab** -> click **Load Sample Cycle** -> tweak vendor penalties, hype suppressors, or shortlist caps and click **Run What-If** to see rankings update instantly, then inspect `issue_content.md`, `feed.xml`, and `podcast_feed.xml` in **Rendered Outputs**.

### Use as an AI Coding-Agent Skill (Claude Code, Codex, Cursor)

1. Connect Exa's hosted MCP server (enabling `web_search_advanced_exa` and `agent_run`):
   ```bash
   claude mcp add --transport http exa "https://mcp.exa.ai/mcp?tools=web_search_exa,web_search_advanced_exa,web_fetch_exa,agent_run&login"
   ```
2. Load or symlink `SKILL.md` into your agent's skills directory (e.g. `~/.claude/skills/siftpress` or `~/.agents/skills/siftpress`), and ask:
   > *"Use siftpress to curate a new build of `systems-and-craft` in deep mode."*

---

## Typical Use Cases

Each case below is a real configuration of the same engine: one `streams/<slug>.json`, one `out/<slug>/` directory, and whichever output formats you enable.

### 1. Analyst or consultant tracking a beat

Maintain a weekly reading guide for a niche (a market, a technology, a regulatory area) that mixes your trusted feeds with open-web discovery, so you find the pieces you do not already follow.

- **Configure:** 10–30 RSS/podcast feeds in Tab 2, 4–6 categories with query phrases in Tab 5, and 3 Exa Agent lenses (`fact`, `pov`, `howto`) in `deep` mode.
- **Read:** `issue_content.md` once a week; every pick carries a thesis, a why-this-matters line, and verbatim quotes you can cite.
- **Then:** paste `## Items of Note` into your own newsletter or notes and write your own commentary on top.

```bash
python3 scripts/siftpress.py --stream my-beat search-plan --mode deep > /tmp/plan.json
python3 scripts/siftpress.py --stream my-beat render-guide --pool /tmp/pool.json --evaluated /tmp/evaluated.json
```

### 2. A reading feed in your RSS reader (Feedbin, NetNewsWire, Reeder)

Subscribe to the *curated* result instead of 40 noisy sources, with scoring already applied.

- **Configure:** enable `text_xml` in Tab 1 (or `--formats markdown,text_xml`).
- **Consume:** point your reader at the generated `out/<stream>/feed.xml`, served over any static host or a local file path.
- **Effect:** one feed, ranked, with the thesis and quotes in the item body and a `[category]` prefix in every title.

### 3. A listening queue for your podcast app

Turn podcast-transcript search plus audio picks from your feeds into a subscribable podcast feed.

- **Configure:** enable `podcast_xml` and keep `resolve_enclosures: true` so Siftpress resolves real `.mp3`/`.m4a` URLs into `<enclosure>` tags.
- **Consume:** add `out/<stream>/podcast_feed.xml` to Apple Podcasts, Overcast, or Pocket Casts.
- **Note:** video-only items are skipped from this feed automatically, since podcast apps require playable audio enclosures. They still appear in the Markdown guide and text feed.

### 4. Competitor and industry monitoring

Watch a defined set of companies, publications, or domains for substantive posts while suppressing vendor marketing.

- **Configure:** add their blog `/feed` URLs as RSS sources, list the parent domains in `publisher_penalties.kind_overrides` as `saas` or `agency`, and set `min_banger_score_10` to your tolerance for vendor content.
- **Result:** an ordinary vendor post is down-weighted; a weak one is hard-gated into `review_content.md` with a `GATED` reason instead of reaching your guide.
- **Audit:** `review_content.md` records every candidate with its disposition, so "why is this missing?" always has an answer.

### 5. Social and video signal monitoring (optional Apify lane)

Track a small set of X handles, LinkedIn profiles or topic searches, and YouTube channels alongside your feeds.

- **Configure:** Tab 3, set `apify.enabled: true`, add sources, and set `max_usd_per_run` to a hard ceiling you are comfortable with.
- **Run:**
  ```bash
  export APIFY_TOKEN="apify_api_..."
  python3 scripts/siftpress.py --stream my-beat poll-apify --out /tmp/apify.json
  ```
- **Team without an Apify key:** use `--emit-mcp-plan` and let your coding agent execute the same actors through the Apify MCP server. X/Twitter posts are held to `min_words_shortform` (default 35 words) rather than the article word floor, so a genuinely short post is still judged on its content.

### 6. Publish several briefs from one repository

Run multiple audiences or topics side by side, sharing the engine and nothing else.

- **Configure:** create each with **+ New Stream** in the Studio, or `POST /api/streams`; every stream gets its own sources, rubric, scoring rules, output formats, and `out/<slug>/` history.
- **Isolation:** novelty banlists, cycle state, and the author leaderboard are per stream, so the same article can be a lead pick for one brief and irrelevant to another.

### 7. Tuning editorial judgment without spending money

Calibrate the rubric against real material before committing to provider spend.

- **Configure:** in the Studio, open **Pins & What-If Lab**, click **Load Sample Cycle**, then adjust axis weights, vendor penalties, hype suppressors, or shortlist quotas and click **Run What-If**.
- **Effect:** every candidate is re-scored locally with a visible before/after delta and a per-item score breakdown (`base`, recency, focus, publisher, hype). No network calls, no LLM calls, no cost.
- **Then:** apply the same settings to a real build:
  ```bash
  python3 scripts/siftpress.py --stream my-beat what-if
  ```

### 8. Guaranteeing a pick that discovery would miss

Coverage-driven briefs (funding rounds, policy filings, vendor launches) where a specific URL must appear.

- **Configure:** pin it for a build date, in the Studio (Tab 7) or from the CLI:
  ```bash
  python3 scripts/siftpress.py --stream my-beat add-url "https://example.com/post" \
    --cycle 2026-11-18 --category strategy --note "Lead pick this week"
  ```
- **Effect:** pinned URLs bypass length floors, vendor and hype gates, and shortlist caps, and are marked in the guide as `Editorial pin: must-include`. Pins are inert once their cycle date passes.

### 9. Turning a noisy feed bundle into a short daily list

For a reader who wants fewer items: use `capped_shortlist` mode with a small `total_items` and `max_per_domain`.

- **Configure:** Tab 1 -> Guide Selection Rule -> **Capped Shortlist**; set `total_items`, `max_per_domain`, `max_per_author`, and `min_final_score`.
- **Effect:** category minima are backfilled at a slightly relaxed score floor so a thin week still produces a complete brief, and the relaxed picks are disclosed in a `## Relaxed constraints` section rather than silently padded.

---

## CLI Reference

```bash
# List configured streams
python3 scripts/siftpress.py streams

# Poll curated RSS/Atom & podcast feeds
python3 scripts/siftpress.py --stream systems-and-craft poll-feeds --out /tmp/rss.json

# Optional: poll enabled Apify actors (requires apify.enabled=true and APIFY_TOKEN, or --emit-mcp-plan)
python3 scripts/siftpress.py --stream systems-and-craft poll-apify --out /tmp/apify.json

# Print interpolated Exa MCP search + Agent lens plan
python3 scripts/siftpress.py --stream systems-and-craft search-plan --mode deep

# Pin a must-include URL for an issue cycle
python3 scripts/siftpress.py --stream systems-and-craft add-url "https://jvns.ca/..." --note "Lead pick"

# Canonicalize, deduplicate, banlist, hydrate, and prefilter
python3 scripts/siftpress.py --stream systems-and-craft prepare-pool --rss /tmp/rss.json --exa /tmp/exa.json --pool-out /tmp/pool.json

# Verify verbatim quotes, check HTTP status codes, score, and render configured outputs
python3 scripts/siftpress.py --stream systems-and-craft render-guide --pool /tmp/pool.json --evaluated /tmp/evaluated.json

# Re-score an existing build instantly after editing scoring rules (zero network/LLM spend)
python3 scripts/siftpress.py --stream systems-and-craft what-if

# Run built-in end-to-end invariant checks
python3 scripts/siftpress.py selfcheck
```

---

## Verification Recipe

1. **Deterministic self-check & lint:**
   ```bash
   python3 scripts/siftpress.py selfcheck
   uvx ruff check .
   ```
   *Expected:* `selfcheck ok` and `All checks passed!` (verifying URL canonicalization, Apify opt-out guard, must-include pin survival, verbatim quote stripping, vendor & hype hard gates, valid `feed.xml` + `podcast_feed.xml` XML parsing, `what-if` re-scoring, and repeat-scrape novelty banlists).
2. **Studio GUI end-to-end check:**
   - Launch: `python3 scripts/siftpress.py studio --port 8765` (wait for `Siftpress Studio listening on http://127.0.0.1:8765`).
   - Open `http://127.0.0.1:8765`, toggle output formats, run **Poll Enabled Feeds Live**, click **Load Sample Cycle** + **Run What-If** in Tab 7, and inspect `issue_content.md`, `feed.xml`, and `podcast_feed.xml` in Tab 8.
   - Cleanup: terminate the owned `siftpress.py studio` server process and remove temporary `out/` test artifacts before committing.

## License

MIT
