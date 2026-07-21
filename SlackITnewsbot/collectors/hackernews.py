"""Hacker News記事の収集（Firebase API・認証不要）"""
from datetime import datetime, timezone

import requests

TOPSTORIES_URL = "https://hacker-news.firebaseio.com/v0/topstories.json"
ITEM_URL = "https://hacker-news.firebaseio.com/v0/item/{item_id}.json"


def fetch(limit: int = 60) -> list[dict]:
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
                "summary_raw": "",
                "popularity_score": item.get("score", 0),
                "body_text": None,
            }
        )
    return articles


def fetch_body(article: dict) -> str:
    from collectors import rss_sources  # 循環importを避けるため遅延import

    return rss_sources.fetch_body_via_trafilatura(article["url"])
