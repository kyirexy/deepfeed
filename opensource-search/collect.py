from __future__ import annotations

import datetime as dt
import feedparser
import hashlib
import json
import os
import re
import sys
import urllib.parse
from pathlib import Path
from typing import Any

import httpx

UTC = dt.timezone.utc
TRACKING = {"utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content", "spm", "from", "source"}
PLATFORMS = {
    "douyin.com": "抖音",
    "iesdouyin.com": "抖音",
    "xiaohongshu.com": "小红书",
    "bilibili.com": "B站",
    "b23.tv": "B站",
    "taptap.cn": "TapTap",
    "mp.weixin.qq.com": "微信公众号",
    "zhihu.com": "知乎",
    "tieba.baidu.com": "贴吧",
}


def now_iso() -> str:
    return dt.datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def compact(value: Any, limit: int = 1000) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()[:limit]


def normalize_url(url: str) -> str:
    try:
        parts = urllib.parse.urlsplit(url.strip())
        host = (parts.hostname or "").lower()
        query = [(k, v) for k, v in urllib.parse.parse_qsl(parts.query, keep_blank_values=True) if k.lower() not in TRACKING]
        query.sort()
        path = re.sub(r"/+$", "", parts.path) or "/"
        return urllib.parse.urlunsplit((parts.scheme.lower(), host, path, urllib.parse.urlencode(query), ""))
    except Exception:
        return url.strip()


def domain(url: str) -> str:
    try:
        return (urllib.parse.urlsplit(url).hostname or "").lower()
    except Exception:
        return ""


def platform(url: str, fallback: str = "网页") -> str:
    host = domain(url)
    for suffix, name in PLATFORMS.items():
        if host == suffix or host.endswith("." + suffix):
            return name
    return fallback


def make_id(url: str) -> str:
    return "live-" + hashlib.sha256(normalize_url(url).encode("utf-8")).hexdigest()[:24]


def build_query(query: str, domains: list[str]) -> str:
    if not domains:
        return query
    return f"({query}) (" + " OR ".join(f"site:{item}" for item in domains) + ")"


def is_relevant(item: dict[str, Any], keywords: list[str], excludes: list[str]) -> tuple[bool, list[str], list[str]]:
    # Do not include the search query itself: otherwise every result would pass.
    haystack = f"{item.get('title', '')} {item.get('description', '')}".lower()
    matched = [word for word in keywords if word.lower() in haystack]
    rejected = [word for word in excludes if word.lower() in haystack]
    return bool(matched) and not rejected, matched, rejected


def search_searxng(client: httpx.Client, base_url: str, source: dict[str, Any], limit: int) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    query = build_query(source["query"], source.get("domains", []))
    checked_at = now_iso()
    try:
        response = client.get(
            base_url.rstrip("/") + "/search",
            params={"q": query, "format": "json", "language": "zh-CN", "safesearch": 0},
        )
        response.raise_for_status()
        raw_results = response.json().get("results", [])[:limit]
        results = []
        for raw in raw_results:
            url = normalize_url(str(raw.get("url") or ""))
            if not url:
                continue
            results.append({
                "url": url,
                "title": compact(raw.get("title"), 300),
                "description": compact(raw.get("content"), 1000),
                "publishedAt": raw.get("publishedDate"),
                "source": domain(url),
                "provider": "SearXNG",
                "platform": source.get("platform") or platform(url),
                "query": query,
                "accessLevel": "search_discovered",
                "mode": "开源搜索发现",
                "engines": raw.get("engines", []),
            })
        return results, {
            "id": source["id"], "provider": "SearXNG", "platform": source.get("platform"),
            "query": query, "ok": True, "count": len(results), "checkedAt": checked_at, "error": None,
        }
    except Exception as exc:
        return [], {
            "id": source["id"], "provider": "SearXNG", "platform": source.get("platform"),
            "query": query, "ok": False, "count": 0, "checkedAt": checked_at,
            "error": f"{type(exc).__name__}: {exc}"[:500],
        }


def read_feed(client: httpx.Client, feed: dict[str, Any]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    checked_at = now_iso()
    try:
        response = client.get(feed["url"])
        response.raise_for_status()
        parsed = feedparser.parse(response.content)
        if parsed.bozo and not parsed.entries:
            raise RuntimeError(str(parsed.bozo_exception))
        results = []
        for entry in parsed.entries:
            url = normalize_url(str(entry.get("link") or ""))
            if not url:
                continue
            results.append({
                "url": url,
                "title": compact(entry.get("title"), 300),
                "description": compact(entry.get("summary") or entry.get("description"), 1000),
                "publishedAt": entry.get("published") or entry.get("updated"),
                "source": domain(url),
                "provider": "RSS/Atom",
                "platform": feed.get("platform") or platform(url),
                "query": feed.get("name"),
                "accessLevel": "feed_item",
                "mode": "公开 Feed",
            })
        return results, {
            "id": feed["id"], "provider": "RSS/Atom", "platform": feed.get("platform"),
            "query": feed.get("name"), "ok": True, "count": len(results), "checkedAt": checked_at, "error": None,
        }
    except Exception as exc:
        return [], {
            "id": feed["id"], "provider": "RSS/Atom", "platform": feed.get("platform"),
            "query": feed.get("name"), "ok": False, "count": 0, "checkedAt": checked_at,
            "error": f"{type(exc).__name__}: {exc}"[:500],
        }


def load_existing(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"items": []}
    try:
        payload = json.loads(path.read_text("utf-8"))
        if payload.get("mock") is True:
            raise ValueError("mock archive is not accepted")
        return payload
    except Exception:
        return {"items": []}


def main() -> int:
    config_path = Path(os.environ.get("DEEPFEED_CONFIG", "opensource-search/config.json"))
    config = json.loads(config_path.read_text("utf-8"))
    output_path = Path(os.environ.get("DEEPFEED_OUTPUT", config.get("output", "data/live.json")))
    searxng = os.environ.get("SEARXNG_BASE_URL", "http://127.0.0.1:8080")
    project = config["project"]
    max_results = int(config.get("search", {}).get("maxResultsPerQuery", 20))

    client = httpx.Client(
        timeout=httpx.Timeout(20.0, connect=10.0),
        follow_redirects=True,
        headers={"User-Agent": "TidePublicOpinionBot/0.3 (public-index; no-login; no-bypass)"},
    )
    discovered: list[dict[str, Any]] = []
    health: list[dict[str, Any]] = []

    for source in config.get("search", {}).get("queries", []):
        items, status = search_searxng(client, searxng, source, max_results)
        discovered.extend(items)
        health.append(status)

    for feed in config.get("feeds", []):
        if not feed.get("enabled", True):
            continue
        items, status = read_feed(client, feed)
        discovered.extend(items)
        health.append(status)
    client.close()

    accepted = []
    excluded_count = 0
    for item in discovered:
        ok, matched, rejected = is_relevant(item, project.get("keywords", []), project.get("excludeKeywords", []))
        if not ok:
            excluded_count += 1
            continue
        item["matchedKeywords"] = matched
        item["rejectedKeywords"] = rejected
        item["relevanceMethod"] = "transparent_keyword_rule"
        accepted.append(item)

    existing = load_existing(output_path)
    merged = {normalize_url(item.get("url", "")): item for item in existing.get("items", []) if item.get("url")}
    collected_at = now_iso()
    for item in accepted:
        url = normalize_url(item["url"])
        old = merged.get(url, {})
        record = dict(old)
        record.update(item)
        record.update({
            "id": old.get("id") or make_id(url),
            "url": url,
            "firstSeenAt": old.get("firstSeenAt") or collected_at,
            "lastSeenAt": collected_at,
            "collectedAt": collected_at,
            "mock": False,
        })
        merged[url] = record

    max_items = int(config.get("retention", {}).get("maxItems", 5000))
    items = sorted(merged.values(), key=lambda item: item.get("lastSeenAt", ""), reverse=True)[:max_items]
    payload = {
        "ok": any(source.get("ok") for source in health),
        "mock": False,
        "generatedAt": collected_at,
        "collection": {
            "scope": "公开搜索索引 + 公开 RSS/Atom",
            "projectId": project.get("id"),
            "projectName": project.get("name"),
            "noLogin": True,
            "noBypass": True,
            "fullCommentCoverage": False,
            "dataLevels": ["search_discovered", "feed_item", "page_extracted", "submitted_link"],
        },
        "sources": health,
        "itemCount": len(items),
        "acceptedThisRun": len(accepted),
        "excludedThisRun": excluded_count,
        "items": items,
    }

    output_path.parent.mkdir(parents=True, exist_ok=True)
    temp = output_path.with_suffix(output_path.suffix + ".tmp")
    temp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), "utf-8")
    temp.replace(output_path)
    print(json.dumps({
        "ok": payload["ok"], "mock": payload["mock"], "itemCount": payload["itemCount"],
        "acceptedThisRun": payload["acceptedThisRun"], "excludedThisRun": payload["excludedThisRun"],
        "generatedAt": payload["generatedAt"],
    }, ensure_ascii=False))
    return 0 if payload["ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
