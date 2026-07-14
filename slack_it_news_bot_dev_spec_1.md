# Slack ITニュース要約Bot 実装仕様書

作成日: 2026年7月13日
ステータス: 実装着手可能

未確定の項目（チャンネルID、除外/追加したい情報源）は仮値・初期案で記載しています。実装中に確定させてください。

---

## 1. 要件サマリー

| 項目 | 内容 |
|---|---|
| 投稿頻度 | 週1回（GitHub Actions cron、毎週月曜9:00 JST） |
| 投稿本数 | 基本3本。Haiku 4.5が「面白い」と判定した候補が多ければ増量（上限の目安: 7〜8本） |
| 情報源 | Qiita, Zenn, Hacker News, Publickey, ITmedia NEWS, CNET Japan, TechCrunch（英語） |
| Slack投稿方式 | Bot Token + Web API（`chat.postMessage`）。将来Q&A Bot化を見据える |
| 要約方針 | Haiku 4.5で候補選定 → Sonnet 5でやや詳しめ要約（3〜5文＋背景） |
| 言語/実行環境 | Python 3.12 / GitHub Actions ubuntu-latest |
| 想定コスト | Claude API 月$1未満、GitHub Actions/Slackは実質無料（詳細は提案書参照） |

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
└── prompts/
    ├── screen_candidates.txt
    └── summarize_article.txt
```

---

## 3. 環境変数 / GitHub Secrets

| 変数名 | 用途 |
|---|---|
| `ANTHROPIC_API_KEY` | Claude API認証 |
| `SLACK_BOT_TOKEN` | Slack Bot Token（`xoxb-`） |
| `SLACK_CHANNEL_ID` | 投稿先チャンネルID（例: `C0XXXXXXX`） |
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
    "source": "Qiita",                 # Qiita/Zenn/HackerNews/Publickey/ITmedia/TechCrunchJP
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
- TechCrunchは英語記事のため、`summarize_article()`（Sonnet 5）が日本語で要約を生成する前提とする（プロンプト側で「英語記事でも日本語で要約する」旨を明記する）

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

1. 全ソースから直近7日以内に公開された記事を集約
2. `popularity_score` があるソースは、ソースごとに正規化（0〜1にmin-max scaling）
3. `popularity_score` が無いソースは、公開日時の新しさで代替スコア（直近ほど高スコア）を付与
4. 正規化スコア順に上位30〜50件を候補として抽出
5. 抽出結果を `list[Article]` としてHaikuに渡す

```python
def build_candidates(articles: list[dict], limit: int = 50) -> list[dict]:
    ...
```

---

## 7. claude_client.py

### 7.1 screen_candidates()（Haiku 4.5）

```python
def screen_candidates(candidates: list[dict]) -> list[dict]:
    """
    candidates: build_candidates()の出力
    return: 選定された記事のリスト（最低3件、目安上限7〜8件）
            各要素に "selection_reason": str を付加
    """
```

- モデル: `claude-haiku-4-5-20251001`
- 入力: 候補記事の `title / source / published_at / popularity_score / url` を圧縮したテキストリスト（本文は渡さない）
- 出力形式: JSON（`response_format`相当をプロンプトで強制し、`json.loads`でパース。失敗時は1回だけリトライ）

```json
{
  "selected": [
    {"id": "qiita:1234567890abcdef", "selection_reason": "生成AI関連の実装Tipsで実務影響が大きい"},
    {"id": "hn:39812345", "selection_reason": "海外で議論が活発なOSSの新リリース"}
  ]
}
```

`prompts/screen_candidates.txt`（雛形）:

```
あなたはIT業界のニュースキュレーターです。以下の候補記事リストから、
エンジニア・IT業界人にとって話題性・実務への影響が大きいものを
最低3件、必要であれば最大8件まで選んでください。

選定基準:
- 単なる目新しさだけでなく、実務・業界動向への影響度を重視する
- 特定ソースに偏りすぎないようにする（技術記事とニュースをバランス良く）
- 内容が薄い/宣伝色が強い記事は避ける

出力は次のJSON形式のみで返してください。説明文は含めないでください。
{"selected": [{"id": "...", "selection_reason": "..."}]}

候補記事:
{{CANDIDATES}}
```

### 7.2 summarize_article()（Sonnet 5）

```python
def summarize_article(article: dict) -> str:
    """
    article: body_text を含む記事dict
    return: 要約テキスト（3〜5文、背景を含む）
    """
```

- モデル: `claude-sonnet-5`
- 入力: `body_text`（`trafilatura`等で抽出した本文。長すぎる場合は先頭8,000文字程度に切り詰める）
- 出力: プレーンテキストの要約（Slack投稿にそのまま使う）

`prompts/summarize_article.txt`（雛形）:

```
以下の記事本文を読み、Slackに投稿するための要約を作成してください。

出力条件:
- 3〜5文程度で、要点に加えて背景や影響も簡潔に含める
- 原文の文章をそのまま引用せず、自分の言葉で要約する
- 専門用語は残してよいが、読み手はエンジニア〜ITビジネス層を想定する
- 見出しや箇条書きは不要。地の文のみ
- 記事本文が英語であっても、要約は必ず日本語で出力する

記事タイトル: {{TITLE}}
記事本文:
{{BODY}}
```

### 7.3 共通のリトライ・エラーハンドリング

- API呼び出しは `tenacity` 等を使わずシンプルに、最大2回まで再試行（指数バックオフ0.5s/1s）
- JSON parse失敗時はエラーログを出し、その週はその記事をスキップ（Bot全体は止めない）

---

## 8. slack_poster.py

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

## 9. main.py（全体フロー）

```python
def main():
    articles = []
    articles += qiita.fetch()
    articles += zenn.fetch()
    articles += hackernews.fetch()
    articles += rss_sources.fetch()

    candidates = scorer.build_candidates(articles, limit=50)
    selected = claude_client.screen_candidates(candidates)

    enriched = []
    for item in selected:
        article = find_by_id(candidates, item["id"])
        article["body_text"] = fetch_body(article)
        article["summary"] = claude_client.summarize_article(article)
        enriched.append(article)

    slack_poster.post_digest(enriched)

if __name__ == "__main__":
    main()
```

---

## 10. GitHub Actions Workflow

`.github/workflows/weekly_digest.yml`

```yaml
name: Weekly IT News Digest

on:
  schedule:
    - cron: '0 0 * * 1'  # 毎週月曜 09:00 JST（UTC 0:00）
  workflow_dispatch: {}

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
          QIITA_ACCESS_TOKEN: ${{ secrets.QIITA_ACCESS_TOKEN }}
```

---

## 11. Slack App セットアップ手順（付録）

1. https://api.slack.com/apps で「Create New App」→「From scratch」
2. 対象ワークスペースを選択
3. 左メニュー「OAuth & Permissions」→ Bot Token Scopesに以下を追加
   - `chat:write`
   - `chat:write.public`（プライベートチャンネル以外に投稿する場合は招待が不要になる）
4. 「Install to Workspace」を実行し、発行された `Bot User OAuth Token`（`xoxb-`）を控える
5. 投稿先チャンネルにBotを招待（`/invite @bot名`）、またはチャンネルIDを控える
6. GitHub Secretsに `SLACK_BOT_TOKEN`、`SLACK_CHANNEL_ID` を登録

---

## 12. 実装チェックリスト

- [ ] Slack App作成・Bot Token発行・チャンネル確定
- [ ] `collectors/qiita.py` 実装・単体動作確認
- [ ] `collectors/zenn.py` 実装・フィードURL確定
- [ ] `collectors/hackernews.py` 実装
- [ ] `collectors/rss_sources.py` 実装・フィードURL確定（Publickey/ITmedia/TechCrunchJP）
- [ ] `scorer.py` 実装（正規化ロジック）
- [ ] `claude_client.screen_candidates()` 実装・プロンプト検証
- [ ] `claude_client.summarize_article()` 実装・プロンプト検証
- [ ] `slack_poster.py` 実装・Block Kit投稿確認
- [ ] `main.py` 結合・ローカル実行確認
- [ ] GitHub Actions workflow追加・Secrets登録
- [ ] `workflow_dispatch` での手動実行テスト
- [ ] 週次自動実行へ切り替え、初回投稿を確認

---

## 13. 未確定事項

### 確定済み（2026-07-13）
- 情報源: Qiita, Zenn, Hacker News, Publickey, ITmedia NEWS, CNET Japan, TechCrunch（英語）の7ソースで確定
  - TechCrunch Japan（`jp.techcrunch.com`）は日本事業終了の影響でフィードのレスポンスが不安定だったため除外
  - 代替としてCNET JapanとTechCrunch本体（英語）を追加。TechCrunchの英語記事はSonnet 5が日本語で要約する
- Zenn / Publickey / ITmedia / CNET Japan / TechCrunchの各フィードURLは疎通確認済み（HTTP 200）

### 未確定（会社の許可待ちのため保留）
- 投稿先Slackチャンネル名・チャンネルID
- Slack App作成・Bot Token発行
- Claude APIキーの発行・利用申請
- GitHub Actions／Secrets登録などのCI設定

上記「未確定（保留）」は会社側の許可が下りてから着手する。それ以外（collectors実装、scorer、プロンプト、main.pyのロジックなど）はローカルでの実装・動作確認を先行して進めて良い。
