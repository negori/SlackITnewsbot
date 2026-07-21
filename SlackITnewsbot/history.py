"""重複除外用の履歴管理（posted_history.json の読み書き）"""
import json
import os
from datetime import datetime, timedelta, timezone

import config


def _load_raw() -> list[dict]:
    if not os.path.exists(config.POSTED_HISTORY_PATH):
        return []
    try:
        with open(config.POSTED_HISTORY_PATH, encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:  # noqa: BLE001
        print(f"[history] load failed: {e}")
        return []


def load_history(weeks: int = config.HISTORY_WEEKS) -> list[dict]:
    cutoff = datetime.now(timezone.utc) - timedelta(weeks=weeks)
    result = []
    for entry in _load_raw():
        try:
            posted_at = datetime.fromisoformat(entry["posted_at"]).replace(tzinfo=timezone.utc)
        except Exception:  # noqa: BLE001
            continue
        if posted_at >= cutoff:
            result.append(entry)
    return result


def append_history(articles: list[dict]) -> None:
    existing = _load_raw()
    today = datetime.now(timezone.utc).date().isoformat()

    for a in articles:
        existing.append({"title": a["title"], "url": a["url"], "posted_at": today})

    cutoff = datetime.now(timezone.utc) - timedelta(weeks=config.HISTORY_WEEKS)
    pruned = []
    for entry in existing:
        try:
            posted_at = datetime.fromisoformat(entry["posted_at"]).replace(tzinfo=timezone.utc)
        except Exception:  # noqa: BLE001
            continue
        if posted_at >= cutoff:
            pruned.append(entry)

    with open(config.POSTED_HISTORY_PATH, "w", encoding="utf-8") as f:
        json.dump(pruned, f, ensure_ascii=False, indent=2)
