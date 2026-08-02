"""キーローテーション時期のチェック・通知。
main.py本体とは独立した key_rotation_reminder.yml から実行される。

key_rotation.json に記録された「前回キーを再発行した日付」から
KEY_ROTATION_MONTHS（6ヶ月）経過していないかを毎月チェックし、
経過していれば管理者にSlack DMで再発行を促す。
"""
import json
import os
from datetime import datetime, timezone

import config
import notifier


def main() -> None:
    # key_rotation.jsonが存在しない（＝初期セットアップがまだ）場合は何もしない
    if not os.path.exists(config.KEY_ROTATION_PATH):
        print(f"[check_key_rotation] {config.KEY_ROTATION_PATH} not found, skipping")
        return

    try:
        with open(config.KEY_ROTATION_PATH, encoding="utf-8") as f:
            data = json.load(f)
        last_rotated = datetime.fromisoformat(data["last_rotated_at"]).replace(tzinfo=timezone.utc)
    except Exception as e:  # noqa: BLE001
        print(f"[check_key_rotation] failed to read {config.KEY_ROTATION_PATH}: {e}")
        return

    # 1ヶ月を30日として簡易的に経過月数を計算する
    months_elapsed = (datetime.now(timezone.utc) - last_rotated).days / 30

    if months_elapsed >= config.KEY_ROTATION_MONTHS:
        print(f"[check_key_rotation] {months_elapsed:.1f} months elapsed, notifying")
        notifier.notify_key_rotation_due(int(months_elapsed))
    else:
        print(f"[check_key_rotation] {months_elapsed:.1f} months elapsed, not due yet")


if __name__ == "__main__":
    main()
