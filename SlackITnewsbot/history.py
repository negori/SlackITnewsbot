"""重複除外用の履歴管理（posted_history.json の読み書き）

「過去に投稿した記事」を記録しておき、次回以降Claudeに選定させる際に
「これらと同じ話題の記事は選ばないでください」という判断材料として渡すためのもの。
古いエントリは自動的に間引かれるので、ファイルが際限なく肥大化することはない。
"""
import json
import os
from datetime import datetime, timedelta, timezone

import config


def _load_raw() -> list[dict]:
    """posted_history.jsonの中身をそのまま（期間による絞り込みなしで）読み込む。
    ファイルが無い・壊れている場合は空リストを返す（＝履歴なし扱い）。"""
    if not os.path.exists(config.POSTED_HISTORY_PATH):
        return []
    try:
        with open(config.POSTED_HISTORY_PATH, encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:  # noqa: BLE001
        print(f"[history] load failed: {e}")
        return []


def load_history(weeks: int = config.HISTORY_WEEKS) -> list[dict]:
    """直近weeks週間以内に投稿された記事だけを返す（重複除外の判定に使う分）。
    それより古いエントリはここでは除外されるが、ファイル自体からはまだ消さない
    （実際にファイルから間引くのはappend_history()のタイミング）。"""
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
    """今回投稿した記事を履歴に追記し、同時に古いエントリ（HISTORY_WEEKS週間より前）を
    間引いてファイルに書き戻す。記事の内容は保存せず、重複判定に必要な
    タイトル・URL・投稿日だけを残す（要約本文はここには保存されない）。"""
    existing = _load_raw()
    today = datetime.now(timezone.utc).date().isoformat()

    for a in articles:
        existing.append({"title": a["title"], "url": a["url"], "posted_at": today})

    # HISTORY_WEEKS週間より古いエントリはここで切り捨てる（ファイルの肥大化防止）
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
