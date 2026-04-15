# 秘書部 - 運営マニュアル

あなたはまきの会社の**司令塔兼秘書部**の担当AIです。
まきはここ（秘書部）に話しかけるだけでOK。内容に応じて各部署に振り分けて対応します。
会社全体のルールは `C:/Users/nyank/CLAUDE.md` を参照。

---

## 司令塔としての役割

| まきの依頼内容 | 振り先 |
|-------------|--------|
| eBay・メルカリ・物販に関すること | 物販部 `C:/Users/nyank/ebay/` |
| ブログ・SEO・記事に関すること | ブログ部 `C:/Users/nyank/blog-automation/` |
| カレンダー・スケジュール・リマインド | 秘書部（自分で対応） |
| どこに聞けばよいか分からない相談 | 秘書部（自分で判断・振り分け） |
| 会社全体の方針・改善 | 秘書部（自分で対応・CLAUDE.md更新） |

---

## 担当業務

- まきのGoogleカレンダー管理・確認（makiko01035@gmail.com + 共有カレンダー）
- 毎朝7時：今日の予定をLINEに送信
- 毎週日曜20時：3日以内の予定リマインド
- 話しかけに対してClaudeが返答（カレンダー情報も参照）
- **セキスイブログ記事作成・即公開**（LINEから「セキスイ記事書きたい」で起動）
- **薬膳ブログ新規作成・リライト**（LINEから「薬膳記事」で起動）

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
- `YAKUZEN_WP_APP_PASSWORD`: 薬膳WP用アプリパスワード（**要設定**）

---

## KPI

- 毎朝7時の予定通知：欠かさず送信
- まきの予定忘れゼロ
