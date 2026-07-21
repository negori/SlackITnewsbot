"""Publickey / ITmedia / CNET Japan / TechCrunch のRSS収集、
および全collector共通で使う本文取得ヘルパー（trafilatura経由）"""
import feedparser
import trafilatura

import config


def fetch() -> list[dict]:
    articles = []
    for source, url in config.RSS_FEEDS.items():
        try:
            feed = feedparser.parse(url)
        except Exception as e:  # noqa: BLE001
            print(f"[rss_sources] fetch failed for {source}: {e}")
            continue

        if getattr(feed, "bozo", 0) and not feed.entries:
            print(f"[rss_sources] fetch failed for {source} (bozo): {getattr(feed, 'bozo_exception', 'unknown error')}")
            continue

        for entry in feed.entries:
            articles.append(
                {
                    "id": f"{source.lower()}:{entry.get('id', entry.get('link', ''))}",
                    "title": entry.get("title", ""),
                    "url": entry.get("link", ""),
                    "source": source,
                    "published_at": entry.get("published", entry.get("updated", "")),
                    "summary_raw": entry.get("summary", "")[:200],
                    "popularity_score": None,  # 鮮度で代替
                    "body_text": None,
                }
            )
    return articles


def fetch_body_via_trafilatura(url: str) -> str:
    """記事URLから本文を抽出する共通ヘルパー。
    collectors配下・WebSearch由来の記事いずれからも呼ばれる"""
    if not url:
        return ""
    try:
        downloaded = trafilatura.fetch_url(url)
        if not downloaded:
            return ""
        text = trafilatura.extract(downloaded) or ""
        return text[: config.BODY_TRUNCATE_CHARS]
    except Exception as e:  # noqa: BLE001
        print(f"[rss_sources] fetch_body failed for {url}: {e}")
        return ""


def fetch_body(article: dict) -> str:
    return fetch_body_via_trafilatura(article["url"])
