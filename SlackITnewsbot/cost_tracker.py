"""APIレスポンスの usage からコストを概算し、cost_log.json に記録する。

Anthropicの管理者用API（Admin API）を使えば正確な請求額が取れるが、
組織Admin権限・別キー管理が必要になるため採用せず、自前概算とする
（詳細は 02_design.md 8節 / 04_operations.md 3節を参照）。
"""
import json
import os
from datetime import datetime, timezone

import config

# 今回のmain.py実行1回分のコストを溜めていくための変数（モジュールレベルの状態）。
# 実行のたびにreset()で0に戻し、claude_client内のAPI呼び出しのたびrecord()で加算していく。
_run_cost = {"total": 0.0, "web_searches": 0}


def reset() -> None:
    """1回のmain.py実行の先頭で呼ぶ"""
    _run_cost["total"] = 0.0
    _run_cost["web_searches"] = 0


def record(usage: dict, model: str) -> None:
    """claude_client内の各API呼び出し直後に呼ぶ。
    usage は {"input_tokens", "output_tokens", "server_tool_use": {"web_search_requests"}} 形式

    トークン数×単価（config.PRICINGで定義）とWeb検索回数×単価から
    その1回のAPI呼び出しのコストを概算し、_run_costに積み上げていく。
    Anthropic公式の請求額とは誤差が生じうる自前の概算であることに注意
    （正確な金額はAnthropic Consoleで確認する）。"""
    pricing = config.PRICING.get(model)
    if not pricing:
        print(f"[cost_tracker] unknown model for pricing: {model}")
        return

    input_tokens = usage.get("input_tokens", 0) or 0
    output_tokens = usage.get("output_tokens", 0) or 0
    # 単価はconfig.PRICINGに「$ / 1Mトークン」で定義されているので、100万で割ってから掛ける
    cost = (input_tokens / 1_000_000) * pricing["input"] + (output_tokens / 1_000_000) * pricing["output"]

    # Web検索を使った場合は、検索1回ごとの追加課金分も加算する
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
    """直近2回分の実行日を比較し、月をまたいでいたらTrue。
    月初の最初の実行時にだけ、前月分の合計コストを追加でDM通知するために使う。
    "date"[:7]で"2026-08"のように年月部分だけを取り出して比較している。"""
    log = _load_log()
    if len(log) < 2:
        return False
    return log[-1]["date"][:7] != log[-2]["date"][:7]


def last_month_total() -> float:
    """ログの中から「直近の月ではない、最後の月」のエントリを集計して合計する
    （＝先月分の合計コスト）。"""
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
