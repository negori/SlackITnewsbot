"""Publickey / ITmedia / CNET Japan / TechCrunch のRSS収集、
および全collector共通で使う本文取得ヘルパー（trafilatura経由）"""
import feedparser
import trafilatura
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
    """config.RSS_FEEDSに登録された各サイトのRSS/Atomフィードを順番に取得し、
    共通フォーマットの辞書リストにまとめて返す。1サイトの取得に失敗しても
    他のサイトの収集は続行する。

    RSSには人気度データが無いため、ソースごとに公開日時が新しい順に並べ替え、
    上位config.RSS_TOP_N件だけを残す（件数の多いフィードがscorer.pyの
    最終候補枠を圧迫しすぎないようにするため）。"""
    articles = []
    for source, url in config.RSS_FEEDS.items():
        try:
            feed = feedparser.parse(url)
        except Exception as e:  # noqa: BLE001
            print(f"[rss_sources] fetch failed for {source}: {e}")
            continue

        # feedparserは致命的なパースエラーでも例外を投げず"bozo"フラグを立てるだけのことがあるため、
        # entriesが空ならエラー扱いにする
        if getattr(feed, "bozo", 0) and not feed.entries:
            print(f"[rss_sources] fetch failed for {source} (bozo): {getattr(feed, 'bozo_exception', 'unknown error')}")
            continue

        source_articles = []
        for entry in feed.entries:
            source_articles.append(
                {
                    "id": f"{source.lower()}:{entry.get('id', entry.get('link', ''))}",
                    "title": entry.get("title", ""),
                    "url": entry.get("link", ""),
                    "source": source,
                    "published_at": entry.get("published", entry.get("updated", "")),
                    "summary_raw": entry.get("summary", "")[:200],
                    "popularity_score": None,  # RSSには人気度指標が無いため、scorer.pyで鮮度により代替
                    "body_text": None,  # 選定後にfetch_body_via_trafilatura()で埋める
                }
            )

        # このソース内で公開日時が新しい順に並べ替えて、上位N件だけ残す
        source_articles.sort(key=_sort_key, reverse=True)
        articles += source_articles[: config.RSS_TOP_N]
    return articles


def fetch_body_via_trafilatura(url: str) -> str:
    """記事URLから本文を抽出する共通ヘルパー。
    collectors配下・WebSearch由来の記事いずれからも呼ばれる。
    trafilaturaはHTMLページから広告・ナビゲーション等を除いた本文だけを
    抽出してくれるライブラリ。ページ取得・抽出どちらかに失敗した場合は
    空文字を返す（要約に本文が使えない記事は、main.py側で要約が空になり
    投稿対象から除外される）。"""
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
