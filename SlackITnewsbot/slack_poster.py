"""Slackへのダイジェスト投稿（設計書 02_design.md 6節に対応）

Slackの「Block Kit」というブロック単位のレイアウト形式でメッセージを組み立て、
chat.postMessage APIで投稿する。DRY_RUN=1の間は実際には投稿せず、
何が投稿される予定かをログに出すだけに留める。
"""
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
        # dateutilのparserは複数の日付形式（RFC822/ISO8601等）を自動判別してくれる
        dt = date_parser.parse(published_at)
    except (ValueError, TypeError):
        return ""
    return f"{dt.year}年{dt.month}月{dt.day}日"


def _build_blocks(articles: list[dict]) -> list[dict]:
    """記事一覧から、Slack投稿用のBlock Kit形式のブロック配列を組み立てる。

    先頭に見出し(header)を1つ置き、記事ごとに
      本文セクション（タイトルへのリンク＋要約） → 出典・日付（context） → 区切り線(divider)
    の3ブロックを繰り返す構成。
    """
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
                    # "*<URL|タイトル>*" はSlack mrkdwn記法。太字＋リンク付きタイトルにしている
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
    """ダイジェストをSlackへ投稿する。

    投稿に成功した場合のみ True を返す（呼び出し元のmain.pyはこれを見て、
    「実際に投稿できた記事だけ」posted_history.jsonに記録する）。
    以下のケースは全てFalseを返す＝履歴には記録されない:
      - 要約が付いた記事が1件も無い
      - DRY_RUNモードで実際には投稿していない
      - Slack API呼び出し自体がエラーになった（チャンネルID誤り・権限不足等）
    """
    # 要約生成に失敗した記事（summaryが空）は投稿対象から除外する
    articles = [a for a in articles if a.get("summary")]
    if not articles:
        print("[slack_poster] no summarized articles to post, skipping")
        return False

    blocks = _build_blocks(articles)

    if config.DRY_RUN:
        # 実際にはSlackへ送らず、投稿予定内容をログに出すだけ
        print(f"[slack_poster] (DRY_RUN) would post {len(articles)} articles to {config.SLACK_CHANNEL_ID}")
        for a in articles:
            print(f"  - [{a['source']}] {a['title']}\n    {a['summary']}\n    {a['url']}")
        return False

    client = WebClient(token=config.SLACK_BOT_TOKEN)
    try:
        client.chat_postMessage(
            channel=config.SLACK_CHANNEL_ID,
            blocks=blocks,
            text="今週のITトレンド記事",  # 通知欄等に表示されるフォールバックテキスト
            # unfurl_links/unfurl_media: メッセージ中のURLをSlackが自動でプレビューカード化
            # する機能を無効化している。有効のままだと、記事ごとのリンクの下に
            # 別途プレビューカードが並んでしまい、ダイジェスト本文と内容が重複して見える。
            unfurl_links=False,
            unfurl_media=False,
        )
        return True
    except SlackApiError as e:
        print(f"[slack_poster] post failed: {e}")
        return False
