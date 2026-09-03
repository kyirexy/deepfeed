#!/usr/bin/env python3
"""Collect public opinion discovery records without login, CAPTCHA bypass, or mock fallback.

Providers:
- Bing News RSS and Google News RSS
- A self-hosted SearXNG JSON API for domain-limited discovery
- A self-hosted RSSHub instance for reviewed public routes

The output always records provider failures. It never fabricates items.
"""
from __future__ import annotations

import datetime as dt
import email.utils
import hashlib
import html
import json
import os
import pathlib
import re
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from typing import Any, Iterable

ROOT = pathlib.Path(__file__).resolve().parents[1]
CONFIG_PATH = pathlib.Path(os.getenv("TIDE_TARGETS_CONFIG", ROOT / "config" / "targets.json"))
OUTPUT = pathlib.Path(os.getenv("TIDE_OUTPUT", ROOT / "data" / "live.json"))
SEARXNG_URL = os.getenv("SEARXNG_URL", "").rstrip("/")
RSSHUB_URL = os.getenv("RSSHUB_URL", "").rstrip("/")
USER_AGENT = os.getenv(
    "TIDE_USER_AGENT",
    "TidePublicOpinionBot/0.3 (+public discovery; no login; no bypass; contact: repo owner)",
)
MAX_QUERIES_PER_PLATFORM = int(os.getenv("MAX_QUERIES_PER_PLATFORM", "2"))
SEARCH_TIME_RANGE = os.getenv("SEARCH_TIME_RANGE", "month")

TRACKING_KEYS = {
    "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
    "spm", "from", "share_from", "share_source", "source", "ref", "referrer",
    "timestamp", "scene", "clicktime", "enterid", "featurecode",
}


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z")


def clean_text(value: str | None) -> str:
    value = html.unescape(value or "")
    value = re.sub(r"<script\b[^>]*>.*?</script>", " ", value, flags=re.I | re.S)
    value = re.sub(r"<style\b[^>]*>.*?</style>", " ", value, flags=re.I | re.S)
    value = re.sub(r"<[^>]+>", " ", value)
    return re.sub(r"\s+", " ", value).strip()


def parse_date(value: Any) -> str | None:
    if not value:
        return None
    if isinstance(value, (int, float)):
        try:
            return dt.datetime.fromtimestamp(value, tz=dt.timezone.utc).isoformat().replace("+00:00", "Z")
        except Exception:
            return None
    raw = str(value).strip()
    if not raw:
        return None
    try:
        parsed = dt.datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=dt.timezone.utc)
        return parsed.astimezone(dt.timezone.utc).isoformat().replace("+00:00", "Z")
    except Exception:
        pass
    try:
        parsed = email.utils.parsedate_to_datetime(raw)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=dt.timezone.utc)
        return parsed.astimezone(dt.timezone.utc).isoformat().replace("+00:00", "Z")
    except Exception:
        return raw


def fetch_bytes(url: str, *, accept: str, timeout: int, method: str = "GET", body: bytes | None = None) -> bytes:
    request = urllib.request.Request(
        url,
        data=body,
        method=method,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": accept,
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.5",
        },
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read()


def fetch_json(url: str, timeout: int) -> dict[str, Any]:
    payload = fetch_bytes(url, accept="application/json", timeout=timeout)
    parsed = json.loads(payload.decode("utf-8", errors="replace"))
    if not isinstance(parsed, dict):
        raise ValueError("JSON response is not an object")
    return parsed


def canonicalize_url(raw_url: str) -> str:
    raw_url = clean_text(raw_url)
    if not raw_url:
        return ""
    try:
        parsed = urllib.parse.urlsplit(raw_url)
        filtered = []
        for key, value in urllib.parse.parse_qsl(parsed.query, keep_blank_values=True):
            lower = key.lower()
            if lower in TRACKING_KEYS or lower.startswith("utm_"):
                continue
            filtered.append((key, value))
        path = re.sub(r"/{2,}", "/", parsed.path or "/")
        return urllib.parse.urlunsplit(
            (
                parsed.scheme.lower() or "https",
                parsed.netloc.lower(),
                path.rstrip("/") or "/",
                urllib.parse.urlencode(filtered, doseq=True),
                "",
            )
        )
    except Exception:
        return raw_url


def stable_id(url: str) -> str:
    return "live-" + hashlib.sha256(url.encode("utf-8")).hexdigest()[:24]


def load_config() -> dict[str, Any]:
    data = json.loads(CONFIG_PATH.read_text("utf-8"))
    if not isinstance(data, dict) or not data.get("games"):
        raise ValueError("targets config must contain at least one game")
    return data


def load_existing() -> tuple[dict[str, dict[str, Any]], list[dict[str, Any]]]:
    if not OUTPUT.exists():
        return {}, []
    try:
        data = json.loads(OUTPUT.read_text("utf-8"))
        items = {
            canonicalize_url(item.get("url", "")): item
            for item in data.get("items", [])
            if item.get("url")
        }
        excluded = [item for item in data.get("excludedItems", []) if item.get("url")]
        return items, excluded
    except Exception:
        return {}, []


def host_of(url: str) -> str:
    try:
        return urllib.parse.urlsplit(url).netloc.lower().removeprefix("www.")
    except Exception:
        return ""


def infer_platform(url: str, platforms: dict[str, Any], fallback: str = "news") -> tuple[str, str]:
    host = host_of(url)
    for key, spec in platforms.items():
        for domain in spec.get("domains", []):
            normalized = domain.lower().removeprefix("www.")
            if host == normalized or host.endswith("." + normalized):
                return key, spec.get("label", key)
    spec = platforms.get(fallback, {})
    return fallback, spec.get("label", fallback)


def evaluate_relevance(title: str, description: str, game: dict[str, Any]) -> dict[str, Any]:
    # Query terms are intentionally excluded from scoring. Including them would
    # make every search result appear relevant even when the returned page is not.
    text = f"{title} {description}".lower()
    title_text = title.lower()
    score = 0
    matched_aliases: list[str] = []
    matched_terms: list[str] = []
    excluded_terms: list[str] = []

    for alias in game.get("aliases", [game.get("name", "")]):
        if alias and alias.lower() in text:
            score += 52 if alias.lower() in title_text else 42
            matched_aliases.append(alias)

    for term in game.get("includeTerms", []):
        if term.lower() in text:
            score += 8
            matched_terms.append(term)

    for term in game.get("excludeTerms", []):
        if term.lower() in text:
            score -= 38
            excluded_terms.append(term)

    score = max(0, min(100, score))
    is_excluded = bool(excluded_terms) and score < 60
    return {
        "score": score,
        "matchedAliases": matched_aliases,
        "matchedTerms": matched_terms,
        "excludedTerms": excluded_terms,
        "isExcluded": is_excluded,
        "isRelevant": score >= 42 and not is_excluded,
    }


def make_item(
    *,
    title: str,
    url: str,
    description: str,
    published_at: Any,
    source: str,
    provider: str,
    provider_type: str,
    query: str,
    collected_at: str,
    platforms: dict[str, Any],
    game: dict[str, Any] | None,
    platform_key: str | None = None,
    access_level: str = "搜索发现",
    engine: str | None = None,
    raw_score: Any = None,
    trusted_scope: bool = False,
) -> dict[str, Any] | None:
    canonical = canonicalize_url(url)
    if not canonical:
        return None
    if platform_key:
        platform_label = platforms.get(platform_key, {}).get("label", platform_key)
    else:
        platform_key, platform_label = infer_platform(canonical, platforms)
    relevance = (
        {
            "score": 95,
            "matchedAliases": [game.get("name", "")] if game else [],
            "matchedTerms": [],
            "excludedTerms": [],
            "isExcluded": False,
            "isRelevant": True,
        }
        if trusted_scope
        else evaluate_relevance(title, description, game)
        if game
        else {
            "score": 35,
            "matchedAliases": [],
            "matchedTerms": [],
            "excludedTerms": [],
            "isExcluded": False,
            "isRelevant": True,
        }
    )
    return {
        "id": stable_id(canonical),
        "title": clean_text(title) or "无标题",
        "url": canonical,
        "source": clean_text(source) or host_of(canonical) or provider,
        "provider": provider,
        "providerType": provider_type,
        "engine": engine,
        "query": query,
        "description": clean_text(description),
        "publishedAt": parse_date(published_at),
        "firstSeenAt": collected_at,
        "lastSeenAt": collected_at,
        "collectedAt": collected_at,
        "mode": access_level,
        "accessLevel": access_level,
        "platform": platform_key,
        "platformLabel": platform_label,
        "gameId": game.get("id") if game else None,
        "gameName": game.get("name") if game else None,
        "relevanceScore": relevance["score"],
        "isRelevant": relevance["isRelevant"],
        "matchedAliases": relevance["matchedAliases"],
        "matchedTerms": relevance["matchedTerms"],
        "excludedTerms": relevance["excludedTerms"],
        "isExcluded": relevance["isExcluded"],
        "rawProviderScore": raw_score,
        "analysisStatus": "unclassified",
    }


def merge_item(existing: dict[str, dict[str, Any]], item: dict[str, Any], collected_at: str) -> bool:
    key = canonicalize_url(item["url"])
    old = existing.get(key)
    is_new = old is None
    if old:
        item["firstSeenAt"] = old.get("firstSeenAt") or collected_at
        item["seenCount"] = int(old.get("seenCount", 1)) + 1
        if not item.get("publishedAt"):
            item["publishedAt"] = old.get("publishedAt")
        if len(old.get("description", "")) > len(item.get("description", "")):
            item["description"] = old.get("description", "")
    else:
        item["seenCount"] = 1
    existing[key] = item
    return is_new


def find_text(node: ET.Element, *names: str) -> str:
    for name in names:
        child = node.find(name)
        if child is not None and child.text:
            return child.text
        for descendant in node.iter():
            if descendant.tag.rsplit("}", 1)[-1] == name and descendant.text:
                return descendant.text
    return ""


def parse_xml_feed(
    payload: bytes,
    *,
    provider: str,
    provider_type: str,
    query: str,
    collected_at: str,
    platforms: dict[str, Any],
    game: dict[str, Any] | None,
    platform_key: str | None,
    access_level: str,
    trusted_scope: bool = False,
) -> list[dict[str, Any]]:
    root = ET.fromstring(payload)
    nodes = root.findall(".//item")
    if not nodes:
        nodes = [node for node in root.iter() if node.tag.rsplit("}", 1)[-1] == "entry"]
    items: list[dict[str, Any]] = []
    for node in nodes:
        title = find_text(node, "title")
        link = find_text(node, "link")
        if not link:
            for descendant in node.iter():
                if descendant.tag.rsplit("}", 1)[-1] == "link" and descendant.attrib.get("href"):
                    link = descendant.attrib["href"]
                    break
        if not link:
            continue
        source = find_text(node, "source", "author", "creator")
        description = find_text(node, "description", "summary", "content")
        published = find_text(node, "pubDate", "published", "updated", "date")
        item = make_item(
            title=title,
            url=link,
            description=description,
            published_at=published,
            source=source,
            provider=provider,
            provider_type=provider_type,
            query=query,
            collected_at=collected_at,
            platforms=platforms,
            game=game,
            platform_key=platform_key,
            access_level=access_level,
            trusted_scope=trusted_scope,
        )
        if item:
            items.append(item)
    return items


def news_rss_endpoints(query: str) -> Iterable[tuple[str, str]]:
    encoded = urllib.parse.quote(query)
    yield "Bing News RSS", f"https://www.bing.com/news/search?q={encoded}&format=rss"
    yield "Google News RSS", (
        f"https://news.google.com/rss/search?q={encoded}&hl=zh-CN&gl=CN&ceid=CN:zh-Hans"
    )


def collect_news_rss(
    *,
    config: dict[str, Any],
    collected_at: str,
    timeout: int,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    platforms = config["platforms"]
    rows: list[dict[str, Any]] = []
    statuses: list[dict[str, Any]] = []
    query_pairs: list[tuple[str, dict[str, Any] | None]] = []
    for game in config.get("games", []):
        query_pairs.extend((query, game) for query in game.get("newsQueries", []))
    query_pairs.extend((query, None) for query in config.get("generalNewsQueries", []))

    for query, game in query_pairs:
        for provider, url in news_rss_endpoints(query):
            started = time.perf_counter()
            try:
                payload = fetch_bytes(
                    url,
                    accept="application/rss+xml,application/xml,text/xml",
                    timeout=timeout,
                )
                parsed = parse_xml_feed(
                    payload,
                    provider=provider,
                    provider_type="news_rss",
                    query=query,
                    collected_at=collected_at,
                    platforms=platforms,
                    game=game,
                    platform_key="news",
                    access_level="新闻 RSS",
                )
                rows.extend(parsed)
                statuses.append({
                    "provider": provider,
                    "providerType": "news_rss",
                    "query": query,
                    "platform": "news",
                    "ok": True,
                    "count": len(parsed),
                    "error": None,
                    "elapsedMs": round((time.perf_counter() - started) * 1000),
                })
            except Exception as exc:
                statuses.append({
                    "provider": provider,
                    "providerType": "news_rss",
                    "query": query,
                    "platform": "news",
                    "ok": False,
                    "count": 0,
                    "error": f"{type(exc).__name__}: {exc}",
                    "elapsedMs": round((time.perf_counter() - started) * 1000),
                })
    return rows, statuses


def searx_queries(config: dict[str, Any]) -> list[tuple[str, dict[str, Any], str]]:
    queries: list[tuple[str, dict[str, Any], str]] = []
    platforms = config["platforms"]
    for game in config.get("games", []):
        suffixes = game.get("searchSuffixes", ["小游戏"])[:MAX_QUERIES_PER_PLATFORM]
        for platform_key in game.get("searchPlatforms", []):
            platform = platforms.get(platform_key)
            if not platform or not platform.get("siteQuery"):
                continue
            for suffix in suffixes:
                query = f'"{game["name"]}" {suffix} {platform["siteQuery"]}'
                queries.append((query, game, platform_key))
    return queries


def collect_searxng(
    *,
    config: dict[str, Any],
    collected_at: str,
    timeout: int,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    rows: list[dict[str, Any]] = []
    statuses: list[dict[str, Any]] = []
    if not SEARXNG_URL:
        statuses.append({
            "provider": "SearXNG",
            "providerType": "metasearch",
            "query": "configuration",
            "platform": "all",
            "ok": False,
            "skipped": True,
            "count": 0,
            "error": "SEARXNG_URL is not configured",
            "elapsedMs": 0,
        })
        return rows, statuses

    limit = int(config.get("limits", {}).get("perSearch", 12))
    platforms = config["platforms"]
    for query, game, platform_key in searx_queries(config):
        started = time.perf_counter()
        params = {
            "q": query,
            "format": "json",
            "categories": "general,news",
            "language": "zh-CN",
            "safesearch": "0",
            "pageno": "1",
        }
        if SEARCH_TIME_RANGE:
            params["time_range"] = SEARCH_TIME_RANGE
        url = f"{SEARXNG_URL}/search?{urllib.parse.urlencode(params)}"
        try:
            payload = fetch_json(url, timeout)
            results = payload.get("results") or []
            parsed_count = 0
            for result in results[:limit]:
                result_url = result.get("url") or ""
                expected_domains = config["platforms"][platform_key].get("domains", [])
                result_host = host_of(result_url)
                if expected_domains and not any(
                    result_host == d or result_host.endswith("." + d)
                    for d in expected_domains
                ):
                    continue
                engines = result.get("engines") or []
                engine = ", ".join(engines) if isinstance(engines, list) else str(engines or result.get("engine") or "")
                item = make_item(
                    title=result.get("title") or "",
                    url=result_url,
                    description=result.get("content") or result.get("description") or "",
                    published_at=result.get("publishedDate") or result.get("published_date"),
                    source=result_host,
                    provider="SearXNG",
                    provider_type="metasearch",
                    query=query,
                    collected_at=collected_at,
                    platforms=platforms,
                    game=game,
                    platform_key=platform_key,
                    access_level="搜索发现",
                    engine=engine,
                    raw_score=result.get("score"),
                )
                if item:
                    rows.append(item)
                    parsed_count += 1
            statuses.append({
                "provider": "SearXNG",
                "providerType": "metasearch",
                "query": query,
                "platform": platform_key,
                "ok": True,
                "count": parsed_count,
                "rawCount": len(results),
                "error": None,
                "elapsedMs": round((time.perf_counter() - started) * 1000),
            })
        except Exception as exc:
            statuses.append({
                "provider": "SearXNG",
                "providerType": "metasearch",
                "query": query,
                "platform": platform_key,
                "ok": False,
                "count": 0,
                "error": f"{type(exc).__name__}: {exc}",
                "elapsedMs": round((time.perf_counter() - started) * 1000),
            })
        time.sleep(0.35)
    return rows, statuses


def render_rsshub_path(feed: dict[str, Any]) -> tuple[str, str]:
    if feed.get("path"):
        return feed["path"], feed.get("query") or feed.get("label") or feed["path"]
    template = feed.get("pathTemplate") or ""
    query = str(feed.get("query") or "")
    encoded = urllib.parse.quote(query, safe="")
    return template.replace("{query}", encoded), query


def collect_rsshub(
    *,
    config: dict[str, Any],
    collected_at: str,
    timeout: int,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    rows: list[dict[str, Any]] = []
    statuses: list[dict[str, Any]] = []
    feeds = [
        (game, feed)
        for game in config.get("games", [])
        for feed in game.get("rsshubFeeds", [])
    ]
    if not feeds:
        return rows, statuses
    if not RSSHUB_URL:
        statuses.append({
            "provider": "RSSHub",
            "providerType": "rsshub",
            "query": "configuration",
            "platform": "all",
            "ok": False,
            "skipped": True,
            "count": 0,
            "error": "RSSHUB_URL is not configured",
            "elapsedMs": 0,
        })
        return rows, statuses

    platforms = config["platforms"]
    for game, feed in feeds:
        path, query = render_rsshub_path(feed)
        url = RSSHUB_URL + (path if path.startswith("/") else "/" + path)
        started = time.perf_counter()
        try:
            payload = fetch_bytes(
                url,
                accept="application/rss+xml,application/atom+xml,application/xml,text/xml",
                timeout=timeout,
            )
            parsed = parse_xml_feed(
                payload,
                provider=f"RSSHub · {feed.get('label', feed.get('id', 'route'))}",
                provider_type="rsshub",
                query=query,
                collected_at=collected_at,
                platforms=platforms,
                game=game,
                platform_key=feed.get("platform"),
                access_level=feed.get("accessLevel", "平台 Feed"),
                trusted_scope=bool(feed.get("scopeTrusted", True)),
            )
            rows.extend(parsed)
            statuses.append({
                "provider": "RSSHub",
                "label": feed.get("label"),
                "providerType": "rsshub",
                "query": query,
                "path": path,
                "platform": feed.get("platform"),
                "ok": True,
                "count": len(parsed),
                "error": None,
                "elapsedMs": round((time.perf_counter() - started) * 1000),
            })
        except Exception as exc:
            statuses.append({
                "provider": "RSSHub",
                "label": feed.get("label"),
                "providerType": "rsshub",
                "query": query,
                "path": path,
                "platform": feed.get("platform"),
                "ok": False,
                "count": 0,
                "error": f"{type(exc).__name__}: {exc}",
                "elapsedMs": round((time.perf_counter() - started) * 1000),
            })
    return rows, statuses


def sort_key(item: dict[str, Any]) -> str:
    return item.get("publishedAt") or item.get("collectedAt") or ""


def main() -> None:
    config = load_config()
    timeout = int(config.get("limits", {}).get("requestTimeoutSeconds", 25))
    archive_limit = int(config.get("limits", {}).get("archiveItems", 5000))
    excluded_limit = int(config.get("limits", {}).get("excludedItems", 500))
    collected_at = utc_now()
    existing, previous_excluded = load_existing()

    provider_runs = [
        collect_news_rss(config=config, collected_at=collected_at, timeout=timeout),
        collect_searxng(config=config, collected_at=collected_at, timeout=timeout),
        collect_rsshub(config=config, collected_at=collected_at, timeout=timeout),
    ]

    statuses: list[dict[str, Any]] = []
    incoming: list[dict[str, Any]] = []
    for rows, source_statuses in provider_runs:
        incoming.extend(rows)
        statuses.extend(source_statuses)

    new_count = 0
    excluded_now: list[dict[str, Any]] = []
    for item in incoming:
        if item.get("isExcluded"):
            excluded_now.append(item)
            continue
        if merge_item(existing, item, collected_at):
            new_count += 1

    items = sorted(existing.values(), key=sort_key, reverse=True)[:archive_limit]
    excluded_by_url = {
        canonicalize_url(item.get("url", "")): item
        for item in [*previous_excluded, *excluded_now]
        if item.get("url")
    }
    excluded_items = sorted(excluded_by_url.values(), key=sort_key, reverse=True)[:excluded_limit]

    successful = [status for status in statuses if status.get("ok")]
    failed = [status for status in statuses if not status.get("ok") and not status.get("skipped")]
    skipped = [status for status in statuses if status.get("skipped")]
    by_type: dict[str, int] = {}
    by_platform: dict[str, int] = {}
    for item in items:
        by_type[item.get("providerType", "unknown")] = by_type.get(item.get("providerType", "unknown"), 0) + 1
        by_platform[item.get("platformLabel", "未知")] = by_platform.get(item.get("platformLabel", "未知"), 0) + 1

    result = {
        "schemaVersion": 2,
        "ok": bool(successful),
        "mock": False,
        "generatedAt": collected_at,
        "collection": {
            "scope": "公开新闻 RSS + SearXNG 跨站搜索 + RSSHub 审核路由",
            "noLogin": True,
            "noBypass": True,
            "noProxyPool": True,
            "persistentArchive": True,
            "searchTimeRange": SEARCH_TIME_RANGE or None,
            "searxngConfigured": bool(SEARXNG_URL),
            "rsshubConfigured": bool(RSSHUB_URL),
            "queries": sorted({status.get("query", "") for status in statuses if status.get("query")}),
        },
        "stats": {
            "itemCount": len(items),
            "newItemCount": new_count,
            "excludedItemCount": len(excluded_items),
            "providerChecks": len(statuses),
            "providerSuccess": len(successful),
            "providerFailure": len(failed),
            "providerSkipped": len(skipped),
            "byProviderType": by_type,
            "byPlatform": by_platform,
        },
        "sources": statuses,
        "itemCount": len(items),
        "items": items,
        "excludedItems": excluded_items,
    }
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(result, ensure_ascii=False, indent=2), "utf-8")
    print(json.dumps({
        "ok": result["ok"],
        "mock": False,
        "itemCount": len(items),
        "newItemCount": new_count,
        "providerSuccess": len(successful),
        "providerFailure": len(failed),
        "generatedAt": collected_at,
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
