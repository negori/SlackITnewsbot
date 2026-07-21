"""週次実行の全体フロー（設計書 02_design.md 7節に対応）"""
from collectors import hackernews, qiita, rss_sources, zenn

import claude_client
import config
import cost_tracker
import history
import notifier
import scorer
import slack_poster

_BODY_FETCHERS = {
    "Qiita": qiita.fetch_body,
    "Zenn": zenn.fetch_body,
    "HackerNews": hackernews.fetch_body,
    "Publickey": rss_sources.fetch_body,
    "ITmedia": rss_sources.fetch_body,
    "CNETJapan": rss_sources.fetch_body,
    "TechCrunch": rss_sources.fetch_body,
}


def fetch_body(article: dict) -> str:
    if article.get("body_text"):
        return article["body_text"][: config.BODY_TRUNCATE_CHARS]

    fetcher = _BODY_FETCHERS.get(article.get("source", ""))
    if fetcher:
        return fetcher(article)

    # WebSearch由来などcollector固有のfetcherが無いソースはtrafilaturaで直接取得
    return rss_sources.fetch_body_via_trafilatura(article.get("url", ""))


def find_by_id(candidates: list[dict], article_id: str) -> dict | None:
    for c in candidates:
        if c["id"] == article_id:
            return c
    return None


def main() -> None:
    cost_tracker.reset()

    print("[main] collecting articles from all sources...")
    articles: list[dict] = []
    articles += qiita.fetch()
    articles += zenn.fetch()
    articles += hackernews.fetch()
    articles += rss_sources.fetch()
    print(f"[main] collected {len(articles)} raw articles")

    candidates = scorer.build_candidates(articles, limit=config.CANDIDATE_LIMIT)
    print(f"[main] {len(candidates)} candidates after scoring")

    posted_history = history.load_history()
    print(f"[main] loaded {len(posted_history)} posted_history entries")

    print("[main] screening candidates (Sonnet 5 + web search)...")
    selected = claude_client.screen_candidates(candidates, posted_history)
    print(f"[main] {len(selected)} articles selected")

    enriched = []
    for item in selected:
        article = find_by_id(candidates, item.get("id", "")) or dict(item)
        article["selection_reason"] = item.get("selection_reason", "")
        article["body_text"] = fetch_body(article)
        article["summary"] = claude_client.summarize_article(article)
        if article["summary"]:
            enriched.append(article)
        else:
            print(f"[main] skipping article with empty summary: {article.get('url')}")

    slack_poster.post_digest(enriched)
    history.append_history(enriched)

    weekly_cost = cost_tracker.finalize_and_log()
    print(f"[main] weekly cost (estimated): ${weekly_cost:.4f}")
    notifier.notify_weekly_cost(weekly_cost)

    if cost_tracker.is_new_month():
        monthly_cost = cost_tracker.last_month_total()
        print(f"[main] last month cost (estimated): ${monthly_cost:.4f}")
        notifier.notify_monthly_cost(monthly_cost)

    print("[main] done")


if __name__ == "__main__":
    main()
