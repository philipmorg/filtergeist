"""Editorial scoring: quote provenance, focus/hype topics, publisher gates, diversity, selection & what-if."""
from __future__ import annotations

import copy
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from . import UTC, WS_RE, domain_of, parse_date

DEFAULT_AXES_WEIGHTS = {
    "audience_fit": 0.45,
    "operator_utility": 0.30,
    "reframe_sharp_thesis": 0.20,
    "evidence_anchoring": 0.0,
    "voice_risk": 0.0,
}

COMPANY_BLOG_PATH_HINTS = (
    "/blog", "/resources", "/insights", "/learn", "/guides", "/customers",
    "/customer-stories", "/case-studies", "/whitepaper", "/whitepapers", "/ebooks", "/webinars",
)
COMPANY_BLOG_SUBDOMAIN_HINTS = ("blog.", "resources.", "insights.", "learn.")
SAAS_SIGNAL_TOKENS = (
    "request a demo", "book a demo", "schedule a demo", "contact sales", "talk to sales",
    "pricing", "pricing plans", "free trial", "start free trial", "try it free", "get started",
    "product", "platform", "features", "solutions", "use cases", "integrations", "api", "customers",
)
AGENCY_SIGNAL_TOKENS = (
    "our services", "services", "portfolio", "our work", "case studies", "case study",
    "our clients", "client results", "hire us", "work with us", "book a call", "schedule a call",
    "request a proposal", "get a quote", "contact us",
)
AGENCY_CTA_TOKENS = (
    "hire us", "work with us", "request a proposal", "get a quote",
    "book a call", "schedule a call", "client results", "our clients",
)

STOPWORDS = frozenset({
    "the", "and", "for", "that", "this", "with", "from", "have", "has", "had", "are", "was",
    "were", "been", "being", "into", "than", "then", "them", "they", "their", "there", "where",
    "when", "what", "which", "while", "will", "would", "could", "should", "about", "after",
    "before", "over", "under", "more", "most", "some", "such", "only", "also", "very", "just",
})

_QUOTE_TRANSLATE = str.maketrans({
    "\u2018": "'", "\u2019": "'", "\u201c": '"', "\u201d": '"',
    "\u2013": "-", "\u2014": "-", "\u00a0": " ",
})


def _norm_quote_match(text: str) -> str:
    return WS_RE.sub(" ", (text or "").translate(_QUOTE_TRANSLATE)).strip().lower()


def verify_quotes(candidate: dict[str, Any]) -> tuple[list[str], int]:
    spine = candidate.get("spine") or {}
    norm_body = _norm_quote_match(candidate.get("text") or "")
    kept: list[str] = []
    dropped = 0
    for q in spine.get("best_quotes") or []:
        if not isinstance(q, str) or not q.strip():
            continue
        if _norm_quote_match(q) and _norm_quote_match(q) in norm_body:
            kept.append(" ".join(q.split()))
        else:
            dropped += 1
        if len(kept) >= 3:
            break
    spine["best_quotes"] = kept
    return kept, dropped


def _axis_val(spine: dict[str, Any], key: str) -> int | None:
    v = (spine.get("axes") or {}).get(key)
    if isinstance(v, (int, float)):
        return max(0, min(5, round(v)))
    return None


def topic_focus_score(cand: dict[str, Any], topic_cfg: dict[str, Any]) -> float:
    if not topic_cfg.get("enabled", True):
        return 0.0
    spine = cand.get("spine") or {}
    cand_tags = {str(t).strip().lower() for t in (spine.get("topic_tags") or [])}
    cfg_tags = {str(t).strip().lower() for t in (topic_cfg.get("topic_tags") or [])}
    score = 0.55 if (cand_tags & cfg_tags) else 0.0

    title = (cand.get("title") or "").lower()
    thesis = (spine.get("thesis") or "").lower()
    excerpt = (cand.get("text") or "")[:3000].lower()
    for raw_term in topic_cfg.get("terms") or []:
        term = str(raw_term).strip().lower()
        if not term:
            continue
        if term in title:
            score += 0.25
        elif term in thesis:
            score += 0.20
        elif term in excerpt:
            score += 0.12
    return min(1.0, round(score, 3))


def _heuristic_company_blog(url: str, title: str, text: str) -> tuple[str | None, int]:
    url_lower = (url or "").lower()
    path_hint = any(hint in url_lower for hint in COMPANY_BLOG_PATH_HINTS)
    subdomain_hint = any(domain_of(url).startswith(hint) for hint in COMPANY_BLOG_SUBDOMAIN_HINTS)
    body = (text or "").lower()
    tlen = len(body)
    zones = "\n".join([(title or "").lower(), body[: int(tlen * 0.18)], body[int(tlen * 0.82) :]])
    saas_hits = {tok for tok in SAAS_SIGNAL_TOKENS if tok in zones}
    agency_hits = {tok for tok in AGENCY_SIGNAL_TOKENS if tok in zones}
    agency_cta_hits = {tok for tok in AGENCY_CTA_TOKENS if tok in zones}

    signal_count = (1 if path_hint else 0) + (1 if subdomain_hint else 0)
    signal_count += min(len(saas_hits), 6) + min(len(agency_hits), 6)
    if not ((path_hint or subdomain_hint) and (len(saas_hits) + len(agency_hits)) >= 2 and signal_count >= 3):
        return None, 0
    if len(saas_hits) >= max(2, len(agency_hits) + 1):
        return "saas", signal_count
    if len(agency_hits) >= 2 and agency_cta_hits:
        return "agency", signal_count
    return "general", signal_count


def company_blog_meta(url: str, title: str, text: str, cfg: dict[str, Any]) -> dict[str, Any]:
    pc = cfg.get("publisher_penalties") or {}
    if not pc.get("enabled", False):
        return {"company_blog": False}
    host = domain_of(url)
    exempt = {str(d).lower() for d in (pc.get("exempt_domains") or [])}
    exempt |= {
        str(s.get("domain") or "").lower()
        for s in (cfg.get("content_sources") or [])
        if s.get("owned_source") and s.get("domain")
    }
    if any(host == d or host.endswith("." + d) for d in exempt if d):
        return {"company_blog": False, "exempt": "owned_or_allowlisted"}

    kind: str | None = None
    signals = 0
    detected_by = "heuristic"
    for override_host, override_kind in (pc.get("kind_overrides") or {}).items():
        oh = override_host.lower()
        if host == oh or host.endswith("." + oh):
            kind, detected_by = override_kind, "override"
            break
    if kind is None:
        kind, signals = _heuristic_company_blog(url, title, text)
        if kind is None:
            return {"company_blog": False}
    base_penalty = float(pc.get(kind, 0.0) or 0.0)
    min_banger = float((pc.get("min_banger_score_10") or {}).get(kind, 0.0) or 0.0)
    if base_penalty <= 0 and min_banger <= 0:
        return {"company_blog": False}
    return {
        "company_blog": True,
        "kind": kind,
        "detected_by": detected_by,
        "signal_count": signals,
        "base_penalty": base_penalty,
        "min_banger_score_10": min_banger,
    }


def _fingerprint_idea(text: str) -> set[str]:
    words = [w for w in re.findall(r"\b[a-z]{3,}\b", (text or "").lower()) if w not in STOPWORDS]
    grams = set(words)
    for idx in range(len(words) - 1):
        grams.add(f"{words[idx]}_{words[idx + 1]}")
    return grams


def load_recent_memory(out_dir: Path, active_cycle_start: str, window: int = 4) -> dict[str, Any]:
    if not out_dir.is_dir():
        return {"seen_domains": set(), "last_domains": set(), "last_authors": set(), "fingerprints": []}
    prior_files = [
        p for p in sorted(out_dir.glob("cycle-*.json"))
        if p.name != f"cycle-{active_cycle_start}.json"
    ][-window:]
    seen_domains: set[str] = set()
    last_domains: set[str] = set()
    last_authors: set[str] = set()
    fingerprints: list[set[str]] = []

    for idx, p in enumerate(prior_files):
        try:
            cyc = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        is_latest = idx == len(prior_files) - 1
        for c in cyc.get("candidates") or []:
            if c.get("final_score") is None:
                continue
            dom = domain_of(c.get("canonical_url") or "")
            if dom:
                seen_domains.add(dom)
                if is_latest:
                    last_domains.add(dom)
            auth = (c.get("author") or "").strip().lower()
            if auth and auth != "unknown" and is_latest:
                last_authors.add(auth)
            spine = c.get("spine") or {}
            fp = _fingerprint_idea(f"{spine.get('thesis') or ''} {spine.get('novelty_claim') or ''}")
            if fp:
                fingerprints.append(fp)

    return {
        "seen_domains": seen_domains,
        "last_domains": last_domains,
        "last_authors": last_authors,
        "fingerprints": fingerprints,
    }


def score_candidate(
    cand: dict[str, Any],
    cfg: dict[str, Any],
    memory: dict[str, Any] | None = None,
    now: datetime | None = None,
) -> float | None:
    spine = cand.get("spine") or {}
    pinned = bool(cand.get("must_include"))
    if (spine.get("reject") and not pinned) or not isinstance(spine.get("axes"), dict):
        cand["final_score"] = None
        return None

    scfg = cfg.get("scoring") or {}
    weights = {**DEFAULT_AXES_WEIGHTS, **(scfg.get("axes_weights") or {})}
    w_total = sum(v for v in weights.values() if v > 0) or 0.95
    weighted = 0.0
    for key in ("audience_fit", "operator_utility", "reframe_sharp_thesis"):
        if _axis_val(spine, key) is None:
            cand["final_score"] = None
            return None
    for key, w in weights.items():
        if w <= 0:
            continue
        val = _axis_val(spine, key)
        if val is not None:
            weighted += val * w

    # Scale so max 5 on all weighted axes yields ~9.5-10.0 matching V2 scale.
    norm_denom = 5.0 if abs(w_total - 0.95) < 0.06 else (5.0 * w_total)
    base = (weighted / norm_denom) * 10.0

    defs = cfg.get("defaults") or {}
    half_life = max(1.0, float(defs.get("recency_half_life_days", 10)))
    ref_now = now or datetime.now(UTC)
    pub_dt = parse_date(cand.get("published_at"))
    age_days = max(0.0, (ref_now - pub_dt).total_seconds() / 86400.0) if pub_dt else 0.0
    recency_bonus = 0.5 ** (age_days / half_life)

    hay = f"{cand.get('title') or ''} {cand.get('text') or ''}".lower()
    hits = sum(1 for a in (cfg.get("focus_anchors") or []) if str(a).lower() in hay)
    anchor_boost = min(hits, 3) * 0.2

    focus_boost = 0.0
    matched_focus: list[str] = []
    for ft in cfg.get("focus_topics") or []:
        fscore = topic_focus_score(cand, ft)
        if fscore >= float(ft.get("focus_threshold", 0.35)):
            focus_boost += float(ft.get("boost", 0.4)) * fscore
            matched_focus.append(str(ft.get("key") or ft.get("label")))
    focus_boost = min(focus_boost, 0.7)

    trusted_boost = 0.0
    aud_fit = _axis_val(spine, "audience_fit") or 0
    if aud_fit >= int(scfg.get("trusted_source_min_audience_fit", 3)):
        host = domain_of(cand.get("canonical_url") or "")
        for ts in cfg.get("trusted_sources") or []:
            thost = str(ts.get("host") or "").lower()
            if thost and (host == thost or host.endswith("." + thost)):
                trusted_boost = min(float(ts.get("boost") or 0.0), float(scfg.get("trusted_source_max_boost", 0.5)))
                break

    slop_map = {"clean": 0.0, "borderline": 0.8, "slop": 2.0, **(scfg.get("slop_penalties") or {})}
    slop_penalty = float(slop_map.get(str(spine.get("slop") or "clean"), 0.0))

    # Publisher down-weighting & hard floor
    pc = cfg.get("publisher_penalties") or {}
    cb_meta = company_blog_meta(cand.get("canonical_url") or "", cand.get("title") or "", cand.get("text") or "", cfg)
    publisher_penalty = 0.0
    if cb_meta.get("company_blog"):
        if not pinned and cb_meta["min_banger_score_10"] > 0 and base < cb_meta["min_banger_score_10"]:
            cb_meta["gated"] = True
            cb_meta["gate_reason"] = f"{cb_meta['kind']} blog below {cb_meta['min_banger_score_10']} (base {base:.2f})"
            cand["company_blog"] = cb_meta
            cand["final_score"] = None
            return None
        if cb_meta["base_penalty"] > 0:
            waive = float(pc.get("waive_banger_score_10", 9.0))
            fade = max(float(pc.get("fade_width", 0.8)), 1e-6)
            fade_factor = max(0.0, min(1.0, (waive + fade - base) / fade))
            full_at = max(int(pc.get("full_at_signals", 6) or 6), 1)
            sig_scale = 1.0 if cb_meta["detected_by"] == "override" else max(0.0, min(1.0, cb_meta["signal_count"] / float(full_at)))
            publisher_penalty = round(cb_meta["base_penalty"] * fade_factor * sig_scale, 3)
            cb_meta["fade_factor"] = round(fade_factor, 3)
            cb_meta["penalty"] = publisher_penalty
    cand["company_blog"] = cb_meta

    # Hype-topic suppressor & hard floor
    hype_penalty = 0.0
    hype_matches: list[dict[str, Any]] = []
    for ht in cfg.get("hype_topics") or []:
        hscore = topic_focus_score(cand, ht)
        if hscore < float(ht.get("focus_threshold", 0.5)):
            continue
        min_b = float(ht.get("min_banger_score_10", 0.0) or 0.0)
        if not pinned and min_b > 0 and base < min_b:
            cand["hype_topics"] = [{"key": ht.get("key"), "gated": True, "reason": f"hype topic '{ht.get('label')}' below {min_b} (base {base:.2f})"}]
            cand["final_score"] = None
            return None
        waive = float(ht.get("waive_banger_score_10", 8.8))
        fade = max(float(ht.get("fade_width", 0.8)), 1e-6)
        fade_f = max(0.0, min(1.0, (waive + fade - base) / fade))
        pen = round(float(ht.get("penalty", 0.0)) * fade_f * hscore, 3)
        hype_penalty += pen
        hype_matches.append({"key": ht.get("key"), "label": ht.get("label"), "focus": hscore, "penalty": pen})
    cand["hype_topics"] = hype_matches

    # Recent-issue diversity & idea-similarity memory
    diversity_delta = 0.0
    dcfg = scfg.get("diversity") or {}
    if dcfg.get("enabled", False) and memory:
        host = domain_of(cand.get("canonical_url") or "")
        owned_hosts = {str(s.get("domain") or "").lower() for s in (cfg.get("content_sources") or []) if s.get("owned_source")}
        if host and host not in owned_hosts:
            if memory["seen_domains"] and host not in memory["seen_domains"]:
                diversity_delta += float(dcfg.get("new_domain_bonus", 0.25))
            if host in memory["last_domains"]:
                diversity_delta -= float(dcfg.get("last_cycle_domain_penalty", 0.35))
        auth = (cand.get("author") or "").strip().lower()
        if auth and auth in memory["last_authors"] and host not in owned_hosts:
            diversity_delta -= float(dcfg.get("last_cycle_author_penalty", 0.35))
        fp = _fingerprint_idea(f"{spine.get('thesis') or ''} {spine.get('novelty_claim') or ''}")
        if fp and memory["fingerprints"]:
            max_sim = max((len(fp & old_fp) / float(len(fp | old_fp)) for old_fp in memory["fingerprints"] if (fp | old_fp)), default=0.0)
            if max_sim >= float(dcfg.get("similarity_threshold", 0.45)):
                diversity_delta -= float(dcfg.get("similarity_penalty", 1.0))

    final = base + recency_bonus + anchor_boost + focus_boost + trusted_boost + diversity_delta - slop_penalty - publisher_penalty - hype_penalty
    cand["score_breakdown"] = {
        "base": round(base, 2),
        "recency_bonus": round(recency_bonus, 2),
        "anchor_boost": round(anchor_boost, 2),
        "focus_boost": round(focus_boost, 2),
        "focus_topics": matched_focus,
        "trusted_boost": round(trusted_boost, 2),
        "diversity_delta": round(diversity_delta, 2),
        "slop_penalty": round(slop_penalty, 2),
        "publisher_penalty": round(publisher_penalty, 2),
        "hype_penalty": round(hype_penalty, 2),
    }
    cand["final_score"] = round(final, 2)
    return cand["final_score"]


def category_of(cand: dict[str, Any], cats: dict[str, Any]) -> str:
    spine_cat = (cand.get("spine") or {}).get("category")
    if spine_cat in cats:
        return str(spine_cat)
    hint = cand.get("category_hint")
    if hint in cats:
        return str(hint)
    return next(iter(cats), "uncategorized")


def select_picks(scored_pool: list[dict[str, Any]], cfg: dict[str, Any]) -> tuple[list[dict[str, Any]], list[str]]:
    sel = cfg.get("selection") or {}
    mode = sel.get("mode") or "running_guide"
    cats = cfg.get("categories") or {}
    hype_caps = {
        str(ht.get("key")): int(ht["max_selected"])
        for ht in (cfg.get("hype_topics") or [])
        if ht.get("enabled", True) and ht.get("max_selected") is not None
    }

    eligible = sorted(
        (c for c in scored_pool if c.get("final_score") is not None),
        key=lambda x: (1 if x.get("must_include") else 0, x["final_score"]),
        reverse=True,
    )

    if mode != "capped_shortlist":
        selected: list[dict[str, Any]] = []
        hype_counts: dict[str, int] = {}
        relaxed: list[str] = []
        for c in eligible:
            if not c.get("must_include"):
                matched_keys = [m.get("key") for m in (c.get("hype_topics") or []) if m.get("key") in hype_caps]
                if any(hype_counts.get(k, 0) >= hype_caps[k] for k in matched_keys):
                    continue
                for k in matched_keys:
                    hype_counts[k] = hype_counts.get(k, 0) + 1
            selected.append(c)
        return selected, relaxed

    total = int(sel.get("total_items", 10))
    max_dom = int(sel.get("max_per_domain", 2))
    max_auth = int(sel.get("max_per_author", 2))
    min_score = float(sel.get("min_final_score", 6.0))
    relax_delta = float(sel.get("backfill_relax_delta", 0.8))
    require_author = bool(sel.get("require_author", False))

    selected = []
    dom_counts: dict[str, int] = {}
    auth_counts: dict[str, int] = {}
    cat_counts: dict[str, int] = {}
    hype_counts = {}
    relaxed = []

    def _admit(c: dict[str, Any]) -> None:
        selected.append(c)
        d = domain_of(c.get("canonical_url") or "")
        if d:
            dom_counts[d] = dom_counts.get(d, 0) + 1
        a = (c.get("author") or "").strip().lower()
        if a and a != "unknown":
            auth_counts[a] = auth_counts.get(a, 0) + 1
        cat = category_of(c, cats)
        cat_counts[cat] = cat_counts.get(cat, 0) + 1
        for m in c.get("hype_topics") or []:
            k = m.get("key")
            if k:
                hype_counts[k] = hype_counts.get(k, 0) + 1

    def _caps_ok(c: dict[str, Any]) -> bool:
        if len(selected) >= total:
            return False
        cat = category_of(c, cats)
        cat_max = int((cats.get(cat) or {}).get("max", total))
        if cat_counts.get(cat, 0) >= cat_max:
            return False
        d = domain_of(c.get("canonical_url") or "")
        if d and dom_counts.get(d, 0) >= max_dom:
            return False
        a = (c.get("author") or "").strip().lower()
        if require_author and (not a or a == "unknown"):
            return False
        if a and a != "unknown" and auth_counts.get(a, 0) >= max_auth:
            return False
        for m in c.get("hype_topics") or []:
            k = m.get("key")
            if k in hype_caps and hype_counts.get(k, 0) >= hype_caps[k]:
                return False
        return True

    for c in eligible:
        if c.get("must_include"):
            _admit(c)

    for c in eligible:
        if c in selected:
            continue
        if (c.get("spine") or {}).get("slop") == "slop":
            continue
        if float(c["final_score"]) < min_score:
            continue
        if _caps_ok(c):
            _admit(c)

    # Pass 2: backfill category minima with relaxed score floor
    floor_2 = min_score - relax_delta
    for cat, ccfg in cats.items():
        needed = int(ccfg.get("min", 0)) - cat_counts.get(cat, 0)
        if needed <= 0:
            continue
        for c in eligible:
            if needed <= 0 or len(selected) >= total:
                break
            if c in selected or category_of(c, cats) != cat:
                continue
            if (c.get("spine") or {}).get("slop") == "slop":
                continue
            if float(c["final_score"]) >= floor_2 and _caps_ok(c):
                _admit(c)
                needed -= 1
                relaxed.append(f"Backfilled {cat} ({c['canonical_url']}) at score {c['final_score']}")

    selected.sort(key=lambda x: (1 if x.get("must_include") else 0, x["final_score"]), reverse=True)
    return selected, relaxed


def update_leaderboard(candidates: list[dict[str, Any]], path: Path, min_score: float = 7.5) -> dict[str, Any]:
    board: dict[str, Any] = {"runs": 0, "updated_at": None, "authors": {}}
    if path.is_file():
        try:
            board = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            pass
    authors = board.setdefault("authors", {})
    seen_this_run: set[str] = set()
    for c in candidates:
        fscore = c.get("final_score")
        if fscore is None or float(fscore) < min_score:
            continue
        raw_auth = (c.get("author") or "").strip()
        if not raw_auth or raw_auth.lower() == "unknown":
            continue
        key = raw_auth.lower()
        rec = authors.setdefault(key, {
            "name": raw_auth,
            "hits": 0,
            "runs_seen": 0,
            "max_score": 0.0,
            "domains": {},
            "examples": [],
        })
        rec["hits"] += 1
        rec["max_score"] = max(float(rec.get("max_score", 0.0)), float(fscore))
        if key not in seen_this_run:
            rec["runs_seen"] += 1
            seen_this_run.add(key)
        dom = domain_of(c.get("canonical_url") or "")
        if dom:
            rec["domains"][dom] = rec["domains"].get(dom, 0) + 1
        ex_urls = {e.get("url") for e in rec.get("examples") or []}
        if c.get("canonical_url") not in ex_urls and len(rec["examples"]) < 5:
            rec["examples"].append({"title": c.get("title"), "url": c.get("canonical_url"), "score": fscore, "lane": c.get("lane")})
    board["runs"] = int(board.get("runs", 0)) + 1
    board["updated_at"] = datetime.now(UTC).isoformat()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(board, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return board


def top_leaderboard_rows(path: Path, limit: int = 25) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    try:
        board = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    rows = list((board.get("authors") or {}).values())
    rows.sort(key=lambda r: (int(r.get("hits", 0)), float(r.get("max_score", 0.0))), reverse=True)
    return rows[:limit]


def run_what_if(cycle_data: dict[str, Any], cfg: dict[str, Any], memory: dict[str, Any] | None = None) -> dict[str, Any]:
    pool_copy = copy.deepcopy(cycle_data.get("candidates") or [])
    before_scores = {c.get("canonical_url"): c.get("final_score") for c in (cycle_data.get("candidates") or [])}
    for c in pool_copy:
        score_candidate(c, cfg, memory=memory)
    selected, relaxed = select_picks(pool_copy, cfg)
    selected_urls = {c.get("canonical_url") for c in selected}

    rows: list[dict[str, Any]] = []
    for c in sorted(pool_copy, key=lambda x: (x.get("final_score") is None, -(x.get("final_score") or 0.0))):
        url = c.get("canonical_url")
        old_s = before_scores.get(url)
        new_s = c.get("final_score")
        delta = round(new_s - old_s, 2) if (old_s is not None and new_s is not None) else None
        cb = c.get("company_blog") or {}
        ht = c.get("hype_topics") or []
        gate_note = cb.get("gate_reason") or next((h.get("reason") for h in ht if h.get("gated")), None)
        rows.append({
            "canonical_url": url,
            "title": c.get("title"),
            "author": c.get("author"),
            "category": category_of(c, cfg.get("categories") or {}),
            "lane": c.get("lane"),
            "before_score": old_s,
            "after_score": new_s,
            "delta": delta,
            "selected": url in selected_urls,
            "gate_reason": gate_note,
            "breakdown": c.get("score_breakdown"),
        })

    return {
        "cycle_start": cycle_data.get("cycle_start"),
        "selection_mode": (cfg.get("selection") or {}).get("mode", "running_guide"),
        "total_candidates": len(pool_copy),
        "selected_count": len(selected),
        "relaxed_notes": relaxed,
        "rows": rows,
    }
