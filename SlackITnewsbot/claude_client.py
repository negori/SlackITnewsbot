"""AIによる選定・要約（設計書 02_design.md 4節・8節に対応）"""
import json
import time

from anthropic import Anthropic

import config
import cost_tracker

_client = Anthropic(api_key=config.ANTHROPIC_API_KEY)


def _compress_candidates(candidates: list[dict]) -> str:
    lines = []
    for c in candidates:
        lines.append(
            f"- id={c['id']} | title={c['title']} | source={c['source']} | "
            f"published_at={c.get('published_at', '')} | "
            f"popularity_score={c.get('popularity_score')} | url={c['url']}"
        )
    return "\n".join(lines) if lines else "（候補記事はありません）"


def _compress_history(posted_history: list[dict]) -> str:
    if not posted_history:
        return "（直近投稿済み記事はありません）"
    return "\n".join(f"- {h['title']} ({h['posted_at']})" for h in posted_history)


def _load_prompt(path: str) -> str:
    with open(path, encoding="utf-8") as f:
        return f.read()


def _usage_dict(response) -> dict:
    usage = response.usage
    data = {
        "input_tokens": getattr(usage, "input_tokens", 0),
        "output_tokens": getattr(usage, "output_tokens", 0),
    }
    server_tool_use = getattr(usage, "server_tool_use", None)
    if server_tool_use is not None:
        data["server_tool_use"] = {
            "web_search_requests": getattr(server_tool_use, "web_search_requests", 0)
        }
    return data


def _extract_text(response) -> str:
    # Web検索ツール使用時は「検索前の一言」等の中間textブロックが挟まるため、
    # 最終回答である最後のtextブロックのみを使う（全結合すると前置きとJSONが混ざる）。
    text_blocks = [block.text for block in response.content if block.type == "text"]
    return text_blocks[-1].strip() if text_blocks else ""


def _call_with_retry(max_retries: int = 2, backoffs: tuple[float, ...] = (0.5, 1.0), **kwargs):
    last_err = None
    for attempt in range(max_retries + 1):
        if attempt > 0:
            time.sleep(backoffs[min(attempt - 1, len(backoffs) - 1)])
        try:
            return _client.messages.create(**kwargs)
        except Exception as e:  # noqa: BLE001
            last_err = e
            print(f"[claude_client] API call attempt {attempt} failed: {e}")
    raise last_err


def screen_candidates(candidates: list[dict], posted_history: list[dict]) -> list[dict]:
    template = _load_prompt("prompts/screen_candidates.txt")
    prompt = template.replace("{{CANDIDATES}}", _compress_candidates(candidates))
    prompt = prompt.replace("{{POSTED_HISTORY}}", _compress_history(posted_history))

    kwargs = dict(
        model=config.MODEL_SCREENING,
        max_tokens=4000,
        tools=[{"type": config.WEB_SEARCH_TOOL_TYPE, "name": "web_search"}],
        messages=[{"role": "user", "content": prompt}],
    )

    # JSONパース失敗時は1回だけ再試行する（設計書 4.3節）
    for json_attempt in range(2):
        try:
            response = _call_with_retry(**kwargs)
        except Exception as e:  # noqa: BLE001
            print(f"[claude_client] screen_candidates failed entirely: {e}")
            return []

        cost_tracker.record(_usage_dict(response), config.MODEL_SCREENING)
        raw_text = _extract_text(response)

        try:
            parsed = json.loads(raw_text)
            return parsed.get("selected", [])[: config.MAX_SELECTED]
        except json.JSONDecodeError:
            print(
                f"[claude_client] screen_candidates JSON parse failed "
                f"(attempt {json_attempt}). raw={raw_text[:300]!r}"
            )

    print("[claude_client] screen_candidates JSON parse failed twice, skipping selection this run")
    return []


def summarize_article(article: dict) -> str:
    template = _load_prompt("prompts/summarize_article.txt")
    prompt = template.replace("{{TITLE}}", article.get("title", ""))
    prompt = prompt.replace("{{BODY}}", (article.get("body_text") or "")[: config.BODY_TRUNCATE_CHARS])

    try:
        response = _call_with_retry(
            model=config.MODEL_SUMMARY,
            max_tokens=1000,
            messages=[{"role": "user", "content": prompt}],
        )
    except Exception as e:  # noqa: BLE001 - 1本の失敗が他の記事に波及しないようにする
        print(f"[claude_client] summarize_article failed for {article.get('url')}: {e}")
        return ""

    cost_tracker.record(_usage_dict(response), config.MODEL_SUMMARY)
    return _extract_text(response)
