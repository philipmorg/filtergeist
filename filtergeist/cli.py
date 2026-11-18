"""Command-line interface and end-to-end self-check for Filtergeist."""
from __future__ import annotations

import argparse
import json
import sys
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from . import (
    DEFAULT_STREAM,
    STREAMS_DIR,
    UTC,
    canonical_url,
    list_streams,
    load_stream,
    normalize_cycle_start,
    out_dir_for,
    save_stream,
)
from .discovery import build_search_plan, poll_apify, poll_feeds
from .pool import load_cycle, prepare_pool, prior_banlists, resolve_cycle_start
from .render import (
    GUIDE_NAME,
    PODCAST_FEED_NAME,
    REVIEW_NAME,
    TEXT_FEED_NAME,
    render_guide,
)
from .scoring import load_recent_memory, run_what_if
from .studio_server import run_studio


def _resolve_out(stream_slug: str, explicit_out: str | None) -> Path:
    return Path(explicit_out) if explicit_out else out_dir_for(stream_slug)


def cmd_streams(_args: argparse.Namespace) -> int:
    print(json.dumps({"streams": list_streams()}, indent=2, ensure_ascii=False))
    return 0


def cmd_poll_feeds(args: argparse.Namespace) -> int:
    cfg = load_stream(args.stream)
    payload = poll_feeds(cfg, lookback_days=args.lookback_days, mcp_only=args.mcp_only)
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    st = payload["feed_stats"]
    print(
        f"[poll-feeds] stream={cfg['slug']} xml_ok={st.get('xml_ok', 0)}/{st['total_sources']} "
        f"items={len(payload['candidates'])} fallback_batches={len(payload['exa_fallback_batches'])} -> {args.out}",
        file=sys.stderr,
    )
    return 0


def cmd_poll_apify(args: argparse.Namespace) -> int:
    cfg = load_stream(args.stream)
    payload = poll_apify(
        cfg,
        lookback_days=args.lookback_days,
        emit_mcp_plan=args.emit_mcp_plan,
        noapify=args.noapify,
    )
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(
        f"[poll-apify] stream={cfg['slug']} enabled={payload['enabled']} "
        f"items={len(payload.get('candidates', []))} spent_usd={payload.get('spent_usd', 0.0)} -> {args.out}",
        file=sys.stderr,
    )
    return 0


def cmd_search_plan(args: argparse.Namespace) -> int:
    cfg = load_stream(args.stream)
    odir = _resolve_out(cfg["slug"], args.out_dir)
    cstart = resolve_cycle_start(odir, args.cycle)
    banned, _ = prior_banlists(odir, load_cycle(odir, cstart))
    mode = args.mode or (cfg.get("defaults") or {}).get("default_exa_mode", "deep")
    effort = args.effort or (cfg.get("defaults") or {}).get("default_exa_effort", "low")
    lookback = args.lookback_days or (cfg.get("defaults") or {}).get("lookback_days", 14)
    plan = build_search_plan(cfg, mode, effort, int(lookback), banned, cstart)
    print(json.dumps(plan, indent=2, ensure_ascii=False))
    return 0


def cmd_add_url(args: argparse.Namespace) -> int:
    cfg = load_stream(args.stream)
    curl = canonical_url(args.url)
    if not curl:
        raise SystemExit(f"Invalid http(s) URL: {args.url!r}")
    odir = _resolve_out(cfg["slug"], args.out_dir)
    cstart = normalize_cycle_start(args.cycle) if args.cycle else resolve_cycle_start(odir, None)
    pins = cfg.setdefault("must_include", [])
    pins.append({
        "url": curl,
        "cycle_start": cstart,
        "title": args.title,
        "author": args.author,
        "category_hint": args.category,
        "note": args.note,
        "enabled": True,
    })
    saved = save_stream(cfg)
    print(f"[add-url] pinned {curl} for cycle {cstart} -> {saved}", file=sys.stderr)
    return 0


def cmd_prepare_pool(args: argparse.Namespace) -> int:
    cfg = load_stream(args.stream)
    odir = _resolve_out(cfg["slug"], args.out_dir)
    raw_batches: list[tuple[Any, str]] = []
    if args.rss and Path(args.rss).is_file():
        raw_batches.append((json.loads(Path(args.rss).read_text(encoding="utf-8")), "push_rss"))
    if args.apify and Path(args.apify).is_file():
        raw_batches.append((json.loads(Path(args.apify).read_text(encoding="utf-8")), "apify"))
    for exa_path in args.exa or []:
        p = Path(exa_path)
        if p.is_file():
            raw_batches.append((json.loads(p.read_text(encoding="utf-8")), "exa_search"))
    hydrated = [
        json.loads(Path(hp).read_text(encoding="utf-8"))
        for hp in (args.hydrated or [])
        if Path(hp).is_file()
    ]
    pool = prepare_pool(
        cfg,
        odir,
        raw_batches,
        hydrated_batches=hydrated,
        cycle=args.cycle,
        archive_rss=args.archive_rss,
        no_http_hydrate=args.no_http_hydrate,
    )
    Path(args.pool_out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.pool_out).write_text(json.dumps(pool, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"[prepare-pool] stream={cfg['slug']} {pool['funnel']} -> {args.pool_out}", file=sys.stderr)
    return 0


def cmd_render_guide(args: argparse.Namespace) -> int:
    cfg = load_stream(args.stream)
    odir = _resolve_out(cfg["slug"], args.out_dir)
    pool = json.loads(Path(args.pool).read_text(encoding="utf-8"))
    eval_raw = json.loads(Path(args.evaluated).read_text(encoding="utf-8"))
    eval_list = eval_raw.get("evaluated") if isinstance(eval_raw, dict) and "evaluated" in eval_raw else eval_raw
    if not isinstance(eval_list, list):
        raise SystemExit("evaluated JSON must be a list of candidate evaluation objects")
    res = render_guide(
        cfg,
        pool,
        eval_list,
        odir,
        cycle=args.cycle,
        model_name=args.model,
        no_link_check=args.no_link_check,
        write_review=args.review,
        formats_override=args.formats,
    )
    print(f"[render-guide] stream={cfg['slug']} picks={res['picks_rendered']} outputs={res['outputs']}", file=sys.stderr)
    return 0


def cmd_what_if(args: argparse.Namespace) -> int:
    cfg = load_stream(args.stream)
    odir = _resolve_out(cfg["slug"], args.out_dir)
    cstart = resolve_cycle_start(odir, args.cycle)
    cpath = odir / f"cycle-{cstart}.json"
    if not cpath.is_file():
        raise SystemExit(f"No saved cycle at {cpath}")
    cycle_data = load_cycle(odir, cstart)
    memory = load_recent_memory(odir, cstart)
    print(json.dumps(run_what_if(cycle_data, cfg, memory=memory), indent=2, ensure_ascii=False))
    return 0


def cmd_studio(args: argparse.Namespace) -> int:
    run_studio(host=args.host, port=args.port, streams_dir=STREAMS_DIR)
    return 0


def cmd_selfcheck(_args: argparse.Namespace) -> int:
    cfg = load_stream(DEFAULT_STREAM)
    assert canonical_url("https://www.Example.com/post/?utm_source=x&b=2&a=1#frag") == "https://example.com/post?a=1&b=2"
    assert canonical_url("https://youtu.be/dQw4w9WgXcQ?si=abc") == "https://youtube.com/watch?v=dQw4w9WgXcQ"

    # Apify disabled by default and obeys --noapify
    ap_off = poll_apify(cfg, noapify=True)
    assert ap_off["enabled"] is False and ap_off["candidates"] == []

    with TemporaryDirectory() as tmp:
        tdir = Path(tmp)
        odir = tdir / "out" / cfg["slug"]
        cfg_test = json.loads(json.dumps(cfg))
        cfg_test["outputs"]["formats"] = {
            "markdown": True,
            "review_markdown": True,
            "text_xml": True,
            "podcast_xml": True,
            "json": True,
        }
        cfg_test["must_include"] = [{
            "url": "https://danluu.com/pinned-latency-note",
            "cycle_start": "2026-11-18",
            "title": "Pinned Note on Tail Latency",
            "author": "Dan Luu",
            "category_hint": "architecture",
            "note": "Editor pin for latency deep dive",
            "enabled": True,
        }]

        indie_body = (
            "Our production postmortem traced p99 tail latency to unbounded connection pools. "
            "Teams don’t fix backpressure with retries alone. " * 25
        )
        vendor_body = (
            "Request a demo. Pricing plans. Start free trial. Our cloud platform traces microservices. " * 25
        )
        hype_body = (
            "AGI creates a 10x engineer overnight and replaces all developers with one prompt hack leaderboard. " * 25
        )
        pod_body = (
            "## Transcript\nDeterministic simulation testing catches subtle distributed systems bugs. " * 18
            + "\n[Listen to the audio](https://cdn.changelog.com/uploads/podcast/ep-sim.mp3)"
        )

        raw_exa = [
            {
                "url": "https://jvns.ca/blog/backpressure-postmortem?utm_source=rss",
                "title": "Fixing Tail Latency with Bounded Queues",
                "author": "Julia Evans",
                "publishedDate": datetime.now(UTC).isoformat(),
                "text": indie_body,
                "lane": "exa_agent_fact",
                "content_lens": "fact",
                "category_hint": "architecture",
            },
            {
                "url": "https://vendor-saas-example.io/blog/tracing-launch",
                "title": "Vendor Tracing Launch",
                "author": "Vendor",
                "publishedDate": datetime.now(UTC).isoformat(),
                "text": vendor_body,
                "lane": "exa_search_playbooks",
                "category_hint": "playbooks",
            },
            {
                "url": "https://hypedaily.example/posts/agi-overnight",
                "title": "AGI 10x Engineer Overnight",
                "author": "Hype",
                "publishedDate": datetime.now(UTC).isoformat(),
                "text": hype_body,
                "lane": "exa_search_craft",
                "category_hint": "craft",
            },
            {
                "url": "https://exa.ai/library/podcast/show1/episode/ep-sim",
                "title": "Simulation Testing Episode",
                "author": "Mitchell Hashimoto",
                "publishedDate": datetime.now(UTC).isoformat(),
                "text": pod_body,
                "lane": "exa_podcasts",
                "platform": "podcast",
            },
            {
                "url": "https://buzzfeed.com/top-10-tricks",
                "title": "Blocked Domain Item",
                "text": indie_body,
                "lane": "exa_search_craft",
            },
        ]
        hydrated = [{"url": "https://danluu.com/pinned-latency-note", "text": "Short pinned note with real text."}]

        pool = prepare_pool(
            cfg_test,
            odir,
            [(raw_exa, "exa_search")],
            hydrated_batches=[hydrated],
            cycle="2026-11-18",
            no_http_hydrate=True,
        )
        # 4 normal + 1 pinned pass; buzzfeed blocked by deny_domains (pinned survived <300 words!)
        assert pool["funnel"]["prefilter_passed"] == 5, pool["funnel"]
        assert pool["funnel"]["prefilter_rejected"] == 1

        evals = [
            {
                "canonical_url": "https://danluu.com/pinned-latency-note",
                "jev": {"relevance": 4, "decision": "likely_keep"},
                "spine": {
                    "reject": False, "reject_reason": None, "slop": "clean", "category": "architecture",
                    "thesis": "Queue bounds cap worst-case tail latency.",
                    "novelty_claim": "Pin verification.",
                    "best_quotes": [],
                    "topic_tags": ["architecture"],
                    "axes": {"reframe_sharp_thesis": 4, "voice_risk": 4, "evidence_anchoring": 4, "audience_fit": 5, "operator_utility": 4},
                },
            },
            {
                "canonical_url": "https://jvns.ca/blog/backpressure-postmortem",
                "jev": {"relevance": 4, "decision": "likely_keep"},
                "spine": {
                    "reject": False, "reject_reason": None, "slop": "clean", "category": "architecture",
                    "thesis": "Bounded connection pools prevent retry storms.",
                    "novelty_claim": "Concrete backpressure sizing method.",
                    "best_quotes": [
                        "Teams don't fix backpressure with retries alone.",
                        "Fabricated quote that does not exist in source.",
                    ],
                    "topic_tags": ["postmortem", "debugging"],
                    "axes": {"reframe_sharp_thesis": 5, "voice_risk": 4, "evidence_anchoring": 5, "audience_fit": 5, "operator_utility": 5},
                },
            },
            {
                "canonical_url": "https://vendor-saas-example.io/blog/tracing-launch",
                "jev": {"relevance": 3, "decision": "uncertain"},
                "spine": {
                    "reject": False, "reject_reason": None, "slop": "clean", "category": "playbooks",
                    "thesis": "Vendor tracing pitch.",
                    "novelty_claim": "Thin vendor post.",
                    "best_quotes": [],
                    "topic_tags": [],
                    "axes": {"reframe_sharp_thesis": 3, "voice_risk": 2, "evidence_anchoring": 3, "audience_fit": 3, "operator_utility": 3},
                },
            },
            {
                "canonical_url": "https://hypedaily.example/posts/agi-overnight",
                "jev": {"relevance": 2, "decision": "uncertain"},
                "spine": {
                    "reject": False, "reject_reason": None, "slop": "borderline", "category": "craft",
                    "thesis": "AGI leaderboard hype.",
                    "novelty_claim": "No methodology.",
                    "best_quotes": [],
                    "topic_tags": ["ai_hype"],
                    "axes": {"reframe_sharp_thesis": 2, "voice_risk": 3, "evidence_anchoring": 1, "audience_fit": 3, "operator_utility": 2},
                },
            },
            {
                "canonical_url": "https://exa.ai/library/podcast/show1/episode/ep-sim",
                "jev": {"relevance": 4, "decision": "likely_keep"},
                "spine": {
                    "reject": False, "reject_reason": None, "slop": "clean", "category": "craft",
                    "thesis": "Deterministic simulation compresses years of distributed bug hunting.",
                    "novelty_claim": "Seeded fault injection workflow.",
                    "best_quotes": ["Deterministic simulation testing catches subtle distributed systems bugs."],
                    "topic_tags": ["testing"],
                    "axes": {"reframe_sharp_thesis": 5, "voice_risk": 4, "evidence_anchoring": 4, "audience_fit": 5, "operator_utility": 4},
                },
            },
        ]

        res = render_guide(cfg_test, pool, evals, odir, cycle="2026-11-18", model_name="selfcheck", no_link_check=True)
        # Vendor (base < 8.6 saas gate) and Hype (base < 7.8 hype gate) gated out; 3 picks rendered!
        assert res["picks_rendered"] == 3, res
        assert res["dropped_quotes"] == 1

        md = (odir / GUIDE_NAME).read_text(encoding="utf-8")
        assert "Teams don't fix backpressure with retries alone." in md
        assert "Fabricated quote" not in md
        assert "Editorial pin**: must-include" in md
        assert "[Make guide with ChatGPT]" in md
        assert (odir / REVIEW_NAME).is_file()
        assert (odir / "issue-2026-11-18.json").is_file()

        # Validate both Text RSS XML and Podcast RSS XML parse cleanly and contain expected tags
        txml = (odir / TEXT_FEED_NAME).read_text(encoding="utf-8")
        pxml = (odir / PODCAST_FEED_NAME).read_text(encoding="utf-8")
        ET.fromstring(txml)
        ET.fromstring(pxml)
        assert "ep-sim.mp3" in pxml and "itunes:author" in pxml

        # What-if simulator runs cleanly with zero network
        wif = run_what_if(load_cycle(odir, "2026-11-18"), cfg_test)
        assert wif["selected_count"] == 3

        # Repeat run suppresses all seen URLs
        cfg_test["must_include"] = []
        pool2 = prepare_pool(cfg_test, odir, [(raw_exa, "exa_search")], cycle="2026-11-18", no_http_hydrate=True)
        assert pool2["funnel"]["novelty_excluded"] == 5

    print("selfcheck ok")
    return 0


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description="Filtergeist — editorial curation CLI & Studio")
    ap.add_argument("--stream", default=DEFAULT_STREAM, help="Stream slug in streams/<slug>.json or path to JSON")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("streams", help="List configured streams").set_defaults(func=cmd_streams)

    p_poll = sub.add_parser("poll-feeds", help="Poll curated RSS/Atom & podcast feeds")
    p_poll.add_argument("--lookback-days", type=int, default=None)
    p_poll.add_argument("--mcp-only", action="store_true")
    p_poll.add_argument("--out", required=True)
    p_poll.set_defaults(func=cmd_poll_feeds)

    p_ap = sub.add_parser("poll-apify", help="Poll optional Apify actors (or emit Apify MCP plan)")
    p_ap.add_argument("--lookback-days", type=int, default=None)
    p_ap.add_argument("--emit-mcp-plan", action="store_true")
    p_ap.add_argument("--noapify", action="store_true")
    p_ap.add_argument("--out", required=True)
    p_ap.set_defaults(func=cmd_poll_apify)

    p_plan = sub.add_parser("search-plan", help="Emit Exa MCP discovery plan (deep or standard)")
    p_plan.add_argument("--mode", choices=["standard", "deep"], default=None)
    p_plan.add_argument("--effort", choices=["minimal", "low", "medium", "high"], default=None)
    p_plan.add_argument("--lookback-days", type=int, default=None)
    p_plan.add_argument("--out-dir", default=None)
    p_plan.add_argument("--cycle", default=None)
    p_plan.set_defaults(func=cmd_search_plan)

    p_add = sub.add_parser("add-url", help="Pin a must-include URL for an issue cycle")
    p_add.add_argument("url")
    p_add.add_argument("--cycle", default=None)
    p_add.add_argument("--title", default=None)
    p_add.add_argument("--author", default=None)
    p_add.add_argument("--category", default=None)
    p_add.add_argument("--note", default=None)
    p_add.add_argument("--out-dir", default=None)
    p_add.set_defaults(func=cmd_add_url)

    p_prep = sub.add_parser("prepare-pool", help="Canonicalize, dedup, banlist, hydrate, and prefilter")
    p_prep.add_argument("--rss", default=None)
    p_prep.add_argument("--apify", default=None)
    p_prep.add_argument("--exa", action="append", default=[])
    p_prep.add_argument("--hydrated", action="append", default=[])
    p_prep.add_argument("--out-dir", default=None)
    p_prep.add_argument("--cycle", default=None)
    p_prep.add_argument("--archive-rss", default=None)
    p_prep.add_argument("--no-http-hydrate", action="store_true")
    p_prep.add_argument("--pool-out", required=True)
    p_prep.set_defaults(func=cmd_prepare_pool)

    p_rend = sub.add_parser("render-guide", help="Verify quotes/links, score, and render configured outputs")
    p_rend.add_argument("--pool", required=True)
    p_rend.add_argument("--evaluated", required=True)
    p_rend.add_argument("--out-dir", default=None)
    p_rend.add_argument("--cycle", default=None)
    p_rend.add_argument("--model", default="agent-curator")
    p_rend.add_argument("--no-link-check", action="store_true")
    p_rend.add_argument("--review", action="store_true")
    p_rend.add_argument("--formats", default=None, help="Comma-separated override: markdown,review_markdown,text_xml,podcast_xml,json")
    p_rend.set_defaults(func=cmd_render_guide)

    p_wif = sub.add_parser("what-if", help="Re-score a saved cycle against current stream config with zero network")
    p_wif.add_argument("--cycle", default=None)
    p_wif.add_argument("--out-dir", default=None)
    p_wif.set_defaults(func=cmd_what_if)

    p_stu = sub.add_parser("studio", help="Launch the local Filtergeist Studio web GUI")
    p_stu.add_argument("--host", default="127.0.0.1")
    p_stu.add_argument("--port", type=int, default=8765)
    p_stu.set_defaults(func=cmd_studio)

    sub.add_parser("selfcheck", help="Run end-to-end invariant checks").set_defaults(func=cmd_selfcheck)
    return ap


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)
