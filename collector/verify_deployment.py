#!/usr/bin/env python3
"""Verify the deployed Live Beta from an independent GitHub Actions runner."""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

BASE = "https://tide-minigame-opinion-live-kyirexys-projects.vercel.app"
OUT = Path(__file__).resolve().parents[1] / "data" / "deployment-health.json"
UA = "DeepFeedDeploymentVerifier/0.1"


def get(path: str) -> tuple[int, str, str]:
    request = urllib.request.Request(BASE + path, headers={"User-Agent": UA, "Accept": "*/*"})
    with urllib.request.urlopen(request, timeout=30) as response:
        body = response.read(2_000_000).decode("utf-8", errors="replace")
        return response.status, response.headers.get("content-type", ""), body


def main() -> None:
    attempts = []
    verified = False
    result = {}
    for attempt in range(1, 7):
        try:
            home_status, home_type, home = get("/")
            health_status, health_type, health_body = get("/api/health")
            live_status, live_type, live_body = get("/api/live")
            health = json.loads(health_body)
            live = json.loads(live_body)
            checks = {
                "homeStatus200": home_status == 200,
                "homeContainsBrand": "潮汐" in home and "theme-toggle" in home,
                "healthStatus200": health_status == 200,
                "healthNotMock": health.get("mock") is False,
                "liveStatus200": live_status == 200,
                "liveNotMock": live.get("mock") is False,
                "liveHasCollectionMeta": isinstance(live.get("collection"), dict),
            }
            result = {
                "baseUrl": BASE,
                "checkedAt": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
                "verified": all(checks.values()),
                "checks": checks,
                "home": {"status": home_status, "contentType": home_type, "bytes": len(home.encode("utf-8"))},
                "health": health,
                "live": {
                    "ok": live.get("ok"),
                    "mock": live.get("mock"),
                    "generatedAt": live.get("generatedAt"),
                    "itemCount": live.get("itemCount"),
                    "sourceCount": len(live.get("sources") or []),
                },
            }
            verified = result["verified"]
            attempts.append({"attempt": attempt, "ok": verified})
            if verified:
                break
        except Exception as exc:
            attempts.append({"attempt": attempt, "ok": False, "error": f"{type(exc).__name__}: {exc}"[:400]})
        time.sleep(20)

    if not result:
        result = {
            "baseUrl": BASE,
            "checkedAt": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "verified": False,
        }
    result["attempts"] = attempts
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if not verified:
        raise SystemExit("Deployment verification failed")


if __name__ == "__main__":
    main()
