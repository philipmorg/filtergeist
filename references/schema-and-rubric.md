# Evaluation Schema, Scoring Rules & Output Formats

## 1. Stage 2 Full Review (`spine`) JSON Schema

For each candidate advancing past quick relevance triage (and past its native lens gate when discovered by `agent_run`), emit an object in `evaluated.json`:

```json
{
  "canonical_url": "https://example.org/post",
  "jev": {
    "relevance": 4,
    "decision": "likely_keep"
  },
  "spine": {
    "reject": false,
    "reject_reason": null,
    "slop": "clean",
    "category": "architecture",
    "thesis": "One crisp sentence stating the piece's actual claim or mechanism.",
    "novelty_claim": "What an experienced practitioner learns here that changes a concrete design, debugging, or operating decision.",
    "best_quotes": [
      "Exact verbatim substring copied character-for-character from candidate text (at most 40 words)."
    ],
    "topic_tags": ["postmortem", "databases", "backpressure"],
    "axes": {
      "reframe_sharp_thesis": 5,
      "voice_risk": 4,
      "evidence_anchoring": 5,
      "audience_fit": 5,
      "operator_utility": 5
    }
  }
}
```

### Five `0–5` Quality Axes

- `reframe_sharp_thesis`: `5` = reframes a widespread assumption with clear causal logic; `2` = names a topic without a sharp mechanism.
- `voice_risk`: `5` = takes a falsifiable, experience-backed stance risking disagreement; `1` = hedges everything.
- `evidence_anchoring`: `5` = reproducible benchmark, dataset, or concrete production postmortem; `1` = assertion only.
- `audience_fit`: `5` = bullseye for the stream's configured `audience.profile`; `1` = off-brief.
- `operator_utility`: `5` = directly actionable by a reader this week; `1` = purely abstract.

---

## 2. Deterministic Scoring & Filtering Pipeline

`siftpress/scoring.py` computes each non-rejected candidate's `final_score` transparently and records `score_breakdown`:

1. **Weighted Axis Base (`0–10`):** weighted sum of `scoring.axes_weights` (default `0.45*audience_fit + 0.30*operator_utility + 0.20*reframe_sharp_thesis + 0.05*evidence_anchoring`) scaled to `0–10`.
2. **Recency Decay:** `0.5 ** (age_days / recency_half_life_days)` (`+0.0` to `+1.0`).
3. **Focus Anchors & Focus-Topic Boosters:** `min(anchor_hits, 3) * 0.2` plus weighted `focus_topics` boosts (capped at `+0.7`).
4. **Trusted-Source Boost:** `trusted_sources` host bonus (capped at `0.5`) when `audience_fit >= 3`.
5. **Slop Penalty:** `-0.8` for `borderline`, `-2.0` for `slop`.
6. **Publisher / Vendor Blog Down-Weighting (`publisher_penalties`):**
   - Editor-owned sources (`owned_source: true`) and `exempt_domains` are exempt.
   - `kind_overrides` classify hosts deterministically; otherwise a conservative heuristic checks structural blog paths/subdomains + header/footer CTA tokens (requiring explicit lead-gen CTAs for `agency`).
   - Blogs below `min_banger_score_10[kind]` are **hard-gated** (`final_score = null`, logged in `review_content.md`); survivors above the gate receive a penalty that fades linearly to `0` across `[waive_banger_score_10, waive + fade_width]`.
7. **Hype-Topic Suppressors (`hype_topics`):**
   - Centrally focused hype pieces below `min_banger_score_10` are hard-gated; survivors receive a fading penalty and optional `max_selected` cap.
8. **Cross-Issue Diversity Memory (`scoring.diversity`):**
   - Rewards domains absent from recent builds (`+new_domain_bonus`), gently penalizes domains/authors selected in the immediately prior build, and penalizes 2-gram thesis overlap (`jaccard >= similarity_threshold`).

---

## 3. Selectable Output Formats (`outputs.formats`)

- **`markdown` (`out/<stream>/issue_content.md`):** Newest-first cumulative research guide with category headings, source HTTP status, author/lane/score, thesis, why-this-matters, audit tags (pinned/down-weighted/hype-suppressed), verified verbatim quotes, transcript links, ChatGPT/Perplexity how-to prompt links, and bottom `## Items of Note`.
- **`text_xml` (`out/<stream>/feed.xml`):** Validated RSS 2.0 XML (`xmlns:content`) for RSS readers (Feedbin, NetNewsWire, Reeder).
- **`podcast_xml` (`out/<stream>/podcast_feed.xml`):** Validated RSS 2.0 + iTunes podcast XML (`xmlns:itunes`) containing audio picks with resolved `<enclosure>` MP3/M4A URLs for podcast apps.
- **`review_markdown` (`out/<stream>/review_content.md`):** Complete discovery audit log for human review.
- **`json` (`out/<stream>/issue-YYYY-MM-DD.json`):** Machine-readable issue export with full score breakdowns.
