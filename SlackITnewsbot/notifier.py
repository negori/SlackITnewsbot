"""特定ユーザー（SLACK_ADMIN_USER_ID）へのSlack DM通知。

ユーザーIDを chat.postMessage の channel にそのまま渡すとDM相当になる
（追加のBot Token Scopeは不要。chat:write のみで動作する）。

用途は3つ:
  - 週次のコスト概算通知（毎回）
  - 月次のコスト概算通知（月が変わった直後のみ）
  - APIキーのローテーション時期の通知（check_key_rotation.pyから呼ばれる）
"""
from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError

import config


def _client() -> WebClient:
    return WebClient(token=config.SLACK_BOT_TOKEN)


def _send(text: str) -> None:
    """DRY_RUN中は実際には送らずログ出力のみ。
    本番実行時は失敗してもエラーで止めず、ログに残すだけにする
    （通知の失敗でBot本体の処理（投稿・履歴更新等）を巻き込みたくないため）。"""
    if config.DRY_RUN:
        print(f"[notifier] (DRY_RUN) would DM {config.SLACK_ADMIN_USER_ID}: {text}")
        return
    try:
        _client().chat_postMessage(channel=config.SLACK_ADMIN_USER_ID, text=text)
    except SlackApiError as e:
        print(f"[notifier] failed to send DM: {e}")


def notify_weekly_cost(cost_usd: float) -> None:
    _send(f"[週次] 今週のClaude API利用額（概算）: ${cost_usd:.4f}")


def notify_monthly_cost(cost_usd: float) -> None:
    _send(f"[月次] 前月分のClaude API利用額（概算合計）: ${cost_usd:.4f}")


def notify_key_rotation_due(months_elapsed: int) -> None:
    _send(
        f"APIキーのローテーション時期です（前回ローテーションから約{months_elapsed}ヶ月経過）。"
        "Anthropic Console / Slack Appでキーを再発行し、GitHub Secretsを更新してください。"
        "更新後は key_rotation.json の last_rotated_at も忘れずに更新してください。"
    )
