"""Slackへのダイジェスト投稿（設計書 02_design.md 6節に対応）"""
from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError

import config


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
                        "text": f"出典: {a['source']} ／ {a.get('published_at', '')[:10]}",
                    }
                ],
            },
            {"type": "divider"},
        ]
    return blocks


def post_digest(articles: list[dict]) -> None:
    articles = [a for a in articles if a.get("summary")]
    if not articles:
        print("[slack_poster] no summarized articles to post, skipping")
        return

    blocks = _build_blocks(articles)

    if config.DRY_RUN:
        print(f"[slack_poster] (DRY_RUN) would post {len(articles)} articles to {config.SLACK_CHANNEL_ID}")
        for a in articles:
            print(f"  - [{a['source']}] {a['title']}\n    {a['summary']}\n    {a['url']}")
        return

    client = WebClient(token=config.SLACK_BOT_TOKEN)
    try:
        client.chat_postMessage(
            channel=config.SLACK_CHANNEL_ID,
            blocks=blocks,
            text="今週のITトレンド記事",
        )
    except SlackApiError as e:
        print(f"[slack_poster] post failed: {e}")
