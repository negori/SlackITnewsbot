"""AIによる選定・要約（設計書 02_design.md 4節・8節に対応）

このファイルには2つの主要な関数がある。
  - screen_candidates(): 候補記事一覧をSonnet 5に渡し、Web検索も使わせながら
    「今週Slackに載せる記事」をJSON形式で選んでもらう
  - summarize_article(): 選ばれた記事1本ずつをHaiku 4.5に渡し、日本語の要約文を作ってもらう

どちらも「プロンプトのテンプレートファイルを読み込み、変数部分を実データで置換して送る」
という同じパターンで動いている。
"""
import json
import time

from anthropic import Anthropic

import config
import cost_tracker

# Anthropic APIクライアント（モジュール読み込み時に一度だけ作成し使い回す）
_client = Anthropic(api_key=config.ANTHROPIC_API_KEY)


def _compress_candidates(candidates: list[dict]) -> str:
    """候補記事のリストを、プロンプトに埋め込むための簡潔なテキスト（箇条書き）に変換する。
    記事オブジェクトのJSONをそのまま渡すよりトークン数を節約できる。"""
    lines = []
    for c in candidates:
        lines.append(
            f"- id={c['id']} | title={c['title']} | source={c['source']} | "
            f"published_at={c.get('published_at', '')} | "
            f"popularity_score={c.get('popularity_score')} | url={c['url']}"
        )
    return "\n".join(lines) if lines else "（候補記事はありません）"


def _compress_history(posted_history: list[dict]) -> str:
    """直近投稿済みの記事一覧を、重複除外の判断材料としてプロンプトに埋め込むための
    簡潔なテキストに変換する。"""
    if not posted_history:
        return "（直近投稿済み記事はありません）"
    return "\n".join(f"- {h['title']} ({h['posted_at']})" for h in posted_history)


def _load_prompt(path: str) -> str:
    """prompts/配下のプロンプトテンプレートファイル（.txt）を読み込む"""
    with open(path, encoding="utf-8") as f:
        return f.read()


def _usage_dict(response) -> dict:
    """APIレスポンスからトークン使用量・Web検索回数を取り出し、
    cost_tracker.record()に渡せる形の辞書にまとめる。"""
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
    """Anthropic APIを呼び出し、失敗した場合は少し待ってから最大max_retries回まで
    再試行する（一時的なネットワークエラー・レート制限対策）。
    全て失敗した場合は最後に発生した例外をそのまま呼び出し元に投げる。"""
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
    """候補記事の中からSonnet 5に「今週載せる記事」を選ばせる。

    プロンプト内でWeb検索ツールの使用も許可しており、候補記事リストに
    無い記事（金融・製造業DX、海外AI企業動向など）も自分で検索して
    追加候補にできる。戻り値は選定結果のJSON（[{"id", "selection_reason", ...}, ...]）
    をパースしたリスト。
    """
    template = _load_prompt("prompts/screen_candidates.txt")
    # プロンプトテンプレート内の {{CANDIDATES}} {{POSTED_HISTORY}} を実データに置換する
    prompt = template.replace("{{CANDIDATES}}", _compress_candidates(candidates))
    prompt = prompt.replace("{{POSTED_HISTORY}}", _compress_history(posted_history))

    kwargs = dict(
        model=config.MODEL_SCREENING,
        max_tokens=4000,
        tools=[{"type": config.WEB_SEARCH_TOOL_TYPE, "name": "web_search"}],
        messages=[{"role": "user", "content": prompt}],
    )

    # Claudeの応答は「JSONのみを返す」よう指示しているが、まれに前置き文や
    # コードブロック記法が混ざって json.loads() が失敗することがあるため、
    # 失敗した場合は1回だけAPIを呼び直す（設計書 4.3節）。
    for json_attempt in range(2):
        try:
            response = _call_with_retry(**kwargs)
        except Exception as e:  # noqa: BLE001
            print(f"[claude_client] screen_candidates failed entirely: {e}")
            return []

        # API呼び出し自体は成功したので、かかったコストを記録する
        cost_tracker.record(_usage_dict(response), config.MODEL_SCREENING)
        raw_text = _extract_text(response)

        try:
            parsed = json.loads(raw_text)
            # MAX_SELECTED件を超えて返ってきた場合に備えて念のため切り詰める
            return parsed.get("selected", [])[: config.MAX_SELECTED]
        except json.JSONDecodeError:
            print(
                f"[claude_client] screen_candidates JSON parse failed "
                f"(attempt {json_attempt}). raw={raw_text[:300]!r}"
            )

    # 2回ともJSONパースに失敗した場合は、今回は選定なし（0件）として諦める
    print("[claude_client] screen_candidates JSON parse failed twice, skipping selection this run")
    return []


def summarize_article(article: dict) -> str:
    """記事1本のタイトル・本文をHaiku 4.5に渡し、日本語の要約文を生成する。
    失敗した場合は空文字を返す（呼び出し元のmain.pyで、要約が空の記事は
    投稿対象から除外される）。"""
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
