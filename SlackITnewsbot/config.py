"""設定・環境変数の読み込み。

未確定の値（SLACK_CHANNEL_ID / SLACK_ADMIN_USER_ID）は仮値をデフォルトに
しているので、社内承認が下りて実値が決まったら GitHub Secrets /
ローカルの .env を実値に差し替えること。差し替えるまでは DRY_RUN=1 の
デフォルト動作により、Slackへの実投稿・DM通知は行われずログ出力のみになる。
"""
import os

from dotenv import load_dotenv

load_dotenv()  # ローカルの .env を読み込む（GitHub Actions では .env が存在しないため無視される）

# --- 必須（本番投入前に実キーへの差し替えが必要） ---
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "sk-ant-placeholder-not-set")
SLACK_BOT_TOKEN = os.environ.get("SLACK_BOT_TOKEN", "xoxb-placeholder-not-set")

# --- 仮値で進めてよい値（社内承認後に実値へ差し替え） ---
SLACK_CHANNEL_ID = os.environ.get("SLACK_CHANNEL_ID", "C0000000000")  # 仮値: 投稿先チャンネル未確定
SLACK_ADMIN_USER_ID = os.environ.get("SLACK_ADMIN_USER_ID", "U0000000000")  # 仮値: 通知先ユーザー未確定

# --- 任意 ---
QIITA_ACCESS_TOKEN = os.environ.get("QIITA_ACCESS_TOKEN") or None

# --- 動作モード ---
# DRY_RUN=1（デフォルト）の場合、Slackへの実投稿・DM通知を行わずログ出力のみに留める。
# 本番投入時は GitHub Secrets 側で DRY_RUN=0 を明示的に設定すること。
DRY_RUN = os.environ.get("DRY_RUN", "1") != "0"

# SKIP_CLAUDE=1の場合、Claude APIによる選定・要約を行わず、候補記事の上位を
# そのままダミー要約で投稿する（GitHub Actionsのcronトリガー・Slack投稿部分の
# 動作確認用。API残高を消費せずに自動実行の疎通確認ができる）。
SKIP_CLAUDE = os.environ.get("SKIP_CLAUDE", "0") == "1"

# --- モデル ---
MODEL_SCREENING = "claude-sonnet-5"                    # 候補選定・Web検索・重複除外
MODEL_SUMMARY = "claude-haiku-4-5-20251001"             # 記事要約
WEB_SEARCH_TOOL_TYPE = "web_search_20260209"

# --- 選定・投稿件数 ---
CANDIDATE_LIMIT = 50
MIN_SELECTED = 3
MAX_SELECTED = 8

# --- 重複除外の履歴保持期間（週） ---
HISTORY_WEEKS = 3

# --- RSS情報源 ---
RSS_FEEDS = {
    "Publickey": "https://www.publickey1.jp/atom.xml",
    "ITmedia": "https://rss.itmedia.co.jp/rss/2.0/news_bursts.xml",
    "CNETJapan": "https://japan.cnet.com/rss/index.rdf",
    "TechCrunch": "https://techcrunch.com/feed/",
}
ZENN_FEED = "https://zenn.dev/feed"

# --- コスト概算用の料金表 ($ / 1Mトークン) ---
# 注意: Sonnet 5は2026/8/31まで導入価格($2/$10)適用中。それ以降は
# 標準価格($3/$15)に変わるため、四半期点検のタイミングで見直すこと。
PRICING = {
    MODEL_SCREENING: {"input": 3.0, "output": 15.0},
    MODEL_SUMMARY: {"input": 1.0, "output": 5.0},
}
WEB_SEARCH_PRICE_PER_1000 = 10.0  # $10 / 1,000回

# --- ファイルパス ---
POSTED_HISTORY_PATH = "posted_history.json"
COST_LOG_PATH = "cost_log.json"
KEY_ROTATION_PATH = "key_rotation.json"
KEY_ROTATION_MONTHS = 6

# --- 記事本文の切り詰め文字数 ---
BODY_TRUNCATE_CHARS = 8000
