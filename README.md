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

### 1. Explore the Starter Config in Siftpress Studio

Siftpress ships with an example stream (`streams/systems-and-craft.json` — *Systems & Craft Weekly*) preloaded with 8 live public engineering & podcast feeds so you can try everything right away with zero API keys:

```bash
python3 scripts/siftpress.py studio --port 8765
```

Open <http://127.0.0.1:8765> to:
- Toggle output formats (**Markdown**, **Text RSS XML**, **Podcast RSS XML**, **Review Pile**, **JSON**).
- Click **Poll Enabled Feeds Live** under *RSS & Web Sources* to pull live articles & podcast episodes.
- Open **Pins & What-If Lab** -> click **Load Sample Cycle** -> tweak vendor penalties, hype suppressors, or shortlist caps and click **Run What-If** to see rankings update instantly, then inspect `issue_content.md`, `feed.xml`, and `podcast_feed.xml` in **Rendered Outputs**.

### 2. Use as an AI Coding-Agent Skill (Claude Code, Codex, Cursor)

1. Connect Exa's hosted MCP server (enabling `web_search_advanced_exa` and `agent_run`):
   ```bash
   claude mcp add --transport http exa "https://mcp.exa.ai/mcp?tools=web_search_exa,web_search_advanced_exa,web_fetch_exa,agent_run&login"
   ```
2. Load or symlink `SKILL.md` into your agent's skills directory (e.g. `~/.claude/skills/siftpress` or `~/.agents/skills/siftpress`), and ask:
   > *"Use siftpress to curate a new build of `systems-and-craft` in deep mode."*

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
