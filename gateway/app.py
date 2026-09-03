from __future__ import annotations

import json
import os
import pathlib
import time
import urllib.parse
from typing import Any

import httpx
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse

ROOT = pathlib.Path(__file__).resolve().parents[1]
SITE_FILE = pathlib.Path(os.getenv("TIDE_SITE_FILE", ROOT / "index.html"))
DATA_FILE = pathlib.Path(os.getenv("TIDE_DATA_FILE", ROOT / "data" / "live.json"))
CONFIG_FILE = pathlib.Path(os.getenv("TIDE_TARGETS_CONFIG", ROOT / "config" / "targets.json"))
SEARXNG_URL = os.getenv("SEARXNG_URL", "http://searxng:8080").rstrip("/")
RSSHUB_URL = os.getenv("RSSHUB_URL", "http://rsshub:1200").rstrip("/")

app = FastAPI(title="Tide Public Opinion Gateway", version="0.3.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[origin.strip() for origin in os.getenv("CORS_ORIGINS", "*").split(",")],
    allow_credentials=False,
    allow_methods=["GET"],
    allow_headers=["*"],
)


def load_config() -> dict[str, Any]:
    return json.loads(CONFIG_FILE.read_text("utf-8"))


def platform_spec(platform: str) -> dict[str, Any] | None:
    return load_config().get("platforms", {}).get(platform)


def infer_platform(url: str, config: dict[str, Any]) -> tuple[str, str]:
    host = urllib.parse.urlsplit(url).netloc.lower().removeprefix("www.")
    for key, spec in config.get("platforms", {}).items():
        for domain in spec.get("domains", []):
            domain = domain.lower().removeprefix("www.")
            if host == domain or host.endswith("." + domain):
                return key, spec.get("label", key)
    return "web", "网页"


@app.get("/")
async def index() -> FileResponse:
    if not SITE_FILE.exists():
        raise HTTPException(status_code=404, detail="index.html is missing")
    return FileResponse(SITE_FILE)


@app.get("/api/live")
async def live() -> JSONResponse:
    if not DATA_FILE.exists():
        raise HTTPException(status_code=404, detail="live archive is missing")
    payload = json.loads(DATA_FILE.read_text("utf-8"))
    if payload.get("mock") is not False:
        raise HTTPException(status_code=409, detail="archive failed mock:false validation")
    return JSONResponse(payload, headers={"Cache-Control": "no-store"})


@app.get("/api/search")
async def search(
    q: str = Query(min_length=2, max_length=120),
    platform: str = Query(default="all", max_length=30),
    time_range: str = Query(default="month", pattern="^(day|month|year|all)$"),
    limit: int = Query(default=20, ge=1, le=50),
) -> JSONResponse:
    config = load_config()
    query = q.strip()
    if platform != "all":
        spec = config.get("platforms", {}).get(platform)
        if not spec:
            raise HTTPException(status_code=400, detail="unsupported platform")
        site_query = spec.get("siteQuery", "").strip()
        if site_query:
            query = f'"{query}" {site_query}'

    params = {
        "q": query,
        "format": "json",
        "categories": "general,news",
        "language": "zh-CN",
        "safesearch": "0",
        "pageno": "1",
    }
    if time_range != "all":
        params["time_range"] = time_range

    started = time.perf_counter()
    try:
        async with httpx.AsyncClient(timeout=20, follow_redirects=True) as client:
            response = await client.get(f"{SEARXNG_URL}/search", params=params)
            response.raise_for_status()
            payload = response.json()
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=f"SearXNG unavailable: {exc}") from exc
    except ValueError as exc:
        raise HTTPException(status_code=502, detail="SearXNG returned invalid JSON") from exc

    results = []
    for row in (payload.get("results") or [])[:limit]:
        url = str(row.get("url") or "")
        if not url:
            continue
        platform_key, platform_label = infer_platform(url, config)
        if platform != "all" and platform_key != platform:
            continue
        engines = row.get("engines") or []
        if isinstance(engines, str):
            engines = [engines]
        results.append({
            "title": row.get("title") or "无标题",
            "url": url,
            "snippet": row.get("content") or "",
            "publishedAt": row.get("publishedDate") or row.get("published_date"),
            "engine": row.get("engine"),
            "engines": engines,
            "score": row.get("score"),
            "platform": platform_key,
            "platformLabel": platform_label,
            "accessLevel": "搜索发现",
        })

    return JSONResponse(
        {
            "ok": True,
            "mock": False,
            "provider": "SearXNG",
            "query": query,
            "platform": platform,
            "timeRange": time_range,
            "resultCount": len(results),
            "elapsedMs": round((time.perf_counter() - started) * 1000),
            "results": results,
        },
        headers={"Cache-Control": "no-store"},
    )


async def check_url(url: str) -> dict[str, Any]:
    started = time.perf_counter()
    try:
        async with httpx.AsyncClient(timeout=6, follow_redirects=True) as client:
            response = await client.get(url)
        return {
            "ok": response.status_code < 500,
            "status": response.status_code,
            "elapsedMs": round((time.perf_counter() - started) * 1000),
        }
    except Exception as exc:
        return {
            "ok": False,
            "status": None,
            "elapsedMs": round((time.perf_counter() - started) * 1000),
            "error": f"{type(exc).__name__}: {exc}",
        }


@app.get("/api/health")
async def health() -> JSONResponse:
    searxng = await check_url(f"{SEARXNG_URL}/")
    rsshub = await check_url(f"{RSSHUB_URL}/healthz")
    return JSONResponse({
        "ok": bool(searxng.get("ok")) and bool(rsshub.get("ok")),
        "mock": False,
        "services": {"searxng": searxng, "rsshub": rsshub},
    })


@app.get("/healthz")
async def healthz() -> dict[str, Any]:
    return {"ok": True, "mock": False}
