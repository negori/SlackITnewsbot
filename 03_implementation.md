# Slack ITニュース要約Bot 実装書

作成日: 2026年7月13日
関連: [企画書](01_proposal.md) / [設計書](02_design.md)

未確定の項目（チャンネルID等）は仮値で記載。社内承認後に確定させる。

---

## 1. 環境変数 / GitHub Secrets

| 変数名 | 用途 |
|---|---|
| `ANTHROPIC_API_KEY` | Claude API認証 |
| `SLACK_BOT_TOKEN` | Slack Bot Token（`xoxb-`） |
| `SLACK_CHANNEL_ID` | 投稿先チャンネルID（例: `C0XXXXXXX`） |
| `QIITA_ACCESS_TOKEN` | （任意）Qiita APIレート緩和用。未設定でも取得は可能だが上限が下がる |

`config.py` で `os.environ` から読み込み、未設定時は明示的にエラーを出す。

---

## 2. requirements.txt

```
anthropic>=0.40.0
requests>=2.32.0
feedparser>=6.0.11
trafilatura>=1.12.0
slack_sdk>=3.33.0
python-dateutil>=2.9.0
```

---

## 3. GitHub Actions Workflow

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

## 4. Slack App セットアップ手順（付録・社内承認後に実施）

1. https://api.slack.com/apps で「Create New App」→「From scratch」
2. 対象ワークスペースを選択
3. 左メニュー「OAuth & Permissions」→ Bot Token Scopesに以下を追加
   - `chat:write`
   - `chat:write.public`（プライベートチャンネル以外に投稿する場合は招待が不要になる）
4. 「Install to Workspace」を実行し、発行された `Bot User OAuth Token`（`xoxb-`）を控える
5. 投稿先チャンネルにBotを招待（`/invite @bot名`）、またはチャンネルIDを控える
6. GitHub Secretsに `SLACK_BOT_TOKEN`、`SLACK_CHANNEL_ID` を登録

---

## 5. 実装チェックリスト

### ローカルで先行着手可能（承認不要）
- [ ] `collectors/qiita.py` 実装・単体動作確認
- [ ] `collectors/zenn.py` 実装
- [ ] `collectors/hackernews.py` 実装
- [ ] `collectors/rss_sources.py` 実装（Publickey/ITmedia/CNET Japan/TechCrunch）
- [ ] `scorer.py` 実装（正規化ロジック）
- [ ] `claude_client.screen_candidates()` 実装・プロンプト検証（要APIキー、個人キー等で仮検証は可）
- [ ] `claude_client.summarize_article()` 実装・プロンプト検証
- [ ] `slack_poster.py` 実装（ロジックのみ、投稿テストは承認後）
- [ ] `main.py` 結合・ローカル実行確認（Slack投稿部分はモック化）

### 社内承認後に着手
- [ ] Slack App作成・Bot Token発行・チャンネル確定
- [ ] Claude API本番キーの発行・Secrets登録
- [ ] GitHub Actions workflow追加・Secrets登録
- [ ] `slack_poster.py` の実チャンネルへの投稿確認
- [ ] `workflow_dispatch` での手動実行テスト
- [ ] 週次自動実行へ切り替え、初回投稿を確認
