from __future__ import annotations

import datetime as dt
import feedparser
import hashlib
import json
import os
import re
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
    "taptap.com": "TapTap",
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


def path_allowed(url: str, prefixes: list[str]) -> bool:
    if not prefixes:
        return True
    try:
        path = urllib.parse.urlsplit(url).path or "/"
    except Exception:
        return False
    return any(path.startswith(prefix) for prefix in prefixes)


def score_relevance(item: dict[str, Any], project: dict[str, Any], policy: dict[str, Any]) -> dict[str, Any]:
    title = compact(item.get("title"), 500).lower()
    description = compact(item.get("description"), 1500).lower()
    aliases = [str(x) for x in project.get("aliases", []) if str(x).strip()]
    strong_anchors = [str(x) for x in project.get("strongAnchors", []) if str(x).strip()]
    topic_terms = [str(x) for x in project.get("topicTerms", []) if str(x).strip()]
    excludes = [str(x) for x in project.get("excludeKeywords", []) if str(x).strip()]

    alias_title: list[str] = []
    alias_description: list[str] = []
    score = 0
    reasons: list[str] = []

    for alias in aliases:
        key = alias.lower()
        pos = title.find(key)
        if pos >= 0:
            alias_title.append(alias)
            if title.startswith(key) or f"#{key}" in title or pos <= 8:
                score = max(score, 72)
                reasons.append("标题核心位置命中游戏名")
            elif pos <= 28:
                score = max(score, 58)
                reasons.append("标题前部命中游戏名")
            else:
                score = max(score, 36)
                reasons.append("标题后部命中游戏名")
        elif key in description:
            alias_description.append(alias)
            score = max(score, 18)
            reasons.append("仅摘要命中游戏名")

    strong_title = [term for term in strong_anchors if term.lower() in title]
    strong_desc = [term for term in strong_anchors if term.lower() in description]
    topics = [term for term in topic_terms if term.lower() in f"{title} {description}"]
    rejected = [term for term in excludes if term.lower() in f"{title} {description}"]

    score += min(32, len(strong_title) * 16)
    score += min(24, len([x for x in strong_desc if x not in strong_title]) * 8)
    score += min(15, len(topics) * 5)

    if strong_title:
        reasons.append("标题命中游戏实体锚点")
    if strong_desc:
        reasons.append("摘要命中游戏实体锚点")
    if topics:
        reasons.append("命中游戏舆情议题词")

    if rejected:
        score -= 100
        reasons.append("命中排除词")

    # Search snippets often include unrelated sidebars/recommendations. A game name
    # that appears only in the snippet can never enter the main opinion pool.
    if not alias_title and alias_description:
        score = min(score, int(policy.get("quarantineMinScore", 45)) + 8)

    # A late title mention without any product fingerprint is usually a natural-language
    # phrase (for example, "玩某游戏梦回甄嬛传"), not the target game entity.
    if alias_title and not strong_title and not strong_desc:
        latest_pos = max((title.find(a.lower()) for a in alias_title), default=0)
        if latest_pos > 28:
            score = min(score, int(policy.get("quarantineMinScore", 45)) - 1)

    score = max(0, min(100, score))
    main_min = int(policy.get("mainMinScore", 70))
    quarantine_min = int(policy.get("quarantineMinScore", 45))
    if rejected or not (alias_title or alias_description):
        bucket = "excluded"
    elif score >= main_min:
        bucket = "main"
    elif score >= quarantine_min:
        bucket = "quarantine"
    else:
        bucket = "excluded"

    return {
        "score": score,
        "bucket": bucket,
        "matchedAliasesTitle": alias_title,
        "matchedAliasesDescription": alias_description,
        "matchedStrongAnchors": sorted(set(strong_title + strong_desc)),
        "matchedTopics": topics,
        "rejectedKeywords": rejected,
        "relevanceReasons": reasons,
        "relevanceMethod": "entity_fingerprint_v3",
    }


def search_searxng(client: httpx.Client, base_url: str, source: dict[str, Any], limit: int) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    query = build_query(source["query"], source.get("domains", []))
    checked_at = now_iso()
    path_filtered = 0
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
            if not path_allowed(url, source.get("pathPrefixes", [])):
                path_filtered += 1
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
                "mode": "精确实体搜索发现",
                "engines": raw.get("engines", []),
            })
        return results, {
            "id": source["id"], "provider": "SearXNG", "platform": source.get("platform"),
            "query": query, "ok": True, "count": len(results), "pathFiltered": path_filtered,
            "checkedAt": checked_at, "error": None,
        }
    except Exception as exc:
        return [], {
            "id": source["id"], "provider": "SearXNG", "platform": source.get("platform"),
            "query": query, "ok": False, "count": 0, "pathFiltered": path_filtered, "checkedAt": checked_at,
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
                "mode": "可信公开 Feed",
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
        return {"items": [], "quarantineItems": []}
    try:
        payload = json.loads(path.read_text("utf-8"))
        if payload.get("mock") is True:
            raise ValueError("mock archive is not accepted")
        return payload
    except Exception:
        return {"items": [], "quarantineItems": []}


def classify_item(item: dict[str, Any], project: dict[str, Any], policy: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    result = score_relevance(item, project, policy)
    enriched = dict(item)
    enriched.update({
        "relevanceScore": result["score"],
        "relevanceBucket": result["bucket"],
        "matchedAliasesTitle": result["matchedAliasesTitle"],
        "matchedAliasesDescription": result["matchedAliasesDescription"],
        "matchedStrongAnchors": result["matchedStrongAnchors"],
        "matchedTopics": result["matchedTopics"],
        "rejectedKeywords": result["rejectedKeywords"],
        "relevanceReasons": result["relevanceReasons"],
        "relevanceMethod": result["relevanceMethod"],
    })
    return result["bucket"], enriched


def main() -> int:
    config_path = Path(os.environ.get("DEEPFEED_CONFIG", "opensource-search/config.json"))
    config = json.loads(config_path.read_text("utf-8"))
    output_path = Path(os.environ.get("DEEPFEED_OUTPUT", config.get("output", "data/live.json")))
    searxng = os.environ.get("SEARXNG_BASE_URL", "http://127.0.0.1:8080")
    project = config["project"]
    search_config = config.get("search", {})
    policy = search_config.get("policy", {})
    max_results = int(search_config.get("maxResultsPerQuery", 20))

    client = httpx.Client(
        timeout=httpx.Timeout(20.0, connect=10.0),
        follow_redirects=True,
        headers={"User-Agent": "MiniGameOpinionRadarBot/1.0 (public-index; no-login; no-bypass)"},
    )
    discovered: list[dict[str, Any]] = []
    health: list[dict[str, Any]] = []

    for source in search_config.get("queries", []):
        if source.get("enabled", True) is False:
            continue
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

    accepted: list[dict[str, Any]] = []
    quarantined: list[dict[str, Any]] = []
    excluded_count = 0
    for item in discovered:
        bucket, enriched = classify_item(item, project, policy)
        if bucket == "main":
            accepted.append(enriched)
        elif bucket == "quarantine":
            quarantined.append(enriched)
        else:
            excluded_count += 1

    existing = load_existing(output_path)
    merged: dict[str, dict[str, Any]] = {}
    # Re-evaluate historical search discoveries so old broad-search false positives
    # disappear from the main pool after the first v3 run.
    for old_item in existing.get("items", []):
        if not old_item.get("url"):
            continue
        if old_item.get("accessLevel") == "search_discovered":
            bucket, cleaned = classify_item(old_item, project, policy)
            if bucket != "main":
                continue
            old_item = cleaned
        merged[normalize_url(old_item["url"])] = old_item

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

    quarantine_merged: dict[str, dict[str, Any]] = {}
    for old_item in existing.get("quarantineItems", []):
        if old_item.get("url"):
            quarantine_merged[normalize_url(old_item["url"])] = old_item
    for item in quarantined:
        url = normalize_url(item["url"])
        old = quarantine_merged.get(url, {})
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
        quarantine_merged[url] = record

    max_items = int(config.get("retention", {}).get("maxItems", 5000))
    max_quarantine = int(policy.get("maxQuarantineItems", 300))
    items = sorted(merged.values(), key=lambda item: item.get("lastSeenAt", ""), reverse=True)[:max_items]
    quarantine_items = sorted(
        quarantine_merged.values(), key=lambda item: item.get("lastSeenAt", ""), reverse=True
    )[:max_quarantine]

    payload = {
        "ok": any(source.get("ok") for source in health),
        "mock": False,
        "generatedAt": collected_at,
        "collection": {
            "scope": "可信公开 Feed + 精确实体搜索发现",
            "projectId": project.get("id"),
            "projectName": project.get("name"),
            "noLogin": True,
            "noBypass": True,
            "fullCommentCoverage": False,
            "relevancePolicy": "entity_fingerprint_v3",
            "dataLevels": ["search_discovered", "feed_item", "page_extracted", "submitted_link"],
        },
        "sources": health,
        "itemCount": len(items),
        "quarantineCount": len(quarantine_items),
        "acceptedThisRun": len(accepted),
        "quarantinedThisRun": len(quarantined),
        "excludedThisRun": excluded_count,
        "items": items,
        "quarantineItems": quarantine_items,
    }

    output_path.parent.mkdir(parents=True, exist_ok=True)
    temp = output_path.with_suffix(output_path.suffix + ".tmp")
    temp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), "utf-8")
    temp.replace(output_path)
    print(json.dumps({
        "ok": payload["ok"], "mock": payload["mock"], "itemCount": payload["itemCount"],
        "quarantineCount": payload["quarantineCount"],
        "acceptedThisRun": payload["acceptedThisRun"], "quarantinedThisRun": payload["quarantinedThisRun"],
        "excludedThisRun": payload["excludedThisRun"], "generatedAt": payload["generatedAt"],
    }, ensure_ascii=False))
    return 0 if payload["ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
