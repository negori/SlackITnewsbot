"""Slackへのダイジェスト投稿（設計書 02_design.md 6節に対応）"""
from dateutil import parser as date_parser
from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError

import config


def _format_date_ja(published_at: str) -> str:
    """RSS由来（英語のRFC822形式等）とISO8601形式が混在するpublished_atを
    日本語の日付表記に揃える。パースできない場合は空文字を返す。"""
    if not published_at:
        return ""
    try:
        dt = date_parser.parse(published_at)
    except (ValueError, TypeError):
        return ""
    return f"{dt.year}年{dt.month}月{dt.day}日"


def _build_blocks(articles: list[dict]) -> list[dict]:
    blocks = [
        {"type": "header", "text": {"type": "plain_text", "text": "今週のITトレンド記事"}},
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
                    {
                        "type": "mrkdwn",
                        "text": f"出典: {a['source']} ／ {_format_date_ja(a.get('published_at', ''))}",
                    }
                ],
            },
            {"type": "divider"},
        ]
    return blocks


def post_digest(articles: list[dict]) -> bool:
    """投稿に成功した場合のみ True を返す。posted_history.json への記録要否の判断に使う。"""
    articles = [a for a in articles if a.get("summary")]
    if not articles:
        print("[slack_poster] no summarized articles to post, skipping")
        return False

    blocks = _build_blocks(articles)

    if config.DRY_RUN:
        print(f"[slack_poster] (DRY_RUN) would post {len(articles)} articles to {config.SLACK_CHANNEL_ID}")
        for a in articles:
            print(f"  - [{a['source']}] {a['title']}\n    {a['summary']}\n    {a['url']}")
        return False

    client = WebClient(token=config.SLACK_BOT_TOKEN)
    try:
        client.chat_postMessage(
            channel=config.SLACK_CHANNEL_ID,
            blocks=blocks,
            text="今週のITトレンド記事",
            unfurl_links=False,
            unfurl_media=False,
        )
        return True
    except SlackApiError as e:
        print(f"[slack_poster] post failed: {e}")
        return False
