"""Candidate pooling: flexible ingestion, link-safety/shortener checks, banlists, hydration, prefilter."""
from __future__ import annotations

import hashlib
import json
import os
import re
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from typing import Any

from . import (
    NET_XML_ERRORS,
    OPENER,
    UA,
    UTC,
    WS_RE,
    canonical_url,
    clean_html,
    domain_of,
    http_get,
    is_safe_public_url,
    normalize_cycle_start,
    title_key,
)
from .discovery import active_must_includes, parse_feed_xml

SHORTENER_HOSTS = {
    "bit.ly", "t.co", "tinyurl.com", "goo.gl", "ow.ly", "rb.gy",
    "is.gd", "buff.ly", "rebrand.ly", "cutt.ly", "tiny.cc", "lnkd.in", "dlvr.it",
}
PAYWALL_HINTS = ("subscribe to read", "sign in to continue", "this story is for members")


def resolve_cycle_start(out_dir: Path, explicit: str | None = None) -> str:
    if explicit:
        return normalize_cycle_start(explicit)
    ptr = out_dir / "CURRENT_BUILD"
    if ptr.is_file():
        try:
            return normalize_cycle_start(ptr.read_text(encoding="utf-8").strip())
        except (OSError, ValueError):
            pass
    return datetime.now(UTC).date().isoformat()


def load_cycle(out_dir: Path, cycle_start: str) -> dict[str, Any]:
    cycle_start = normalize_cycle_start(cycle_start)
    cpath = out_dir / f"cycle-{cycle_start}.json"
    if not cpath.is_file():
        return {
            "cycle_start": cycle_start,
            "created_at": datetime.now(UTC).isoformat(),
            "updated_at": None,
            "runs": [],
            "seen_urls": [],
            "candidates": [],
        }
    data = json.loads(cpath.read_text(encoding="utf-8"))
    data.setdefault("runs", [])
    data.setdefault("seen_urls", [])
    data.setdefault("candidates", [])
    return data


def save_cycle(out_dir: Path, cycle: dict[str, Any]) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    cpath = out_dir / f"cycle-{cycle['cycle_start']}.json"
    cycle["updated_at"] = datetime.now(UTC).isoformat()
    tmp = cpath.with_suffix(".tmp")
    tmp.write_text(json.dumps(cycle, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(tmp, cpath)
    (out_dir / "CURRENT_BUILD").write_text(cycle["cycle_start"] + "\n", encoding="utf-8")
    return cpath


def prior_banlists(out_dir: Path, active_cycle: dict[str, Any], archive_rss: str | None = None) -> tuple[set[str], set[str]]:
    urls: set[str] = set(active_cycle.get("seen_urls") or [])
    titles: set[str] = set()
    for c in active_cycle.get("candidates") or []:
        if c.get("canonical_url"):
            urls.add(c["canonical_url"])
        for alias in c.get("url_aliases") or []:
            urls.add(alias)

    if out_dir.is_dir():
        for p in sorted(out_dir.glob("cycle-*.json")):
            if p.name == f"cycle-{active_cycle['cycle_start']}.json":
                continue
            try:
                old = json.loads(p.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            for c in old.get("candidates") or []:
                spine = c.get("spine") or {}
                if spine.get("reject"):
                    continue
                if c.get("canonical_url"):
                    urls.add(c["canonical_url"])
                tkey = title_key(c.get("title"))
                if len(tkey) > 20:
                    titles.add(tkey)

    if archive_rss:
        try:
            _, _, body, _ = http_get(archive_rss, timeout_s=12)
            for item in parse_feed_xml(body, {"name": "archive", "max_entries": 200}, datetime(1970, 1, 1, tzinfo=UTC)):
                if item.get("canonical_url"):
                    urls.add(item["canonical_url"])
                tkey = title_key(item.get("title"))
                if len(tkey) > 20:
                    titles.add(tkey)
        except NET_XML_ERRORS:
            pass

    return urls, titles


def unwrap_shortener(url: str, allow_network: bool = True) -> str:
    host = domain_of(url)
    if not allow_network or host not in SHORTENER_HOSTS or not is_safe_public_url(url):
        return url
    try:
        req = urllib.request.Request(url, method="HEAD", headers={"User-Agent": UA})
        with OPENER.open(req, timeout=8) as resp:
            resolved = resp.geturl()
            return resolved if is_safe_public_url(resolved) else url
    except NET_XML_ERRORS:
        return url


def ingest_flexible_records(raw: Any, default_lane: str = "exa_search") -> list[dict[str, Any]]:
    if raw is None:
        return []
    if isinstance(raw, list):
        out: list[dict[str, Any]] = []
        for entry in raw:
            out.extend(ingest_flexible_records(entry, default_lane=default_lane))
        return out
    if isinstance(raw, dict):
        if "candidates" in raw and isinstance(raw["candidates"], list):
            return ingest_flexible_records(raw["candidates"], default_lane=raw.get("lane") or default_lane)
        if "results" in raw and isinstance(raw["results"], list):
            return ingest_flexible_records(raw["results"], default_lane=raw.get("lane") or default_lane)
        if "articles" in raw and isinstance(raw["articles"], list):
            lane = raw.get("lane") or "exa_agent"
            lens = raw.get("content_lens")
            items = ingest_flexible_records(raw["articles"], default_lane=lane)
            if lens:
                for it in items:
                    it.setdefault("content_lens", lens)
            return items
        url = raw.get("canonical_url") or raw.get("url") or ""
        if not url:
            return []
        lane = raw.get("lane") or default_lane
        is_pod_lib = "exa.ai/library/podcast/" in str(url)
        return [{
            "url": str(url),
            "title": raw.get("title") or raw.get("headline"),
            "author": raw.get("author"),
            "published_at": raw.get("published_at") or raw.get("publishedDate") or raw.get("published_date"),
            "text": raw.get("text") or "",
            "lane": lane,
            "platform": raw.get("platform") or ("podcast" if is_pod_lib or "podcast" in lane else "article"),
            "category_hint": raw.get("category_hint") or raw.get("category"),
            "source": raw.get("source"),
            **({"content_lens": raw["content_lens"]} if raw.get("content_lens") else {}),
            **({"agent_why": raw["why"]} if raw.get("why") else {}),
            **({"transcript_url": str(url)} if is_pod_lib or raw.get("transcript_url") else {}),
            **({"must_include": True, "editor_note": raw.get("editor_note")} if raw.get("must_include") else {}),
            **({k: raw[k] for k in ("media_url", "media_type", "url_aliases") if k in raw}),
        }]
    return []


def fetch_full_text_http(url: str, max_chars: int = 12000, timeout_s: int = 18) -> str:
    if not is_safe_public_url(url):
        return ""
    try:
        status, _, body, _ = http_get(url, timeout_s=timeout_s)
        if status == 200 and len(body) > 2000:
            cleaned = clean_html(body.decode("utf-8", errors="ignore"))
            if len(cleaned.split()) >= 120:
                return cleaned[:max_chars]
    except NET_XML_ERRORS:
        pass
    try:
        status, _, body, _ = http_get(f"https://r.jina.ai/{url}", timeout_s=timeout_s + 12)
        if status == 200:
            cleaned = WS_RE.sub(" ", body.decode("utf-8", errors="ignore")).strip()
            if len(cleaned.split()) >= 120:
                return cleaned[:max_chars]
    except NET_XML_ERRORS:
        return ""
    return ""


def lane_min_words(cand: dict[str, Any], cfg: dict[str, Any]) -> int:
    defs = cfg.get("defaults") or {}
    if cand.get("platform") == "podcast" or cand.get("lane") in {"exa_podcasts", "push_podcast"}:
        return int(defs.get("min_words_podcast", 100))
    if cand.get("platform") == "tweet" or str(cand.get("lane") or "").startswith("apify_x_"):
        return int(defs.get("min_words_shortform", 35))
    return int(defs.get("min_words_article", 300))


def prefilter_candidate(cand: dict[str, Any], cfg: dict[str, Any]) -> tuple[bool, list[str]]:
    curl = cand.get("canonical_url") or ""
    if not is_safe_public_url(curl):
        return False, ["unsafe_url"]

    text = cand.get("text") or ""
    wc = len(text.split())
    cand["word_count"] = wc

    if cand.get("must_include"):
        return (wc > 0), ([] if wc > 0 else ["too_short"])

    reasons: list[str] = []
    low_url = curl.lower()
    host = domain_of(curl)
    if host in SHORTENER_HOSTS:
        reasons.append("unresolved_shortener")

    for d in cfg.get("deny_domains") or []:
        dl = d.lower()
        if "/" in dl:
            if dl in low_url:
                reasons.append(f"deny_domain:{d}")
                break
        elif host == dl or host.endswith("." + dl):
            reasons.append(f"deny_domain:{d}")
            break

    if wc < lane_min_words(cand, cfg):
        reasons.append("too_short")

    if cand.get("transcript_url"):
        max_pod = int((cfg.get("defaults") or {}).get("max_text_chars_podcast", 100000))
        if len(text) > max_pod:
            reasons.append(f"transcript_over_limit:{len(text)}>{max_pod}")

    hay = f"{cand.get('title') or ''} {text}".lower()
    for term in cfg.get("topic_deny_terms") or []:
        if re.search(rf"\b{re.escape(term.lower())}\b", hay):
            reasons.append(f"topic_denied:{term}")
            break

    pattern = cfg.get("prefilter_reject_regex") or r"\b(job board|we're hiring|apply now|sponsored post)\b"
    if re.search(pattern, text, re.IGNORECASE):
        reasons.append("promo_or_job_post")

    if wc < 500 and any(h in text.lower() for h in PAYWALL_HINTS):
        reasons.append("paywall_or_subscription")

    return (not reasons), reasons


def prepare_pool(
    cfg: dict[str, Any],
    out_dir: Path,
    raw_batches: list[tuple[Any, str]],
    hydrated_batches: list[Any] | None = None,
    cycle: str | None = None,
    archive_rss: str | None = None,
    no_http_hydrate: bool = False,
) -> dict[str, Any]:
    cycle_start = resolve_cycle_start(out_dir, cycle)
    active_cycle = load_cycle(out_dir, cycle_start)
    already_in_cycle = {c.get("canonical_url") for c in active_cycle.get("candidates") or [] if c.get("canonical_url")}

    raw_items: list[dict[str, Any]] = list(active_must_includes(cfg, cycle_start))
    for batch, default_lane in raw_batches:
        raw_items.extend(ingest_flexible_records(batch, default_lane))

    hydrated_by_url: dict[str, dict[str, Any]] = {}
    for h_raw in hydrated_batches or []:
        for rec in ingest_flexible_records(h_raw, "hydrated"):
            cu = canonical_url(rec.get("url") or "")
            if cu:
                hydrated_by_url[cu] = rec

    by_url: dict[str, dict[str, Any]] = {}
    for item in raw_items:
        raw_u = str(item.get("url") or "")
        expanded = unwrap_shortener(raw_u, allow_network=not no_http_hydrate)
        curl = canonical_url(expanded)
        if not curl:
            continue
        existing = by_url.get(curl)
        if existing is None:
            by_url[curl] = {**item, "canonical_url": curl}
        else:
            if len(item.get("text") or "") > len(existing.get("text") or ""):
                existing["text"] = item["text"]
            if item.get("must_include"):
                existing["must_include"] = True
                existing["editor_note"] = item.get("editor_note") or existing.get("editor_note")
            for key in ("title", "author", "published_at", "transcript_url", "content_lens", "agent_why", "category_hint"):
                if not existing.get(key) and item.get(key):
                    existing[key] = item[key]

    max_fetch = int((cfg.get("defaults") or {}).get("max_fetch", 120))
    deduped = list(by_url.values())[:max_fetch]
    banned_urls, banned_titles = prior_banlists(out_dir, active_cycle, archive_rss)

    novel: list[dict[str, Any]] = []
    excluded: list[dict[str, Any]] = []
    for c in deduped:
        if c.get("must_include") and c["canonical_url"] not in already_in_cycle:
            novel.append(c)
            continue
        tkey = title_key(c.get("title"))
        aliases = {c["canonical_url"], *(c.get("url_aliases") or [])}
        if (aliases & banned_urls) or (len(tkey) > 20 and tkey in banned_titles):
            excluded.append({"canonical_url": c["canonical_url"], "title": c.get("title"), "reason": "novelty_banlist"})
        else:
            novel.append(c)

    defs = cfg.get("defaults") or {}
    max_art = int(defs.get("max_text_chars_article", 12000))
    max_pod = int(defs.get("max_text_chars_podcast", 100000))

    for c in novel:
        hyd = hydrated_by_url.get(c["canonical_url"])
        if hyd:
            cap = max_pod if c.get("transcript_url") else max_art
            if len(hyd.get("text") or "") > len(c.get("text") or ""):
                c["text"] = hyd["text"][:cap]
            for k in ("title", "author", "published_at"):
                if not c.get(k) and hyd.get(k):
                    c[k] = hyd[k]

    http_hydrated = 0
    if not no_http_hydrate:
        to_fetch = [c for c in novel if not c.get("transcript_url") and len((c.get("text") or "").split()) < lane_min_words(c, cfg)]

        def _hydrate(cand: dict[str, Any]) -> bool:
            txt = fetch_full_text_http(cand["canonical_url"], max_chars=max_art)
            if txt:
                cand["text"] = f"{cand.get('text') or ''} {txt}".strip()[:max_art]
                return True
            return False

        with ThreadPoolExecutor(max_workers=8) as ex:
            http_hydrated = sum(1 for ok in ex.map(_hydrate, to_fetch) if ok)

    probe_pct = round(float(defs.get("exploration_probe_rate", 0.1)) * 100)
    needs_exa_fetch: list[str] = []
    passed: list[dict[str, Any]] = []
    prefilter_rejected: list[dict[str, Any]] = []

    for c in novel:
        wc = len((c.get("text") or "").split())
        if wc < lane_min_words(c, cfg):
            needs_exa_fetch.append(c["canonical_url"])
        ok, reasons = prefilter_candidate(c, cfg)
        if ok:
            cap = max_pod if c.get("transcript_url") else max_art
            c["text"] = (c.get("text") or "")[:cap]
            bucket = int(hashlib.sha256(c["canonical_url"].encode("utf-8")).hexdigest(), 16) % 100
            c["explore_probe"] = bucket < probe_pct
            passed.append(c)
        else:
            c["_prefilter"] = reasons
            prefilter_rejected.append(c)

    return {
        "stream": cfg.get("slug"),
        "cycle_start": cycle_start,
        "prepared_at": datetime.now(UTC).isoformat(),
        "funnel": {
            "raw_ingested": len(raw_items),
            "deduped": len(deduped),
            "novelty_excluded": len(excluded),
            "http_hydrated": http_hydrated,
            "needs_exa_fetch": len(needs_exa_fetch),
            "prefilter_passed": len(passed),
            "prefilter_rejected": len(prefilter_rejected),
        },
        "needs_exa_fetch": needs_exa_fetch,
        "passed": passed,
        "prefilter_rejected": prefilter_rejected,
        "novelty_excluded": excluded,
    }
