"""週次実行の全体フロー（設計書 02_design.md 7節に対応）

処理の流れ（上から順に実行される）:
  1. 各情報源（Qiita/Zenn/HackerNews/RSS）から記事を集める
  2. scorer.pyで人気度・鮮度に基づいてスコアリングし、上位N件に絞る（候補記事）
  3. Claude(Sonnet 5)に候補記事を渡し、Web検索も使いながら実際に載せる記事を選定してもらう
  4. 選ばれた記事ごとに本文を取得し、Claude(Haiku 4.5)で要約を作る
  5. 出来上がったダイジェストをSlackに投稿する
  6. 投稿が成功した記事だけ「投稿済み」として履歴に記録する（重複防止用）
  7. 今回の実行でかかったAPI利用料（概算）を記録し、管理者にDM通知する
"""
from collectors import hackernews, qiita, rss_sources, zenn

import claude_client
import config
import cost_tracker
import history
import notifier
import scorer
import slack_poster

# 記事の出典（source）ごとに、本文取得用の関数を出し分けるための対応表。
# 出典によって本文取得のAPI・方法が違う（Qiita/Zenn/HackerNews専用API vs RSS収集先はtrafilaturaでスクレイピング）ため、
# ここで一元管理している。
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
    """選定された記事1件について、要約に使う本文テキストを取得する。

    - すでに本文（body_text）を持っている場合はそれを使う（Zennのfeedにはあらかじめ本文相当が入っているため）
    - 出典に対応する取得関数があれば、それを使ってAPI/スクレイピングで本文を取ってくる
    - どちらにも当てはまらない場合（Claudeが自分のWeb検索で新たに見つけてきた記事など）は、
      URLから直接本文をスクレイピングするtrafilaturaにフォールバックする
    """
    if article.get("body_text"):
        return article["body_text"][: config.BODY_TRUNCATE_CHARS]

    fetcher = _BODY_FETCHERS.get(article.get("source", ""))
    if fetcher:
        return fetcher(article)

    # WebSearch由来などcollector固有のfetcherが無いソースはtrafilaturaで直接取得
    return rss_sources.fetch_body_via_trafilatura(article.get("url", ""))


def find_by_id(candidates: list[dict], article_id: str) -> dict | None:
    """Claudeが選定結果として返してきたid（例: "qiita:xxxx"）から、
    元の候補記事リスト（タイトル・URL・出典などの情報を持つ）を逆引きする。
    Claudeの選定結果自体には最小限の情報（id・選定理由）しか入っていないため、
    元の詳細情報とマージするのに使う。"""
    for c in candidates:
        if c["id"] == article_id:
            return c
    return None


def main() -> None:
    # 今回の実行分のコスト集計をリセット（前回実行分の値を引き継がないようにする）
    cost_tracker.reset()

    # --- 1. 記事収集: 4つの情報源から記事を集めてくる ---
    print("[main] collecting articles from all sources...")
    articles: list[dict] = []
    articles += qiita.fetch()
    articles += zenn.fetch()
    articles += hackernews.fetch()
    articles += rss_sources.fetch()
    print(f"[main] collected {len(articles)} raw articles")

    # --- 2. スコアリング: 人気度・鮮度で並び替え、上位CANDIDATE_LIMIT件に絞る ---
    candidates = scorer.build_candidates(articles, limit=config.CANDIDATE_LIMIT)
    print(f"[main] {len(candidates)} candidates after scoring")

    # 直近数週間に投稿済みの記事一覧（重複除外のためClaudeに渡す）
    posted_history = history.load_history()
    print(f"[main] loaded {len(posted_history)} posted_history entries")

    # --- 3. 選定: Claude(Sonnet 5)にWeb検索させながら実際に載せる記事を選んでもらう ---
    # SKIP_CLAUDE=1の場合はテストモードなので、Claudeを呼ばずに候補の上位を機械的に採用する
    # （GitHub Actionsのcron・Slack投稿部分の動作確認用。詳しくはconfig.pyのコメント参照）
    if config.SKIP_CLAUDE:
        print("[main] SKIP_CLAUDE enabled: skipping Claude API selection/summarization")
        selected = candidates[: config.MIN_SELECTED]
    else:
        print("[main] screening candidates (Sonnet 5 + web search)...")
        selected = claude_client.screen_candidates(candidates, posted_history)
    print(f"[main] {len(selected)} articles selected")

    # --- 4. 要約: 選ばれた記事1件ずつ、本文取得→Claude(Haiku 4.5)で要約 ---
    enriched = []
    for item in selected:
        # Claudeの選定結果（id・選定理由のみ）に、元の候補記事情報（タイトル・URL等）をマージする
        article = find_by_id(candidates, item.get("id", "")) or dict(item)
        article["selection_reason"] = item.get("selection_reason", "")
        article["body_text"] = fetch_body(article)
        if config.SKIP_CLAUDE:
            article["summary"] = "[テスト投稿] Claude APIを使わずに生成したダミー要約です。"
        else:
            article["summary"] = claude_client.summarize_article(article)
        # 要約が空（＝Claude呼び出し失敗など）の記事は投稿対象から除外する
        if article["summary"]:
            enriched.append(article)
        else:
            print(f"[main] skipping article with empty summary: {article.get('url')}")

    # --- 5. Slackへ投稿 ---
    # post_digestは投稿に成功した場合のみTrueを返す（DRY_RUN中や投稿APIエラー時はFalse）。
    # 実際に投稿できた場合だけ「投稿済み」として履歴に残す（投稿失敗した記事まで
    # 重複除外の対象にしてしまうと、次回以降本来出すべき記事が出なくなってしまうため）。
    posted = slack_poster.post_digest(enriched)
    if posted:
        history.append_history(enriched)

    # --- 6. コスト集計・通知 ---
    weekly_cost = cost_tracker.finalize_and_log()
    print(f"[main] weekly cost (estimated): ${weekly_cost:.4f}")
    notifier.notify_weekly_cost(weekly_cost)

    # 月が変わった直後の実行では、前月分の合計コストも併せて通知する
    if cost_tracker.is_new_month():
        monthly_cost = cost_tracker.last_month_total()
        print(f"[main] last month cost (estimated): ${monthly_cost:.4f}")
        notifier.notify_monthly_cost(monthly_cost)

    print("[main] done")


if __name__ == "__main__":
    main()
