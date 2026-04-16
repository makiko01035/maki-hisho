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
| X投稿・SNS・広報に関すること | 広報部（同フォルダ内） |
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

## Claude CodeのMCP連携

| サービス | 状態 | 備考 |
|---------|------|------|
| Google Drive | 連携済み | claude.ai経由 |
| Notion | 連携済み（2026-04-15） | `C:/Users/nyank/.claude/.mcp.json` に設定済み |

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
- `YAKUZEN_WP_APP_PASSWORD`: 薬膳WP用アプリパスワード（**設定済み**）
- `PINTEREST_APP_ID`: 1553666（MAKOYAKUZEN）
- `PINTEREST_APP_SECRET`: 未設定（Trial拒否のため保留）
- `PINTEREST_REFRESH_TOKEN`: 未設定（同上）
- `PINTEREST_BOARD_SEASONAL` / `PINTEREST_BOARD_RECIPE` / `PINTEREST_BOARD_BASICS` / `PINTEREST_BOARD_QUALIF`: 未設定
- `X_API_KEY` / `X_API_SECRET` / `X_ACCESS_TOKEN` / `X_ACCESS_TOKEN_SECRET`: 広報部X自動投稿用（設定状況は広報部CLAUDE.md参照）

---

## LINEから使える機能（main.py）

| 送るキーワード | 動作 |
|-------------|------|
| `薬膳記事` | 薬膳ブログメニュー表示（新規/リライト選択） |
| → `1` または `新規作成` | テーマを聞いてきて記事作成→即公開 |
| → `2` または `リライト` | 季節に合った記事を自動選択→リライト→即公開 |
| `セキスイ記事` | セキスイブログ記事作成フロー起動 |
| 画像送信 | チラシからイベント情報を読み取り |
| `登録して` | 読み取ったイベントをGoogleカレンダーに登録 |
| `〇〇の期限 4月10日` | 申込期限をカレンダーに登録＋リマインド設定 |

## Pinterest連携の現状（2026-04-15時点）

- Pinterest Developer App（MAKOYAKUZEN / ID:1553666）は作成済み
- **Trial accessが拒否されている → pins:writeが使えない**
- Standard access申請が必要（審査に数週間かかる可能性あり）
- **現在の動作**：記事公開後にピンテキスト（タイトル・説明文・ボード名）をLINEに送信
- 認証用エンドポイントは実装済み（承認後にすぐ使える）：
  - `/auth/pinterest` → OAuth認証ページ
  - `/auth/pinterest/callback` → トークン取得
  - `/auth/pinterest/boards` → ボードID一覧

## Instagram連携の現状

- Instagram Graph APIもMeta審査が必要でPinterestと同様に困難
- Instagram→Pinterest自動フローはMetaのAPI制限で現在は使えない
- 代替案：Zapierを使えばWP新記事→Instagram→Pinterestの自動化が可能（画像作成は手動）

---

## 広報部

- Xアカウント：@kvision_m
- 毎朝8:30に自動投稿
- テーマ：AI副業実体験（LINE秘書ボット・カレンダー自動登録など）
- 目的：認知拡大 → フォロワー獲得 → note・コンサルへの導線
- 将来目標：フォロワー1,000人でnote有料記事販売開始

---

## トラブルシューティング

### Google認証エラー（「登録中にエラーが発生しました」）
- `https://maki-hisho.onrender.com/check-creds` を開く
- `OK` → 認証情報は正常、別の原因
- `JSON parse error` → RenderのGOOGLE_CREDENTIALSが壊れている
  → `credentials_for_render.txt` の内容をRenderに貼り直す

---

## KPI

- 毎朝7時の予定通知：欠かさず送信
- まきの予定忘れゼロ
