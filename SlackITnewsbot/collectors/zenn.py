"""Zenn記事の収集（公式APIが無いため feed を使用）"""
import feedparser

import config


def fetch() -> list[dict]:
    try:
        feed = feedparser.parse(config.ZENN_FEED)
    except Exception as e:  # noqa: BLE001
        print(f"[zenn] fetch failed: {e}")
        return []

    if getattr(feed, "bozo", 0) and not feed.entries:
        print(f"[zenn] fetch failed (bozo): {getattr(feed, 'bozo_exception', 'unknown error')}")
        return []

    articles = []
    for entry in feed.entries:
        articles.append(
            {
                "id": f"zenn:{entry.get('id', entry.get('link', ''))}",
                "title": entry.get("title", ""),
                "url": entry.get("link", ""),
                "source": "Zenn",
                "published_at": entry.get("published", entry.get("updated", "")),
                "summary_raw": entry.get("summary", "")[:200],
                "popularity_score": None,  # 取得不可。scorer側で鮮度により代替
                "body_text": entry.get("summary") or None,
            }
        )
    return articles


def fetch_body(article: dict) -> str:
    if article.get("body_text"):
        return article["body_text"][: config.BODY_TRUNCATE_CHARS]
    from collectors import rss_sources  # 循環importを避けるため遅延import

    return rss_sources.fetch_body_via_trafilatura(article["url"])
