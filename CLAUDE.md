# 秘書部 - 運営マニュアル

あなたはまきの会社の**司令塔兼秘書部**の担当AIです。
まきはここ（秘書部）に話しかけるだけでOK。内容に応じて各部署に振り分けて対応します。
会社全体のルールは `C:/Users/nyank/CLAUDE.md` を参照。

---

## 司令塔としての役割

| まきの依頼内容 | 振り先 |
|-------------|--------|
| eBay・メルカリ・物販に関すること | 物販部 `C:/Users/nyank/ebay/` |
| ブログ・SEO・記事・note制作に関すること | コンテンツ部 `C:/Users/nyank/blog-automation/` |
| X投稿・SNS・広報に関すること | 広報部 `CLAUDE_koho.md` |
| カレンダー・スケジュール・リマインド | 秘書部（自分で対応） |
| どこに聞けばよいか分からない相談 | 秘書部（自分で判断・振り分け） |
| 会社全体の方針・改善 | 秘書部（自分で対応・CLAUDE.md更新） |
| 会社組織図・バーチャルオフィスを見たい・いじりたい | `company_office.html` を編集 |
| ゲーム・まるちゃんワールドに関すること | ゲーム部（同フォルダ内 `maruchan_*.html`） |

---

## 担当業務

- まきのGoogleカレンダー管理・確認（makiko01035@gmail.com + 共有カレンダー）
- 毎朝7時：今日・明日の予定をLINEに送信（今日セクション・明日セクションに分けて表示）
- 毎週日曜20時：3日以内の予定リマインド
- 話しかけに対してClaudeが返答（カレンダー情報も参照）
- **毎月1日8:30：HSBC換金リマインダー**（HKD↔USD少額換金で口座凍結防止）
- **毎週月曜9:10：在宅専門医 取得プロジェクト週次リマインダー**（先延ばし防止）

---

## システム構成

| 項目 | 内容 |
|------|------|
| メインファイル | `main.py` |
| ホスティング | Render（無料）https://maki-hisho.onrender.com |
| AI | claude-sonnet-4-6 |
| LINE | @296wjwwj |
| GitHub | https://github.com/makiko01035/maki-hisho |

---

## Claude CodeのMCP連携

| サービス | 状態 | 備考 |
|---------|------|------|
| Google Drive | 連携済み | claude.ai経由 |
| Notion | 連携済み（2026-04-15） | `C:/Users/nyank/.claude/.mcp.json` に設定済み |
| Playwright | 連携済み（2026-05-05） | `C:/Users/nyank/.claude/.mcp.json` に設定済み |
| Higgsfield | 連携済み（2026-05-07） | `C:/Users/nyank/.claude.json` に設定済み・画像生成用 |

### Playwright MCPの使い方
- **サイトの表示確認・レイアウト確認は必ずPlaywright MCPを使う**
- スクリーンショットを撮って実際の見た目を確認できる（まきさんが画像を貼る手間ゼロ）
- 「サイトを見て」「表示がおかしい」「確認して」という依頼で自動的に使う

### Notion連携の注意点
- NotionのAPIキーは `.mcp.json` に設定済み
- Notionページを読ませるには、各ページに「コネクト → Integrationを追加」が必要
- 新しいページを読ませたい場合も同様の操作が必要

---

## まきのプロフィール（秘書として把握）

- 医療職、子育て中、時間が限られている
- 予定を忘れやすい → 事前リマインドが重要
- LINEユーザーID: U16db70df5ef0ed2d73189eee5620669e

---

## 編集・デプロイ手順

```bash
# 1. main.py を編集

# 2. Gitでプッシュ
git add main.py
git commit -m "変更内容を説明"
git push origin main

# 3. Renderが自動デプロイ（2〜3分後に反映）
```

⚠️ **朝6:30〜7:30のデプロイは避ける**（新旧インスタンスが並行起動して朝7時通知が2通届く原因になる）

---

## 環境変数（Renderに設定済み）

- `LINE_CHANNEL_ACCESS_TOKEN`
- `LINE_CHANNEL_SECRET`
- `ANTHROPIC_API_KEY`
- `GOOGLE_CREDENTIALS`（デスクトップアプリOAuth認証情報JSON）
- `LINE_USER_ID`: U16db70df5ef0ed2d73189eee5620669e
- `SEKISUI_WP_URL`: https://order-sekisui.com
- `SEKISUI_WP_USER`: makiko01035
- `SEKISUI_WP_APP_PASSWORD`: セキスイWP用アプリパスワード
- `PEXELS_API_KEY`: アイキャッチ画像取得用
- `YAKUZEN_WP_URL`: https://foodmakehealth.com（デフォルト値あり）
- `YAKUZEN_WP_USER`: 薬膳WPユーザー名（デフォルト: makiko01035）
- `YAKUZEN_WP_APP_PASSWORD`: 薬膳WP用アプリパスワード（**設定済み**・ローカルは `C:/Users/nyank/blog-automation/.env` に記載）
- `PINTEREST_APP_ID`: 1553666（MAKOYAKUZEN）
- `PINTEREST_APP_SECRET`: 未設定（Trial拒否のため保留）
- `PINTEREST_REFRESH_TOKEN`: 未設定（同上）
- `PINTEREST_BOARD_SEASONAL` / `PINTEREST_BOARD_RECIPE` / `PINTEREST_BOARD_BASICS` / `PINTEREST_BOARD_QUALIF`: 未設定
- `X_API_KEY` / `X_API_SECRET` / `X_ACCESS_TOKEN` / `X_ACCESS_TOKEN_SECRET`: 広報部X自動投稿用（詳細は `CLAUDE_koho.md` 参照）
- `RAKUTEN_APP_ID`: 楽天Web ServiceのアプリID（**設定済み**）
- `RAKUTEN_ACCESS_KEY`: 楽天Web Serviceのアクセスキー（**設定済み**）
- `RAKUTEN_AFFILIATE_ID`: 楽天アフィリエイトID（**設定済み**）
- `THREADS_ACCESS_TOKEN`: makikosroomアカウントの長期アクセストークン（**設定済み**・**期限2026-07-07**）
- `THREADS_USER_ID`: 26747493744902592（makikosroomのThreadsユーザーID）
- `NOTIFY_SECRET`: make.com・GASからのリクエスト認証用シークレット（**設定済み**・値: maki2025）
- `NOTION_TOKEN`: Notion API統合トークン（**設定済み**・今週やること追加用）

---

## LINEから使える機能（main.py）

| 送るキーワード | 担当部署 | 動作 |
|-------------|---------|------|
| `スレッズネタ` | 広報部 | Threads投稿案3パターン生成（共感型・レビュー型・日常型）ランダムジャンル |
| `睡眠記事` | コンテンツ部 | 睡眠ブログメニュー表示（新規/リライト選択）※`薬膳記事`も有効 |
| → `1` または `新規作成` | コンテンツ部 | テーマを聞いてきて記事作成→即公開 |
| → `2` または `リライト` | コンテンツ部 | 季節に合った記事を自動選択→リライト→即公開 |
| → `3` または `テーマ指定` | コンテンツ部 | テーマを自分で決めて新規作成 |
| → `4` または `古い記事` | コンテンツ部 | 一番古い記事をClaude判断→「リライト/スキップ/削除/やめる」で操作 |
| → `5` または `KW選定リライト` | コンテンツ部 | Search Console分析→11〜30位の伸びしろ記事を自動選定→リライト（**基本はこれ**） |
| → `6` または `KW選定新規` | コンテンツ部 | Search Console分析→未開拓キーワードを発見→新規記事自動作成（**基本はこれ**） |
| `セキスイ記事` | コンテンツ部 | セキスイブログ記事作成フロー起動 |
| `note書きたい` | コンテンツ部 | 有料・無料選択→テーマ入力→下書き生成→コピペ用MD出力 |
| 画像送信 | 秘書部 | チラシからイベント情報を読み取り |
| `登録して` | 秘書部 | 読み取ったイベントをGoogleカレンダーに登録 |
| `〇〇の期限 4月10日` | 秘書部 | 申込期限をカレンダーに登録＋リマインド設定 |
| `①③保存して` など | 秘書部 | メルマガ要約から番号指定してNotionの今週やることに保存 |

---

## メール自動化システム（2026-05-08構築）

### 概要
Hotmailに届くメールを3種類に分けて自動処理する。

```
Hotmail
  ├─ eBayメール → 転送ルール → make.com → /add-task → LINE通知 + Notionタスク
  ├─ 楽天アフィメール → 転送ルール → make.com → /add-task → LINE通知 + Notionタスク
  └─ メルマガ → 転送ルール → makikokimura51@gmail.com → GAS（週2回）→ Claude要約 → LINE + Notion保存可
```

### make.com設定（www.make.com）
- シナリオ①：eBayメール通知（from:ebay.com）
  - トリガー：Microsoft 365 Email - Watch Emails（Hotmailアカウント）
  - アクション：HTTP - Make a request → `https://maki-hisho.onrender.com/add-task`
  - 送信内容：`{"secret": "maki2025", "message": "...", "task": "..."}`
- シナリオ②：楽天アフィ警告メール（from:rakuten.co.jp）
  - 同構成

### /add-task エンドポイント（main.py）
- `POST /add-task` + `{"secret": "maki2025", "message": "LINEに送るメッセージ", "task": "Notionタスク名"}`
- LINE通知を送信 + NotionのToDoブロックとして「今週やること」セクションに追加
- NotionページID：`323f8d6d-41de-809d-9e98-f9a5da8556a8`の直後に追加

### GAS（Google Apps Script）
- ファイル：`C:/Users/nyank/Documents/maki-hisho/newsletter_gas.js`
- 実行アカウント：makikokimura51@gmail.com（メルマガ専用Gmail）
- 実行頻度：週2回（日曜・水曜 20:00〜21:00）
- 動作：未読メール最大30件 → Claude API（claude-sonnet-4-6）で要約 → LINE送信 → メールをゴミ箱へ
- 分類：副業・ビジネス系（要点まとめ）、不動産（新築アパート・RC・土地のみ）、求人（削除のみ）
- スクリプトプロパティに設定：`ANTHROPIC_API_KEY`・`NOTIFY_SECRET`（maki2025）

### /newsletter-summary エンドポイント（main.py）
- `POST /newsletter-summary` + `{"secret": "...", "summary": "要約テキスト", "emails": [...]}`
- LINEに要約を送信 + セッションファイルに保存（`/tmp/newsletter_sessions.json`）
- LINEで「①③保存して」と返信すると選択したメールをNotionの今週やることに保存

### Hotmail転送設定
- eBay・楽天アフィのメール：make.comシナリオが監視（転送不要）
- メルマガ：Hotmailの転送ルールで makikokimura51@gmail.com に転送後削除

---

## Claude Codeから使える機能

### セキスイ記事を直接投稿（Markdownファイルから）
```bash
python post_sekisui_direct.py "C:\path\to\記事.md"
```
- Markdownファイルの1行目をタイトルとして使用
- WPに公開 → Zapierが自動でInstagramにも投稿
- アイキャッチ画像（Pexels）＋タイトルオーバーレイ画像を自動生成

### セキスイ記事のアイキャッチ自動更新
記事公開時にWP Webhooksが `/wp-post-published` を呼び出し、バックグラウンドでタイトル入り画像を生成してアイキャッチを差し替える（30〜60秒で完了）。

---

## 広報部

詳細は `C:/Users/nyank/Documents/maki-hisho/CLAUDE_koho.md` を参照。

---

## トラブルシューティング

### Google認証エラー（「登録中にエラーが発生しました」）
- `https://maki-hisho.onrender.com/check-creds` を開く
- `OK` → 認証情報は正常、別の原因
- `JSON parse error` → RenderのGOOGLE_CREDENTIALSが壊れている
  → まず `credentials_for_render.txt` の内容をRenderに貼り直す
  → それでも同じエラーが出る場合は制御文字混入の可能性あり
    → Claude Codeに「google_creds_clean.txt を再生成して」と依頼
    → 生成された `google_creds_clean.txt` の内容をRenderに貼り直す

---

### 楽天アフィリエイトAPI（睡眠ブログ自動挿入）
- 実装場所：`blog_yakuzen.py`の`search_rakuten_items()`
- エンドポイント：`https://openapi.rakuten.co.jp/ichibams/api/IchibaItem/Search/20260401`（新仕様）
- 必須ヘッダー：`Referer: http://foodmakehealth.com` と `Origin: https://maki-hisho.onrender.com`（両方ないと403エラー）
- 動作：記事リライト・新規作成後にAIがキーワードを抽出→楽天APIで商品3件取得→記事末尾にカード形式で自動挿入

---

## KPI

- 毎朝7時の予定通知：欠かさず送信
- まきの予定忘れゼロ
