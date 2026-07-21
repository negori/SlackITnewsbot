"""APIレスポンスの usage からコストを概算し、cost_log.json に記録する。

Anthropicの管理者用API（Admin API）を使えば正確な請求額が取れるが、
組織Admin権限・別キー管理が必要になるため採用せず、自前概算とする
（詳細は 02_design.md 8節 / 04_operations.md 3節を参照）。
"""
import json
import os
from datetime import datetime, timezone

import config

_run_cost = {"total": 0.0, "web_searches": 0}


def reset() -> None:
    """1回のmain.py実行の先頭で呼ぶ"""
    _run_cost["total"] = 0.0
    _run_cost["web_searches"] = 0


def record(usage: dict, model: str) -> None:
    """claude_client内の各API呼び出し直後に呼ぶ。
    usage は {"input_tokens", "output_tokens", "server_tool_use": {"web_search_requests"}} 形式"""
    pricing = config.PRICING.get(model)
    if not pricing:
        print(f"[cost_tracker] unknown model for pricing: {model}")
        return

    input_tokens = usage.get("input_tokens", 0) or 0
    output_tokens = usage.get("output_tokens", 0) or 0
    cost = (input_tokens / 1_000_000) * pricing["input"] + (output_tokens / 1_000_000) * pricing["output"]

    web_searches = (usage.get("server_tool_use") or {}).get("web_search_requests", 0) or 0
    if web_searches:
        cost += (web_searches / 1000) * config.WEB_SEARCH_PRICE_PER_1000
        _run_cost["web_searches"] += web_searches

    _run_cost["total"] += cost


def _load_log() -> list[dict]:
    if not os.path.exists(config.COST_LOG_PATH):
        return []
    try:
        with open(config.COST_LOG_PATH, encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:  # noqa: BLE001
        print(f"[cost_tracker] load failed: {e}")
        return []


def finalize_and_log() -> float:
    """今回runの合計コストをcost_log.jsonに追記し、その値を返す"""
    log = _load_log()
    today = datetime.now(timezone.utc).date().isoformat()
    entry = {
        "date": today,
        "cost_usd": round(_run_cost["total"], 4),
        "web_searches": _run_cost["web_searches"],
    }
    log.append(entry)
    with open(config.COST_LOG_PATH, "w", encoding="utf-8") as f:
        json.dump(log, f, ensure_ascii=False, indent=2)
    return entry["cost_usd"]


def is_new_month() -> bool:
    log = _load_log()
    if len(log) < 2:
        return False
    return log[-1]["date"][:7] != log[-2]["date"][:7]


def last_month_total() -> float:
    log = _load_log()
    if not log:
        return 0.0
    this_month = log[-1]["date"][:7]
    prev_entries = [e for e in log if e["date"][:7] != this_month]
    if not prev_entries:
        return 0.0
    last_prev_month = prev_entries[-1]["date"][:7]
    total = sum(e["cost_usd"] for e in prev_entries if e["date"][:7] == last_prev_month)
    return round(total, 4)
