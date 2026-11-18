"""Local HTTP server for Filtergeist Studio GUI (bound to 127.0.0.1 by default)."""
from __future__ import annotations

import json
import mimetypes
import os
import urllib.parse
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from . import (
    DEFAULT_STREAM,
    REPO_ROOT,
    STREAMS_DIR,
    UTC,
    WEB_DIR,
    canonical_url,
    list_streams,
    load_stream,
    out_dir_for,
    save_stream,
    validate_slug,
)
from .discovery import (
    DEFAULT_APIFY_ACTORS,
    build_search_plan,
    poll_feeds,
    test_single_feed,
)
from .pool import load_cycle, prior_banlists
from .render import (
    GUIDE_NAME,
    LEADERBOARD_NAME,
    PODCAST_FEED_NAME,
    REVIEW_NAME,
    TEXT_FEED_NAME,
    render_guide,
)
from .scoring import load_recent_memory, run_what_if, top_leaderboard_rows


def _demo_pool_and_evals(cfg: dict[str, Any], cycle_start: str) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    cats = list((cfg.get("categories") or {"architecture": {}, "craft": {}, "playbooks": {}, "research": {}}).keys())
    c0 = cats[0]
    c1 = cats[1 % len(cats)]
    c2 = cats[2 % len(cats)]
    c3 = cats[3 % len(cats)]
    now_iso = datetime.now(UTC).isoformat()

    indie_text = (
        "When our primary Postgres replica lagged under bursty write traffic, our root cause postmortem showed "
        "connection queueing behind long transactions. Moving batch jobs onto bounded backpressure queues cut p99 "
        "latency from 840ms to 38ms without adding a single new infrastructure dependency. " * 14
    )
    vendor_text = (
        "Request a demo. Pricing plans. Start free trial. Our cloud observability platform helps teams trace "
        "distributed systems and view dashboards across microservices with zero configuration. " * 14
    )
    hype_text = (
        "AGI will create a 10x engineer overnight and replace all developers within six months using one secret "
        "prompt hack that tops every leaderboard. " * 16
    )
    pod_text = "## Transcript\n" + (
        "In today's episode we walk through deterministic simulation testing for storage engines and why reproducible "
        "seeds catch subtle concurrency bugs years faster than staging soak tests. " * 14
    )
    bench_text = (
        "Across 420 production schema migrations over eighteen months, zero-downtime expand-and-contract rollouts "
        "reduced incident rollbacks by 79% compared with lock-taking DDL changes, with full dataset and methodology disclosed. " * 14
    )

    passed = [
        {
            "canonical_url": "https://jvns.ca/blog/demo-postgres-backpressure-postmortem",
            "title": "Cutting Postgres p99 Latency 20x with Bounded Backpressure",
            "author": "Julia Evans",
            "published_at": now_iso,
            "text": indie_text,
            "word_count": len(indie_text.split()),
            "lane": "push_rss",
            "platform": "article",
            "category_hint": c0,
        },
        {
            "canonical_url": "https://eli.thegreenplace.net/demo/zero-downtime-migration-study",
            "title": "Empirical Study of 420 Production Schema Migrations",
            "author": "Eli Bendersky",
            "published_at": now_iso,
            "text": bench_text,
            "word_count": len(bench_text.split()),
            "lane": "exa_agent_fact",
            "content_lens": "fact",
            "platform": "article",
            "category_hint": c3,
        },
        {
            "canonical_url": "https://exa.ai/library/podcast/demo-show/episode/deterministic-sim",
            "title": "Deterministic Simulation Testing in Practice",
            "author": "Mitchell Hashimoto",
            "published_at": now_iso,
            "text": pod_text,
            "word_count": len(pod_text.split()),
            "lane": "exa_podcasts",
            "platform": "podcast",
            "transcript_url": "https://exa.ai/library/podcast/demo-show/episode/deterministic-sim",
            "media_url": "https://cdn.changelog.com/uploads/podcast/demo-deterministic-sim.mp3",
            "media_type": "audio/mpeg",
            "category_hint": c1,
        },
        {
            "canonical_url": "https://vendor-cloud-demo.io/blog/observability-platform-launch",
            "title": "How Modern Microservices Teams Use Cloud Tracing Dashboards",
            "author": "Vendor Marketing Team",
            "published_at": now_iso,
            "text": vendor_text,
            "word_count": len(vendor_text.split()),
            "lane": "exa_search_" + c2,
            "platform": "article",
            "category_hint": c2,
        },
        {
            "canonical_url": "https://hypedaily-demo.net/posts/agi-10x-overnight-leaderboard",
            "title": "Why AGI Prompt Hacks Create 10x Engineers Overnight",
            "author": "Hype Blogger",
            "published_at": now_iso,
            "text": hype_text,
            "word_count": len(hype_text.split()),
            "lane": "exa_search_" + c1,
            "platform": "article",
            "category_hint": c1,
        },
    ]

    pool = {
        "stream": cfg.get("slug"),
        "cycle_start": cycle_start,
        "funnel": {"raw_ingested": 5, "deduped": 5, "novelty_excluded": 0, "http_hydrated": 0, "needs_exa_fetch": 0, "prefilter_passed": 5, "prefilter_rejected": 0},
        "passed": passed,
        "prefilter_rejected": [],
    }
    evals = [
        {
            "canonical_url": "https://jvns.ca/blog/demo-postgres-backpressure-postmortem",
            "jev": {"relevance": 4, "decision": "likely_keep"},
            "spine": {
                "reject": False, "reject_reason": None, "slop": "clean", "category": c0,
                "thesis": "Moving bursty batch writes onto bounded backpressure queues eliminates replica lock contention without adding new infrastructure.",
                "novelty_claim": "Shows exact queue depth and transaction timeout thresholds that cut p99 latency from 840ms to 38ms.",
                "best_quotes": ["Moving batch jobs onto bounded backpressure queues cut p99 latency from 840ms to 38ms without adding a single new infrastructure dependency."],
                "topic_tags": ["postmortem", "databases", "architecture"],
                "axes": {"reframe_sharp_thesis": 5, "voice_risk": 4, "evidence_anchoring": 5, "audience_fit": 5, "operator_utility": 5},
            },
        },
        {
            "canonical_url": "https://eli.thegreenplace.net/demo/zero-downtime-migration-study",
            "jev": {"relevance": 4, "decision": "likely_keep"},
            "spine": {
                "reject": False, "reject_reason": None, "slop": "clean", "category": c3,
                "thesis": "Expand-and-contract schema migrations cut production DDL rollbacks by 79% across 420 observed deployments.",
                "novelty_claim": "Provides empirical failure-rate baselines by migration type so teams know when dual-writes are worth the ceremony.",
                "best_quotes": ["Across 420 production schema migrations over eighteen months, zero-downtime expand-and-contract rollouts reduced incident rollbacks by 79%"],
                "topic_tags": ["migrations", "databases"],
                "axes": {"reframe_sharp_thesis": 4, "voice_risk": 4, "evidence_anchoring": 5, "audience_fit": 5, "operator_utility": 5},
            },
        },
        {
            "canonical_url": "https://exa.ai/library/podcast/demo-show/episode/deterministic-sim",
            "jev": {"relevance": 4, "decision": "likely_keep"},
            "spine": {
                "reject": False, "reject_reason": None, "slop": "clean", "category": c1,
                "thesis": "Deterministic simulation testing compresses years of distributed fault schedules into minutes on a single core using seeded pseudo-random I/O.",
                "novelty_claim": "Explains how to isolate clock and network seams in an existing codebase without rewriting the entire runtime.",
                "best_quotes": ["In today's episode we walk through deterministic simulation testing for storage engines"],
                "topic_tags": ["testing", "debugging"],
                "axes": {"reframe_sharp_thesis": 5, "voice_risk": 4, "evidence_anchoring": 4, "audience_fit": 5, "operator_utility": 4},
            },
        },
        {
            "canonical_url": "https://vendor-cloud-demo.io/blog/observability-platform-launch",
            "jev": {"relevance": 3, "decision": "uncertain"},
            "spine": {
                "reject": False, "reject_reason": None, "slop": "borderline", "category": c2,
                "thesis": "Centralized tracing dashboards shorten mean time to detect cross-service regressions.",
                "novelty_claim": "Standard vendor overview of distributed tracing setup.",
                "best_quotes": [],
                "topic_tags": ["observability"],
                "axes": {"reframe_sharp_thesis": 4, "voice_risk": 3, "evidence_anchoring": 4, "audience_fit": 4, "operator_utility": 5},
            },
        },
        {
            "canonical_url": "https://hypedaily-demo.net/posts/agi-10x-overnight-leaderboard",
            "jev": {"relevance": 2, "decision": "uncertain"},
            "spine": {
                "reject": False, "reject_reason": None, "slop": "borderline", "category": c1,
                "thesis": "New benchmark leaderboards and prompt tricks promise dramatic coding speedups.",
                "novelty_claim": "Speculative claims with no production methodology.",
                "best_quotes": [],
                "topic_tags": ["ai_hype", "prompt_tips"],
                "axes": {"reframe_sharp_thesis": 3, "voice_risk": 4, "evidence_anchoring": 2, "audience_fit": 4, "operator_utility": 3},
            },
        },
    ]
    return pool, evals


def make_handler(streams_dir: Path = STREAMS_DIR, base_out: Path | None = None, web_dir: Path = WEB_DIR) -> type[BaseHTTPRequestHandler]:
    out_root = base_out if base_out is not None else (REPO_ROOT / "out")

    class StudioHandler(BaseHTTPRequestHandler):
        def log_message(self, fmt: str, *args: Any) -> None:
            return

        def _send_json(self, status: int, payload: Any) -> None:
            body = (json.dumps(payload, ensure_ascii=False) + "\n").encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _read_json(self) -> dict[str, Any]:
            length = int(self.headers.get("Content-Length") or "0")
            if length <= 0 or length > 5_000_000:
                return {}
            raw = self.rfile.read(length).decode("utf-8", errors="replace")
            data = json.loads(raw)
            return data if isinstance(data, dict) else {}

        def _serve_static(self, rel_path: str) -> None:
            safe_name = "index.html" if rel_path in {"", "/"} else rel_path.lstrip("/")
            target = (web_dir / safe_name).resolve()
            if not str(target).startswith(str(web_dir.resolve())) or not target.is_file():
                self._send_json(404, {"error": "Not found"})
                return
            mime, _ = mimetypes.guess_type(str(target))
            data = target.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", f"{mime or 'application/octet-stream'}; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self) -> None:
            parsed = urllib.parse.urlsplit(self.path)
            path = parsed.path
            try:
                if path == "/api/status":
                    self._send_json(200, {
                        "ok": True,
                        "repo_root": str(REPO_ROOT),
                        "skill_path": str(REPO_ROOT / "SKILL.md"),
                        "default_stream": DEFAULT_STREAM,
                        "apify_token_present": bool(os.environ.get("APIFY_TOKEN", "").strip()),
                        "exa_api_key_present": bool(os.environ.get("EXA_API_KEY", "").strip()),
                        "noapify_env": os.environ.get("FILTERGEIST_NOAPIFY", "") == "1" or os.environ.get("NOAPIFY", "") == "1",
                        "default_actors": DEFAULT_APIFY_ACTORS,
                    })
                    return

                if path == "/api/streams":
                    self._send_json(200, {"streams": list_streams(streams_dir)})
                    return

                if path.startswith("/api/streams/"):
                    slug = validate_slug(path.split("/api/streams/", 1)[1])
                    cfg = load_stream(slug, streams_dir)
                    odir = out_dir_for(slug, out_root)
                    cycles = sorted((p.stem.removeprefix("cycle-") for p in odir.glob("cycle-*.json")), reverse=True) if odir.is_dir() else []
                    self._send_json(200, {
                        "stream": cfg,
                        "cycles": cycles,
                        "open_build": (odir / "CURRENT_BUILD").read_text(encoding="utf-8").strip() if (odir / "CURRENT_BUILD").is_file() else None,
                        "has_guide": (odir / GUIDE_NAME).is_file(),
                        "has_review": (odir / REVIEW_NAME).is_file(),
                        "has_text_xml": (odir / TEXT_FEED_NAME).is_file(),
                        "has_podcast_xml": (odir / PODCAST_FEED_NAME).is_file(),
                    })
                    return

                if path.startswith("/api/outputs/"):
                    slug = validate_slug(path.split("/api/outputs/", 1)[1])
                    odir = out_dir_for(slug, out_root)
                    guide = (odir / GUIDE_NAME).read_text(encoding="utf-8") if (odir / GUIDE_NAME).is_file() else ""
                    review = (odir / REVIEW_NAME).read_text(encoding="utf-8") if (odir / REVIEW_NAME).is_file() else ""
                    text_xml = (odir / TEXT_FEED_NAME).read_text(encoding="utf-8") if (odir / TEXT_FEED_NAME).is_file() else ""
                    pod_xml = (odir / PODCAST_FEED_NAME).read_text(encoding="utf-8") if (odir / PODCAST_FEED_NAME).is_file() else ""
                    issue_jsons = sorted(odir.glob("issue-*.json"), reverse=True) if odir.is_dir() else []
                    latest_json = issue_jsons[0].read_text(encoding="utf-8") if issue_jsons else ""
                    lb_rows = top_leaderboard_rows(odir / LEADERBOARD_NAME, limit=25)
                    self._send_json(200, {
                        "guide_markdown": guide,
                        "review_markdown": review,
                        "text_xml": text_xml,
                        "podcast_xml": pod_xml,
                        "issue_json": latest_json,
                        "leaderboard": lb_rows,
                    })
                    return

                if path.startswith("/api/search-plan/"):
                    slug = validate_slug(path.split("/api/search-plan/", 1)[1])
                    qs = dict(urllib.parse.parse_qsl(parsed.query))
                    cfg = load_stream(slug, streams_dir)
                    odir = out_dir_for(slug, out_root)
                    cstart = datetime.now(UTC).date().isoformat()
                    banned, _ = prior_banlists(odir, load_cycle(odir, cstart))
                    mode = qs.get("mode") or (cfg.get("defaults") or {}).get("default_exa_mode", "deep")
                    effort = qs.get("effort") or (cfg.get("defaults") or {}).get("default_exa_effort", "low")
                    lookback = int(qs.get("lookback_days") or (cfg.get("defaults") or {}).get("lookback_days", 14))
                    self._send_json(200, build_search_plan(cfg, mode, effort, lookback, banned, cstart))
                    return

                self._serve_static(path)
            except FileNotFoundError as exc:
                self._send_json(404, {"error": str(exc)})
            except ValueError as exc:
                self._send_json(400, {"error": str(exc)})

        def do_PUT(self) -> None:
            parsed = urllib.parse.urlsplit(self.path)
            try:
                if parsed.path.startswith("/api/streams/"):
                    slug = validate_slug(parsed.path.split("/api/streams/", 1)[1])
                    payload = self._read_json()
                    cfg = payload.get("stream") if isinstance(payload.get("stream"), dict) else payload
                    cfg["slug"] = slug
                    saved = save_stream(cfg, streams_dir)
                    self._send_json(200, {"ok": True, "slug": slug, "path": str(saved)})
                    return
                self._send_json(404, {"error": "Unknown endpoint"})
            except ValueError as exc:
                self._send_json(400, {"error": str(exc)})

        def do_POST(self) -> None:
            parsed = urllib.parse.urlsplit(self.path)
            try:
                body = self._read_json()
                if parsed.path == "/api/streams":
                    slug = validate_slug(str(body.get("slug") or ""))
                    if (streams_dir / f"{slug}.json").is_file():
                        self._send_json(409, {"error": f"Stream '{slug}' already exists"})
                        return
                    tpl_slug = validate_slug(str(body.get("template_slug") or DEFAULT_STREAM))
                    base_cfg = load_stream(tpl_slug, streams_dir)
                    base_cfg["slug"] = slug
                    base_cfg["newsletter"] = str(body.get("newsletter") or slug.replace("-", " ").title()).strip()
                    base_cfg["owner"] = str(body.get("owner") or "").strip()
                    base_cfg["owner_site"] = canonical_url(str(body.get("owner_site") or "")) or str(body.get("owner_site") or "").strip()
                    base_cfg["description"] = str(body.get("description") or "").strip()
                    base_cfg["must_include"] = []
                    saved = save_stream(base_cfg, streams_dir)
                    self._send_json(201, {"ok": True, "stream": base_cfg, "path": str(saved)})
                    return

                if parsed.path == "/api/test-feed":
                    url = str(body.get("url") or "").strip()
                    days = int(body.get("lookback_days") or 30)
                    self._send_json(200, test_single_feed(url, lookback_days=days))
                    return

                if parsed.path == "/api/poll-preview":
                    cfg = body.get("stream") if isinstance(body.get("stream"), dict) else load_stream(str(body.get("slug") or DEFAULT_STREAM), streams_dir)
                    res = poll_feeds(cfg, lookback_days=int(body.get("lookback_days") or (cfg.get("defaults") or {}).get("lookback_days", 14)))
                    self._send_json(200, {
                        "feed_stats": res["feed_stats"],
                        "exa_fallback_batches": res["exa_fallback_batches"],
                        "sample_candidates": [
                            {"source": c.get("source"), "title": c.get("title"), "url": c.get("canonical_url"), "published_at": c.get("published_at"), "words": len((c.get("text") or "").split())}
                            for c in res["candidates"][:20]
                        ],
                    })
                    return

                if parsed.path == "/api/seed-demo-cycle":
                    slug = validate_slug(str(body.get("slug") or DEFAULT_STREAM))
                    cfg = body.get("stream") if isinstance(body.get("stream"), dict) else load_stream(slug, streams_dir)
                    cfg["slug"] = slug
                    odir = out_dir_for(slug, out_root)
                    cstart = str(body.get("cycle_start") or datetime.now(UTC).date().isoformat())
                    pool, evals = _demo_pool_and_evals(cfg, cstart)
                    res = render_guide(cfg, pool, evals, odir, cycle=cstart, model_name="demo-seed", no_link_check=True, write_review=True)
                    self._send_json(200, {"ok": True, "cycle_start": cstart, **res})
                    return

                if parsed.path == "/api/what-if":
                    slug = validate_slug(str(body.get("slug") or DEFAULT_STREAM))
                    cfg = body.get("stream") if isinstance(body.get("stream"), dict) else load_stream(slug, streams_dir)
                    odir = out_dir_for(slug, out_root)
                    cycles = sorted((p.stem.removeprefix("cycle-") for p in odir.glob("cycle-*.json")), reverse=True) if odir.is_dir() else []
                    cstart = str(body.get("cycle_start") or (cycles[0] if cycles else ""))
                    if not cstart or not (odir / f"cycle-{cstart}.json").is_file():
                        self._send_json(404, {"error": "No saved cycle found for this stream yet. Click 'Load Sample Cycle' or run a curation pass first."})
                        return
                    cycle_data = load_cycle(odir, cstart)
                    memory = load_recent_memory(odir, cstart)
                    self._send_json(200, run_what_if(cycle_data, cfg, memory=memory))
                    return

                self._send_json(404, {"error": "Unknown endpoint"})
            except ValueError as exc:
                self._send_json(400, {"error": str(exc)})

    return StudioHandler


def run_studio(host: str = "127.0.0.1", port: int = 8765, streams_dir: Path = STREAMS_DIR, base_out: Path | None = None) -> None:
    handler = make_handler(streams_dir=streams_dir, base_out=base_out)
    server = ThreadingHTTPServer((host, port), handler)
    actual_host, actual_port = server.server_address[:2]
    print(f"Filtergeist Studio listening on http://{actual_host}:{actual_port}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
