"""Zenn記事の収集（公式APIが無いため feed を使用）"""
import feedparser
from dateutil import parser as date_parser

import config


def _sort_key(article: dict):
    """公開日時でソートするためのキー関数。パース失敗時は最も古い扱いにして
    末尾に回す（後段のtop-N切り詰めで真っ先に落ちるようにする）。"""
    try:
        return date_parser.parse(article["published_at"])
    except (ValueError, TypeError, KeyError):
        return date_parser.parse("1970-01-01T00:00:00Z")


def fetch() -> list[dict]:
    """ZennのRSSフィードから最近の記事一覧を取得し、共通フォーマットの辞書リストで返す。

    Zennには人気度データが無いため、公開日時が新しい順に並べ替えて
    上位config.ZENN_TOP_N件だけを残す（RSS系ソースと同様の絞り込み）。"""
    try:
        feed = feedparser.parse(config.ZENN_FEED)
    except Exception as e:  # noqa: BLE001
        print(f"[zenn] fetch failed: {e}")
        return []

    # feedparserは致命的なパースエラーでも例外を投げず"bozo"フラグを立てるだけのことがあるため、
    # entriesが空ならエラー扱いにする
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
                "body_text": entry.get("summary") or None,  # フィードのsummaryをそのまま本文代わりに使う
            }
        )

    articles.sort(key=_sort_key, reverse=True)
    return articles[: config.ZENN_TOP_N]


def fetch_body(article: dict) -> str:
    """フィード由来のsummaryがあればそれを使い、無ければURLから直接スクレイピングする。"""
    if article.get("body_text"):
        return article["body_text"][: config.BODY_TRUNCATE_CHARS]
    from collectors import rss_sources  # 循環importを避けるため遅延import

    return rss_sources.fetch_body_via_trafilatura(article["url"])
