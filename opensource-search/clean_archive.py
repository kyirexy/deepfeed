from __future__ import annotations

import json
import os
from pathlib import Path

CONFIG = Path(os.environ.get("DEEPFEED_CONFIG", "opensource-search/config.json"))
OUTPUT = Path(os.environ.get("DEEPFEED_OUTPUT", "data/live.json"))


def strict_legacy_relevant(item: dict, project: dict, policy: dict) -> bool:
    main_min = int(policy.get("mainMinScore", 65))
    if item.get("relevanceMethod") == "entity_fingerprint_v3":
        return item.get("relevanceBucket") == "main" and int(item.get("relevanceScore") or 0) >= main_min

    title = str(item.get("title") or "").lower()
    description = str(item.get("description") or item.get("snippet") or "").lower()
    text = f"{title} {description}"
    excludes = [str(x).lower() for x in project.get("excludeKeywords", [])]
    if any(term and term in text for term in excludes):
        return False

    aliases = [str(x).lower() for x in project.get("aliases", [])]
    anchors = [str(x).lower() for x in project.get("strongAnchors", [])]
    has_anchor = any(term and term in text for term in anchors)
    for alias in aliases:
        pos = title.find(alias)
        if pos < 0:
            continue
        if pos <= 8:
            return True
        if pos <= 28 and has_anchor:
            return True
    return False


def main() -> int:
    config = json.loads(CONFIG.read_text("utf-8"))
    payload = json.loads(OUTPUT.read_text("utf-8"))
    project = config["project"]
    policy = config.get("search", {}).get("policy", {})
    before = list(payload.get("items", []))
    kept = []
    removed = []
    for item in before:
        is_search = (
            item.get("provider") == "SearXNG"
            or item.get("accessLevel") in {"search_discovered", "搜索发现"}
            or item.get("mode") in {"开源搜索发现", "搜索发现", "精确实体搜索发现"}
        )
        if not is_search or strict_legacy_relevant(item, project, policy):
            kept.append(item)
        else:
            removed.append(item)

    payload["items"] = kept
    payload["itemCount"] = len(kept)
    payload["cleanedLegacyNoiseThisRun"] = len(removed)
    payload["relevancePolicy"] = "balanced_recall_v4"
    payload.setdefault("collection", {})["relevancePolicy"] = "balanced_recall_v4"
    payload.setdefault("collection", {})["poolPolicy"] = "main + discovery + excluded"
    OUTPUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2), "utf-8")
    print(json.dumps({
        "before": len(before),
        "after": len(kept),
        "removedLegacyNoise": len(removed),
        "mainMinScore": int(policy.get("mainMinScore", 65)),
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
