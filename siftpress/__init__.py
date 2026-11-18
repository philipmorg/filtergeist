"""Siftpress core package: multi-source discovery, editorial scoring, and guide rendering."""
from __future__ import annotations

import html as htmlmod
import ipaddress
import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import date, datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any

# ponytail: support stock macOS /usr/bin/python3 (3.9) alongside uv Python 3.11+.
UTC = timezone.utc  # noqa: UP017
NET_XML_ERRORS = (OSError, urllib.error.URLError, ET.ParseError, ValueError, TimeoutError)

REPO_ROOT = Path(__file__).resolve().parent.parent
STREAMS_DIR = REPO_ROOT / "streams"
WEB_DIR = REPO_ROOT / "web"
DEFAULT_STREAM = "systems-and-craft"

SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"
TAG_RE = re.compile(r"<(script|style)\b[^>]*>.*?</\1>", re.IGNORECASE | re.DOTALL)
HTML_RE = re.compile(r"<[^>]+>")
WS_RE = re.compile(r"\s+")
SUFFIX_RE = re.compile(r"\s+[-–—|:]\s+[^-–—|:]{2,80}$")
TRACKING_PARAMS = {"fbclid", "gclid", "mc_cid", "mc_eid", "mkt_tok", "ref", "s", "si", "spm"}


class _Redirect308Handler(urllib.request.HTTPRedirectHandler):
    http_error_308 = urllib.request.HTTPRedirectHandler.http_error_302
    http_error_307 = urllib.request.HTTPRedirectHandler.http_error_302

    def redirect_request(self, req: Any, fp: Any, code: int, msg: str, headers: Any, newurl: str) -> Any:
        return super().redirect_request(req, fp, 307 if code == 308 else code, msg, headers, newurl)


OPENER = urllib.request.build_opener(_Redirect308Handler())


def validate_slug(slug: str) -> str:
    clean = (slug or "").strip().lower()
    if not SLUG_RE.match(clean):
        raise ValueError(f"Invalid stream slug {slug!r}; use lowercase letters, digits, '-' or '_'")
    return clean


def stream_path(slug: str, streams_dir: Path = STREAMS_DIR) -> Path:
    return streams_dir / f"{validate_slug(slug)}.json"


def list_streams(streams_dir: Path = STREAMS_DIR) -> list[dict[str, Any]]:
    if not streams_dir.is_dir():
        return []
    items: list[dict[str, Any]] = []
    for p in sorted(streams_dir.glob("*.json")):
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            slug = validate_slug(data.get("slug") or p.stem)
            items.append({
                "slug": slug,
                "newsletter": data.get("newsletter") or slug,
                "owner": data.get("owner") or "",
                "description": data.get("description") or "",
                "sources_count": len([s for s in (data.get("content_sources") or []) if s.get("enabled", True)]),
                "apify_enabled": bool((data.get("apify") or {}).get("enabled")),
                "path": str(p),
            })
        except (OSError, ValueError):
            continue
    return items


def load_stream(slug_or_path: str = DEFAULT_STREAM, streams_dir: Path = STREAMS_DIR) -> dict[str, Any]:
    candidate = Path(slug_or_path)
    if candidate.suffix == ".json" and candidate.is_file():
        data = json.loads(candidate.read_text(encoding="utf-8"))
        data["slug"] = validate_slug(data.get("slug") or candidate.stem)
        return data
    slug = validate_slug(slug_or_path)
    p = stream_path(slug, streams_dir)
    if not p.is_file():
        raise FileNotFoundError(f"Stream config not found: {p}")
    data = json.loads(p.read_text(encoding="utf-8"))
    data["slug"] = slug
    return data


def save_stream(cfg: dict[str, Any], streams_dir: Path = STREAMS_DIR) -> Path:
    slug = validate_slug(cfg.get("slug") or DEFAULT_STREAM)
    cfg["slug"] = slug
    streams_dir.mkdir(parents=True, exist_ok=True)
    target = stream_path(slug, streams_dir)
    tmp = target.with_suffix(".tmp")
    tmp.write_text(json.dumps(cfg, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(tmp, target)
    return target


def out_dir_for(slug: str, base_out: Path | None = None) -> Path:
    root = base_out if base_out is not None else (REPO_ROOT / "out")
    clean = validate_slug(slug)
    return root / clean


def is_safe_public_url(url: str) -> bool:
    p = urllib.parse.urlsplit((url or "").strip())
    if p.scheme.lower() not in {"http", "https"} or not p.hostname:
        return False
    host = p.hostname.lower().strip(".")
    if host in {"localhost", "0.0.0.0"} or host.endswith(".local"):
        return False
    try:
        ip = ipaddress.ip_address(host)
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved:
            return False
    except ValueError:
        pass
    return True


def http_get(url: str, timeout_s: int = 15, max_bytes: int = 1_500_000) -> tuple[int, str, bytes, str]:
    if not is_safe_public_url(url):
        raise ValueError(f"Blocked non-public URL: {url}")
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "*/*"})
    with OPENER.open(req, timeout=timeout_s) as resp:
        return resp.status, (resp.headers.get("Content-Type") or ""), resp.read(max_bytes), resp.geturl()


def http_post_json(url: str, payload: dict[str, Any], headers: dict[str, str] | None = None, timeout_s: int = 120) -> Any:
    body = json.dumps(payload).encode("utf-8")
    hdrs = {"User-Agent": UA, "Content-Type": "application/json", "Accept": "application/json", **(headers or {})}
    req = urllib.request.Request(url, data=body, headers=hdrs, method="POST")
    with OPENER.open(req, timeout=timeout_s) as resp:
        return json.loads(resp.read(4_000_000).decode("utf-8", errors="replace"))


def clean_html(raw: str) -> str:
    unescaped = htmlmod.unescape(raw or "")
    stripped = HTML_RE.sub(" ", TAG_RE.sub(" ", unescaped))
    return WS_RE.sub(" ", stripped).strip()


def canonical_url(url: str) -> str:
    p = urllib.parse.urlsplit((url or "").strip())
    if p.scheme.lower() not in {"http", "https"} or not p.netloc:
        return ""
    kept = [
        (k, v)
        for k, v in urllib.parse.parse_qsl(p.query, keep_blank_values=False)
        if not k.lower().startswith("utm_") and k.lower() not in TRACKING_PARAMS
    ]
    query = urllib.parse.urlencode(sorted(kept))
    host = p.netloc.lower().removeprefix("www.")
    path = p.path.rstrip("/") or "/"
    m = re.match(r"^/(shorts/)?([A-Za-z0-9_-]{11})$", path)
    if host in {"youtube.com", "youtu.be", "m.youtube.com"} and m and not path.startswith("/watch"):
        path, query, host = "/watch", f"v={m.group(2)}", "youtube.com"
    return urllib.parse.urlunsplit((p.scheme.lower(), host, path, query, ""))


def domain_of(url: str) -> str:
    return (url or "").split("://", 1)[-1].split("/", 1)[0].lower().removeprefix("www.")


def title_key(title: str | None) -> str:
    t = WS_RE.sub(" ", (title or "").strip().lower())
    t = SUFFIX_RE.sub("", t)
    return re.sub(r"[^a-z0-9]+", " ", t).strip()


def normalize_cycle_start(value: str) -> str:
    try:
        return date.fromisoformat(value).isoformat()
    except ValueError as exc:
        raise ValueError(f"Cycle must be an ISO date (YYYY-MM-DD), got {value!r}") from exc


def parse_date(raw: str | None) -> datetime | None:
    if not raw or not str(raw).strip():
        return None
    val = str(raw).strip()
    try:
        dt = parsedate_to_datetime(val)
        return dt.astimezone(UTC) if dt.tzinfo else dt.replace(tzinfo=UTC)
    except (TypeError, ValueError, IndexError):
        pass
    try:
        iso_val = f"{val[:-1]}+00:00" if val.endswith("Z") else val
        dt = datetime.fromisoformat(iso_val)
        return dt.astimezone(UTC) if dt.tzinfo else dt.replace(tzinfo=UTC)
    except ValueError:
        pass
    m = re.match(r"^(\d{4}-\d{2}-\d{2})", val)
    if m:
        try:
            return datetime.fromisoformat(m.group(1)).replace(tzinfo=UTC)
        except ValueError:
            return None
    return None
