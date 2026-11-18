"""Discovery lanes: RSS/Atom polling, optional Apify actors, Exa MCP plans, and must-include pins."""
from __future__ import annotations

import os
import urllib.parse
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from typing import Any

from . import (
    NET_XML_ERRORS,
    UTC,
    canonical_url,
    clean_html,
    http_get,
    http_post_json,
    normalize_cycle_start,
    parse_date,
)

DEFAULT_APIFY_ACTORS = {
    "linkedin_profiles": "apimaestro~linkedin-batch-profile-posts-scraper",
    "linkedin_search": "apimaestro~linkedin-posts-search-scraper-no-cookies",
    "x_profiles": "apidojo~twitter-scraper-lite",
    "x_lists": "apidojo~twitter-list-scraper",
    "youtube_channel": "streamers~youtube-scraper",
    "youtube_transcript": "starvibe~youtube-video-transcript",
    "web_crawler": "apify~website-content-crawler",
}

AGENT_OUTPUT_SCHEMA = {
    "type": "object",
    "required": ["articles"],
    "properties": {
        "articles": {
            "type": "array",
            "maxItems": 8,
            "items": {
                "type": "object",
                "required": ["title", "url", "why", "category", "quality_score"],
                "properties": {
                    "title": {"type": "string"},
                    "url": {"type": "string", "format": "uri"},
                    "author": {"type": "string"},
                    "published_date": {"type": "string"},
                    "why": {"type": "string"},
                    "category": {"type": "string"},
                    "quality_score": {"type": "number"},
                },
            },
        }
    },
}


def _local_tag(tag: str) -> str:
    return tag.rsplit("}", 1)[-1].lower()


def _child_text(elem: ET.Element, names: tuple[str, ...]) -> str:
    wanted = {n.lower() for n in names}
    for ch in elem:
        if _local_tag(ch.tag) in wanted:
            if _local_tag(ch.tag) == "author":
                for sub in ch:
                    if _local_tag(sub.tag) == "name" and (sub.text or "").strip():
                        return sub.text.strip()
            txt = "".join(ch.itertext()).strip()
            if txt:
                return txt
    return ""


def parse_feed_xml(xml_bytes: bytes, source: dict[str, Any], cutoff: datetime) -> list[dict[str, Any]]:
    text = xml_bytes.decode("utf-8", errors="replace").lstrip()
    root = ET.fromstring(text)
    entries = (
        [el for el in root if _local_tag(el.tag) == "entry"]
        if _local_tag(root.tag) == "feed"
        else [el for el in root.iter() if _local_tag(el.tag) == "item"]
    )
    max_entries = int(source.get("max_entries") or 15)
    platform = source.get("platform") or "rss"
    lane = source.get("lane") or ("push_podcast" if platform == "podcast" else "push_rss")
    hints = source.get("category_hints") or []
    cat_hint = source.get("category_hint") or (hints[0] if hints else None)
    out: list[dict[str, Any]] = []

    for entry in entries[: max_entries * 2]:
        link = ""
        media_url = None
        media_type = None
        for ch in entry:
            lname = _local_tag(ch.tag)
            if lname == "link":
                href = (ch.attrib.get("href") or "").strip()
                rel = (ch.attrib.get("rel") or "alternate").lower()
                if href and rel in {"alternate", ""}:
                    link = href
                elif not href and (ch.text or "").strip():
                    link = ch.text.strip()
            elif lname == "enclosure":
                eurl = (ch.attrib.get("url") or "").strip()
                etype = (ch.attrib.get("type") or "").strip()
                if eurl and ("audio" in etype or eurl.lower().endswith((".mp3", ".m4a"))):
                    media_url, media_type = eurl, etype or "audio/mpeg"
        if not link:
            link = _child_text(entry, ("guid", "id"))
        curl = canonical_url(link)
        if not curl:
            continue
        pub_dt = parse_date(_child_text(entry, ("published", "pubdate", "updated", "date")))
        if pub_dt and pub_dt < cutoff:
            continue
        title = clean_html(_child_text(entry, ("title",)))
        author = clean_html(_child_text(entry, ("creator", "author"))) or source.get("name")
        body_raw = _child_text(entry, ("encoded", "content")) or _child_text(entry, ("summary", "description"))
        item: dict[str, Any] = {
            "canonical_url": curl,
            "url": link,
            "title": title or None,
            "author": author or None,
            "published_at": pub_dt.isoformat() if pub_dt else None,
            "text": clean_html(body_raw),
            "lane": lane,
            "platform": platform,
            "source": source.get("name"),
            "category_hint": cat_hint,
        }
        if media_url:
            item["media_url"] = media_url
            item["media_type"] = media_type
        out.append(item)
        if len(out) >= max_entries:
            break
    return out


def build_exa_fallback_batches(failed_sources: list[dict[str, Any]], start_date: str, cfg: dict[str, Any]) -> list[dict[str, Any]]:
    domain_sources: list[dict[str, Any]] = []
    podcast_batches: list[dict[str, Any]] = []
    anchor_query = " ".join((cfg.get("focus_anchors") or ["engineering", "architecture"])[:8])
    max_chars = int((cfg.get("defaults") or {}).get("max_text_chars_article", 12000))

    for src in failed_sources:
        if src.get("platform") == "podcast" or src.get("exa_query_hint"):
            dom = src.get("domain") or ""
            payload: dict[str, Any] = {
                "query": src.get("exa_query_hint") or f"{src.get('name')} {anchor_query}",
                "type": "auto",
                "numResults": 4,
                "startPublishedDate": start_date,
                "textMaxCharacters": max_chars,
            }
            if dom:
                payload["includeDomains"] = [dom]
            podcast_batches.append({"source": src.get("name"), "tool": "web_search_advanced_exa", "arguments": payload})
        elif src.get("domain"):
            domain_sources.append(src)

    batches: list[dict[str, Any]] = []
    for idx in range(0, len(domain_sources), 8):
        chunk = domain_sources[idx : idx + 8]
        domains = [s["domain"] for s in chunk if s.get("domain")]
        batches.append({
            "sources": [s.get("name") for s in chunk],
            "tool": "web_search_advanced_exa",
            "arguments": {
                "query": anchor_query,
                "type": "auto",
                "numResults": min(15, max(6, len(domains) * 2)),
                "includeDomains": domains,
                "startPublishedDate": start_date,
                "textMaxCharacters": max_chars,
            },
        })
    return batches + podcast_batches


def test_single_feed(url: str, lookback_days: int = 30) -> dict[str, Any]:
    now = datetime.now(UTC)
    cutoff = now - timedelta(days=lookback_days)
    try:
        status, ct, body, final_url = http_get(url, timeout_s=12)
        head = body[:1500].decode("utf-8", errors="ignore").lstrip().lower()
        is_xml = head.startswith("<?xml") or "<rss" in head or "<feed" in head or "<rdf" in head or "xml" in ct.lower()
        if not is_xml:
            return {"ok": False, "status": status, "xml": False, "reason": f"Non-XML response ({ct[:40]})", "final_url": final_url}
        items = parse_feed_xml(body, {"name": "Preview", "max_entries": 5}, cutoff)
        return {
            "ok": True,
            "status": status,
            "xml": True,
            "final_url": final_url,
            "items_in_window": len(items),
            "sample_titles": [it.get("title") for it in items[:3] if it.get("title")],
        }
    except NET_XML_ERRORS as exc:
        return {"ok": False, "xml": False, "reason": f"{type(exc).__name__}: {exc}"}


def poll_feeds(cfg: dict[str, Any], lookback_days: int | None = None, mcp_only: bool = False) -> dict[str, Any]:
    lookback = int(lookback_days or (cfg.get("defaults") or {}).get("lookback_days", 14))
    now = datetime.now(UTC)
    start_date = (now - timedelta(days=lookback)).date().isoformat()
    enabled_sources = [s for s in (cfg.get("content_sources") or []) if s.get("enabled", True)]

    if mcp_only:
        return {
            "mode": "mcp_only",
            "polled_at": now.isoformat(),
            "lookback_days": lookback,
            "candidates": [],
            "feed_stats": {"total_sources": len(enabled_sources), "xml_ok": 0, "fallback_needed": len(enabled_sources)},
            "exa_fallback_batches": build_exa_fallback_batches(enabled_sources, start_date, cfg),
        }

    def _poll_one(src: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]], str | None]:
        days = int(src.get("age_limit_days") or (cfg["defaults"].get("owned_source_lookback_days", 90) if src.get("owned_source") else lookback))
        cutoff = now - timedelta(days=days)
        try:
            _, ct, body, _ = http_get(src["url"], timeout_s=14)
            head = body[:1500].decode("utf-8", errors="ignore").lstrip().lower()
            if not (head.startswith("<?xml") or "<rss" in head or "<feed" in head or "<rdf" in head or "xml" in ct.lower()):
                return src, [], f"non_xml:{ct[:40]}"
            return src, parse_feed_xml(body, src, cutoff), None
        except NET_XML_ERRORS as exc:
            return src, [], f"{type(exc).__name__}: {exc}"

    with ThreadPoolExecutor(max_workers=10) as ex:
        results = list(ex.map(_poll_one, enabled_sources))

    candidates: list[dict[str, Any]] = []
    failed: list[dict[str, Any]] = []
    errors: dict[str, str] = {}
    for src, items, err in results:
        if err is not None:
            failed.append(src)
            errors[src.get("name", src.get("url", "?"))] = err
        else:
            candidates.extend(items)

    return {
        "mode": "rss_with_exa_fallback",
        "polled_at": now.isoformat(),
        "lookback_days": lookback,
        "candidates": candidates,
        "feed_stats": {
            "total_sources": len(enabled_sources),
            "xml_ok": len(enabled_sources) - len(failed),
            "fallback_needed": len(failed),
            "items_found": len(candidates),
            "fallback_reasons": errors,
        },
        "exa_fallback_batches": build_exa_fallback_batches(failed, start_date, cfg),
    }


def _apify_actor_input(src: dict[str, Any], lookback_days: int) -> dict[str, Any]:
    kind = src.get("kind") or "web_crawler"
    target = str(src.get("target") or "").strip()
    max_items = int(src.get("max_items") or 10)
    if kind == "linkedin_profiles":
        return {"usernames": [target], "maxPosts": max_items}
    if kind == "linkedin_search":
        return {"keyword": target, "maxResults": max_items, "sortBy": "date_posted"}
    if kind == "x_profiles":
        handle = target.lstrip("@").split("/")[-1]
        return {"twitterHandles": [handle], "maxItems": max_items, "sort": "Latest"}
    if kind == "x_lists":
        return {"listUrls": [target], "maxItems": max_items}
    if kind == "youtube_channel":
        return {"startUrls": [{"url": target}], "maxResults": max_items, "maxResultsShorts": 0}
    return {"startUrls": [{"url": target}], "maxCrawlPages": max_items, "crawlerType": "playwright:adaptive"}


def _normalize_apify_item(raw: dict[str, Any], src: dict[str, Any]) -> dict[str, Any] | None:
    kind = src.get("kind") or "web_crawler"
    url = (
        raw.get("url")
        or raw.get("postUrl")
        or raw.get("twitterUrl")
        or raw.get("videoUrl")
        or ""
    )
    curl = canonical_url(str(url))
    if not curl:
        return None
    text = str(raw.get("text") or raw.get("fullText") or raw.get("description") or raw.get("markdown") or "").strip()
    title = str(raw.get("title") or raw.get("headline") or (text[:96] + "..." if len(text) > 96 else text) or "(untitled)").strip()
    author = (
        raw.get("authorName")
        or (raw.get("author") if isinstance(raw.get("author"), str) else (raw.get("author") or {}).get("userName"))
        or raw.get("channelName")
        or src.get("name")
    )
    pub = raw.get("publishedAt") or raw.get("createdAt") or raw.get("date") or raw.get("postedAtISO")
    platform = "video" if "youtube" in kind else ("tweet" if kind.startswith("x_") else "article")
    return {
        "canonical_url": curl,
        "url": curl,
        "title": title,
        "author": str(author) if author else None,
        "published_at": parse_date(str(pub)).isoformat() if pub and parse_date(str(pub)) else None,
        "text": text,
        "lane": f"apify_{kind}",
        "platform": platform,
        "source": src.get("name"),
        "category_hint": src.get("category_hint"),
    }


def poll_apify(
    cfg: dict[str, Any],
    lookback_days: int | None = None,
    emit_mcp_plan: bool = False,
    noapify: bool = False,
) -> dict[str, Any]:
    ap_cfg = cfg.get("apify") or {}
    env_block = os.environ.get("FILTERGEIST_NOAPIFY", "").strip() == "1" or os.environ.get("NOAPIFY", "").strip() == "1"
    if noapify or env_block or not ap_cfg.get("enabled", False):
        return {"enabled": False, "skipped_reason": "disabled_or_noapify", "candidates": [], "spent_usd": 0.0, "mcp_calls": []}

    lookback = int(lookback_days or (cfg.get("defaults") or {}).get("lookback_days", 14))
    actors = {**DEFAULT_APIFY_ACTORS, **(ap_cfg.get("actors") or {})}
    enabled_sources = [s for s in (ap_cfg.get("sources") or []) if s.get("enabled", True) and s.get("target")]

    mcp_calls = []
    for src in enabled_sources:
        kind = src.get("kind") or "web_crawler"
        actor_id = actors.get(kind, DEFAULT_APIFY_ACTORS["web_crawler"]).replace("~", "/")
        mcp_calls.append({
            "source": src.get("name"),
            "kind": kind,
            "mcp_server": "https://mcp.apify.com",
            "actor": actor_id,
            "input": _apify_actor_input(src, lookback),
        })

    if emit_mcp_plan:
        return {"enabled": True, "mode": "mcp_plan", "candidates": [], "spent_usd": 0.0, "mcp_calls": mcp_calls}

    token = os.environ.get("APIFY_TOKEN", "").strip()
    if not token:
        return {"enabled": True, "mode": "missing_token_fallback_to_mcp", "candidates": [], "spent_usd": 0.0, "mcp_calls": mcp_calls}

    max_usd = float(ap_cfg.get("max_usd_per_run", 1.5))
    timeout_s = int(ap_cfg.get("timeout_s", 180))
    spent_usd = 0.0
    candidates: list[dict[str, Any]] = []
    runs_log: list[dict[str, Any]] = []

    for src in enabled_sources:
        if spent_usd >= max_usd:
            runs_log.append({"source": src.get("name"), "skipped": f"budget_cap_reached ({spent_usd:.3f} >= {max_usd:.2f})"})
            continue
        kind = src.get("kind") or "web_crawler"
        actor_slug = actors.get(kind, DEFAULT_APIFY_ACTORS["web_crawler"]).replace("/", "~")
        act_input = _apify_actor_input(src, lookback)
        try:
            run_url = f"https://api.apify.com/v2/acts/{urllib.parse.quote(actor_slug, safe='~')}/runs?waitForFinish={min(timeout_s, 120)}"
            run_resp = http_post_json(run_url, act_input, headers={"Authorization": f"Bearer {token}"}, timeout_s=timeout_s)
            run_data = (run_resp or {}).get("data") or {}
            cost = float(run_data.get("usageTotalUsd") or 0.0)
            spent_usd += cost
            ds_id = run_data.get("defaultDatasetId")
            items: list[dict[str, Any]] = []
            if ds_id:
                _, _, raw_bytes, _ = http_get(
                    f"https://api.apify.com/v2/datasets/{ds_id}/items?format=json&clean=true&token={urllib.parse.quote(token)}",
                    timeout_s=30,
                )
                import json as _json
                parsed_items = _json.loads(raw_bytes.decode("utf-8", errors="replace"))
                if isinstance(parsed_items, list):
                    for raw_it in parsed_items:
                        if isinstance(raw_it, dict):
                            norm = _normalize_apify_item(raw_it, src)
                            if norm:
                                items.append(norm)
            candidates.extend(items)
            runs_log.append({"source": src.get("name"), "actor": actor_slug, "items": len(items), "cost_usd": round(cost, 4)})
        except NET_XML_ERRORS as exc:
            runs_log.append({"source": src.get("name"), "actor": actor_slug, "error": f"{type(exc).__name__}: {exc}"})

    return {
        "enabled": True,
        "mode": "rest_api",
        "spent_usd": round(spent_usd, 4),
        "max_usd_per_run": max_usd,
        "runs": runs_log,
        "candidates": candidates,
        "mcp_calls": mcp_calls,
    }


def build_search_plan(
    cfg: dict[str, Any],
    mode: str,
    effort: str,
    lookback_days: int,
    banned_urls: set[str],
    cycle_start: str,
) -> dict[str, Any]:
    start_date = (datetime.now(UTC) - timedelta(days=lookback_days)).date().isoformat()
    deny_domains = [d for d in (cfg.get("deny_domains") or []) if "/" not in d]
    cats = cfg.get("categories") or {}
    lenses = [ln for ln in (cfg.get("agent_lenses") or []) if ln.get("enabled", True)]

    covered_by_agent = {ln.get("category_hint") for ln in lenses if ln.get("category_hint")} if mode == "deep" else set()
    cats_for_search = [c for c in cats if c not in covered_by_agent] or list(cats.keys())
    max_art_chars = int((cfg.get("defaults") or {}).get("max_text_chars_article", 12000))

    search_calls: list[dict[str, Any]] = []
    for cat in cats_for_search:
        ccfg = cats[cat]
        call_args: dict[str, Any] = {
            "query": ccfg["query"],
            "type": "auto",
            "numResults": 8,
            "startPublishedDate": start_date,
            "excludeDomains": deny_domains,
            "excludeText": ccfg.get("exclude_text") or [],
            "additionalQueries": ccfg.get("additional_queries") or [],
            "textMaxCharacters": max_art_chars,
        }
        if ccfg.get("exa_mcp_category"):
            call_args["category"] = ccfg["exa_mcp_category"]
        if ccfg.get("include_text_single"):
            call_args["includeText"] = ccfg["include_text_single"][:1]
        search_calls.append({
            "lane": f"exa_search_{cat}",
            "category_hint": cat,
            "tool": "web_search_advanced_exa",
            "arguments": call_args,
        })

    allowlist = cfg.get("query_site_allowlist") or []
    if allowlist and mode == "standard":
        anchor_q = " ".join((cfg.get("focus_anchors") or ["architecture", "engineering"])[:8])
        search_calls.append({
            "lane": "exa_allowlist",
            "category_hint": next(iter(cats), None),
            "tool": "web_search_advanced_exa",
            "arguments": {
                "query": anchor_q,
                "type": "auto",
                "numResults": 10,
                "includeDomains": allowlist,
                "startPublishedDate": start_date,
                "textMaxCharacters": max_art_chars,
            },
        })

    pdisc = cfg.get("podcast_discovery") or {}
    if pdisc.get("enabled"):
        max_pod_chars = int((cfg.get("defaults") or {}).get("max_text_chars_podcast", 100000))
        for idx, q in enumerate(pdisc.get("queries") or [], start=1):
            search_calls.append({
                "lane": "exa_podcasts",
                "query_index": idx,
                "platform": "podcast",
                "tool": "web_search_advanced_exa",
                "arguments": {
                    "query": q,
                    "type": "auto",
                    "numResults": int(pdisc.get("results_per_query", 4)),
                    "includeDomains": pdisc.get("include_domains") or ["exa.ai/library/podcast"],
                    "startPublishedDate": start_date,
                    "textMaxCharacters": max_pod_chars,
                },
            })

    agent_calls: list[dict[str, Any]] = []
    if mode == "deep":
        exclusions = [{"url": u} for u in sorted(banned_urls)[:40]]
        schema = dict(AGENT_OUTPUT_SCHEMA)
        for ln in lenses:
            key = ln["key"]
            prompt = str(ln.get("prompt") or "").replace("<LOOKBACK_DAYS>", str(lookback_days))
            eff = effort or ln.get("effort") or "low"
            max_items = int(ln.get("max_items") or 8)
            lens_schema = {
                **schema,
                "properties": {"articles": {**schema["properties"]["articles"], "maxItems": max_items}},
            }
            agent_calls.append({
                "lane": f"exa_agent_{key}",
                "content_lens": key,
                "category_hint": ln.get("category_hint"),
                "min_gate_score_10": float(ln.get("min_gate_score_10", 7.5)),
                "gate_rubric": ln.get("gate_rubric") or "",
                "tool": "agent_run",
                "arguments": {
                    "query": prompt,
                    "effort": eff,
                    "outputSchema": lens_schema,
                    "input": {"exclusion": exclusions},
                },
            })

    return {
        "stream": cfg.get("slug"),
        "newsletter": cfg.get("newsletter"),
        "mode": mode,
        "cycle_start": cycle_start,
        "lookback_days": lookback_days,
        "start_published_date": start_date,
        "agent_calls": agent_calls,
        "search_calls": search_calls,
        "exemplar_seeds": (cfg.get("exemplars") or {}).get("urls") or [],
    }


def active_must_includes(cfg: dict[str, Any], cycle_start: str) -> list[dict[str, Any]]:
    target = normalize_cycle_start(cycle_start)
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for entry in cfg.get("must_include") or []:
        if not isinstance(entry, dict) or not entry.get("enabled", True):
            continue
        cdate = entry.get("cycle_start")
        if cdate and normalize_cycle_start(str(cdate)) != target:
            continue
        curl = canonical_url(str(entry.get("url") or ""))
        if not curl or curl in seen:
            continue
        seen.add(curl)
        out.append({
            "url": curl,
            "canonical_url": curl,
            "title": entry.get("title") or None,
            "author": entry.get("author") or None,
            "lane": "editorial_must_include",
            "platform": "article",
            "category_hint": entry.get("category_hint"),
            "must_include": True,
            "editor_note": (entry.get("note") or "").strip() or None,
        })
    return out
