"""Output rendering: Markdown guide, review pile, Text RSS XML, Podcast RSS XML, and Issue JSON."""
from __future__ import annotations

import hashlib
import html as htmlmod
import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from email.utils import format_datetime
from pathlib import Path
from typing import Any
from xml.sax.saxutils import escape, quoteattr

from . import (
    NET_XML_ERRORS,
    OPENER,
    UA,
    UTC,
    canonical_url,
    domain_of,
    http_get,
    is_safe_public_url,
    normalize_cycle_start,
    parse_date,
)
from .pool import load_cycle, save_cycle
from .scoring import (
    category_of,
    load_recent_memory,
    score_candidate,
    select_picks,
    update_leaderboard,
    verify_quotes,
)

GUIDE_NAME = "issue_content.md"
REVIEW_NAME = "review_content.md"
TEXT_FEED_NAME = "feed.xml"
PODCAST_FEED_NAME = "podcast_feed.xml"
LEADERBOARD_NAME = "author_leaderboard.json"
GUIDE_SEPARATOR = "\n---\n\n"

ITUNES_NS = (
    'xmlns:itunes="http://www.itunes.com/dtds/podcast-1.0.dtd" '
    'xmlns:content="http://purl.org/rss/1.0/modules/content/"'
)
CONTENT_NS = 'xmlns:content="http://purl.org/rss/1.0/modules/content/"'

MEDIA_RE = re.compile(r'https?://[^\s"\'<>\\]+\.(?:mp3|m4a|aac)(?:\?[^\s"\'<>\\]*)?', re.IGNORECASE)
EXA_LISTEN_RE = re.compile(r"\[Listen to the audio\]\((https?://[^\s)]+)\)", re.IGNORECASE)
CAPTIVATE_RE = re.compile(r"player\.captivate\.fm/episode/([a-f0-9-]{36})", re.IGNORECASE)

AUDIO_PLATFORMS = {"podcast", "video"}
AUDIO_URL_RE = re.compile(
    r"youtu\.be|youtube\.com|podcasts\.apple\.com|open\.spotify\.com/(episode|show)|"
    r"buzzsprout\.com|podbean\.com|pod\.wave\.co|exa\.ai/library/podcast|/podcast/|/episode",
    re.IGNORECASE,
)
PREFILL_URLS = (
    ("Make guide with ChatGPT", "https://chatgpt.com/?hints=search&q={}"),
    ("Make guide with Perplexity", "https://www.perplexity.ai/search/new?q={}"),
)


def _cdata(text: str) -> str:
    return "<![CDATA[" + (text or "").replace("]]>", "]]]]><![CDATA[>") + "]]>"


def _mime_for_audio(url: str, fallback: str = "audio/mpeg") -> str:
    path = urllib.parse.urlsplit(url).path.lower()
    if path.endswith(".m4a"):
        return "audio/mp4"
    if path.endswith(".aac"):
        return "audio/aac"
    return fallback


def resolve_audio_enclosure(item: dict[str, Any], allow_network: bool = True) -> tuple[str, str] | None:
    if item.get("media_url") and is_safe_public_url(str(item["media_url"])):
        murl = str(item["media_url"])
        return murl, str(item.get("media_type") or _mime_for_audio(murl))

    text = str(item.get("text") or "")
    m_listen = EXA_LISTEN_RE.search(text)
    if m_listen and is_safe_public_url(m_listen.group(1)):
        lurl = m_listen.group(1)
        return lurl, _mime_for_audio(lurl)
    m_text_mp3 = MEDIA_RE.search(text)
    if m_text_mp3 and is_safe_public_url(m_text_mp3.group(0)):
        murl = m_text_mp3.group(0)
        return murl, _mime_for_audio(murl)

    url = str(item.get("canonical_url") or "")
    if not allow_network or not url or "youtube.com" in url or "youtu.be" in url or not is_safe_public_url(url):
        return None
    try:
        status, _, raw_bytes, _ = http_get(url, timeout_s=12, max_bytes=600_000)
        if status != 200:
            return None
        page = htmlmod.unescape(raw_bytes.decode("utf-8", errors="ignore").replace(r"\/", "/"))
        if m_cap := CAPTIVATE_RE.search(page):
            return f"https://episodes.captivate.fm/episode/{m_cap.group(1)}.mp3", "audio/mpeg"
        if m_mp3 := MEDIA_RE.search(page):
            found = m_mp3.group(0)
            if is_safe_public_url(found):
                return found, _mime_for_audio(found)
    except NET_XML_ERRORS:
        return None
    return None


def check_links(items: list[dict[str, Any]], timeout_s: int = 12) -> None:
    def _probe(it: dict[str, Any]) -> None:
        url = it.get("canonical_url") or ""
        status = None
        for method in ("HEAD", "GET"):
            try:
                req = urllib.request.Request(url, method=method, headers={"User-Agent": UA})
                with OPENER.open(req, timeout=timeout_s) as resp:
                    status = resp.status
                if status not in {403, 405, 501}:
                    break
            except urllib.error.HTTPError as exc:
                status = exc.code
                if method == "HEAD" and status in {403, 405, 501}:
                    continue
                break
            except NET_XML_ERRORS:
                status = None
        it["link_check_status"] = status

    with ThreadPoolExecutor(max_workers=8) as ex:
        list(ex.map(_probe, items))


def is_audio_item(item: dict[str, Any], cfg: dict[str, Any]) -> bool:
    if item.get("platform") in AUDIO_PLATFORMS or item.get("transcript_url") or item.get("media_url"):
        return True
    url = item.get("canonical_url") or ""
    if AUDIO_URL_RE.search(url):
        return True
    host = domain_of(url)
    return any(host == d or host.endswith("." + d) for d in (cfg.get("howto_episode_domains") or []))


def howto_links(item: dict[str, Any], cfg: dict[str, Any]) -> str:
    if not is_audio_item(item, cfg):
        return ""
    target = item.get("transcript_url") or item["canonical_url"]
    kind = "YouTube video" if (item.get("platform") == "video" or "youtube.com" in target) else "podcast"
    prompt = (
        f"Turn this {kind} into a practical step-by-step guide: {target}. "
        "Use its transcript, show notes, and page content. Include prerequisites, "
        "numbered actions, concrete examples, and important caveats. Keep claims "
        "grounded in the source."
    )
    if item.get("thesis"):
        prompt += f" Core claim: {item['thesis']}"
    encoded = urllib.parse.quote(prompt, safe="")
    return " · ".join(f"[{label}]({tpl.format(encoded)})" for label, tpl in PREFILL_URLS)


def build_markdown_block(
    cfg: dict[str, Any],
    cycle_start: str,
    selected: list[dict[str, Any]],
    model_name: str,
    relaxed: list[str] | None = None,
    now: datetime | None = None,
) -> str:
    stamp = (now or datetime.now(UTC)).strftime("%Y-%m-%d %H:%M UTC")
    cats = cfg.get("categories") or {}
    added = len(selected)
    newsletter = cfg.get("newsletter") or "Research Guide"
    lines = [
        f"# {newsletter} — build {cycle_start}",
        f"_scrape {stamp} · {added} new pick{'s' if added != 1 else ''} · model {model_name}_",
        "",
    ]
    by_cat: dict[str, list[dict[str, Any]]] = {}
    for c in selected:
        by_cat.setdefault(category_of(c, cats), []).append(c)

    for cat, ccfg in cats.items():
        rows = by_cat.get(cat, [])
        if not rows:
            continue
        lines.append(f"## {cat} — {ccfg.get('label', cat)}")
        for c in rows:
            spine = c.get("spine") or {}
            headline = c.get("title") or "(untitled)"
            thesis = spine.get("thesis") or "—"
            why = spine.get("novelty_claim") or "—"
            status = c.get("link_check_status")
            lines += [
                f"### {headline}",
                f"- **Source**: {c['canonical_url']} ({status})",
                f"- **Author**: {c.get('author') or 'unknown'} · **Lane**: {c.get('lane') or 'push_rss'} · **Score**: {c.get('final_score')}",
                f"- **Thesis**: {thesis}",
                f"- **Why this matters**: {why}",
            ]
            if c.get("must_include"):
                note_sfx = f" — {c['editor_note']}" if c.get("editor_note") else ""
                lines.append(f"- **Editorial pin**: must-include{note_sfx}")
            cb = c.get("company_blog") or {}
            if cb.get("company_blog") and cb.get("penalty"):
                lines.append(f"- **Publisher down-weight**: {cb['kind']} blog, -{cb['penalty']} (fade {cb.get('fade_factor')}, signals {cb.get('detected_by')})")
            for ht in c.get("hype_topics") or []:
                if ht.get("penalty"):
                    lines.append(f"- **Hype suppressor**: {ht.get('label') or ht.get('key')}, -{ht['penalty']}")
            for q in spine.get("best_quotes") or []:
                lines.append(f"> {q}")
            if c.get("transcript_url"):
                lines.append(f"- **Transcript**: [Read transcript]({c['transcript_url']})")
            item_view = {**c, "thesis": thesis, "headline": headline}
            if links := howto_links(item_view, cfg):
                lines.append(f"- **How-to guide prompt**: {links}")
            lines.append("")

    if relaxed:
        lines.append("## Relaxed constraints")
        lines.extend(f"- {r}" for r in relaxed)
        lines.append("")

    lines += ["", "------", "", "## Items of Note", ""]
    for it in selected:
        spine = it.get("spine") or {}
        item_view = {**it, "thesis": spine.get("thesis"), "headline": it.get("title") or "(untitled)"}
        lines.append(f"**{item_view['headline']}**")
        lines.append("")
        lines.append(it["canonical_url"])
        if links := howto_links(item_view, cfg):
            lines.append("")
            lines.append(links)
        lines.append("")
    return "\n".join(lines) + "\n"


def build_review_block(cfg: dict[str, Any], cycle_start: str, all_candidates: list[dict[str, Any]], now: datetime | None = None) -> str:
    stamp = (now or datetime.now(UTC)).strftime("%Y-%m-%d %H:%M UTC")
    newsletter = cfg.get("newsletter") or "Research Guide"
    lines = [
        f"# {newsletter} — review pile, build {cycle_start}",
        f"_{stamp} · {len(all_candidates)} new discoveries · human judgment pending_",
        "",
        "Mark each source surprising / useful / noise. Model reservations are advisory.",
        "",
    ]
    for c in all_candidates:
        spine = c.get("spine") or {}
        triage = c.get("jev") or {}
        cb = c.get("company_blog") or {}
        ht_gated = next((h for h in (c.get("hype_topics") or []) if h.get("gated")), None)
        if c.get("_prefilter"):
            status = "Prefilter: " + ", ".join(c["_prefilter"])
        elif spine.get("_error"):
            status = "Analysis failed: " + str(spine["_error"])
        elif cb.get("gated") or ht_gated:
            status = "Gated by scoring rule"
        elif spine:
            status = "Model rejected" if spine.get("reject") else "Model retained"
        elif triage.get("decision") == "likely_reject":
            status = "Skipped by relevance triage"
        else:
            status = "Not fully reviewed (budget or text-length limit)"

        title = " ".join((c.get("title") or "(untitled)").split())
        lines += [
            f"## {title}",
            c["canonical_url"],
            "",
            f"- **Lane**: {c.get('lane') or 'unknown'} · **Model disposition**: {status}",
        ]
        if triage:
            lines.append(f"- **Triage**: {triage.get('decision')} · relevance {triage.get('relevance')}/4")
        if spine.get("reject_reason"):
            lines.append(f"- **Model reservation**: {spine['reject_reason']}")
        if cb.get("company_blog"):
            bits = [f"kind={cb.get('kind')}", f"detected_by={cb.get('detected_by')}"]
            if cb.get("penalty"):
                bits.append(f"penalty=-{cb['penalty']}")
            if cb.get("gated"):
                bits.append(f"GATED: {cb.get('gate_reason')}")
            lines.append(f"- **Publisher classification**: {', '.join(bits)}")
        if ht_gated:
            lines.append(f"- **Hype gate**: {ht_gated.get('reason')}")
        if spine.get("thesis"):
            lines.append(f"- **Model summary**: {spine['thesis']}")
        if spine.get("novelty_claim"):
            lines.append(f"- **Model learning claim**: {spine['novelty_claim']}")
        excerpt = " ".join((c.get("text") or "").split())[:700]
        if excerpt:
            lines.append(f"- **Source excerpt**: {excerpt}")
        lines += ["", "**Your judgment:**", ""]
    return "\n".join(lines) + "\n"


def build_text_rss_xml(cfg: dict[str, Any], items: list[dict[str, Any]], allow_network: bool = True) -> str:
    meta = ((cfg.get("outputs") or {}).get("feed_metadata")) or {}
    slug = cfg.get("slug") or "default"
    cats = cfg.get("categories") or {}
    title = escape(meta.get("title") or f"{cfg.get('newsletter') or 'Filtergeist'} — Research Feed")
    link = escape(meta.get("link") or cfg.get("owner_site") or "https://example.org")
    desc = escape(meta.get("description") or cfg.get("description") or "Curated research feed generated by Filtergeist.")
    resolve_enc = bool(meta.get("resolve_enclosures", True))
    now_rfc = format_datetime(datetime.now(UTC))

    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        f'<rss version="2.0" {CONTENT_NS}>',
        "  <channel>",
        f"    <title>{title}</title>",
        f"    <link>{link}</link>",
        f"    <description>{desc}</description>",
        f"    <lastBuildDate>{now_rfc}</lastBuildDate>",
        "    <generator>filtergeist</generator>",
    ]
    for it in items[:100]:
        spine = it.get("spine") or {}
        cat = category_of(it, cats)
        headline = it.get("title") or "(untitled)"
        url = it["canonical_url"]
        pub_dt = parse_date(it.get("published_at")) or datetime.now(UTC)
        enc = resolve_audio_enclosure(it, allow_network=allow_network) if (resolve_enc and is_audio_item(it, cfg)) else None
        listen_html = f'<p>🎧 <a href={quoteattr(enc[0])}>Listen to episode audio</a></p>' if enc else ""
        quotes_html = "".join(f"<blockquote>{escape(q)}</blockquote>" for q in (spine.get("best_quotes") or []))
        transcript_html = f'<p><a href={quoteattr(it["transcript_url"])}>Read transcript</a></p>' if it.get("transcript_url") else ""
        html_body = (
            f"{listen_html}"
            f"<p><strong>Category:</strong> {escape(cat)} · <strong>Score:</strong> {escape(str(it.get('final_score')))} · "
            f"<strong>Author:</strong> {escape(str(it.get('author') or 'unknown'))}</p>"
            f"<p><strong>Thesis:</strong> {escape(str(spine.get('thesis') or ''))}</p>"
            f"<p><strong>Why this matters:</strong> {escape(str(spine.get('novelty_claim') or ''))}</p>"
            f"{quotes_html}{transcript_html}"
            f"<p><a href={quoteattr(url)}>Open original source</a></p>"
        )
        lines.append("    <item>")
        if enc:
            lines.append(f'      <enclosure url={quoteattr(enc[0])} type={quoteattr(enc[1])} length="0"/>')
        lines += [
            f"      <title>{escape(f'[{cat}] {headline}')}</title>",
            f"      <link>{escape(url)}</link>",
            f'      <guid isPermaLink="false">filtergeist:{escape(slug)}:{escape(url)}</guid>',
            f"      <pubDate>{format_datetime(pub_dt)}</pubDate>",
            f"      <description>{_cdata(str(spine.get('thesis') or ''))}</description>",
            f"      <content:encoded>{_cdata(html_body)}</content:encoded>",
            "    </item>",
        ]
    lines += ["  </channel>", "</rss>", ""]
    xml_str = "\n".join(lines)
    ET.fromstring(xml_str)
    return xml_str


def build_podcast_rss_xml(cfg: dict[str, Any], items: list[dict[str, Any]], allow_network: bool = True) -> str:
    meta = ((cfg.get("outputs") or {}).get("feed_metadata")) or {}
    slug = cfg.get("slug") or "default"
    cats = cfg.get("categories") or {}
    title = escape(meta.get("podcast_title") or f"{cfg.get('newsletter') or 'Filtergeist'} — Listen Queue")
    link = escape(meta.get("link") or cfg.get("owner_site") or "https://example.org")
    desc = escape(meta.get("description") or cfg.get("description") or "Curated audio episodes surfaced by Filtergeist.")
    author = escape(meta.get("author") or cfg.get("owner") or "Filtergeist")
    lang = escape(meta.get("language") or "en-us")
    now_rfc = format_datetime(datetime.now(UTC))

    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        f'<rss version="2.0" {ITUNES_NS}>',
        "  <channel>",
        f"    <title>{title}</title>",
        f"    <link>{link}</link>",
        f"    <description>{desc}</description>",
        f"    <language>{lang}</language>",
        f"    <lastBuildDate>{now_rfc}</lastBuildDate>",
        "    <generator>filtergeist</generator>",
        f"    <itunes:author>{author}</itunes:author>",
        f"    <itunes:owner><itunes:name>{author}</itunes:name></itunes:owner>",
    ]
    for it in items:
        if not is_audio_item(it, cfg):
            continue
        enc = resolve_audio_enclosure(it, allow_network=allow_network)
        if not enc:
            continue
        spine = it.get("spine") or {}
        cat = category_of(it, cats)
        headline = it.get("title") or "(untitled)"
        url = it["canonical_url"]
        pub_dt = parse_date(it.get("published_at")) or datetime.now(UTC)
        body = (
            f"<p><strong>Category:</strong> {escape(cat)} · <strong>Score:</strong> {escape(str(it.get('final_score')))}</p>"
            f"<p>🎧 Curated episode audio.</p>"
            f"<p><strong>Thesis:</strong> {escape(str(spine.get('thesis') or ''))}</p>"
            f"<p><strong>Why this matters:</strong> {escape(str(spine.get('novelty_claim') or ''))}</p>"
        )
        if it.get("transcript_url"):
            body += f'<p><a href={quoteattr(it["transcript_url"])}>Read transcript</a></p>'
        lines += [
            "    <item>",
            f"      <title>{escape(f'[{cat}] {headline}')}</title>",
            f"      <link>{escape(url)}</link>",
            f'      <guid isPermaLink="false">filtergeist-audio:{escape(slug)}:{escape(url)}</guid>',
            f"      <pubDate>{format_datetime(pub_dt)}</pubDate>",
            f'      <enclosure url={quoteattr(enc[0])} type={quoteattr(enc[1])} length="0"/>',
            f"      <description>{_cdata(str(spine.get('thesis') or ''))}</description>",
            f"      <content:encoded>{_cdata(body)}</content:encoded>",
            "    </item>",
        ]
    lines += ["  </channel>", "</rss>", ""]
    xml_str = "\n".join(lines)
    ET.fromstring(xml_str)
    return xml_str


def build_issue_json(
    cfg: dict[str, Any],
    cycle_start: str,
    selected: list[dict[str, Any]],
    funnel: dict[str, Any],
    relaxed: list[str],
    model_name: str,
) -> dict[str, Any]:
    cats = cfg.get("categories") or {}
    items = []
    for c in selected:
        spine = c.get("spine") or {}
        items.append({
            "candidate_id": hashlib.sha256(c["canonical_url"].encode("utf-8")).hexdigest()[:16],
            "canonical_url": c["canonical_url"],
            "category": category_of(c, cats),
            "platform": c.get("platform") or "article",
            "headline": c.get("title"),
            "author": c.get("author"),
            "domain": domain_of(c["canonical_url"]),
            "lane": c.get("lane"),
            "thesis": spine.get("thesis"),
            "why_this_matters": spine.get("novelty_claim"),
            "top_quotes": spine.get("best_quotes") or [],
            "topic_tags": spine.get("topic_tags") or [],
            "published_at": c.get("published_at"),
            "final_score": c.get("final_score"),
            "score_breakdown": c.get("score_breakdown"),
            "link_check_status": c.get("link_check_status"),
            **({k: c[k] for k in ("transcript_url", "media_url", "media_type", "must_include", "editor_note") if k in c}),
        })
    return {
        "stream": cfg.get("slug"),
        "newsletter": cfg.get("newsletter"),
        "cycle_start": cycle_start,
        "generated_at": datetime.now(UTC).isoformat(),
        "model": model_name,
        "funnel": funnel,
        "relaxed_constraints": relaxed,
        "selected": items,
    }


def _atomic_write_text(target: Path, content: str) -> Path:
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(target.suffix + ".tmp")
    tmp.write_text(content, encoding="utf-8")
    os.replace(tmp, target)
    return target


def active_output_formats(cfg: dict[str, Any], cli_formats: str | None = None, force_review: bool = False) -> dict[str, bool]:
    configured = dict(((cfg.get("outputs") or {}).get("formats")) or {})
    if not configured:
        configured = {
            "markdown": True,
            "review_markdown": False,
            "text_xml": bool((cfg.get("defaults") or {}).get("write_rss_feed", False)),
            "podcast_xml": False,
            "json": False,
        }
    if cli_formats:
        wanted = {part.strip().lower() for part in cli_formats.split(",") if part.strip()}
        configured = {k: (k in wanted) for k in ("markdown", "review_markdown", "text_xml", "podcast_xml", "json")}
    if force_review:
        configured["review_markdown"] = True
    return configured


def render_guide(
    cfg: dict[str, Any],
    pool: dict[str, Any],
    eval_list: list[dict[str, Any]],
    out_dir: Path,
    cycle: str | None = None,
    model_name: str = "agent-curator",
    no_link_check: bool = False,
    write_review: bool = False,
    formats_override: str | None = None,
) -> dict[str, Any]:
    eval_by_url: dict[str, dict[str, Any]] = {}
    for rec in eval_list:
        if isinstance(rec, dict):
            cu = canonical_url(rec.get("canonical_url") or rec.get("url") or "")
            if cu:
                eval_by_url[cu] = rec

    cycle_start = normalize_cycle_start(cycle or pool["cycle_start"])
    active_cycle = load_cycle(out_dir, cycle_start)
    known_urls = {c.get("canonical_url") for c in active_cycle.get("candidates") or [] if c.get("canonical_url")}
    memory = load_recent_memory(out_dir, cycle_start)

    passed = pool.get("passed") or []
    prefilter_rejected = pool.get("prefilter_rejected") or []
    spined: list[dict[str, Any]] = []
    triage_rejected: list[dict[str, Any]] = []
    dropped_quotes = 0

    for c in passed:
        ev = eval_by_url.get(c["canonical_url"])
        if not ev:
            continue
        if isinstance(ev.get("jev"), dict):
            c["jev"] = ev["jev"]
        if isinstance(ev.get("spine"), dict):
            c["spine"] = ev["spine"]
            c["spine"].setdefault("_model", model_name)
            _, dropped = verify_quotes(c)
            dropped_quotes += dropped
            score_candidate(c, cfg, memory=memory)
            if not c["spine"].get("_error"):
                spined.append(c)
        elif (c.get("jev") or {}).get("decision") == "likely_reject":
            triage_rejected.append(c)

    fresh_spined = [c for c in spined if c["canonical_url"] not in known_urls]
    selected, relaxed = select_picks(fresh_spined, cfg)

    if not no_link_check:
        check_links(selected)

    discovered_urls = {
        c["canonical_url"]
        for c in (passed + prefilter_rejected)
        if c.get("canonical_url") and not (c.get("spine") or {}).get("_error")
    }
    funnel = {
        **(pool.get("funnel") or {}),
        "spined": len(spined),
        "scored": sum(1 for c in fresh_spined if c.get("final_score") is not None),
        "picks_rendered": len(selected),
        "triage_rejected": len(triage_rejected),
        "non_verbatim_quotes_dropped": dropped_quotes,
        "company_blog_flagged": sum(1 for c in spined if (c.get("company_blog") or {}).get("company_blog")),
        "company_blog_penalized": sum(1 for c in spined if (c.get("company_blog") or {}).get("penalty")),
        "company_blog_gated": sum(1 for c in spined if (c.get("company_blog") or {}).get("gated")),
    }
    active_cycle["candidates"].extend(fresh_spined)
    active_cycle["seen_urls"] = sorted(set(active_cycle.get("seen_urls") or []) | discovered_urls)
    active_cycle["runs"].append({
        "started_at": datetime.now(UTC).isoformat(),
        "model": model_name,
        "funnel": funnel,
        "added": len(fresh_spined),
        "picks_rendered": len(selected),
    })
    cycle_path = save_cycle(out_dir, active_cycle)
    update_leaderboard(fresh_spined, out_dir / LEADERBOARD_NAME)

    fmts = active_output_formats(cfg, cli_formats=formats_override, force_review=write_review)
    all_scored = [c for c in active_cycle["candidates"] if c.get("final_score") is not None]
    all_scored.sort(key=lambda x: (1 if x.get("must_include") else 0, x["final_score"]), reverse=True)

    written: dict[str, str | None] = {
        "markdown": None,
        "review_markdown": None,
        "text_xml": None,
        "podcast_xml": None,
        "json": None,
    }

    if fmts.get("markdown", True):
        guide_path = out_dir / GUIDE_NAME
        block = build_markdown_block(cfg, cycle_start, selected, model_name, relaxed=relaxed)
        older = guide_path.read_text(encoding="utf-8") if guide_path.is_file() else ""
        _atomic_write_text(guide_path, block + (GUIDE_SEPARATOR + older if older.strip() else ""))
        written["markdown"] = str(guide_path)

    if fmts.get("review_markdown", False):
        review_path = out_dir / REVIEW_NAME
        rblock = build_review_block(cfg, cycle_start, passed + prefilter_rejected)
        rolder = review_path.read_text(encoding="utf-8") if review_path.is_file() else ""
        _atomic_write_text(review_path, rblock + (GUIDE_SEPARATOR + rolder if rolder.strip() else ""))
        written["review_markdown"] = str(review_path)

    if fmts.get("text_xml", False):
        tfeed_path = out_dir / TEXT_FEED_NAME
        _atomic_write_text(tfeed_path, build_text_rss_xml(cfg, all_scored, allow_network=not no_link_check))
        written["text_xml"] = str(tfeed_path)

    if fmts.get("podcast_xml", False):
        pfeed_path = out_dir / PODCAST_FEED_NAME
        _atomic_write_text(pfeed_path, build_podcast_rss_xml(cfg, all_scored, allow_network=not no_link_check))
        written["podcast_xml"] = str(pfeed_path)

    if fmts.get("json", False):
        json_path = out_dir / f"issue-{cycle_start}.json"
        issue_obj = build_issue_json(cfg, cycle_start, selected, funnel, relaxed, model_name)
        _atomic_write_text(json_path, json.dumps(issue_obj, indent=2, ensure_ascii=False) + "\n")
        written["json"] = str(json_path)

    return {
        "cycle_path": str(cycle_path),
        "outputs": written,
        "picks_rendered": len(selected),
        "dropped_quotes": dropped_quotes,
        "funnel": funnel,
    }
