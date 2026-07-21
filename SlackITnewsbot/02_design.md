# Slack ITニュース要約Bot 設計書

作成日: 2026年7月13日
関連: [企画書](01_proposal.md) / [実装書](03_implementation.md)

---

## 1. ディレクトリ構成

```
repo-root/
├── .github/workflows/
│   ├── weekly_digest.yml
│   └── key_rotation_reminder.yml   # 半年ごとのキーローテーション通知（独立スケジュール）
├── main.py
├── requirements.txt
├── config.py
├── collectors/
│   ├── __init__.py
│   ├── qiita.py
│   ├── zenn.py
│   ├── hackernews.py
│   └── rss_sources.py
├── scorer.py
├── claude_client.py
├── slack_poster.py
├── history.py
├── notifier.py               # 特定ユーザーへのSlack DM送信
├── cost_tracker.py           # usage情報からのコスト概算・記録
├── posted_history.json       # 直近投稿済み記事の履歴（重複除外用）
├── cost_log.json             # 週ごとのコスト概算ログ（月次集計の元データ）
├── key_rotation.json         # 最終ローテーション日の記録
└── prompts/
    ├── screen_candidates.txt
    └── summarize_article.txt
```

- Web検索そのものは独立したcollectorではなく、`claude_client.screen_candidates()`内でSonnet 5にWeb検索ツールを持たせて実行する（モデルが自律的にクエリを組み立てて検索する）。検索で見つかった記事は他のcollector結果と同じArticle dict形式に正規化し、`source: "WebSearch"`として候補に混ぜる
- `history.py` は `posted_history.json` の読み書きを担当する薄いモジュール（直近数週間分のタイトル・URL・投稿日を保持し、選定時にSonnetへ渡して重複除外の判断材料にする）

---

## 2. データモデル（共通フォーマット）

各collectorは以下の辞書のリストを返す。ソース差異はここで吸収する。

```python
# Article dict
{
    "id": "qiita:1234567890abcdef",   # ソース名:一意ID
    "title": "記事タイトル",
    "url": "https://...",
    "source": "Qiita",                 # Qiita/Zenn/HackerNews/Publickey/ITmedia/CNETJapan/TechCrunch/WebSearch
    "published_at": "2026-07-10T09:00:00+09:00",  # ISO8601
    "summary_raw": "記事概要やRSSのdescription（あれば）",
    "popularity_score": 42,            # ソースごとの生スコア(いいね数/HN score等)。無ければ None
    "body_text": None,                 # 本文取得は候補選定後に遅延実行（後述）
}
```

- `body_text` は全候補に対して取得すると無駄にコストがかかるため、選定後の記事のみ取得する（`collectors`側に `fetch_body(article) -> str` を用意し、`trafilatura.fetch_url` / `trafilatura.extract` を使う。Qiita/Zennは本文API/フィードのcontentをそのまま使う）

### 2.1 collectors/qiita.py

- エンドポイント: `GET https://qiita.com/api/v2/items?query=created:>YYYY-MM-DD&page=1&per_page=100`
- `popularity_score` = `likes_count`
- 認証: `Authorization: Bearer {QIITA_ACCESS_TOKEN}`（設定時のみ付与）

### 2.2 collectors/zenn.py

- Zennは公式APIが無いため、フィード（`https://zenn.dev/feed`）を`feedparser`で取得（疎通確認済み、HTTP 200）
- `popularity_score` は取得できないため `None`。スコアリング側では鮮度（公開日時が直近3日以内か）で代替

### 2.3 collectors/hackernews.py

- Firebase API: `GET https://hacker-news.firebaseio.com/v0/topstories.json` → 上位N件の `GET /v0/item/{id}.json`
- `popularity_score` = `score`
- レート制限なし・認証不要

### 2.4 collectors/rss_sources.py

- Publickey / ITmedia NEWS / CNET Japan / TechCrunch（英語）の各RSSを `feedparser` で取得
- `popularity_score` は `None`（鮮度のみで判定）
- URL一覧は `config.py` の `RSS_FEEDS` にリストで定義し、増減を容易にする
- TechCrunchは英語記事のため、`summarize_article()` が日本語で要約を生成する前提とする（プロンプト側で「英語記事でも日本語で要約する」旨を明記する）

```python
RSS_FEEDS = {
    "Publickey": "https://www.publickey1.jp/atom.xml",
    "ITmedia": "https://rss.itmedia.co.jp/rss/2.0/news_bursts.xml",
    "CNETJapan": "https://japan.cnet.com/rss/index.rdf",
    "TechCrunch": "https://techcrunch.com/feed/",
}
```

※ 上記URLは設計時（2026年7月13日）に疎通確認済み（すべてHTTP 200）。TechCrunch Japan（`jp.techcrunch.com`）は日本事業終了の影響でフィードのレスポンスが不安定だったため除外し、代わりにCNET JapanとTechCrunch本体（英語）を採用（詳細経緯は[企画書](01_proposal.md)参照）。

---

## 3. scorer.py（候補抽出ロジック）

1. 全ソースから直近7日以内に公開された記事を集約（Web検索で見つかった記事はこの時点では含まれず、Sonnetの選定ステップ内で動的に追加される）
2. `popularity_score` があるソースは、ソースごとに正規化（0〜1にmin-max scaling）
3. `popularity_score` が無いソース（RSS各種・`WebSearch`も同様）は、公開日時の新しさで代替スコア（直近ほど高スコア）を付与
4. 正規化スコア順に上位30〜50件を候補として抽出
5. 抽出結果を `list[Article]` として選定処理に渡す

```python
def build_candidates(articles: list[dict], limit: int = 50) -> list[dict]:
    ...
```

---

## 4. claude_client.py（AIによる選定・要約の設計）

### 4.1 screen_candidates()（高性能モデル・Web検索ツール有効）

```python
def screen_candidates(candidates: list[dict], posted_history: list[dict]) -> list[dict]:
    """
    candidates: build_candidates()の出力
    posted_history: history.load_history()の出力（直近数週間分の投稿済み記事）
    return: 選定された記事のリスト（最低3件、目安上限10件）
            候補外でWeb検索から新規に見つかった記事は、
            同じArticle dict形式（source="WebSearch"）で結果に混ぜて返す
            各要素に "selection_reason": str を付加
    """
```

- モデル: `claude-sonnet-5`
- ツール: `web_search`（`tools=[{"type": "web_search_20260209"}]` を付与。ドメインのallowlist/blocklistは指定しない運用が基本。低品質サイトが混入した場合のみ`blocked_domains`で個別追加）
- 入力:
  - 候補記事の `title / source / published_at / popularity_score / url` を圧縮したテキストリスト（本文は渡さない）
  - `posted_history`（直近投稿済み記事のタイトル・URL・投稿日。重複判定の材料として渡す）
  - Web検索の指示は固定クエリではなく、大枠の方針をプロンプトで与える（例:「金融・製造業のIT×DX動向、海外大手AI企業/GAFAMの最新動向を検索してよい」）。クエリ自体はモデルが自律的に組み立てる
- 出力形式: JSON（プロンプトで強制し、`json.loads`でパース。失敗時は1回だけリトライ）

```json
{
  "selected": [
    {"id": "qiita:1234567890abcdef", "selection_reason": "生成AI関連の実装Tipsで実務影響が大きい"},
    {"id": "hn:39812345", "selection_reason": "海外で議論が活発なOSSの新リリース"},
    {
      "id": "websearch:mufg-genai-202607",
      "selection_reason": "三菱UFJの生成AI活用拡大、業界インパクト大",
      "title": "...", "url": "...", "source": "WebSearch", "published_at": "...", "body_text": null
    }
  ]
}
```

選定基準:
- 単なる目新しさだけでなく、実務・業界動向への影響度を重視する
- 特定ソースに偏りすぎないようにする（技術記事とニュースをバランス良く。銀行×IT関連も含める）
- 内容が薄い/宣伝色が強い記事は避ける
- `posted_history`と同一テーマ・類似内容の記事は除外する（Vector検索等は使わず、モデルの意味理解による判断に委ねる。詳細は別途プロンプト設計で詰める）

### 4.2 summarize_article()（軽量モデル）

```python
def summarize_article(article: dict) -> str:
    """
    article: body_text を含む記事dict
    return: 要約テキスト（3〜5文、背景を含む）
    """
```

- モデル: `claude-haiku-4-5-20251001`
- 入力: `body_text`（`trafilatura`等で抽出した本文。長すぎる場合は先頭8,000文字程度に切り詰める。Web検索経由の記事も同様に`fetch_body()`で本文取得してから渡す）
- 呼び出し方式: 記事ごとに個別呼び出し（1本の失敗が他に波及しないようにする）。最大10本になってもHaiku 4.5は200Kコンテキスト/64K出力/低単価のため、量的な制約にはならない
- 出力: プレーンテキストの要約（Slack投稿にそのまま使う）
- 出力条件:
  - 3〜5文程度で、要点に加えて背景や影響も簡潔に含める
  - 原文の文章をそのまま引用せず、自分の言葉で要約する
  - 専門用語は残してよいが、読み手はエンジニア〜ITビジネス層を想定する
  - 見出しや箇条書きは不要。地の文のみ
  - 記事本文が英語であっても、要約は必ず日本語で出力する

### 4.3 共通のリトライ・エラーハンドリング

- API呼び出しは `tenacity` 等を使わずシンプルに、最大2回まで再試行（指数バックオフ0.5s/1s）
- JSON parse失敗時はエラーログを出し、その週はその記事をスキップ（Bot全体は止めない）
- Web検索がエラーになった場合も選定処理自体は継続する（既存collector由来の候補のみで選定を完了させる）

---

## 5. history.py（重複除外用の履歴管理）

Vector検索による類似度判定は、Slack過去ログ読み取り権限の追加・埋め込み（embedding）API契約・永続化基盤が別途必要になり、週10本規模の運用にはコストが見合わないため採用しない。代わりに、直近投稿済み記事のタイトル・URLをJSONで保持し、選定モデル（Sonnet 5）に渡して意味的な重複判断を委ねる方式とする。

```python
def load_history(weeks: int = 3) -> list[dict]:
    """posted_history.json から直近N週間分を読み込んで返す"""

def append_history(articles: list[dict]) -> None:
    """今回投稿した記事を posted_history.json に追記する
    （古いものは weeks を超えたら間引く）"""
```

```json
// posted_history.json のイメージ
[
  {"title": "三菱UFJ、生成AI基盤を刷新", "url": "https://...", "posted_at": "2026-07-13"},
  {"title": "...", "url": "https://...", "posted_at": "2026-07-06"}
]
```

`main.py`は投稿成功後に`append_history()`を呼び、GitHub Actions側で`posted_history.json`の変更をコミット・pushし直す（詳細は[実装書](03_implementation.md)のワークフロー定義を参照）。

---

## 6. slack_poster.py（投稿設計）

```python
def post_digest(articles: list[dict]) -> None:
    """
    articles: [{"title","url","source","summary","published_at"}, ...]
    Block Kitに整形して chat.postMessage で投稿
    """
```

Block Kit構成例:

```python
blocks = [
    {"type": "header", "text": {"type": "plain_text", "text": "📰 今週のITトレンド記事"}},
    {"type": "divider"},
]
for a in articles:
    blocks += [
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"*<{a['url']}|{a['title']}>*\n{a['summary']}",
            },
        },
        {
            "type": "context",
            "elements": [
                {"type": "mrkdwn", "text": f"出典: {a['source']} ／ {a['published_at'][:10]}"}
            ],
        },
        {"type": "divider"},
    ]
```

（Slackはヘッダー絵文字を許容するが、社風に合わせて `header` のテキストから絵文字を外してもよい）

`text` はフォールバック用に必須（通知プレビュー等に使われる）。

---

## 7. main.py（全体フロー）

```python
def main():
    cost_tracker.reset()  # このrun分のusageを集計するためのカウンタをリセット

    articles = []
    articles += qiita.fetch()
    articles += zenn.fetch()
    articles += hackernews.fetch()
    articles += rss_sources.fetch()

    candidates = scorer.build_candidates(articles, limit=50)
    posted_history = history.load_history(weeks=3)

    # Sonnet 5がWeb検索も併用しつつ選定・重複除外を行う
    # (candidatesに無い新規記事はここでWeb検索経由で追加される)
    # claude_client内の各API呼び出し後、cost_tracker.record(usage)を呼ぶ
    selected = claude_client.screen_candidates(candidates, posted_history)

    enriched = []
    for item in selected:
        article = find_by_id(candidates, item["id"]) or item  # WebSearch由来はitem自体を使う
        article["body_text"] = fetch_body(article)
        article["summary"] = claude_client.summarize_article(article)  # Haiku 4.5・記事ごとに個別呼び出し
        enriched.append(article)

    slack_poster.post_digest(enriched)
    history.append_history(enriched)  # ワークフロー側でコミット・push

    # 今回runのコストをcost_log.jsonに追記し、週次通知を送る
    # 月が変わっていれば前月分を合計して月次通知も送る
    weekly_cost = cost_tracker.finalize_and_log()
    notifier.notify_weekly_cost(weekly_cost)
    if cost_tracker.is_new_month():
        notifier.notify_monthly_cost(cost_tracker.last_month_total())

if __name__ == "__main__":
    main()
```

---

## 8. notifier.py / cost_tracker.py（特定ユーザーへのコスト通知）

Anthropicの管理者用API（Admin API・`sk-ant-admin-`キー）を使えば組織全体の正確な請求額を取得できるが、Admin権限の付与・別キーの管理が必要になり運用コストが増える。週10本規模の運用では見合わないため採用せず、**APIレスポンスの`usage`情報から自前で概算する**方式とする。

```python
# cost_tracker.py
PRICING = {  # config.pyで一元管理。Sonnet 5は導入価格($2/$10, 〜2026/8/31)適用中は要更新
    "claude-sonnet-5": {"input": 3.0, "output": 15.0},      # $ / 1Mトークン
    "claude-haiku-4-5-20251001": {"input": 1.0, "output": 5.0},
}
WEB_SEARCH_PRICE_PER_1000 = 10.0  # $10 / 1,000回

def record(usage: dict, model: str) -> None:
    """claude_client内の各API呼び出し直後に呼び、input/output token数と
    server_tool_use.web_search_requestsからそのrunの累計コストに加算する"""

def finalize_and_log() -> float:
    """今回runの合計コストを cost_log.json に {date, cost} で追記し、
    その値を返す"""

def is_new_month() -> bool:
    """cost_log.json内で、今回の日付が前回エントリと月をまたいでいるか判定"""

def last_month_total() -> float:
    """cost_log.json から前月分のエントリを合計して返す"""
```

```python
# notifier.py
def notify_weekly_cost(cost_usd: float) -> None:
    """SLACK_ADMIN_USER_ID宛にDMで週次コストを通知する"""

def notify_monthly_cost(cost_usd: float) -> None:
    """SLACK_ADMIN_USER_ID宛にDMで月次コスト合計を通知する"""

def notify_key_rotation_due(months_elapsed: int) -> None:
    """SLACK_ADMIN_USER_ID宛にDMでキーローテーション時期を通知する"""
```

- 送信方法: `client.chat_postMessage(channel=os.environ["SLACK_ADMIN_USER_ID"], text=...)`
  （ユーザーIDを直接`channel`に渡すとそのユーザーとのDMに投稿される。追加のBot Token Scopeは不要）
- あくまで概算のため、メッセージ内に「概算」であることを明記する
- 料金改定（Sonnet 5の導入価格終了など）があった場合は`PRICING`を手動更新する必要がある。四半期点検（[運用資料](04_operations.md)4.2節）のタイミングで公式Pricingページと突き合わせる運用とする

---

## 9. key_rotation_reminder.yml（キーローテーション通知の独立ワークフロー）

コスト通知とは異なり、記事収集・投稿処理の成否に依存させたくないため、`main.py`とは別の独立したGitHub Actionsワークフローとして実装する。

```yaml
# .github/workflows/key_rotation_reminder.yml（イメージ）
on:
  schedule:
    - cron: '0 0 1 * *'  # 毎月1日にチェック（実際の通知は半年経過時のみ）
  workflow_dispatch: {}

permissions:
  contents: write  # key_rotation.json の更新はしないが、将来の拡張に備えて明示

jobs:
  check:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: '3.12'
      - run: python check_key_rotation.py
        env:
          SLACK_BOT_TOKEN: ${{ secrets.SLACK_BOT_TOKEN }}
          SLACK_ADMIN_USER_ID: ${{ secrets.SLACK_ADMIN_USER_ID }}
```

`key_rotation.json`には`{"last_rotated_at": "2026-07-21"}`のように最終ローテーション日を持たせ、`check_key_rotation.py`が現在日時との差分を計算し、6ヶ月（設定可能）を超えていれば`notifier.notify_key_rotation_due()`を呼ぶ。実際にキーを再発行・入れ替えた際は、`last_rotated_at`を手動で更新する（[運用資料](04_operations.md) 2節の手順に追記）。

if __name__ == "__main__":
    main()
```
