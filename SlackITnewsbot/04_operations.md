# Slack ITニュース要約Bot 運用・保守資料

作成日: 2026年7月21日
関連: [企画書](01_proposal.md) / [設計書](02_design.md) / [実装書](03_implementation.md) / [仕様書](slack_it_news_bot_dev_spec_1.md)

このドキュメントは、開発担当者（自分）が本Botの立ち上げ・運用のために
実際に行う作業と、稼働後の保守ルールをまとめたものです。

---

## 1. 初期セットアップで自分が行う作業

### 1.1 Anthropic APIキーの取得

1. https://console.anthropic.com （Claude Developer Platform）にアクセスし、
   会社用のメールアドレスでアカウントを作成（既に会社の組織アカウントが
   ある場合はそこに参加）
2. 「Billing」から支払い方法を登録する（会社カード払い/請求書払いは
   経理・総務との調整が必要な場合あり。事前に社内確認）
3. 可能であれば、本Bot専用の **Workspace**（Anthropic Console内の
   プロジェクト単位の区切り）を新規作成し、他のプロジェクトと
   キー・利用量を分離する
   - 個人検証用と本番運用用でWorkspaceを分けると、後述の利用量監視や
     キーローテーションがしやすくなる
4. 「API Keys」からキーを発行する（`sk-ant-...`の形式）
   - 名前は `slackitnewsbot-prod` のように用途が分かるものにする
   - 発行直後しか全文表示されないため、その場でGitHub Secretsに登録する
     （1.4節参照）
5. 「Limits」または「Spend limits」から、想定コスト（月$3〜5程度）に
   余裕を持たせた上限額（例: 月$20）を設定し、想定外の高額請求を防ぐ

### 1.2 Slack Appの作成・Bot Token発行

実装書（[03_implementation.md](03_implementation.md) 4節）記載の手順どおり。
ワークスペースの管理者権限が必要なため、自分が管理者でない場合は
情報システム部門等に依頼する。

### 1.3 Qiita Access Tokenの取得（任意）

1. Qiitaにログイン → 設定 → アプリケーション
2. 「個人用アクセストークン」から新規発行（スコープは `read_qiita` のみで可）
3. 発行したトークンをGitHub Secretsに登録

未設定でも動作するが、APIレート制限が厳しくなる（週1回の実行であれば
未設定でも実用上問題ない可能性が高い。まず未設定で稼働させ、
レート制限エラーが出た場合に取得する運用でよい）

### 1.4 GitHub Secretsへの登録

対象リポジトリの Settings → Secrets and variables → Actions で、
以下を登録する。

| Secret名 | 値 |
|---|---|
| `ANTHROPIC_API_KEY` | 1.1で発行したキー |
| `SLACK_BOT_TOKEN` | 1.2で発行した`xoxb-`トークン |
| `SLACK_CHANNEL_ID` | 投稿先チャンネルのID |
| `SLACK_ADMIN_USER_ID` | コスト・キーローテーション通知を受け取る特定ユーザーのID |
| `QIITA_ACCESS_TOKEN` | 1.3で発行したトークン（任意） |

`SLACK_ADMIN_USER_ID`は、Slackで対象ユーザーのプロフィールを開き、
「その他」メニューから「メンバーIDをコピー」（`U`で始まるID）で取得できる。
追加のBot Token Scopeは不要（`chat:write`があれば、ユーザーIDを
`channel`に直接指定してDM送信できる）。

リポジトリの管理者権限が必要。自分がAdminでない場合はリポジトリ管理者に依頼する。

### 1.5 key_rotation.json の初期設定

初回セットアップ時に、リポジトリ内の`key_rotation.json`へ
APIキー発行日を記録しておく（未設定だと初回チェック時に
誤って「6ヶ月経過」と判定される可能性があるため）。

```json
{"last_rotated_at": "2026-07-21"}
```

---

## 2. APIキーの管理方法

- **キーは環境変数・Secrets以外の場所に絶対に置かない**
  - ローカル検証時は `.env` ファイルを使い、`.gitignore` に `.env` を
    必ず追加する（誤コミット防止）
  - Slack・チャットツール・ドキュメントに平文で貼らない
- **個人検証用キーと本番用キーを分ける**
  - ローカルでのプロンプト検証（1節参照）には自分の個人検証用キーを使い、
    本番のGitHub Actionsには専用の本番キーを使う
  - こうしておくと、検証中の誤操作が本番の利用量・Spend limitに
    影響しない
- **キーローテーション（自動リマインド付き）**
  - 半年に1回を目安に、本番キーを再発行し、GitHub Secretsを更新する
    （旧キーはAnthropic Console上で無効化する）
  - `key_rotation_reminder.yml`ワークフローが毎月チェックし、
    最終ローテーション日（`key_rotation.json`の`last_rotated_at`）から
    半年経過していたら`SLACK_ADMIN_USER_ID`宛にDMで通知する
    （[仕様書](slack_it_news_bot_dev_spec_1.md) 8.2節）。
    カレンダーへの登録は不要になるが、初回セットアップ時に
    `last_rotated_at`の初期値（キー発行日）を設定しておくこと
  - 実際にキーを再発行・入れ替えたら、`key_rotation.json`の
    `last_rotated_at`を必ず更新する（更新を忘れると翌月も通知が来る）
  - 開発担当者の異動・退職時は、その時点で速やかに本番キーを
    再発行・入れ替える
- **万が一の漏洩時の対応**
  - Anthropic Consoleから該当キーを即座に無効化（Revoke）
  - 新しいキーを発行し、GitHub Secretsを更新
  - 利用量ログ（Console上のUsage）を確認し、想定外の利用がないか確認

---

## 3. コスト監視

- **自動通知**: `SLACK_ADMIN_USER_ID`に設定した特定ユーザー宛に、
  週次実行のたびにその回のコスト概算（Sonnet 5/Haiku 4.5のトークン代＋
  Web検索回数分）がSlack DMで届く。月をまたいだ最初の実行では、
  前月分の合計も併せて届く（[仕様書](slack_it_news_bot_dev_spec_1.md) 8.1節）
  - あくまで自前計算による**概算**（Anthropic公式の請求額とは
    誤差が生じうる）。正確な金額はAnthropic Consoleで確認する
- Anthropic Console の「Usage」ページでも、モデル別・日別の利用量を
  随時確認できる。月1回程度、Slack通知の概算値とConsole上の実額を
  突き合わせて大きくズレていないか確認するとよい
  - 想定コスト: 月$3〜5程度（[企画書](01_proposal.md) 3節参照）
  - 大きく乖離する場合（想定の2倍以上等）は、原因を確認する
    - Web検索の実行回数が想定より多くないか（Sonnet 5が候補記事だけで
      判断がつかず、検索を繰り返している可能性）
    - 記事本文の取得漏れ・エラーで無駄なリトライが発生していないか
    - 選定件数が恒常的に上限の10件に張り付いていないか
- Sonnet 5の導入価格（$2/$10 per MTok、〜2026年8月31日）が終了すると
  標準価格（$3/$15）に切り替わるため、`cost_tracker.py`の`PRICING`を
  9月以降に更新する（忘れるとDM通知の概算が実際より安く出続ける）
- 1.1で設定したSpend limitに達すると以降のAPI呼び出しが失敗するため、
  上限に近づいた場合はSlack投稿が止まる。週次のコスト通知DMで
  気づける状態にしておく

---

## 4. 保守運用ルール

### 4.1 週次実行の確認

- GitHub Actionsの実行結果は、リポジトリの「Actions」タブから確認できる
- 失敗時はGitHubがリポジトリ管理者にメール通知する設定がデフォルトで
  有効（無効化されていないか一度確認しておく）
- 失敗した場合、まずは `workflow_dispatch`（手動実行ボタン）で
  再実行し、一時的なAPIエラー等でないか切り分ける

### 4.2 情報源（RSS/API）の定期点検

- 四半期に1回を目安に、各collectorの疎通確認を行う
  （Zenn/Publickey/ITmedia/CNET Japan/TechCrunchのフィードURL、
  Qiita/Hacker NewsのAPIエンドポイント）
- フィードURLが変わった・停止した場合は、[設計書](02_design.md) 2.4節の
  `RSS_FEEDS` を更新する（[企画書](01_proposal.md)にある
  TechCrunch Japan除外の経緯のように、配信が不安定なソースは
  除外・代替を検討する）

### 4.3 プロンプトのチューニング

- 選定結果（`selection_reason`）と実際に投稿された記事は、
  当面はGitHub Actionsの実行ログで確認できるようにしておく
  （必要であれば、選定理由をログとして`posted_history.json`とは
  別ファイルに残すことも検討。個人情報は含まれないため保存の
  ハードルは低い）
- 「金融・製造業のIT動向」「海外大手AI企業/GAFAM」の記事が思ったより多い/少ない、
  重複除外が効きすぎている/緩すぎる、といった傾向が見えたら、
  `prompts/screen_candidates.txt` の記述を調整する
- プロンプト変更は影響範囲が大きいため、変更後1〜2週間は
  選定結果を目視確認する

### 4.4 posted_history.json の運用

- `history.py` が投稿の都度、直近分を追記し、指定週数（デフォルト3週間）
  より古いものを自動的に間引く設計のため、通常は肥大化しない
- GitHub Actionsが自動コミットする仕組みのため、手動でこのファイルを
  編集する必要は基本的にない。誤って大きく壊れた場合は、
  直近のコミット履歴から復元する

### 4.5 依存パッケージの更新

- `requirements.txt` に記載のライブラリ（`anthropic` / `requests` /
  `feedparser` / `trafilatura` / `slack_sdk` / `python-dateutil`）は、
  半年に1回を目安にバージョン確認・更新する
- 余力があれば、GitHubのDependabotを有効化し、更新PRを自動作成させる
  運用に切り替えるとよい

### 4.6 障害時のロールバック

- ワークフロー変更（プロンプト・collector・workflow yml等）で
  問題が起きた場合は、GitHubの当該コミットをrevertして前の状態に戻す
- `workflow_dispatch` で手動実行し、正常化を確認してから
  週次自動実行に戻す

---

## 5. セキュリティ・ガバナンス上の注意

- **Web検索クエリとして送信される内容**: `screen_candidates()`が
  Web検索を行う際、候補記事のタイトル等がプロンプトに含まれた状態で
  Anthropicの検索基盤にクエリが送られる。社内限定情報（未公表の
  提携先名、社内システム名等）をプロンプトや候補記事データに
  含めないよう注意する
- **投稿内容の最終責任**: AIによる要約・選定のため、まれに事実誤認や
  不適切な表現を含む可能性がある。投稿は自動化されるが、
  週1回程度は投稿内容に目を通す運用にする
- **承認・変更の記録**: プロンプトや情報源、モデル選定などの
  意思決定は、各ドキュメントの「未確定事項」「確定済み」欄に
  日付とともに追記していく（[仕様書](slack_it_news_bot_dev_spec_1.md)
  14節の運用を踏襲する）

---

## 6. チェックリスト（初期セットアップ用サマリー）

- [ ] Anthropic Consoleでアカウント作成・Workspace分離・Spend limit設定
- [ ] Anthropic APIキー発行（本番用）
- [ ] Slack App作成・Bot Token発行・チャンネル確定
- [ ] コスト・キーローテーション通知を受け取るユーザーのメンバーIDを取得
- [ ] Qiita Access Token発行（任意）
- [ ] GitHub Secrets登録（`ANTHROPIC_API_KEY` / `SLACK_BOT_TOKEN` /
      `SLACK_CHANNEL_ID` / `SLACK_ADMIN_USER_ID` / `QIITA_ACCESS_TOKEN`）・
      Actionsの失敗通知が有効か確認
- [ ] `key_rotation.json`にキー発行日を初期設定
- [ ] `.env` を `.gitignore` に追加（ローカル検証用）
- [ ] 月次でSlack通知の概算コストとAnthropic Console実額を突き合わせる運用を関係者と合意
- [ ] Sonnet 5導入価格終了（2026年8月31日）後に`cost_tracker.py`の`PRICING`を更新する予定をカレンダー等に登録
- [ ] 四半期フィード点検をカレンダー等に登録
