# Slack ITニュース要約Bot 設計書

作成日: 2026年7月13日
関連: [企画書](01_proposal.md) / [実装書](03_implementation.md)

---

## 1. ディレクトリ構成

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

## 2. データモデル（共通フォーマット）

各collectorは以下の辞書のリストを返す。ソース差異はここで吸収する。

```python
# Article dict
{
    "id": "qiita:1234567890abcdef",   # ソース名:一意ID
    "title": "記事タイトル",
    "url": "https://...",
    "source": "Qiita",                 # Qiita/Zenn/HackerNews/Publickey/ITmedia/CNETJapan/TechCrunch
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

1. 全ソースから直近7日以内に公開された記事を集約
2. `popularity_score` があるソースは、ソースごとに正規化（0〜1にmin-max scaling）
3. `popularity_score` が無いソースは、公開日時の新しさで代替スコア（直近ほど高スコア）を付与
4. 正規化スコア順に上位30〜50件を候補として抽出
5. 抽出結果を `list[Article]` として選定処理に渡す

```python
def build_candidates(articles: list[dict], limit: int = 50) -> list[dict]:
    ...
```

---

## 4. claude_client.py（AIによる選定・要約の設計）

### 4.1 screen_candidates()（軽量モデル）

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
- 出力形式: JSON（プロンプトで強制し、`json.loads`でパース。失敗時は1回だけリトライ）

```json
{
  "selected": [
    {"id": "qiita:1234567890abcdef", "selection_reason": "生成AI関連の実装Tipsで実務影響が大きい"},
    {"id": "hn:39812345", "selection_reason": "海外で議論が活発なOSSの新リリース"}
  ]
}
```

選定基準:
- 単なる目新しさだけでなく、実務・業界動向への影響度を重視する
- 特定ソースに偏りすぎないようにする（技術記事とニュースをバランス良く）
- 内容が薄い/宣伝色が強い記事は避ける

### 4.2 summarize_article()（高性能モデル）

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
- 出力条件:
  - 3〜5文程度で、要点に加えて背景や影響も簡潔に含める
  - 原文の文章をそのまま引用せず、自分の言葉で要約する
  - 専門用語は残してよいが、読み手はエンジニア〜ITビジネス層を想定する
  - 見出しや箇条書きは不要。地の文のみ
  - 記事本文が英語であっても、要約は必ず日本語で出力する

### 4.3 共通のリトライ・エラーハンドリング

- API呼び出しは `tenacity` 等を使わずシンプルに、最大2回まで再試行（指数バックオフ0.5s/1s）
- JSON parse失敗時はエラーログを出し、その週はその記事をスキップ（Bot全体は止めない）

---

## 5. slack_poster.py（投稿設計）

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

## 6. main.py（全体フロー）

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
