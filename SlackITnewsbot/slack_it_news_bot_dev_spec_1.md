# Slack ITニュース要約Bot 実装仕様書

作成日: 2026年7月13日
ステータス: 実装着手可能

未確定の項目（チャンネルID、除外/追加したい情報源）は仮値・初期案で記載しています。実装中に確定させてください。

---

## 1. 要件サマリー

| 項目 | 内容 |
|---|---|
| 投稿頻度 | 週1回（GitHub Actions cron、毎週月曜9:00 JST） |
| 投稿本数 | 基本3本。Sonnet 5が「面白い」と判定した候補が多ければ増量（上限の目安: 10本） |
| 情報源 | Qiita, Zenn, Hacker News, Publickey, ITmedia NEWS, CNET Japan, TechCrunch（英語）＋ Claude API Web検索（金融・製造業のIT/DX動向、海外大手AI企業/GAFAMの最新動向） |
| Slack投稿方式 | Bot Token + Web API（`chat.postMessage`）。将来Q&A Bot化を見据える |
| 要約方針 | Sonnet 5（Web検索ツール有効）で候補選定・重複除外 → Haiku 4.5でやや詳しめ要約（3〜5文＋背景） |
| 重複除外方式 | `posted_history.json`（直近投稿済み記事のタイトル/URL）をSonnet 5に渡し、意味的な重複をAIに判断させる。Vector検索は導入コストが見合わないため不採用 |
| 言語/実行環境 | Python 3.12 / GitHub Actions ubuntu-latest |
| 想定コスト | Claude API 月$3〜5程度、GitHub Actions/Slackは実質無料（詳細は提案書参照） |

---

## 2. ディレクトリ構成

```
repo-root/
├── .github/workflows/weekly_digest.yml
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
├── posted_history.json       # 直近投稿済み記事の履歴（重複除外用。ワークフローがコミットし直す）
├── cost_log.json             # 週ごとのコスト概算ログ（月次集計の元データ）
├── key_rotation.json         # 最終ローテーション日の記録
└── prompts/
    ├── screen_candidates.txt
    └── summarize_article.txt
```

- Web検索は独立したcollectorではなく、`claude_client.screen_candidates()`内でSonnet 5にWeb検索ツール（`web_search_20260209`）を持たせて実行する。見つかった記事は他candidateと同じArticle dict形式（`source: "WebSearch"`）に正規化する
- `history.py` は `posted_history.json` の読み書きを担当し、選定時にSonnet 5へ渡して重複除外の判断材料にする

---

## 3. 環境変数 / GitHub Secrets

| 変数名 | 用途 |
|---|---|
| `ANTHROPIC_API_KEY` | Claude API認証 |
| `SLACK_BOT_TOKEN` | Slack Bot Token（`xoxb-`） |
| `SLACK_CHANNEL_ID` | 投稿先チャンネルID（例: `C0XXXXXXX`） |
| `SLACK_ADMIN_USER_ID` | コスト通知・キーローテーション通知の送付先ユーザーID（例: `U0XXXXXXX`） |
| `QIITA_ACCESS_TOKEN` | （任意）Qiita APIレート緩和用。未設定でも取得は可能だが上限が下がる |

`config.py` で `os.environ` から読み込み、未設定時は明示的にエラーを出す。

---

## 4. requirements.txt

```
anthropic>=0.40.0
requests>=2.32.0
feedparser>=6.0.11
trafilatura>=1.12.0
slack_sdk>=3.33.0
python-dateutil>=2.9.0
```

---

## 5. データモデル（共通フォーマット）

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

- `body_text` は全候補に対して取得すると無駄にコストがかかるため、Haiku選定後の記事のみ取得する（`collectors`側に `fetch_body(article) -> str` を用意し、`trafilatura.fetch_url` / `trafilatura.extract` を使う。Qiita/Zennは本文API/フィードのcontentをそのまま使う）

### 5.1 collectors/qiita.py

- エンドポイント: `GET https://qiita.com/api/v2/items?query=created:>YYYY-MM-DD&page=1&per_page=100`
- `popularity_score` = `likes_count`
- 認証: `Authorization: Bearer {QIITA_ACCESS_TOKEN}`（設定時のみ付与）

### 5.2 collectors/zenn.py

- Zennは公式APIが無いため、フィード（`https://zenn.dev/feed`）を`feedparser`で取得（疎通確認済み、HTTP 200）
- `popularity_score` は取得できないため `None`。スコアリング側では鮮度（公開日時が直近3日以内か）で代替

### 5.3 collectors/hackernews.py

- Firebase API: `GET https://hacker-news.firebaseio.com/v0/topstories.json` → 上位N件の `GET /v0/item/{id}.json`
- `popularity_score` = `score`
- レート制限なし・認証不要

### 5.4 collectors/rss_sources.py

- Publickey / ITmedia NEWS / CNET Japan / TechCrunch（英語）の各RSSを `feedparser` で取得
- `popularity_score` は `None`（鮮度のみで判定）
- URL一覧は `config.py` の `RSS_FEEDS` にリストで定義し、増減を容易にする
- TechCrunchは英語記事のため、`summarize_article()`（Haiku 4.5）が日本語で要約を生成する前提とする（プロンプト側で「英語記事でも日本語で要約する」旨を明記する）

```python
RSS_FEEDS = {
    "Publickey": "https://www.publickey1.jp/atom.xml",
    "ITmedia": "https://rss.itmedia.co.jp/rss/2.0/news_bursts.xml",
    "CNETJapan": "https://japan.cnet.com/rss/index.rdf",
    "TechCrunch": "https://techcrunch.com/feed/",
}
```

※ 上記URLは実装時（2026年7月13日）に疎通確認済み（すべてHTTP 200）。TechCrunch Japan（`jp.techcrunch.com`）は日本事業終了の影響でフィードのレスポンスが不安定だったため除外し、代わりにCNET JapanとTechCrunch本体（英語）を採用。

---

## 6. scorer.py（候補抽出ロジック）

1. 全ソースから直近7日以内に公開された記事を集約（Web検索由来の記事はこの時点では含まれず、Sonnetの選定ステップ内で動的に追加される）
2. `popularity_score` があるソースは、ソースごとに正規化（0〜1にmin-max scaling）
3. `popularity_score` が無いソース（RSS各種・`WebSearch`も同様）は、公開日時の新しさで代替スコア（直近ほど高スコア）を付与
4. 正規化スコア順に上位30〜50件を候補として抽出
5. 抽出結果を `list[Article]` としてSonnetに渡す

```python
def build_candidates(articles: list[dict], limit: int = 50) -> list[dict]:
    ...
```

---

## 7. claude_client.py

### 7.1 screen_candidates()（Sonnet 5・Web検索ツール有効）

```python
def screen_candidates(candidates: list[dict], posted_history: list[dict]) -> list[dict]:
    """
    candidates: build_candidates()の出力
    posted_history: history.load_history()の出力（直近数週間分の投稿済み記事）
    return: 選定された記事のリスト（最低3件、目安上限10件）
            Web検索で新規に見つかった記事も同じArticle dict形式
            （source="WebSearch"）で結果に混ぜて返す
            各要素に "selection_reason": str を付加
    """
```

- モデル: `claude-sonnet-5`
- ツール: `tools=[{"type": "web_search_20260209"}]`。ドメインのallowlist/blocklistは基本指定しない（新規メディアの取りこぼし防止）。低品質サイトが混入した場合のみ`blocked_domains`で個別追加する運用
- 入力:
  - 候補記事の `title / source / published_at / popularity_score / url` を圧縮したテキストリスト（本文は渡さない）
  - `posted_history`（直近投稿済み記事のタイトル・URL・投稿日）
  - Web検索は固定クエリでなく、大枠のブリーフのみプロンプトで与え、クエリ自体はモデルが自律的に組み立てる
- 出力形式: JSON（`response_format`相当をプロンプトで強制し、`json.loads`でパース。失敗時は1回だけリトライ）

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

`prompts/screen_candidates.txt`（確定版。全文は同ファイルを参照）:

要旨:
- 候補記事リスト（既存collectors収集分）とWeb検索の両方から最低3件・最大10件を選定
- Web検索の対象領域: ①金融業界（メガバンク・大手保険会社中心）のIT×DX動向、
  ②大手製造業のIT×DX動向、③海外大手AI企業・GAFAMの最新動向
  （いずれも「必ず入れる枠」ではなく、質が無ければ0件でよい）
- 選定基準: 実務・業界影響度の重視、ソースの偏り防止、薄い記事の除外、
  信頼できるソースの優先
- `posted_history.json`との重複除外（続報は新しい重要な進展があれば許容）
- 出力はJSON形式のみ（詳細フォーマットは実ファイル参照）

### 7.2 summarize_article()（Haiku 4.5）

```python
def summarize_article(article: dict) -> str:
    """
    article: body_text を含む記事dict
    return: 要約テキスト（3〜5文、背景を含む）
    """
```

- モデル: `claude-haiku-4-5-20251001`
- 入力: `body_text`（`trafilatura`等で抽出した本文。長すぎる場合は先頭8,000文字程度に切り詰める。Web検索経由の記事も`fetch_body()`で本文取得してから渡す）
- 呼び出し方式: 記事ごとに個別呼び出し（1本の失敗が他に波及しないようにする）。最大10本でも200Kコンテキスト/64K出力/低単価のHaiku 4.5なら量的な制約にはならない
- 出力: プレーンテキストの要約（Slack投稿にそのまま使う）

`prompts/summarize_article.txt`（確定版。全文は同ファイルを参照）:

要旨:
- 3〜5文程度、文体は「である調」で統一（「です・ます」は不使用）
- 原文をそのまま引用せず自分の言葉で要約、見出し・箇条書きは不要
- 本文に無い事実・数字を推測で補わない
- 英語記事でも要約は必ず日本語

### 7.3 共通のリトライ・エラーハンドリング

- API呼び出しは `tenacity` 等を使わずシンプルに、最大2回まで再試行（指数バックオフ0.5s/1s）
- JSON parse失敗時はエラーログを出し、その週はその記事をスキップ（Bot全体は止めない）
- Web検索がエラーになった場合も選定処理自体は継続する（既存collector由来の候補のみで選定を完了させる）

---

## 8. history.py（重複除外用の履歴管理）

Slack過去ログの読み取り・Vector検索による類似度判定は、追加のBot Token Scope・埋め込み（embedding）API契約・永続化基盤が別途必要になり、週10本規模の運用にはコストが見合わないため不採用とする。代わりに、直近投稿済み記事のタイトル・URLをリポジトリ内のJSONで保持し、選定モデル（Sonnet 5）に渡して意味的な重複判断を委ねる。

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

`main.py`は投稿成功後に`append_history()`を呼び、GitHub Actions側で`posted_history.json`の変更をコミット・pushし直す（詳細は11節のワークフロー定義を参照）。

### 8.1 notifier.py / cost_tracker.py（特定ユーザーへのコスト通知）

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
    """今回runの合計コストを cost_log.json に {date, cost} で追記し、その値を返す"""

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
- 料金改定（Sonnet 5の導入価格終了など）があった場合は`PRICING`を手動更新する必要がある

### 8.2 key_rotation_reminder.yml（キーローテーション通知の独立ワークフロー）

コスト通知とは異なり、記事収集・投稿処理の成否に依存させたくないため、`main.py`とは別の独立したGitHub Actionsワークフローとして実装する（詳細は11.1節参照）。`key_rotation.json`に`{"last_rotated_at": "2026-07-21"}`のように最終ローテーション日を持たせ、`check_key_rotation.py`が現在日時との差分を計算し、6ヶ月（設定可能）を超えていれば`notifier.notify_key_rotation_due()`を呼ぶ。実際にキーを再発行・入れ替えた際は、`last_rotated_at`を手動で更新する（[運用資料](04_operations.md) 2節）。

---

## 9. slack_poster.py

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

呼び出し:

```python
from slack_sdk import WebClient
client = WebClient(token=os.environ["SLACK_BOT_TOKEN"])
client.chat_postMessage(channel=os.environ["SLACK_CHANNEL_ID"], blocks=blocks, text="今週のITトレンド記事")
```

`text` はフォールバック用に必須（通知プレビュー等に使われる）。

---

## 10. main.py（全体フロー）

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
    # claude_client内の各API呼び出し後、cost_tracker.record(usage, model)を呼ぶ
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

## 11. GitHub Actions Workflow

`.github/workflows/weekly_digest.yml`

```yaml
name: Weekly IT News Digest

on:
  schedule:
    - cron: '0 0 * * 1'  # 毎週月曜 09:00 JST（UTC 0:00）
  workflow_dispatch: {}

permissions:
  contents: write  # posted_history.json / cost_log.json をコミット・pushするために必要

jobs:
  digest:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: '3.12'
      - run: pip install -r requirements.txt
      - run: python main.py
        env:
          ANTHROPIC_API_KEY: ${{ secrets.ANTHROPIC_API_KEY }}
          SLACK_BOT_TOKEN: ${{ secrets.SLACK_BOT_TOKEN }}
          SLACK_CHANNEL_ID: ${{ secrets.SLACK_CHANNEL_ID }}
          SLACK_ADMIN_USER_ID: ${{ secrets.SLACK_ADMIN_USER_ID }}
          QIITA_ACCESS_TOKEN: ${{ secrets.QIITA_ACCESS_TOKEN }}
      - name: Commit updated posted_history.json / cost_log.json
        run: |
          git config user.name "github-actions[bot]"
          git config user.email "github-actions[bot]@users.noreply.github.com"
          git add posted_history.json cost_log.json
          git diff --cached --quiet || git commit -m "chore: update posted_history.json / cost_log.json"
          git push
```

※ Slack過去ログの読み取りやVector検索用の埋め込みAPI契約は不要（重複除外は`posted_history.json`をSonnet 5に渡す方式のため）。

### 11.1 GitHub Actions Workflow（キーローテーション通知・独立実行）

`.github/workflows/key_rotation_reminder.yml`

```yaml
on:
  schedule:
    - cron: '0 0 1 * *'  # 毎月1日にチェック（実際の通知は半年経過時のみ）
  workflow_dispatch: {}

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

`main.py`本体の週次実行とは独立させ、記事収集や投稿処理の失敗がキーローテーション通知の見落としにつながらないようにする。

---

## 12. Slack App セットアップ手順（付録）

1. https://api.slack.com/apps で「Create New App」→「From scratch」
2. 対象ワークスペースを選択
3. 左メニュー「OAuth & Permissions」→ Bot Token Scopesに以下を追加
   - `chat:write`
   - `chat:write.public`（プライベートチャンネル以外に投稿する場合は招待が不要になる）
4. 「Install to Workspace」を実行し、発行された `Bot User OAuth Token`（`xoxb-`）を控える
5. 投稿先チャンネルにBotを招待（`/invite @bot名`）、またはチャンネルIDを控える
6. GitHub Secretsに `SLACK_BOT_TOKEN`、`SLACK_CHANNEL_ID` を登録

---

## 13. 実装チェックリスト

- [ ] Slack App作成・Bot Token発行・チャンネル確定
- [ ] `collectors/qiita.py` 実装・単体動作確認
- [ ] `collectors/zenn.py` 実装・フィードURL確定
- [ ] `collectors/hackernews.py` 実装
- [ ] `collectors/rss_sources.py` 実装・フィードURL確定（Publickey/ITmedia/CNET Japan/TechCrunch）
- [ ] `scorer.py` 実装（正規化ロジック）
- [ ] `history.py` 実装（`posted_history.json`の読み書き・週数での間引き）
- [ ] `notifier.py` 実装（`SLACK_ADMIN_USER_ID`宛のDM送信）
- [ ] `cost_tracker.py` 実装（`usage`からのコスト概算・`cost_log.json`記録・月次集計）
- [ ] `check_key_rotation.py` 実装（`key_rotation.json`の経過月数判定）
- [ ] `prompts/screen_candidates.txt` 詳細設計（業界動向枠のブリーフ、Web検索の使い方、重複除外基準）※別途プロンプト設計で実施
- [ ] `prompts/summarize_article.txt` 詳細設計 ※別途プロンプト設計で実施
- [ ] `claude_client.screen_candidates()` 実装・Web検索ツール(`web_search_20260209`)連携・プロンプト検証
- [ ] `claude_client.summarize_article()` 実装・プロンプト検証
- [ ] `slack_poster.py` 実装・Block Kit投稿確認
- [ ] `main.py` 結合・ローカル実行確認
- [ ] GitHub Actions workflow追加・Secrets登録（`permissions: contents: write`含む）
- [ ] `workflow_dispatch` での手動実行テスト
- [ ] 週次自動実行へ切り替え、初回投稿を確認

---

## 14. 未確定事項

### 確定済み（2026-07-13）
- 情報源: Qiita, Zenn, Hacker News, Publickey, ITmedia NEWS, CNET Japan, TechCrunch（英語）の7ソースで確定
  - TechCrunch Japan（`jp.techcrunch.com`）は日本事業終了の影響でフィードのレスポンスが不安定だったため除外
  - 代替としてCNET JapanとTechCrunch本体（英語）を追加。TechCrunchの英語記事はHaiku 4.5が日本語で要約する
- Zenn / Publickey / ITmedia / CNET Japan / TechCrunchの各フィードURLは疎通確認済み（HTTP 200）

### 確定済み（2026-07-21）
- モデルの役割: 候補選定・重複除外はSonnet 5（Web検索ツール有効）、記事要約はHaiku 4.5に決定（当初案から入替）
- Web検索の追加: 既存情報源が汎用IT系メディアのみで銀行・保険等をカバーしていないため、Sonnet 5にWeb検索ツールを持たせてメガバンク中心のIT×銀行ニュースを都度検索・発掘する方式を採用
  - 検索クエリは固定文言ではなくモデルが大枠のブリーフから自律的に組み立てる
  - ドメインのallowlist制限は行わない（新規メディアの取りこぼし防止）。低品質サイト混入時のみ`blocked_domains`で個別除外
- 重複除外の方式: Vector検索は追加権限・embedding契約・永続化基盤のコストが見合わないため不採用。`posted_history.json`（直近投稿済みタイトル/URL）をSonnet 5に渡し、意味的な重複判断をAIに委ねる方式に決定
- 投稿本数の上限目安を7〜8本→10本に変更

### 確定済み（2026-07-21・その2）
- コスト通知・キーローテーション通知は特定ユーザー（`SLACK_ADMIN_USER_ID`）へのSlack DMで行う方式に決定
  - コストはAnthropic Admin API（正確だが組織Admin権限・別キー管理が必要）ではなく、
    APIレスポンスの`usage`から自前で概算する方式を採用（`cost_tracker.py`）
  - 週次実行のたびに今回分のコストをDM通知し、月が変わった最初の実行時に前月合計もDM通知
  - キーローテーション通知は`main.py`本体と独立したワークフロー（`key_rotation_reminder.yml`）で
    毎月チェックし、半年経過していたらDM通知する

### 確定済み（2026-07-21・その3）
- `prompts/screen_candidates.txt` / `prompts/summarize_article.txt` を確定
  - Web検索対象の業界動向枠（金融・製造業・海外AI企業/GAFAM）は
    「ノルマではない」と明記（質が無ければ0件でよい、他ジャンル優先）
  - 要約の文体は「である調」に統一（「です・ます」は使わない）
  - 続報記事の重複判定は「新しい重要な進展があれば許容」の基準を維持
  - いずれも実際の候補データでの検証結果次第で再調整の余地あり

### 確定済み（2026-07-21・その4）
- Web検索の対象領域を、当初の「メガバンク×IT」から、
  ①金融業界全般（メガバンク・大手保険会社中心）、②大手製造業、
  ③海外大手AI企業・GAFAM、の3領域に拡張
  - いずれも0件許容（無理に選ばせない）方針は変更なし
  - GAFAM/海外AI企業ニュースは既存collector（Hacker News/TechCrunch）
    と重複しやすいため、Web検索結果が候補記事リストと同一の場合は
    候補記事リスト側のid・sourceで選ぶよう`screen_candidates.txt`に明記

### 未確定（会社の許可待ちのため保留）
- 投稿先Slackチャンネル名・チャンネルID
- Slack App作成・Bot Token発行
- Claude APIキーの発行・利用申請
- GitHub Actions／Secrets登録などのCI設定

上記「未確定（保留）」は会社側の許可が下りてから着手する。それ以外（collectors実装、scorer、history、notifier、cost_tracker、main.pyのロジックなど）はローカルでの実装・動作確認を先行して進めて良い。


