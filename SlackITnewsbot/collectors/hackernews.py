"""Hacker News記事の収集（Firebase API・認証不要）"""
from datetime import datetime, timezone

import requests

TOPSTORIES_URL = "https://hacker-news.firebaseio.com/v0/topstories.json"
ITEM_URL = "https://hacker-news.firebaseio.com/v0/item/{item_id}.json"


def fetch(limit: int = 60) -> list[dict]:
    """Hacker Newsのトップ記事ID一覧を取得し、上位limit件について
    記事詳細（タイトル・URL・スコア等）を1件ずつ取得して返す。
    HackerNews API自体には「まとめて取得」する手段が無いため、記事数分だけ
    リクエストが飛ぶ点に注意（=遅くなりやすい）。"""
    try:
        resp = requests.get(TOPSTORIES_URL, timeout=15)
        resp.raise_for_status()
        ids = resp.json()[:limit]
    except Exception as e:  # noqa: BLE001
        print(f"[hackernews] fetch failed: {e}")
        return []

    articles = []
    for item_id in ids:
        try:
            r = requests.get(ITEM_URL.format(item_id=item_id), timeout=10)
            r.raise_for_status()
            item = r.json()
        except Exception:  # noqa: BLE001 - 1件の失敗で全体を止めない
            continue

        # 外部リンクの無い自己投稿（Ask HN等）や、既に削除された記事はスキップする
        if not item or item.get("type") != "story" or not item.get("url"):
            continue

        articles.append(
            {
                "id": f"hn:{item_id}",
                "title": item.get("title", ""),
                "url": item["url"],
                "source": "HackerNews",
                "published_at": datetime.fromtimestamp(
                    item.get("time", 0), tz=timezone.utc
                ).isoformat(),
                "summary_raw": "",  # HackerNewsは要約に使えるスニペットが無い
                "popularity_score": item.get("score", 0),  # scorer.pyでの正規化に使う人気度指標
                "body_text": None,  # 選定後にfetch_body()で埋める
            }
        )
    return articles


def fetch_body(article: dict) -> str:
    """HackerNews自体は本文を持たない（外部サイトへのリンクのみ）ため、
    リンク先URLをtrafilaturaで直接スクレイピングして本文を取得する。"""
    from collectors import rss_sources  # 循環importを避けるため遅延import

    return rss_sources.fetch_body_via_trafilatura(article["url"])
