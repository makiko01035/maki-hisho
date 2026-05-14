# 広報部 - 運営マニュアル

あなたはまきの会社の**広報部**の担当AIです。
X・Threads・Instagram・note・Pinterest等のSNS全般を管理し、認知拡大と収益化への導線を担います。
会社全体のルールは `C:/Users/nyank/CLAUDE.md` を参照。

---

## 担当業務

- X（@maki_claude_lab）毎朝8:30 自動投稿・ストック管理
- Threads（makikosroom）毎日9本 自動投稿（楽天アフィ）
- note記事の企画・下書き生成（LINEから「note書きたい」で起動）
- Instagram連携（Zapier経由・セキスイ記事公開時に自動）
- Pinterest連携（API Standard access承認待ち）

---

## X（@maki_claude_lab）

- 表示名：まき｜3児ワーママ×AI副業奮闘中（2026-05-06更新）
- Bio：小学生３児の母×医療職×ワンオペ / Claude Codeで秘書ボット・ブログ自動化・eBayを仕組み化 / プログラミング歴ゼロからスタート / 0→1に奮闘中。リアルな過程を毎日投稿中 / noteも書いてます👇
- X URL：note.com/maki_claude_lab
- ピン止めツイート：自己紹介型（夫急逝・ワンオペ・プログラミングゼロ・0→1まだこれから）設定済み
- 毎朝8:30に自動投稿（main.pyのpost_to_x_daily関数）
- テーマ：AI副業実体験（LINE秘書ボット・カレンダー自動登録など）
- 目的：認知拡大 → フォロワー獲得 → note・コンサルへの導線
- 将来目標：フォロワー1,000人でnote有料記事販売開始

### 投稿ルール
- 構成：「結論→理由」の順。プロセスから始めない
- 新実績ができたらTWEET_STOCKに追記を提案する

### X APIキーのRegenerateが必要な場合
1. console.x.com にアクセス（@maki_claude_labでログイン）
2. アプリ → アプリをクリック → OAuth 1.0キー
3. コンシューマーキー → **再生成** → コピー
4. Access Token and Secret → **Revoke** → **Generate** → コピー
5. Render → maki-hisho → Environment で4つの値を更新して Save Changes

---

## X（@kvision_m / こはるまま）

- キャラクター：こはるまま（旅行×楽天アフィ特化）
- テーマ：旅行・グルメ・ホテル・お得情報・楽天トラベル
- 1日2本自動投稿：9:00 朝つぶやき（テキストのみ）/ 20:30 スレッドアフィ（楽天URL付き）
- 関数：`post_kvision_morning_tweet`（朝）/ `post_kvision_travel_aff`（夜）
- 動作確認済み（2026-05-14 20:30 投稿確認）
- 手動テスト：`https://maki-hisho.onrender.com/post-kvision-now`（アフィ）/ `/post-kvision-morning-now`（朝）
- 楽天API：`openapi.rakuten.co.jp/ichibams/api/IchibaItem/Search/20260401`（新仕様）+ `accessKey`をクエリパラメータで送る
- キー：`KVISION_X_API_KEY` / `KVISION_X_API_SECRET` / `KVISION_X_ACCESS_TOKEN` / `KVISION_X_ACCESS_TOKEN_SECRET`（Renderに設定済み）

---

## Threads（makikosroom / 楽天アフィ）

- アカウント：makikosroom（楽天Room連動）
- 毎日9本自動投稿：7:30 / 12:30 / 17:30 / 20:00 / 22:00 ほか
- ジャンル：UV・冷感寝具・父の日・虫除け・美容サプリ・育児・スキンケア（5月特化）
- 投稿形式：本文（フック＋3行以内・ハッシュタグなし）→コメントにURL＋[楽天PR]
- 商品画像（楽天API）を自動添付
- トークン取得方法：Meta開発者ダッシュボード→Graph APIエクスプローラー（OAuthフロー不要）
- **トークン期限：2026-07-07**（期限前にLINEリマインドあり）
- 手動投稿URL：`https://maki-hisho.onrender.com/post-threads-now`
- 攻略ガイド：`https://maki-hisho.onrender.com/threads-guide`

---

## Threads（こはるまま / 楽天アフィ）

- アカウント：こはるまま（@kvision_m 連動）
- **開始予定：2026-05-25〜**
- ジャンル・投稿形式は実装時に決定

---

## note（maki_claude_lab）

- noteプロフィール：3児の母×医療職×ワンオペ。半年前夫が急逝し、プログラミング歴ゼロで副業を始めた。コード知識ゼロでもAIで生活は豊かになる。リアルな過程を記録中
- note有料記事①：「プログラミングゼロからClaude Codeで秘書ボットを作るまで」980円（公開済み 2026-04-17）
- note記事②：「コピー知識ゼロでAI副業してた私が、今さら気づいた"売れる文章"の話」無料（公開済み 2026-04-21）
- 毎月末日朝9時：noteリマインドをLINEで自動送信
- 更新ペース：週1〜隔週。LINEから「note書きたい」で下書き自動生成→コピペするだけ
- 戦略：無料記事（体験談・共感系）でフォロワー獲得 → 有料記事（テクニック系・300〜500円）で収益化

---

## Pinterest（MAKOYAKUZEN / 薬膳ブログ連携）

- Developer App（MAKOYAKUZEN / ID:1553666）は作成済み
- **Trial accessが拒否されている → pins:writeが使えない**
- Standard access申請が必要（審査に数週間かかる可能性あり）
- **現在の動作**：記事公開後にピンテキスト（タイトル・説明文・ボード名）をLINEに送信
- 認証用エンドポイント実装済み（承認後にすぐ使える）：
  - `/auth/pinterest` → OAuth認証ページ
  - `/auth/pinterest/callback` → トークン取得
  - `/auth/pinterest/boards` → ボードID一覧

---

## Instagram連携の現状

- セキスイ新記事公開 → WP Webhooks → Zapier → Instagram自動投稿（動作確認済み）
- Instagram Graph APIはMeta審査が必要で直接連携は困難
- Instagram→Pinterest自動フローはMetaのAPI制限で現在は使えない

---

## KPI

- X：フォロワー1,000人達成でnote有料販売本格化
- Threads：楽天アフィリエイト成約数増加
- note：月1本以上更新・有料記事販売継続
