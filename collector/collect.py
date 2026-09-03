#!/usr/bin/env python3
"""Collect public RSS/news-index results without login or access-control bypass."""

from __future__ import annotations

import hashlib
import html
import json
import re
import sys
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / "collector" / "config.json"
DATA_PATH = ROOT / "data" / "live.json"
USER_AGENT = "TideOpinionBot/0.2 (+public-RSS-discovery; no-login; no-bypass)"


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def clean_text(value: str | None) -> str:
    text = html.unescape(value or "")
    text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def parse_date(value: str | None) -> str | None:
    if not value:
        return None
    try:
        parsed = parsedate_to_datetime(value)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    except (TypeError, ValueError, OverflowError):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
        except ValueError:
            return None


def fetch(url: str, timeout: int = 20) -> bytes:
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "application/rss+xml, application/atom+xml, application/xml, text/xml, */*",
        },
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        if response.status != 200:
            raise RuntimeError(f"HTTP {response.status}")
        return response.read(2_000_000)


def child_text(node: ET.Element, names: tuple[str, ...]) -> str:
    for child in list(node):
        local = child.tag.rsplit("}", 1)[-1].lower()
        if local in names:
            if local == "link" and child.attrib.get("href"):
                return child.attrib["href"]
            return "".join(child.itertext())
    return ""


def parse_feed(payload: bytes, provider: str, query: str) -> list[dict[str, Any]]:
    root = ET.fromstring(payload)
    nodes = [n for n in root.iter() if n.tag.rsplit("}", 1)[-1].lower() in {"item", "entry"}]
    seen_at = now_iso()
    rows: list[dict[str, Any]] = []
    for node in nodes:
        title = clean_text(child_text(node, ("title",)))
        link = clean_text(child_text(node, ("link",)))
        description = clean_text(child_text(node, ("description", "summary", "content")))
        source = clean_text(child_text(node, ("source",))) or provider
        date_raw = child_text(node, ("pubdate", "published", "updated"))
        if not title or not link:
            continue
        stable = hashlib.sha256(f"{provider}|{link}".encode("utf-8")).hexdigest()[:24]
        rows.append(
            {
                "id": f"live-{stable}",
                "title": title[:300],
                "url": link,
                "source": source[:160],
                "provider": provider,
                "query": query,
                "description": description[:600],
                "publishedAt": parse_date(date_raw),
                "firstSeenAt": seen_at,
                "lastSeenAt": seen_at,
                "collectedAt": seen_at,
                "mode": "实时公开RSS",
            }
        )
    return rows


def feed_jobs(query: str) -> list[tuple[str, str]]:
    encoded = urllib.parse.quote(query)
    return [
        ("Bing News RSS", f"https://www.bing.com/news/search?q={encoded}&format=rss&setlang=zh-cn"),
        ("Google News RSS", f"https://news.google.com/rss/search?q={encoded}&hl=zh-CN&gl=CN&ceid=CN:zh-Hans"),
    ]


def load_previous() -> dict[str, Any]:
    if not DATA_PATH.exists():
        return {"items": []}
    try:
        return json.loads(DATA_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"items": []}


def main() -> int:
    config = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    previous = load_previous()
    previous_by_key = {str(item.get("url") or item.get("id")): item for item in previous.get("items", [])}
    incoming: list[dict[str, Any]] = []
    statuses: list[dict[str, Any]] = []

    for query in config.get("queries", []):
        for provider, url in feed_jobs(str(query)):
            try:
                rows = parse_feed(fetch(url), provider, str(query))
                incoming.extend(rows)
                statuses.append({"provider": provider, "query": query, "ok": True, "count": len(rows), "error": None})
            except Exception as exc:  # record partial failures; do not fabricate rows
                statuses.append({"provider": provider, "query": query, "ok": False, "count": 0, "error": f"{type(exc).__name__}: {exc}"[:300]})

    merged: dict[str, dict[str, Any]] = dict(previous_by_key)
    for item in incoming:
        key = str(item.get("url") or item.get("id"))
        old = merged.get(key)
        if old:
            item["firstSeenAt"] = old.get("firstSeenAt") or item["firstSeenAt"]
        merged[key] = {**(old or {}), **item}

    items = sorted(
        merged.values(),
        key=lambda item: str(item.get("publishedAt") or item.get("lastSeenAt") or ""),
        reverse=True,
    )[: int(config.get("max_items", 500))]

    output = {
        "ok": any(status["ok"] for status in statuses),
        "mock": False,
        "generatedAt": now_iso(),
        "collection": {
            "scope": "公开 RSS / 新闻索引",
            "queries": config.get("queries", []),
            "noLogin": True,
            "noBypass": True,
            "persistentArchive": True,
        },
        "sources": statuses,
        "itemCount": len(items),
        "items": items,
    }
    DATA_PATH.parent.mkdir(parents=True, exist_ok=True)
    DATA_PATH.write_text(json.dumps(output, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Collected {len(incoming)} rows; archive now has {len(items)} unique items")
    failed = [s for s in statuses if not s["ok"]]
    if failed:
        print(f"Partial failures: {len(failed)}", file=sys.stderr)
    return 0 if output["ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
