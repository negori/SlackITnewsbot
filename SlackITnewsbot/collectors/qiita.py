"""Qiita記事の収集（GET /api/v2/items）"""
from datetime import datetime, timedelta, timezone

import requests

import config

QIITA_API = "https://qiita.com/api/v2/items"


def _headers() -> dict:
    """アクセストークンがあれば付与する（無くても取得は可能だがレート制限が厳しくなる）"""
    if config.QIITA_ACCESS_TOKEN:
        return {"Authorization": f"Bearer {config.QIITA_ACCESS_TOKEN}"}
    return {}


def fetch(days: int = 7) -> list[dict]:
    """直近days日以内に作成されたQiita記事を取得し、共通フォーマットの辞書リストで返す。
    この時点では本文全体は取らず、一覧APIのレスポンスに含まれる情報だけを使う
    （本文は選定後にfetch_body()で改めて取得する）。

    Qiita APIは「作成日時が新しい順」で返してくるため、そのままだと
    投稿直後でまだいいねが付いていない記事も大量に混ざってしまう。
    そこで取得後にいいね数（likes_count）順で並べ直し、上位
    config.QIITA_TOP_N件だけに絞り込んでから返す。"""
    since = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%d")
    params = {"query": f"created:>{since}", "page": 1, "per_page": 100}

    try:
        resp = requests.get(QIITA_API, headers=_headers(), params=params, timeout=15)
        resp.raise_for_status()
        items = resp.json()
    except Exception as e:  # noqa: BLE001 - 収集失敗はBot全体を止めない
        print(f"[qiita] fetch failed: {e}")
        return []

    articles = []
    for item in items:
        articles.append(
            {
                "id": f"qiita:{item['id']}",
                "title": item.get("title", ""),
                "url": item.get("url", ""),
                "source": "Qiita",
                "published_at": item.get("created_at", ""),
                "summary_raw": (item.get("rendered_body") or "")[:200],  # 未使用だが参考情報として保持
                "popularity_score": item.get("likes_count", 0),  # scorer.pyでの正規化に使う人気度指標
                "body_text": None,  # 選定後にfetch_body()で埋める
            }
        )

    # いいね数の多い順に並べ替えて、上位のみを候補としてscorer.pyに渡す
    articles.sort(key=lambda a: a["popularity_score"], reverse=True)
    return articles[: config.QIITA_TOP_N]


def fetch_body(article: dict) -> str:
    """選定後の記事のみ、詳細APIから本文を取得する"""
    item_id = article["id"].split(":", 1)[1]  # "qiita:xxxx" からxxxx部分だけ取り出す
    try:
        resp = requests.get(f"{QIITA_API}/{item_id}", headers=_headers(), timeout=15)
        resp.raise_for_status()
        body = resp.json().get("body", "")
        return body[: config.BODY_TRUNCATE_CHARS]
    except Exception as e:  # noqa: BLE001
        print(f"[qiita] fetch_body failed for {article.get('url')}: {e}")
        return article.get("summary_raw", "")  # 取得失敗時は一覧取得時のスニペットで代用
