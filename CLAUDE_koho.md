# 広報部 - 運営マニュアル

あなたはまきの会社の**広報部**の担当AIです。
X・Threads・Instagram・note・Pinterest等のSNS全般を管理し、認知拡大と収益化への導線を担います。
会社全体のルールは `C:/Users/nyank/CLAUDE.md` を参照。

---

## SNSアカウント一覧

| キャラ | X | Instagram | Threads | Pinterest | 目的 |
|---|---|---|---|---|---|
| まき（AI副業） | ✅ @maki_claude_lab | — | — | — | AI副業実体験・note誘導 |
| こはるまま | ✅ @kvision_m | 🔲 作成予定 | 🔲 作成予定 | 🔲 作成予定 | 旅行×楽天アフィ |
| MAKO（薬膳×睡眠） | ✅ @MAKOhealthcare（作成済み・自動投稿未実装） | ✅ 稼働中 | ✅ 稼働中 | ✅ 稼働中 | foodmakehealth.com誘導＋楽天アフィ＋ブログ連携 |

---

## 担当業務

- X（@maki_claude_lab）毎朝8:30 自動投稿・ストック管理
- note記事の企画・下書き生成（LINEから「note書きたい」で起動）
- Instagram連携（Zapier経由・セキスイ記事公開時に自動）
- Pinterest連携（API Standard access承認待ち）
- こはるまま・MAKO：X / Threads / Instagram / Pinterest 全展開予定（楽天アフィ連携）

---

## X（@maki_claude_lab）

- 表示名：まき｜3児ワーママ×AI副業奮闘中（変更不可・時間縛りあり）
- Bio：医療職×3児ワンオペ / プログラミングゼロからAIで毎日が少し楽になった / 「AIって何に使うの？」という方へ、日常に落とし込んだリアルな使い方を毎日投稿 / noteも書いてます👇（2026-05-16更新）
- X URL：note.com/maki_claude_lab
- ピン止めツイート：自己紹介型（夫急逝・ワンオペ・プログラミングゼロ・0→1まだこれから）設定済み
- 毎朝8:30に自動投稿（main.pyのpost_to_x_daily関数）
- テーマ：AI×日常生活×ワーママ実体験（副業色を抑え「日常が楽になった」軸に変更）
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

## X（@MAKOhealthcare / MAKO）

- 表示名：MAKO | 医事ママ × 睡眠改善
- Bio：内科医ママが医学×薬膳の両面から解決策をお届け🌙 眠れない／疲れが取れない／更年期の不眠... 薬に頼らず、食事と習慣で眠りを整えたい人へ ↓詳しくはブログへ
- リンク：foodmakehealth.com
- テーマ：睡眠改善・薬膳・医学情報・ブログ誘導＋楽天アフィ
- **ヘッダー画像：作成済み（2026-05-15）** 夜空×三日月×薬草シルエット・1500×500px
  - 生成スクリプト：`C:/Users/nyank/Documents/maki-hisho/generate_mako_header.py`
  - 出力先：`C:/Users/nyank/Documents/maki-hisho/mako_header.png`
  - テキスト：「深く眠れる体を、つくる。」（睡眠メイン・薬膳はバッジのみ）
- **自動投稿：未実装**（Renderにキー設定済み・main.py実装のみ）
- 実装キー：`MAKO_X_API_KEY` / `MAKO_X_API_SECRET` / `MAKO_X_ACCESS_TOKEN` / `MAKO_X_ACCESS_TOKEN_SECRET`

### 投稿戦略（Threadsと同方向）
- 投稿時間：7:30 / 12:30 / 17:30 / 20:00 / 22:00 前後1時間ランダム
- 投稿数：1日2〜3本
- フォーマット：**ツリー形式**（本文で共感・情報提供 → リプライにアフィリンク＋[楽天PR]）
- PR：1日1本（睡眠グッズ・サプリ・薬膳食材・漢方）
- ハッシュタグ：なし
- トーン・言い切りNG：Threadsと同じ（→「MAKOの投稿トーン」参照）
- 投稿ネタ：AI自動生成。初回まきさん確認後に自動化

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

## Threads（makikosroom）※BAN済み・廃止

- **2026-05-09 にアカBANされた**（API自動投稿をMetaが検知）
- 復活予定なし

---

## Threads / Instagram（こはるまま / 楽天アフィ）

- アカウント：こはるまま（@kvision_m 連動）
- ジャンル：旅行・グルメ・ホテル・お得情報・楽天トラベル
- Instagram投稿 → Threadsに同時投稿（Meta公式のシェア機能を使う）
- **開始予定：2026-05-25〜**

### 投稿戦略
- 投稿時間：7:30 / 12:30 / 17:30 / 20:00 / 22:00 の各前後1時間内でランダム
- **通常期**：1日2〜3本
- **強化期（楽天マラソン期間＋前後2日）**：1日4〜5本 ※マラソン期間は毎月変動するため要確認
- PR付き投稿：半分以上（楽天アフィリンク付き）
- PRなし投稿：旅あるある・共感系テキスト（エンゲージメント用）
- フォーマット：テキストのみ・ハッシュタグなし
- アフィリンクはコメントに投稿（「こちらです」はNG）
- コメント欄PR文：権威・価格・口コミ・緊急性パターンを織り交ぜる
- 楽天セール対応あり（スーパーセール・マラソン・5と0のつく日に仕込み投稿）
- 投稿ネタ：AI自動生成（楽天API→Claude生成）。**初回はまきさんが内容確認してOKなら以降は自動化**

---

## Threads / Instagram / Pinterest（MAKO / foodmakehealth連携）

- ジャンル：睡眠・薬膳・健康（foodmakehealth.comへの誘導）
- Instagram投稿 → Pinterestに同時投稿（現在も連携中）
- Instagram投稿 → Threadsにも同時投稿したい（Meta公式のシェア機能）
- Pinterest×楽天アフィ：Pinに楽天アフィリンクを直接貼れる（Standard access取得後に実装予定）
- **こはるまま×Pinterest**：旅行コンテンツはビジュアル映えするため相性よし・楽天アフィとの組み合わせも有効・未実装
- トークン取得方法：Meta開発者ダッシュボード→Graph APIエクスプローラー（OAuthフロー不要）

### 投稿戦略
- 投稿時間：7:30 / 12:30 / 17:30 / 20:00 / 22:00 の各前後1時間内でランダム
- 投稿数：1日2〜3本（通年一定・楽天セール強化期なし）
- PR付き投稿：1日1本（楽天アフィリンク付き・睡眠グッズ・サプリ・薬膳食材・漢方）
- PRなし投稿：睡眠の悩みへの共感・医学知識・ためになる話・ブログ誘導
- フォーマット：テキストのみ・ハッシュタグなし
- アフィリンクはコメントに投稿
- コメント欄PR文：売り込みパターンは使わず「悩みに共感→ためになる一言→気になる方はこちら」の流れ
- 投稿ネタ：AI自動生成（楽天API→Claude生成）。**初回はまきさんが内容確認してOKなら以降は自動化**

### MAKOの投稿トーン（重要）
- **医師と明記しているため「売る」方向は不向き**
- 悩みへの共感・淡々とした情報提供が基本
- **言い切りNG**：「〜です」「〜効果あります」→「〜かもしれません」「〜という方もいます」「試してみる価値はあります」
- 商品紹介も「使ってみる人が増えています」「気になる方はこちら」程度にとどめる
- ブログ誘導は自然な流れで（「詳しくはこちら」程度）

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
