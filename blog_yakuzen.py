import os
import re
import json
import datetime
import requests
import markdown as md_lib
from linebot.models import TextSendMessage
from clients import line_bot_api, anthropic_client

RAKUTEN_APP_ID = os.environ.get('RAKUTEN_APP_ID', '')
RAKUTEN_ACCESS_KEY = os.environ.get('RAKUTEN_ACCESS_KEY', '')
RAKUTEN_AFFILIATE_ID = os.environ.get('RAKUTEN_AFFILIATE_ID', '')
AMAZON_ASSOCIATE_ID = os.environ.get('AMAZON_ASSOCIATE_ID', 'makiko01035-22')


def _amazon_url(url):
    """AmazonURLにアソシエイトタグを付与する"""
    tag = f'tag={AMAZON_ASSOCIATE_ID}'
    return f'{url}?{tag}' if '?' not in url else f'{url}&{tag}'

YAKUZEN_BOARD_RULES = {
    # 更年期×睡眠
    '更年期': '更年期×睡眠', 'ほてり': '更年期×睡眠', '50代': '更年期×睡眠', '閉経': '更年期×睡眠',
    # 子ども・家族の睡眠
    '子ども': '子ども・家族の睡眠', '夜泣き': '子ども・家族の睡眠', '小学生': '子ども・家族の睡眠',
    '中学生': '子ども・家族の睡眠', '産後': '子ども・家族の睡眠', '育児': '子ども・家族の睡眠',
    # 薬膳×睡眠
    '薬膳': '薬膳×睡眠', '東洋医学': '薬膳×睡眠', '漢方': '薬膳×睡眠',
    'なつめ': '薬膳×睡眠', '白きくらげ': '薬膳×睡眠', '酸棗仁': '薬膳×睡眠',
    '陰虚': '薬膳×睡眠', '気虚': '薬膳×睡眠', '食材': '薬膳×睡眠',
    # 不眠・睡眠改善（デフォルト候補）
    '不眠': '不眠・睡眠改善', '眠れない': '不眠・睡眠改善', '寝つき': '不眠・睡眠改善',
    '睡眠薬': '不眠・睡眠改善', '睡眠時無呼吸': '不眠・睡眠改善', 'いびき': '不眠・睡眠改善',
    'サプリ': '不眠・睡眠改善', 'グリシン': '不眠・睡眠改善', 'メラトニン': '不眠・睡眠改善',
    '枕': '不眠・睡眠改善', 'マットレス': '不眠・睡眠改善', 'ツボ': '不眠・睡眠改善',
}


def get_yakuzen_wp_creds():
    return (
        os.environ.get('YAKUZEN_WP_URL', 'https://foodmakehealth.com'),
        os.environ.get('YAKUZEN_WP_USER', 'makiko01035'),
        os.environ['YAKUZEN_WP_APP_PASSWORD']
    )


def search_yakuzen_posts(keyword):
    wp_url, wp_user, wp_pass = get_yakuzen_wp_creds()
    res = requests.get(
        f'{wp_url}/wp-json/wp/v2/posts',
        auth=(wp_user, wp_pass),
        params={'search': keyword, 'per_page': 5, 'status': 'publish'},
        timeout=15
    )
    if res.status_code == 200:
        return res.json()
    return []


def get_all_yakuzen_posts():
    wp_url, wp_user, wp_pass = get_yakuzen_wp_creds()
    res = requests.get(
        f'{wp_url}/wp-json/wp/v2/posts',
        auth=(wp_user, wp_pass),
        params={'per_page': 100, 'status': 'publish', '_fields': 'id,title,date'},
        timeout=20
    )
    if res.status_code == 200:
        return res.json()
    return []


def auto_select_yakuzen_post(posts):
    """季節に合った記事を1本自動選択"""
    today = datetime.date.today()
    month = today.month
    season_hint = {
        1: "冬・乾燥・冷え・免疫",
        2: "冬から春へ・花粉準備・肝",
        3: "春・花粉症・肝・デトックス",
        4: "春・花粉症・気の巡り・肝",
        5: "晩春・初夏・梅雨準備・脾胃",
        6: "梅雨・湿気・脾・むくみ",
        7: "夏・暑気・心・熱中症",
        8: "真夏・心・夏バテ・冷え",
        9: "初秋・肺・乾燥・免疫",
        10: "秋・肺・乾燥・便秘",
        11: "晩秋・腎・冷え・疲労回復",
        12: "冬・腎・冷え・年末養生",
    }.get(month, "季節の養生")

    post_list_text = '\n'.join([
        f"ID:{p['id']} タイトル:{p['title']['rendered']}"
        for p in posts[:80]
    ])
    response = anthropic_client.messages.create(
        model='claude-haiku-4-5-20251001',
        max_tokens=200,
        messages=[{
            'role': 'user',
            'content': f"""今日は{today}（{month}月）です。
キーワード：{season_hint}

以下は薬膳ブログの記事一覧です。この季節にリライトするのに最適な記事を1本選んでください。
タイトルが古い形式（◇◆■□などの記号入り）なら優先的に選んでください。

{post_list_text}

以下のJSON形式のみで回答（説明不要）：
{{"id": 記事ID, "reason": "選んだ理由（1文）"}}"""
        }]
    )
    raw = response.content[0].text.strip()
    match = re.search(r'\{.*\}', raw, re.DOTALL)
    if match:
        return json.loads(match.group())
    return None


def auto_rewrite_yakuzen(user_id):
    try:
        posts = get_all_yakuzen_posts()
        if not posts:
            line_bot_api.push_message(user_id, TextSendMessage(text="😢 記事の取得に失敗しました。"))
            return

        selected = auto_select_yakuzen_post(posts)
        if not selected:
            line_bot_api.push_message(user_id, TextSendMessage(text="😢 記事の選択に失敗しました。"))
            return

        post_id = selected['id']
        reason = selected.get('reason', '')

        wp_url, wp_user, wp_pass = get_yakuzen_wp_creds()
        res = requests.get(
            f'{wp_url}/wp-json/wp/v2/posts/{post_id}',
            auth=(wp_user, wp_pass),
            params={'_fields': 'id,title,content'},
            timeout=15
        )
        if res.status_code != 200:
            line_bot_api.push_message(user_id, TextSendMessage(text="😢 記事の取得に失敗しました。"))
            return

        post = res.json()
        post_title = post['title']['rendered']
        post_content = post['content']['rendered']

        line_bot_api.push_message(user_id, TextSendMessage(
            text=f"📄 「{post_title}」をリライト中...\n理由：{reason}\n\n少しお待ちください！"
        ))

        article_md = generate_yakuzen_rewrite(post_title, post_content)
        lines = article_md.split('\n')
        new_title = lines[0].lstrip('# ').strip()
        new_content = '\n'.join(lines[1:]).lstrip('\n')

        keyword = generate_pexels_keyword(new_title)
        image_url = fetch_pexels_image_url(keyword)
        media_id = upload_image_to_yakuzen_wp(image_url, new_title) if image_url else None
        # リライト後のタイトルからカテゴリーを自動判定（薬膳→睡眠系に移動）
        new_cat_id = detect_category_id(new_title, new_content)
        _, link = post_to_yakuzen_wp(new_title, new_content, post_id=post_id, status='publish',
                                     featured_media_id=media_id, categories=[new_cat_id])
        try_post_to_pinterest(new_title, link, new_content, image_url=image_url)
        line_bot_api.push_message(user_id, TextSendMessage(text=f"✅ リライト・更新完了！\n\n📝 {new_title}\n🔗 {link}"))
        send_sns_messages(user_id, new_title, link, image_url, new_content)

    except Exception as e:
        print(f"Auto rewrite error: {e}")
        import traceback; traceback.print_exc()
        line_bot_api.push_message(user_id, TextSendMessage(text=f"😢 エラーが発生しました。\n{str(e)[:150]}"))


def check_old_yakuzen_post(user_id, skip_ids=None):
    """古い順に記事を1件取得してClaude に方針適合を判断させLINEに報告"""
    skip_ids = skip_ids or []
    try:
        wp_url, wp_user, wp_pass = get_yakuzen_wp_creds()
        res = requests.get(
            f'{wp_url}/wp-json/wp/v2/posts',
            auth=(wp_user, wp_pass),
            params={'per_page': 100, 'orderby': 'date', 'order': 'asc',
                    '_fields': 'id,title,date,content'},
            timeout=15
        )
        posts = [p for p in res.json() if p['id'] not in skip_ids]
        if not posts:
            line_bot_api.push_message(user_id, TextSendMessage(
                text="✅ チェックできる記事がなくなりました！お疲れ様でした。"
            ))
            return None

        import html as html_lib
        post = posts[0]
        post_id = post['id']
        post_title = html_lib.unescape(post['title']['rendered'])
        post_date = post['date'][:10]
        raw_content = post['content']['rendered']
        import re as _re
        plain = _re.sub(r'<[^>]+>', '', raw_content)[:600]

        prompt = f"""以下の薬膳ブログ記事を評価してください。

ブログの現在の方針：「睡眠×医療×薬膳」軸。内科医が書く、眠れない悩みを薬膳・東洋医学・医療知識で解決するブログ。ターゲット：更年期・睡眠障害・疲労で悩む30〜50代女性。

記事タイトル：{post_title}
投稿日：{post_date}
内容（先頭600文字）：{plain}

3点で評価してください：
1. 方針適合度：「合う」「修正でいける」「ズレてる」のどれか
2. 判定理由（1行）
3. 推奨アクション：「リライト推奨」「軽いリライトでOK」「削除推奨」のどれか

返答は以下の形式で：
【適合度】〇〇
【理由】〇〇
【推奨】〇〇"""

        response = anthropic_client.messages.create(
            model='claude-sonnet-4-6',
            max_tokens=300,
            messages=[{'role': 'user', 'content': prompt}]
        )
        judgement = response.content[0].text.strip()

        msg = (f"📋 古い記事チェック\n\n"
               f"📝 {post_title}\n"
               f"📅 投稿日：{post_date}\n\n"
               f"{judgement}\n\n"
               f"どうしますか？\n"
               f"1️⃣ リライトして\n"
               f"2️⃣ スキップ（次の記事へ）\n"
               f"3️⃣ 削除して\n"
               f"4️⃣ やめる")
        line_bot_api.push_message(user_id, TextSendMessage(text=msg))
        return post_id

    except Exception as e:
        print(f"check_old_yakuzen_post error: {e}")
        import traceback; traceback.print_exc()
        line_bot_api.push_message(user_id, TextSendMessage(text=f"😢 エラーが発生しました。\n{str(e)[:150]}"))
        return None


def delete_yakuzen_post(post_id):
    """薬膳記事を削除（WP REST API）"""
    wp_url, wp_user, wp_pass = get_yakuzen_wp_creds()
    requests.delete(
        f'{wp_url}/wp-json/wp/v2/posts/{post_id}',
        auth=(wp_user, wp_pass),
        params={'force': True},
        timeout=15
    )


def _get_recent_yakuzen_titles(n=5):
    """直近n件のWP投稿タイトルを返す"""
    try:
        wp_url = os.environ.get('YAKUZEN_WP_URL', 'https://foodmakehealth.com')
        wp_user = os.environ.get('YAKUZEN_WP_USER', 'makiko01035')
        wp_pass = os.environ.get('YAKUZEN_WP_APP_PASSWORD', '')
        res = requests.get(
            f"{wp_url}/wp-json/wp/v2/posts",
            params={'per_page': n, 'orderby': 'date', 'order': 'desc', 'status': 'publish'},
            auth=(wp_user, wp_pass),
            timeout=10
        )
        if res.status_code == 200:
            return [p['title']['rendered'] for p in res.json()]
    except Exception as e:
        print(f"[Topic] recent titles fetch error: {e}")
    return []


# 睡眠×医学×薬膳 カテゴリローテーション（優先度順）
YAKUZEN_CATEGORY_ROTATION = [
    "更年期×不眠・ほてり",
    "更年期×夜中の目覚め・朝4時覚醒",
    "50代女性の睡眠の悩み",
    "寝つきが悪い（30〜40代女性）",
    "夜中に何度も目が覚める",
    "寝ても疲れが取れない",
    "ストレス・考えすぎで眠れない",
    "産後・育児疲れと睡眠",
    "不眠×漢方・薬膳食材（なつめ・白きくらげ・酸棗仁）",
    "子どもの寝つき・夜泣き改善",
    "東洋医学×不眠（陰虚・気虚）",
    "枕・マットレス×睡眠の質",
    "睡眠サプリ（グリシン・メラトニン）",
    "不眠×ツボ・生活習慣",
    "中学生・小学生の睡眠トラブル",
    "睡眠時無呼吸症候群・いびきの対策",
    "不眠症の診断・何科に行くべきか（医師が解説）",
    "睡眠薬への依存・自然にやめる方法",
]

# 各カテゴリに紐づくキーワード（直近記事タイトルと照合用）
CATEGORY_KEYWORDS = {
    "冷え": ["冷え", "冷たい", "体を温"],
    "むくみ": ["むくみ", "むくむ", "浮腫"],
    "疲労・だるさ": ["疲労", "だるさ", "だるい", "疲れ", "倦怠"],
    "肌荒れ・美肌": ["肌荒れ", "美肌", "肌トラブル", "ニキビ", "くすみ"],
    "便秘": ["便秘", "お通じ", "腸活"],
    "PMS・生理痛": ["PMS", "生理痛", "生理前", "月経"],
    "不眠": ["不眠", "眠れ", "睡眠", "寝つき"],
    "胃腸不調・食欲不振": ["胃腸", "食欲", "胃もたれ", "消化"],
    "花粉症・アレルギー": ["花粉", "アレルギー", "鼻炎", "くしゃみ"],
    "頭痛・肩こり": ["頭痛", "肩こり", "頭が重"],
    "貧血": ["貧血", "鉄分", "フェリチン"],
    "免疫力アップ": ["免疫", "風邪予防", "インフルエンザ"],
    "子ども・家族向け": ["子ども", "こども", "子供", "家族", "キッズ"],
    "目の疲れ・ドライアイ": ["目の疲れ", "ドライアイ", "眼精疲労"],
    "乾燥・保湿": ["乾燥", "保湿", "潤い"],
    "五月病・気分の落ち込み": ["五月病", "気分", "落ち込み", "メンタル", "うつ"],
    "夏バテ": ["夏バテ", "夏の疲れ", "熱中症"],
    "冬の乾燥・風邪予防": ["冬", "乾燥肌", "風邪", "喉"],
}


def _detect_used_categories(titles):
    """記事タイトルリストからどのカテゴリが使われているか判定"""
    used = set()
    for title in titles:
        for cat, keywords in CATEGORY_KEYWORDS.items():
            if any(kw in title for kw in keywords):
                used.add(cat)
    return used


def select_yakuzen_topic():
    """カテゴリローテーションで次のテーマを選定（直近記事と被らないカテゴリを優先）"""
    today = datetime.date.today()
    month = today.month

    recent_titles = _get_recent_yakuzen_titles(15)
    used_cats = _detect_used_categories(recent_titles)

    # 直近で使われていないカテゴリを先頭から選ぶ
    next_cat = None
    for cat in YAKUZEN_CATEGORY_ROTATION:
        if cat not in used_cats:
            next_cat = cat
            break
    # 全部使い切っていたらリストの先頭に戻る
    if not next_cat:
        next_cat = YAKUZEN_CATEGORY_ROTATION[0]

    print(f"[Topic] category={next_cat}, used_recent={used_cats}")

    seasonal_hint = {
        1: "冬の冷えによる血行不良・乾燥・ホルモンバランスの乱れ",
        2: "春の準備期・自律神経の乱れ・花粉シーズンのストレス",
        3: "春の気温変化・自律神経・季節の変わり目の不眠",
        4: "新生活ストレス・五月病前の疲れ・環境変化による眠れなさ",
        5: "五月病・精神的疲労・気温上昇による睡眠の浅さ",
        6: "梅雨の湿気・だるさ・熱帯夜準備",
        7: "熱帯夜の寝苦しさ・夏バテ×睡眠・体の余分な熱",
        8: "残暑・夏疲れの蓄積・冷房冷えと睡眠の質",
        9: "秋の乾燥・夏疲れ解消・気候変化による入眠困難",
        10: "秋冬の移行期・気温低下・更年期ほてりと冷えの混在",
        11: "冬の血行不良・冷え×不眠・年末ストレス",
        12: "冬の冷え・年末の忙しさによるストレス・免疫×睡眠",
    }.get(month, "季節の変わり目の自律神経の乱れ")

    response = anthropic_client.messages.create(
        model='claude-haiku-4-5-20251001',
        max_tokens=120,
        messages=[{
            'role': 'user',
            'content': f"""睡眠改善ブログ（foodmakehealth.com）の記事タイトルを1つ作ってください。

テーマカテゴリ：{next_cat}
今日：{today}（{month}月）／季節的な悩み：{seasonal_hint}

条件：
- 「睡眠改善×医師監修」視点の記事（薬膳・東洋医学は補助として活用）
- 「〇〇で眠れない」「〇〇の不眠対策」など悩みキーワードを含む
- 「医師監修」または「内科医が解説」を含む32字以内のSEOタイトル
- 30〜50代女性が検索しそうなタイトル
- 記事タイトルのみ出力（説明不要）"""
        }]
    )
    return response.content[0].text.strip()


ARTICLE_TEMPLATE = """
## 全記事共通テンプレート構成

① 共感（H2）
- 読者の悩みを会話体で言語化（「〜していませんか？」）
- 2〜3行で共感→「原因があります」へ橋渡し

② 原因（H2）医学的＋生活習慣
- H3で3〜4個に分けて解説
- 医学的メカニズム（ホルモン・神経・体温など）をわかりやすく
- 「なるほど」と思える専門情報を入れてE-E-A-Tを高める

③ 改善方法（H2）すぐできる
- H3で3〜7個の具体的アクション
- 箇条書き・表を積極的に使う
- 「今夜から」「明日の朝から」など即実践できるレベルで書く

④ 薬膳的アプローチ（H2）補助として
- 東洋医学の体質タイプ名と簡単な説明（難しい言葉は避ける）
- H3：おすすめ食材を表形式で（食材・働き・使い方）
- 「補助として」のトーンを維持する

⑤ おすすめ商品・食品（H2）
- H3ごとに1商品：食品系・サプリ系・寝具系を状況に応じて
- 「<!-- yakuzen-affiliate-cta -->」プレースホルダーを入れる
- 医師コメントを添えて自然な推薦にする

⑥ まとめ（H2）
- STEP形式で「今夜から試す3ステップ」
- 「2週間改善しなければ内科・睡眠外来へ」の案内を必ず入れる
- 関連記事への内部リンクを2〜3本
"""


def generate_yakuzen_article(topic):
    response = anthropic_client.messages.create(
        model='claude-sonnet-4-6',
        max_tokens=4000,
        messages=[{
            'role': 'user',
            'content': f"""あなたは内科医であり薬膳の専門家です。foodmakehealth.comのブログ記事を書いてください。

テーマ：{topic}

【ライター設定】
- 内科医・睡眠外来担当医として、医学的根拠のある情報を提供する専門家
- 薬膳・東洋医学の知識を持ち「補助的アプローチ」として活用
- 読者の悩みに共感し、「今夜から試せる」具体策を伝える

【ターゲット】
- 30〜50代女性（睡眠の悩み・更年期・疲れが取れないなどを抱えている）
- 「病院に行くほどじゃないけど眠れない・だるい」と感じている
- 薬に頼らず食事・生活習慣から改善したいと思っている

【SEO要件】
- タイトル：悩みキーワード＋「医師監修」or「医師が解説」を含む32字以内
- 冒頭100字：悩みへの共感＋この記事で何がわかるかを明示
- 見出し（H2・H3）：検索キーワードを自然に含める

【記事構成（必ずこの順番で）】
{ARTICLE_TEMPLATE}

【文章スタイル】
- 1文は40字以内を目安に短く
- 箇条書き・表を積極的に使う
- 「〜してみてください」など親しみやすい語尾
- 1500〜2000文字
- Markdown形式、最初の行は「# タイトル」
- 記事末尾に「<!-- yakuzen-affiliate-cta -->」を1行追加"""
        }]
    )
    return response.content[0].text.strip()


def generate_yakuzen_rewrite(title, original_html, instruction=''):
    import html as html_lib
    plain = re.sub(r'<[^>]+>', '', original_html)
    plain = html_lib.unescape(plain)
    extra = f"\nまきからの追加指示：{instruction}" if instruction else ""
    response = anthropic_client.messages.create(
        model='claude-sonnet-4-6',
        max_tokens=4000,
        messages=[{
            'role': 'user',
            'content': f"""あなたは内科医であり薬膳の専門家です。以下のブログ記事を「睡眠・健康改善×東洋医学・薬膳」の視点でリライトしてください。

元のタイトル：{title}
元の記事内容：
{plain[:3000]}
{extra}

【ライター設定】
- 内科医・睡眠外来担当医として、医学的根拠のある情報を提供する専門家
- 薬膳・東洋医学の知識も持ち、西洋医学と組み合わせた実践的アドバイスが強み
- 読者の悩みに共感し、「今日から試せる」具体策を伝える

【ターゲット】
- 30〜50代女性（睡眠の悩み・更年期・疲れが取れないなどを抱えている）
- 「病院に行くほどじゃないけど眠れない・だるい」と感じている
- 薬に頼らず食事・生活習慣から改善したいと思っている

【リライト方針】
- 元の薬膳・食材の知識を活かしながら、睡眠・疲労回復・更年期などの健康テーマに結びつける
- 「この食材を食べると眠りやすくなる」「更年期の不調を和らげる」など読者の悩み解決につなげる
- 医師としての専門知識（box-doctorコメント）を盛り込み信頼性を高める
- タイトルは「睡眠・更年期・疲れ・体調」などの悩みキーワード＋解決策を含む32字以内

【SEO要件】
- 冒頭100字：悩みへの共感＋この記事で何がわかるかを明示
- 見出し（##）：3〜5個、各見出しに検索キーワードを含める
- 箇条書き・表を積極的に使う
- 2000〜2500文字、Markdown形式、最初の行は「# タイトル」
- 記事末尾に「<!-- yakuzen-affiliate-cta -->」を1行追加"""
        }]
    )
    return response.content[0].text.strip()


import random as _random

# レシピ記事専用（固定）
AFFILIATE_BOOK_RECIPE = {
    'url': 'https://amzn.asia/d/0bkhnDrf',
    'title': '「まいにちのごはん」で健康になっちゃう！ずぼら薬膳',
    'desc': '特別な食材は不要。いつものごはんに薬膳の考え方をプラスするだけで体が変わる一冊。',
}

# アーユルヴェーダ記事専用（もしもアフィ）
AFFILIATE_BOOK_AYURVEDA = {
    'raw_html': '<a href="//af.moshimo.com/af/c/click?a_id=5429137&p_id=4140&pc_id=10486&pl_id=56829" rel="nofollow" referrerpolicy="no-referrer-when-downgrade" attributionsrc><img src="//image.moshimo.com/af-img/3597/000000056829.jpg" width="240" height="120" style="border:none;"></a><img src="//i.moshimo.com/af/i/impression?a_id=5429137&p_id=4140&pc_id=10486&pl_id=56829" width="1" height="1" style="border:none;" loading="lazy">',
}

# 睡眠本の紹介記事（foodmakehealth.com）で取り上げた本のプール → ここからランダム選択
# 新しい睡眠本を紹介記事に追加したらここにも追記する（睡眠特化本のみ）
AFFILIATE_BOOKS_POOL = [
    {
        'url': 'https://www.amazon.co.jp/dp/4763136011/',
        'title': 'スタンフォード式 最高の睡眠',
        'desc': '世界最高の睡眠研究機関が教える、眠りの質を劇的に上げる90分の法則。医師も推薦の一冊。',
    },
    {
        'url': 'https://www.amazon.co.jp/dp/402333409X/',
        'title': '今さら聞けない 睡眠の超基本',
        'desc': '睡眠研究の第一人者・柳沢正史教授が、睡眠の「なぜ？」を科学的にわかりやすく解説した入門書。',
    },
    {
        'url': 'https://www.amazon.co.jp/dp/4315527351/',
        'title': 'ニュートン超図解新書 最強に面白い 睡眠',
        'desc': '超図解でわかる睡眠の科学。眠れない理由から快眠のコツまで、視覚的にスッキリ理解できる。',
    },
    {
        'url': 'https://www.amazon.co.jp/dp/477620956X/',
        'title': '1万人を治療した睡眠の名医が教える 誰でも簡単にぐっすり眠れるようになる方法',
        'desc': '1万人の患者を診てきた睡眠専門医が教える、今夜から試せる実践的な快眠メソッド。',
    },
    {
        'url': 'https://www.amazon.co.jp/dp/4797395842/',
        'title': '睡眠こそ最強の解決策である',
        'desc': 'カリフォルニア大学の睡眠科学者が書いた世界的ベストセラー。睡眠が人生のあらゆる問題を解決する。',
    },
    {
        'url': 'https://www.amazon.co.jp/dp/4569904718/',
        'title': 'スタンフォード大学西野教授が教える 間違いだらけの睡眠常識',
        'desc': 'あなたが信じていた睡眠の「常識」は間違いだらけ？スタンフォード教授が正しい睡眠を教える。',
    },
    {
        'url': 'https://www.amazon.co.jp/dp/4837986579/',
        'title': '驚くほど眠りの質がよくなる 睡眠メソッド100',
        'desc': '100の実践メソッドを1テーマ1ページで解説。今日から試せる快眠習慣がきっと見つかる。',
    },
    {
        'url': 'https://www.amazon.co.jp/dp/4866801697/',
        'title': '働くあなたの快眠地図',
        'desc': '忙しくて眠れない人へ。仕事のパフォーマンスを上げる睡眠戦略を、わかりやすく丁寧に解説。',
    },
    {
        'url': 'https://www.amazon.co.jp/dp/4479798285/',
        'title': '人生を変えるモーニングメソッド（新版）',
        'desc': '朝の時間の使い方が人生を変える世界的ベストセラー。良質な睡眠でより良い朝を迎えるヒントに。',
    },
    {
        'url': 'https://www.amazon.co.jp/dp/4484221241/',
        'title': 'いつでも調子がいいカラダになる！ホルモンをととのえる本',
        'desc': 'ホルモンバランスと睡眠の深い関係を解説。更年期・睡眠の悩みを抱える女性に特におすすめ。',
    },
]


def _select_affiliate_book(title, content_md):
    ayurveda_keywords = ['アーユルヴェーダ', 'スパイス検定', 'アーユル']
    recipe_keywords = ['レシピ', '作り方', '材料', 'お粥', '粥', '料理', '献立', '炒め', '煮', '茹で', '蒸し', '食べ方']
    text = title + content_md[:500]
    if any(k in text for k in ayurveda_keywords):
        return AFFILIATE_BOOK_AYURVEDA
    if any(k in text for k in recipe_keywords):
        return AFFILIATE_BOOK_RECIPE
    return _random.choice(AFFILIATE_BOOKS_POOL)


def _build_affiliate_cta(title, content_md):
    book = _select_affiliate_book(title, content_md)
    if 'raw_html' in book:
        return f'<div style="margin:30px 0;">{book["raw_html"]}</div>'
    return f'''<div style="background:#f9f6f0;border-left:4px solid #8b6914;padding:20px;margin:30px 0;border-radius:4px;">
<p style="font-weight:bold;margin:0 0 8px;">📚 睡眠をもっと深く知りたい方へ</p>
<p style="margin:0 0 4px;font-weight:bold;">{book["title"]}</p>
<p style="margin:0 0 15px;font-size:0.95em;">{book["desc"]}</p>
<a href="{_amazon_url(book["url"])}" target="_blank" rel="nofollow" style="display:inline-block;background:#ff9900;color:white;padding:10px 24px;border-radius:4px;text-decoration:none;font-weight:bold;">Amazonで見る →</a>
</div>'''


def search_rakuten_items(keyword, hits=3):
    if not RAKUTEN_APP_ID or not RAKUTEN_AFFILIATE_ID:
        return []
    try:
        params = {
            'applicationId': RAKUTEN_APP_ID,
            'affiliateId': RAKUTEN_AFFILIATE_ID,
            'keyword': keyword,
            'hits': hits,
            'sort': '-reviewCount',
            'format': 'json',
            'accessKey': RAKUTEN_ACCESS_KEY,
        }
        res = requests.get(
            'https://openapi.rakuten.co.jp/ichibams/api/IchibaItem/Search/20260401',
            params=params,
            headers={'Referer': 'http://foodmakehealth.com', 'Origin': 'https://maki-hisho.onrender.com'},
            timeout=10
        )
        data = res.json()
        if 'error' in data:
            print(f"楽天API エラーレスポンス: {data}")
            return []
        items = []
        for item_wrap in data.get('Items', []):
            i = item_wrap.get('Item', item_wrap)
            image_url = i['mediumImageUrls'][0].get('imageUrl', '') if i.get('mediumImageUrls') else ''
            items.append({
                'name': i.get('itemName', ''),
                'price': i.get('itemPrice', 0),
                'url': i.get('affiliateUrl') or i.get('itemUrl', ''),
                'image': image_url,
            })
        return items
    except Exception as e:
        print(f"楽天API エラー: {e}")
        return []


def _extract_rakuten_keyword(title, content_md):
    try:
        response = anthropic_client.messages.create(
            model='claude-haiku-4-5-20251001',
            max_tokens=30,
            messages=[{
                'role': 'user',
                'content': f"""この薬膳ブログ記事で紹介している食材・食品名を1つだけ答えてください。
タイトル：{title}
記事冒頭：{content_md[:600]}

食材名のみ回答（例：なつめ、クコの実、黒ごま、生姜）。説明不要。"""
            }]
        )
        return response.content[0].text.strip()
    except Exception as e:
        print(f"楽天キーワード抽出エラー: {e}")
        return title.replace('薬膳', '').strip()[:20] or title


def _build_rakuten_natural_intro(title, content_md, keyword):
    """3パターンの自然な導入文をタイトルの特徴で切り替える"""
    text = title + content_md
    # パターンC：忙しさ・手軽さキーワードがあるとき
    if any(kw in text for kw in ['忙しい', '簡単', '手軽', '時間がない', '夜勤', '育児']):
        return f'''<p>「食材を揃えて調理する余裕はない…」という方には、すでにブレンドされたお茶タイプが便利です。</p>
<p>なつめ・百合根・蓮の実など、東洋医学で「心を落ち着かせる」とされる食材が入ったものを選ぶのがポイント。寝る前の「儀式」にすると、体だけでなく気持ちもオフになりやすいです。</p>'''
    # パターンB：薬膳食材が具体的に挙がっているとき
    elif any(kw in text for kw in ['なつめ', 'クコ', '白きくらげ', '百合根', '蓮の実', '酸棗仁']):
        return f'''<p>「{keyword}ってどこで買えるの？」と思った方へ。スーパーで見かけない場合は楽天で探すと個包装タイプや飲みやすいお茶タイプが見つかります。無農薬・国産のものを選ぶと安心です。</p>'''
    # パターンA：デフォルト（改善方法の流れに乗せる）
    else:
        return f'''<p>就寝前のひとときを変えるだけで、眠りの深さが変わることがあります。体を内側から落ち着かせる薬膳食材を、まずは飲み物から試してみませんか。特別な調理も不要なので忙しい夜でも続けやすいです。</p>'''


def _build_item_card(item):
    name = item['name'][:50] + ('...' if len(item['name']) > 50 else '')
    return f'''<div style="display:flex;gap:12px;margin-bottom:12px;padding:12px;border:1px solid #e8d5c5;border-radius:6px;background:#fff;">
  <a href="{item['url']}" target="_blank" rel="nofollow" style="flex-shrink:0;"><img src="{item['image']}" alt="" style="width:70px;height:70px;object-fit:cover;border-radius:4px;"></a>
  <div>
    <a href="{item['url']}" target="_blank" rel="nofollow" style="font-size:0.9em;font-weight:bold;color:#333;text-decoration:none;">{name}</a>
    <p style="margin:4px 0 8px;color:#bf0000;font-weight:bold;">¥{item['price']:,}</p>
    <a href="{item['url']}" target="_blank" rel="nofollow" style="background:#bf0000;color:#fff;padding:4px 12px;border-radius:4px;text-decoration:none;font-size:0.85em;font-weight:bold;">楽天で見る →</a>
  </div>
</div>'''


def _extract_rakuten_keywords_multi(title, content_md):
    """記事内容から楽天で検索できる商品・食材を2〜3件抽出する"""
    try:
        response = anthropic_client.messages.create(
            model='claude-haiku-4-5-20251001',
            max_tokens=80,
            messages=[{
                'role': 'user',
                'content': f"""この薬膳・睡眠ブログ記事に登場する食材・商品から、楽天市場で検索できるものを2〜3件選んでください。
食材だけでなく、枕・アイマスク・アロマオイル・ナイトウェアなど寝具・グッズも記事に出ていれば含めてください。
タイトル：{title}
記事：{content_md[:800]}

カンマ区切りで商品名のみ答えてください（例：カモミールティー,温熱アイマスク,なつめ茶）。説明不要。"""
            }]
        )
        raw = response.content[0].text.strip()
        keywords = [k.strip() for k in raw.replace('、', ',').replace('・', ',').split(',') if k.strip()]
        return keywords[:3]
    except Exception as e:
        print(f"楽天マルチキーワード抽出エラー: {e}")
        return [title.replace('薬膳', '').strip()[:20] or title]


def _build_rakuten_section(title, content_md=''):
    keywords = _extract_rakuten_keywords_multi(title, content_md)
    cards = ''
    found_labels = []
    for kw in keywords:
        items = search_rakuten_items(kw, hits=1)
        if items:
            cards += _build_item_card(items[0])
            found_labels.append(kw)
    if not cards:
        return ''
    intro = _build_rakuten_natural_intro(title, content_md, found_labels[0] if found_labels else keywords[0])
    label = '・'.join(found_labels)
    return f'''<div style="background:#fff5f5;border-left:4px solid #bf0000;padding:20px;margin:30px 0;border-radius:4px;">
{intro}
<p style="font-weight:bold;margin:12px 0 16px;">🛒 楽天市場で探す（{label}）</p>
{cards}
</div>'''


SLEEP_KEYWORDS = ['睡眠', '不眠', '眠れ', '眠り', '熟睡', '入眠', '夜勤', '快眠', '睡眠外来', '起きられ', '朝起き', '目が覚め', 'いびき', '無呼吸', 'メラトニン', 'セロトニン', '睡眠の質', '寝つき', '中途覚醒', '早朝覚醒']
KIDS_SLEEP_KEYWORDS = ['子ども', 'こども', '子供', '小学生', '赤ちゃん', '乳幼児', '中学生', '高校生']
MENOPAUSE_KEYWORDS = ['更年期', '閉経', 'ホットフラッシュ', 'ほてり', 'のぼせ', 'エストロゲン', 'HRT', '女性ホルモン', 'PMS', '生理前', '月経前']
ORIENTAL_KEYWORDS = ['東洋医学', '漢方', '中医学', '気血水', '陰陽', '五臓', '経絡', '鍼灸', '生薬', '薬膳', '陰虚', '気虚', '血虚', '瘀血', '湿熱']


def detect_category_id(title, content=''):
    """タイトル・内容から適切なWPカテゴリーIDを判定する（優先順位順）"""
    text = title + content
    has_kids = any(kw in text for kw in KIDS_SLEEP_KEYWORDS)
    has_sleep = any(kw in text for kw in SLEEP_KEYWORDS)
    # 更年期ケア（216）：更年期系キーワードがあるとき（睡眠との組み合わせも含む）
    if any(kw in text for kw in MENOPAUSE_KEYWORDS):
        return 216
    # 子どもの睡眠（215）：子ども系＋睡眠系が両方あるとき
    if has_kids and has_sleep:
        return 215
    # 睡眠の悩み（219）：睡眠系キーワードがあるとき
    if has_sleep:
        return 219
    # 東洋医学（217）：東洋医学・漢方系キーワードがあるとき
    if any(kw in text for kw in ORIENTAL_KEYWORDS):
        return 217
    # デフォルト：普段使いの薬膳レシピ（9）—新記事は原則増やさない
    return 9


def post_to_yakuzen_wp(title, content_md, post_id=None, status='draft', featured_media_id=None, categories=None, tags=None):
    wp_url, wp_user, wp_pass = get_yakuzen_wp_creds()
    html = md_lib.markdown(content_md, extensions=['tables', 'nl2br'])
    html = html.replace('<!-- yakuzen-affiliate-cta -->', _build_affiliate_cta(title, content_md))
    try:
        rakuten_section = _build_rakuten_section(title, content_md)
        if rakuten_section:
            html += rakuten_section
    except Exception as e:
        print(f"楽天セクション生成エラー（スキップ）: {e}")
    data = {'title': title, 'content': html, 'status': status}
    if featured_media_id:
        data['featured_media'] = featured_media_id
    # カテゴリー：指定がなければタイトルから自動判定
    cat_id = categories if categories else [detect_category_id(title, content_md)]
    data['categories'] = cat_id
    # タグ：渡された場合はID変換して設定
    if tags:
        tag_ids = []
        for tag_name in tags:
            tr = requests.get(f'{wp_url}/wp-json/wp/v2/tags',
                              params={'search': tag_name}, auth=(wp_user, wp_pass), timeout=15)
            hits = tr.json() if tr.status_code == 200 else []
            exact = next((t for t in hits if t['name'] == tag_name), None)
            if exact:
                tag_ids.append(exact['id'])
            else:
                cr = requests.post(f'{wp_url}/wp-json/wp/v2/tags',
                                   auth=(wp_user, wp_pass), json={'name': tag_name}, timeout=15)
                if cr.status_code == 201:
                    tag_ids.append(cr.json()['id'])
        if tag_ids:
            data['tags'] = tag_ids
    if post_id:
        res = requests.post(
            f'{wp_url}/wp-json/wp/v2/posts/{post_id}',
            auth=(wp_user, wp_pass),
            json=data,
            timeout=30
        )
    else:
        res = requests.post(
            f'{wp_url}/wp-json/wp/v2/posts',
            auth=(wp_user, wp_pass),
            json=data,
            timeout=30
        )
    if res.status_code in (200, 201):
        post = res.json()
        return post['id'], post['link']
    raise Exception(f"WP投稿エラー: {res.status_code} {res.text[:200]}")


def process_yakuzen_new_article(user_id, topic=None):
    try:
        if not topic:
            topic = select_yakuzen_topic()
        article_md = generate_yakuzen_article(topic)
        lines = article_md.split('\n')
        title = lines[0].lstrip('# ').strip()
        content = '\n'.join(lines[1:]).lstrip('\n')
        keyword = generate_pexels_keyword(title)
        image_url = fetch_pexels_image_url(keyword)
        media_id = upload_image_to_yakuzen_wp(image_url, title) if image_url else None
        post_id, link = post_to_yakuzen_wp(title, content, status='publish', featured_media_id=media_id)
        try_post_to_pinterest(title, link, content, image_url=image_url)
        line_bot_api.push_message(user_id, TextSendMessage(text=f"✅ 睡眠記事を公開しました！\n\n📝 {title}\n🔗 {link}"))
        send_sns_messages(user_id, title, link, image_url, content)
    except Exception as e:
        print(f"Yakuzen new article error: {e}")
        line_bot_api.push_message(user_id, TextSendMessage(text=f"😢 記事作成中にエラーが発生しました。\n{str(e)[:150]}"))


def rewrite_yakuzen_by_keyword(user_id, keyword):
    """キーワードで記事を検索してリライト（複数ヒット時は最初の1件）"""
    try:
        posts = search_yakuzen_posts(keyword)
        if not posts:
            line_bot_api.push_message(user_id, TextSendMessage(text=f"😢 「{keyword}」に一致する記事が見つかりませんでした。"))
            return
        post = posts[0]
        title = post['title']['rendered']
        line_bot_api.push_message(user_id, TextSendMessage(text=f"📄 「{title}」をリライトします！"))
        process_yakuzen_rewrite(user_id, post['id'], title, post.get('content', {}).get('rendered', ''))
    except Exception as e:
        print(f"rewrite_by_keyword error: {e}")
        line_bot_api.push_message(user_id, TextSendMessage(text=f"😢 エラーが発生しました。\n{str(e)[:150]}"))


def rewrite_yakuzen_by_slug(user_id, slug):
    """URLのslugで記事を特定してリライト"""
    try:
        wp_url, wp_user, wp_pass = get_yakuzen_wp_creds()
        res = requests.get(
            f'{wp_url}/wp-json/wp/v2/posts',
            auth=(wp_user, wp_pass),
            params={'slug': slug, '_fields': 'id,title,content'},
            timeout=15
        )
        if res.status_code != 200 or not res.json():
            line_bot_api.push_message(user_id, TextSendMessage(text=f"😢 記事が見つかりませんでした。\nslug: {slug}"))
            return
        post = res.json()[0]
        process_yakuzen_rewrite(user_id, post['id'], post['title']['rendered'], post['content']['rendered'])
    except Exception as e:
        print(f"rewrite_by_slug error: {e}")
        line_bot_api.push_message(user_id, TextSendMessage(text=f"😢 エラーが発生しました。\n{str(e)[:150]}"))


def process_yakuzen_rewrite(user_id, post_id, post_title, post_content, instruction=''):
    try:
        article_md = generate_yakuzen_rewrite(post_title, post_content, instruction)
        lines = article_md.split('\n')
        new_title = lines[0].lstrip('# ').strip()
        new_content = '\n'.join(lines[1:]).lstrip('\n')
        keyword = generate_pexels_keyword(new_title)
        image_url = fetch_pexels_image_url(keyword)
        media_id = upload_image_to_yakuzen_wp(image_url, new_title) if image_url else None
        new_cat_id = detect_category_id(new_title, new_content)
        _, link = post_to_yakuzen_wp(new_title, new_content, post_id=post_id, status='publish',
                                     featured_media_id=media_id, categories=[new_cat_id])
        try_post_to_pinterest(new_title, link, new_content, image_url=image_url)
        line_bot_api.push_message(user_id, TextSendMessage(text=f"✅ 睡眠記事をリライト・更新しました！\n\n📝 {new_title}\n🔗 {link}"))
        send_sns_messages(user_id, new_title, link, image_url, new_content)
    except Exception as e:
        print(f"Yakuzen rewrite error: {e}")
        line_bot_api.push_message(user_id, TextSendMessage(text=f"😢 リライト中にエラーが発生しました。\n{str(e)[:150]}"))


# ========== SNS投稿セット生成 ==========

def generate_instagram_caption(title, content_md, article_url):
    """Instagram @foodmakehealth 用キャプションを生成"""
    response = anthropic_client.messages.create(
        model='claude-haiku-4-5-20251001',
        max_tokens=300,
        messages=[{
            'role': 'user',
            'content': f"""睡眠・健康情報を発信するInstagram（@foodmakehealth）用キャプションを作成してください。

記事タイトル：{title}
記事冒頭：{content_md[:300]}

条件：
- 1行目：共感を呼ぶ一言（絵文字1個＋睡眠の悩みへの共感）
- 2〜4行目：今日から使える睡眠改善のポイントを箇条書き（絵文字付き）
- 最後：「詳しくはプロフのリンクから🔗」
- ハッシュタグ5〜8個（#睡眠 #不眠 #睡眠改善 #女性の健康 #更年期 など記事に合ったもの）
- 全体150文字以内
- キャプションのみ出力"""
        }]
    )
    return response.content[0].text.strip()


def extract_slide_content(title, content_md):
    """記事から2枚目（悩み）・3枚目（改善tips）用テキストを抽出"""
    response = anthropic_client.messages.create(
        model='claude-haiku-4-5-20251001',
        max_tokens=400,
        messages=[{
            'role': 'user',
            'content': f"""睡眠・健康ブログ記事から以下を抽出してください。

タイトル：{title}
本文：{content_md[:2000]}

以下のJSON形式のみで回答（説明不要）：
{{
  "concerns": ["悩み1", "悩み2", "悩み3"],
  "tips": ["改善策1", "改善策2", "改善策3"]
}}

条件：
- concerns：3〜4個、記事が対象とする睡眠の悩み（例：「なかなか寝つけない」「夜中に目が覚める」）
- tips：3〜4個、今日から実践できる具体的な改善策（例：「就寝90分前に入浴する」）、10文字以内で簡潔に"""
        }]
    )
    raw = response.content[0].text.strip()
    match = re.search(r'\{.*\}', raw, re.DOTALL)
    if match:
        return json.loads(match.group())
    return {'concerns': [], 'tips': []}


def _resolve_font_path():
    """フォントパスを返す。ローカルになければGitHubからダウンロード"""
    local = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'fonts', 'NotoSansJP-Bold.otf')
    if os.path.exists(local):
        print(f"[Font] local found: {local}")
        return local
    tmp = '/tmp/NotoSansJP-Bold.otf'
    if os.path.exists(tmp):
        print(f"[Font] tmp cache: {tmp}")
        return tmp
    try:
        import requests as _req
        url = "https://github.com/googlefonts/noto-cjk/raw/main/Sans/SubsetOTF/JP/NotoSansJP-Bold.otf"
        r = _req.get(url, timeout=30)
        r.raise_for_status()
        with open(tmp, 'wb') as f:
            f.write(r.content)
        print(f"[Font] downloaded to {tmp}")
        return tmp
    except Exception as e:
        print(f"[Font] download failed: {e}")
        return None


def build_slide1_color(title: str) -> bytes:
    """Pexels画像なし時のフォールバック：グラデーション背景でタイトルスライドを生成"""
    from PIL import Image, ImageDraw, ImageFont
    from io import BytesIO

    W, H = 1080, 1080
    img = Image.new('RGB', (W, H))
    draw = ImageDraw.Draw(img)
    for y in range(H):
        r = int(20 + 40 * y / H)
        g = int(30 + 30 * y / H)
        b = int(60 + 40 * y / H)
        draw.line([(0, y), (W, y)], fill=(r, g, b))

    font_path = _resolve_font_path()
    if not font_path:
        raise RuntimeError("Font not available")

    font_title = ImageFont.truetype(font_path, 60)
    font_footer = ImageFont.truetype(font_path, 34)

    max_chars = 14
    lines, t = [], title
    while len(t) > max_chars:
        lines.append(t[:max_chars])
        t = t[max_chars:]
    lines.append(t)

    line_height = 72
    y_start = (H - line_height * len(lines)) // 2 - 40
    for line in lines:
        bbox = draw.textbbox((0, 0), line, font=font_title)
        x = (W - (bbox[2] - bbox[0])) // 2
        draw.text((x + 2, y_start + 2), line, font=font_title, fill=(0, 0, 0, 180))
        draw.text((x, y_start), line, font=font_title, fill=(255, 255, 255, 255))
        y_start += line_height

    footer = "@foodmakehealth"
    bbox_f = draw.textbbox((0, 0), footer, font=font_footer)
    draw.text(((W - (bbox_f[2] - bbox_f[0])) // 2, H - 80), footer, font=font_footer, fill=(200, 180, 150))

    buf = BytesIO()
    img.save(buf, format='JPEG', quality=90)
    return buf.getvalue()


def build_slide1_image(title: str, img_url: str) -> bytes:
    """1枚目スライド：Pexels画像にタイトルオーバーレイを合成してJPEGバイト列を返す"""
    from PIL import Image, ImageDraw, ImageFont
    from io import BytesIO

    r = requests.get(img_url, timeout=15, headers={'User-Agent': 'Mozilla/5.0'}, allow_redirects=True)
    r.raise_for_status()
    img = Image.open(BytesIO(r.content)).convert('RGBA')
    img = img.resize((1080, 1080), Image.LANCZOS)

    overlay = Image.new('RGBA', (1080, 1080), (0, 0, 0, 0))
    draw_ov = ImageDraw.Draw(overlay)
    for y in range(1080):
        alpha = int(180 * (y / 1080))
        draw_ov.line([(0, y), (1080, y)], fill=(0, 0, 0, alpha))
    img = Image.alpha_composite(img, overlay)

    draw = ImageDraw.Draw(img)
    font_path = _resolve_font_path()
    if not font_path:
        raise RuntimeError("Font not available")

    with open(font_path, 'rb') as f:
        font_data = f.read()
    font_title = ImageFont.truetype(BytesIO(font_data), 60)
    font_footer = ImageFont.truetype(BytesIO(font_data), 34)

    max_chars = 14
    lines = []
    t = title
    while len(t) > max_chars:
        lines.append(t[:max_chars])
        t = t[max_chars:]
    lines.append(t)

    line_height = 72
    y_start = 1080 - line_height * len(lines) - 120
    for line in lines:
        bbox = draw.textbbox((0, 0), line, font=font_title)
        x = (1080 - (bbox[2] - bbox[0])) // 2
        draw.text((x + 2, y_start + 2), line, font=font_title, fill=(0, 0, 0, 200))
        draw.text((x, y_start), line, font=font_title, fill=(255, 255, 255, 255))
        y_start += line_height

    footer = "@foodmakehealth"
    bbox_f = draw.textbbox((0, 0), footer, font=font_footer)
    x_f = (1080 - (bbox_f[2] - bbox_f[0])) // 2
    draw.text((x_f + 1, 1080 - 55 + 1), footer, font=font_footer, fill=(0, 0, 0, 180))
    draw.text((x_f, 1080 - 55), footer, font=font_footer, fill=(255, 255, 255, 200))

    buf = BytesIO()
    img.convert('RGB').save(buf, format='JPEG', quality=90)
    return buf.getvalue()


def build_slide_image(header, items, accent_color=(139, 105, 20)):
    """テキストリスト画像を生成してJPEGバイト列を返す"""
    from PIL import Image, ImageDraw, ImageFont
    from io import BytesIO

    W, H = 1080, 1080
    bg_color = (245, 240, 232)
    img = Image.new('RGB', (W, H), bg_color)
    draw = ImageDraw.Draw(img)
    font_path = _resolve_font_path()
    if not font_path:
        raise RuntimeError("Font not available")

    # アクセントライン上部
    draw.rectangle([(0, 0), (W, 12)], fill=accent_color)
    draw.rectangle([(0, H - 12), (W, H)], fill=accent_color)

    # ヘッダー
    font_h = ImageFont.truetype(font_path, 52)
    bbox = draw.textbbox((0, 0), header, font=font_h)
    draw.text(((W - (bbox[2] - bbox[0])) // 2, 80), header, font=font_h, fill=accent_color)

    # 区切り線
    draw.rectangle([(80, 160), (W - 80, 166)], fill=accent_color)

    # アイテムリスト
    font_item = ImageFont.truetype(font_path, 42)
    y = 210
    for item in items:
        draw.text((100, y), f"• {item}", font=font_item, fill=(60, 40, 20))
        y += 80

    # フッター
    font_f = ImageFont.truetype(font_path, 34)
    footer = "@foodmakehealth"
    bbox_f = draw.textbbox((0, 0), footer, font=font_f)
    draw.text(((W - (bbox_f[2] - bbox_f[0])) // 2, H - 80), footer, font=font_f, fill=accent_color)

    buf = BytesIO()
    img.convert('RGB').save(buf, format='JPEG', quality=90)
    return buf.getvalue()


def upload_bytes_to_yakuzen_wp(img_bytes, filename):
    """画像バイト列をWPメディアにアップロードしてURLを返す"""
    wp_url, wp_user, wp_pass = get_yakuzen_wp_creds()
    try:
        res = requests.post(
            f"{wp_url}/wp-json/wp/v2/media",
            auth=(wp_user, wp_pass),
            headers={
                'Content-Disposition': f'attachment; filename="{filename}"',
                'Content-Type': 'image/jpeg'
            },
            data=img_bytes,
            timeout=30
        )
        if res.status_code == 201:
            return res.json()['source_url']
    except Exception as e:
        print(f"WP bytes upload error: {e}")
    return None


def build_carousel_images(title, content_md, slide1_url):
    """1〜3枚目スライド画像を生成してURLリストを返す。エラー文字列も返す"""
    import traceback
    errors = []
    urls = []
    slug = re.sub(r'[^a-z0-9-]', '-', title[:20].encode('ascii', 'ignore').decode())[:20] or 'yakuzen'

    # 1枚目：タイトルオーバーレイ画像（Pexels失敗時は色背景フォールバック）
    try:
        if slide1_url:
            img1 = build_slide1_image(title, slide1_url)
        else:
            img1 = build_slide1_color(title)
            errors.append("slide1: Pexels画像なし→色背景で生成")
        url1 = upload_bytes_to_yakuzen_wp(img1, f"slide1-{slug}.jpg")
        urls.append(url1 if url1 else slide1_url or '')
        if not url1:
            errors.append("slide1: WPアップロード失敗")
        else:
            print(f"[Carousel] slide1 url: {url1}")
    except Exception as e:
        tb = traceback.format_exc()
        if slide1_url:
            urls.append(slide1_url)
        errors.append(f"slide1: {e}")
        print(f"[Carousel] slide1 error: {e}\n{tb}")

    # 2・3枚目：悩み・改善tipsスライド
    try:
        data = extract_slide_content(title, content_md)
    except Exception as e:
        errors.append(f"extract: {e}")
        return urls, errors

    for label, key, filename_prefix in [("こんな悩みに", "concerns", "slide2"), ("今夜から試せること", "tips", "slide3")]:
        try:
            img = build_slide_image(label, data.get(key, []))
            url = upload_bytes_to_yakuzen_wp(img, f"{filename_prefix}-{slug}.jpg")
            if url:
                urls.append(url)
                print(f"[Carousel] {filename_prefix} url: {url}")
            else:
                errors.append(f"{filename_prefix}: WPアップロード失敗")
        except Exception as e:
            tb = traceback.format_exc()
            errors.append(f"{filename_prefix}: {e}\n{tb[-300:]}")
            print(f"[Carousel] {filename_prefix} error: {e}\n{tb}")

    return urls, errors


def build_sns_message(title, link, image_url, content_md):
    """後方互換用：send_sns_messagesに移行済み。文字列を返すだけにする"""
    ig_caption = generate_instagram_caption(title, content_md, link)
    pin = generate_yakuzen_pin_text(title, link, content_md)
    return f"キャプション：{ig_caption}\nPinterestタイトル：{pin.get('pin_title', '')}"


def send_sns_messages(user_id, title, link, image_url, content_md):
    """Instagram・Pinterest用のコピペパーツをメッセージ分割して送信"""
    from clients import line_bot_api
    from linebot.models import TextSendMessage

    ig_caption = generate_instagram_caption(title, content_md, link)
    pin = generate_yakuzen_pin_text(title, link, content_md)
    carousel_urls, carousel_errors = build_carousel_images(title, content_md, image_url)

    # 画像URL（保存用・コピペ不要なのでまとめて1通）
    img_lines = "\n".join([f"📸 {i}枚目：{url}" for i, url in enumerate(carousel_urls, 1)])
    if carousel_errors:
        img_lines += "\n⚠️ " + "・".join(carousel_errors[:2])
    line_bot_api.push_message(user_id, TextSendMessage(
        text=f"📸 Instagramカルーセル画像（3枚保存）\nボード：{pin.get('board', '')}\n\n{img_lines if img_lines else '（画像なし）'}"
    ))

    # Instagramキャプション（コピペ用・単独メッセージ）
    line_bot_api.push_message(user_id, TextSendMessage(text=ig_caption))

    # Pinterestタイトル（コピペ用・単独メッセージ）
    line_bot_api.push_message(user_id, TextSendMessage(text=pin.get('pin_title', '')))

    # Pinterest説明文（コピペ用・単独メッセージ）
    line_bot_api.push_message(user_id, TextSendMessage(text=pin.get('description', '')))


# ========== Pinterest連携 ==========

def generate_pexels_keyword(title):
    """日本語タイトルから完成料理写真が出る英語キーワードを生成"""
    response = anthropic_client.messages.create(
        model='claude-haiku-4-5-20251001',
        max_tokens=30,
        messages=[{
            'role': 'user',
            'content': f"""薬膳ブログ記事タイトルから、Pexelsで料理の完成品写真を検索する英語キーワードを1〜3語で出力してください。
必ず料理・食事の完成品写真が出るキーワードにすること。キーワードのみ出力。

例：
「生姜スープで冷えを改善」→ japanese ginger soup bowl
「黒豆の薬膳レシピ」→ healthy black bean dish
「むくみに効く薬膳粥」→ japanese congee porridge

タイトル：{title}"""
        }]
    )
    return response.content[0].text.strip()


def upload_image_to_yakuzen_wp(image_url, title):
    """PexelsのURLをWPメディアにアップロードしてmedia_idを返す"""
    wp_url, wp_user, wp_pass = get_yakuzen_wp_creds()
    try:
        img_data = requests.get(image_url, timeout=15).content
        filename = f"yakuzen-{re.sub(r'[^a-z0-9]', '-', title[:30])}.jpg"
        res = requests.post(
            f"{wp_url}/wp-json/wp/v2/media",
            auth=(wp_user, wp_pass),
            headers={
                'Content-Disposition': f'attachment; filename="{filename}"',
                'Content-Type': 'image/jpeg'
            },
            data=img_data,
            timeout=30
        )
        if res.status_code == 201:
            return res.json()['id']
    except Exception as e:
        print(f"WP media upload error: {e}")
    return None


def fetch_pexels_image_url(keyword):
    """PexelsからキーワードにマッチするサムネイルのURLを返す"""
    pexels_key = os.environ.get('PEXELS_API_KEY')
    if not pexels_key:
        return None
    try:
        res = requests.get(
            'https://api.pexels.com/v1/search',
            headers={'Authorization': pexels_key},
            params={'query': keyword, 'per_page': 1, 'orientation': 'squarish'},
            timeout=10
        )
        photos = res.json().get('photos', [])
        if photos:
            return photos[0]['src']['large2x']
    except Exception as e:
        print(f"Pexels URL error: {e}")
    return None


def guess_yakuzen_board(title):
    for keyword, board in YAKUZEN_BOARD_RULES.items():
        if keyword in title:
            return board
    return '不眠・睡眠改善'


def generate_yakuzen_pin_text(title, url, content_md):
    board = guess_yakuzen_board(title)
    response = anthropic_client.messages.create(
        model='claude-haiku-4-5-20251001',
        max_tokens=300,
        messages=[{
            'role': 'user',
            'content': f"""以下の薬膳ブログ記事のPinterestピン用テキストを作成してください。

タイトル：{title}
本文（冒頭）：{content_md[:500]}

要件：
- ピンタイトル：40字以内・興味を引くキャッチーな表現
- 説明文：150字以内・絵文字2〜3個・ハッシュタグ3〜4個

以下のJSON形式のみで回答（説明不要）：
{{"pin_title": "ピンタイトル", "description": "説明文"}}"""
        }]
    )
    raw = response.content[0].text.strip()
    match = re.search(r'\{.*\}', raw, re.DOTALL)
    if match:
        result = json.loads(match.group())
        return {
            'board': board,
            'pin_title': result.get('pin_title', title),
            'description': result.get('description', ''),
            'url': url,
        }
    return {'board': board, 'pin_title': title, 'description': '', 'url': url}


def get_pinterest_board_id(board_name):
    board_env = {
        '不眠・睡眠改善': 'PINTEREST_BOARD_SLEEP',
        '更年期×睡眠': 'PINTEREST_BOARD_MENOPAUSE',
        '薬膳×睡眠': 'PINTEREST_BOARD_YAKUZEN_SLEEP',
        '子ども・家族の睡眠': 'PINTEREST_BOARD_KIDS_SLEEP',
    }
    env_key = board_env.get(board_name, 'PINTEREST_BOARD_SLEEP')
    return os.environ.get(env_key, '')


_SLEEP_KW_PRIORITY = [
    '不眠', '睡眠', '眠れ', '眠り', '熟睡', '入眠', '寝つき', '中途覚醒',
    '早朝覚醒', '睡眠の質', '更年期', 'ほてり', '夜中に目', '朝起き',
    'メラトニン', 'セロトニン', '寝ても疲れ', '睡眠外来',
]


def _fetch_sleep_kw_data(creds, days=90, limit=100):
    """Search Consoleから睡眠系優先でKWデータを取得（睡眠系を先頭に並べる）"""
    from googleapiclient.discovery import build
    service = build('searchconsole', 'v1', credentials=creds)
    end_date = datetime.date.today()
    start_date = end_date - datetime.timedelta(days=days)
    result = service.searchanalytics().query(
        siteUrl='https://foodmakehealth.com/',
        body={
            'startDate': start_date.isoformat(),
            'endDate': end_date.isoformat(),
            'dimensions': ['query'],
            'rowLimit': limit,
            'orderBy': [{'fieldName': 'impressions', 'sortOrder': 'DESCENDING'}],
        }
    ).execute()
    rows = result.get('rows', [])
    sleep_rows = [r for r in rows if any(kw in r['keys'][0] for kw in _SLEEP_KW_PRIORITY)]
    other_rows = [r for r in rows if r not in sleep_rows]
    return sleep_rows + other_rows


def kw_auto_rewrite(user_id, creds):
    """KW選定→リライト全自動：Search Consoleで睡眠系11〜30位の伸びしろ記事を選んでリライト"""
    try:
        if not creds:
            line_bot_api.push_message(user_id, TextSendMessage(
                text="😢 Google認証情報が取得できませんでした。"
            ))
            return

        line_bot_api.push_message(user_id, TextSendMessage(
            text="🔍 Search Consoleで睡眠系KWを分析中...\nリライト対象を自動選定します！"
        ))

        rows = _fetch_sleep_kw_data(creds)
        if not rows:
            line_bot_api.push_message(user_id, TextSendMessage(
                text="😢 Search Consoleのデータが取得できませんでした。"
            ))
            return

        fall_zone = [r for r in rows if 11 <= r.get('position', 0) <= 30]
        fall_zone.sort(key=lambda x: x.get('impressions', 0), reverse=True)
        if not fall_zone:
            fall_zone = sorted(rows, key=lambda x: x.get('impressions', 0), reverse=True)[:15]

        top_kws = [r['keys'][0] for r in fall_zone[:10]]

        posts = get_all_yakuzen_posts()
        if not posts:
            line_bot_api.push_message(user_id, TextSendMessage(text="😢 記事の取得に失敗しました。"))
            return

        post_list_text = '\n'.join([
            f"ID:{p['id']} タイトル:{p['title']['rendered']}"
            for p in posts[:80]
        ])

        response = anthropic_client.messages.create(
            model='claude-haiku-4-5-20251001',
            max_tokens=200,
            messages=[{
                'role': 'user',
                'content': f"""Search Consoleで伸びしろがある睡眠系キーワードと記事一覧から、
リライトで最も順位が上がりそうな記事を1本選んでください。

伸びしろKW（表示多い・順位11〜30位）：
{chr(10).join(top_kws)}

記事一覧：
{post_list_text}

以下のJSON形式のみで回答（説明不要）：
{{"id": 記事ID, "keyword": "狙うキーワード", "reason": "選んだ理由（1文）"}}"""
            }]
        )

        raw = response.content[0].text.strip()
        match = re.search(r'\{.*\}', raw, re.DOTALL)
        if not match:
            line_bot_api.push_message(user_id, TextSendMessage(text="😢 記事選定に失敗しました。"))
            return

        selected = json.loads(match.group())
        post_id = selected['id']
        keyword = selected.get('keyword', '')
        reason = selected.get('reason', '')

        wp_url, wp_user, wp_pass = get_yakuzen_wp_creds()
        res = requests.get(
            f'{wp_url}/wp-json/wp/v2/posts/{post_id}',
            auth=(wp_user, wp_pass),
            params={'_fields': 'id,title,content'},
            timeout=15
        )
        if res.status_code != 200:
            line_bot_api.push_message(user_id, TextSendMessage(text="😢 記事の取得に失敗しました。"))
            return

        post = res.json()
        post_title = post['title']['rendered']
        post_content = post['content']['rendered']

        line_bot_api.push_message(user_id, TextSendMessage(
            text=f"🎯 狙うKW：「{keyword}」\n📄 「{post_title}」をリライト開始！\n理由：{reason}\n\n少しお待ちください！"
        ))

        instruction = f"「{keyword}」で検索上位を狙うようにリライトしてください。"
        process_yakuzen_rewrite(user_id, post_id, post_title, post_content, instruction)

    except Exception as e:
        print(f"kw_auto_rewrite error: {e}")
        import traceback; traceback.print_exc()
        line_bot_api.push_message(user_id, TextSendMessage(
            text=f"😢 KW選定中にエラーが発生しました。\n{str(e)[:150]}"
        ))


def kw_auto_new_article(user_id, creds):
    """KW選定→新規記事全自動：Search Consoleで未開拓の睡眠系KWを選んで新規作成"""
    try:
        if not creds:
            line_bot_api.push_message(user_id, TextSendMessage(
                text="😢 Google認証情報が取得できませんでした。"
            ))
            return

        line_bot_api.push_message(user_id, TextSendMessage(
            text="🔍 Search Consoleで未開拓の睡眠系KWを分析中...\n新規記事テーマを自動選定します！"
        ))

        rows = _fetch_sleep_kw_data(creds)
        if not rows:
            line_bot_api.push_message(user_id, TextSendMessage(
                text="😢 Search Consoleのデータが取得できませんでした。"
            ))
            return

        untapped = [r for r in rows if r.get('impressions', 0) >= 100 and r.get('clicks', 0) <= 3]
        untapped.sort(key=lambda x: x.get('impressions', 0), reverse=True)
        if not untapped:
            untapped = rows[:20]

        top_kws = [r['keys'][0] for r in untapped[:15]]
        recent_titles = _get_recent_yakuzen_titles(20)
        existing_text = '\n'.join(recent_titles) if recent_titles else ''

        today = datetime.date.today()
        month = today.month
        seasonal_hint = {
            1: "冬・冷え", 2: "春の準備・花粉", 3: "春・自律神経",
            4: "新生活ストレス", 5: "五月病・疲れ", 6: "梅雨・だるさ",
            7: "熱帯夜・睡眠浅い", 8: "残暑・夏疲れ", 9: "秋・乾燥",
            10: "秋冬の移行期", 11: "冬・冷え×不眠", 12: "年末ストレス",
        }.get(month, "季節の変わり目")

        response = anthropic_client.messages.create(
            model='claude-haiku-4-5-20251001',
            max_tokens=150,
            messages=[{
                'role': 'user',
                'content': f"""Search Consoleの検索KWから新規記事テーマを1つ選んでください。

検索されているKW（表示多い・クリック少ない＝記事が弱い）：
{chr(10).join(top_kws)}

今の季節：{month}月（{seasonal_hint}）
既存記事（重複禁止）：
{existing_text[:500]}

条件：
- 「睡眠×医療×薬膳」視点・30〜50代女性ターゲット
- 「医師監修」or「内科医が解説」を含む32字以内のSEOタイトル

以下のJSON形式のみで回答（説明不要）：
{{"keyword": "狙うキーワード", "title": "記事タイトル"}}"""
            }]
        )

        raw = response.content[0].text.strip()
        match = re.search(r'\{.*\}', raw, re.DOTALL)
        if not match:
            line_bot_api.push_message(user_id, TextSendMessage(text="😢 テーマ選定に失敗しました。"))
            return

        selected = json.loads(match.group())
        keyword = selected.get('keyword', '')
        title = selected.get('title', '')

        line_bot_api.push_message(user_id, TextSendMessage(
            text=f"🎯 狙うKW：「{keyword}」\n📝 テーマ：「{title}」\n\n記事作成中...少しお待ちください！（1〜2分かかります）"
        ))

        process_yakuzen_new_article(user_id, topic=title)

    except Exception as e:
        print(f"kw_auto_new_article error: {e}")
        import traceback; traceback.print_exc()
        line_bot_api.push_message(user_id, TextSendMessage(
            text=f"😢 KW分析中にエラーが発生しました。\n{str(e)[:150]}"
        ))


def get_pinterest_access_token():
    """refresh_tokenでaccess_tokenを取得。なければ静的トークンを使用"""
    app_id = os.environ.get('PINTEREST_APP_ID')
    app_secret = os.environ.get('PINTEREST_APP_SECRET')
    refresh_token = os.environ.get('PINTEREST_REFRESH_TOKEN')
    if all([app_id, app_secret, refresh_token]):
        try:
            import base64
            creds = base64.b64encode(f"{app_id}:{app_secret}".encode()).decode()
            res = requests.post(
                'https://api.pinterest.com/v5/oauth/token',
                headers={
                    'Authorization': f'Basic {creds}',
                    'Content-Type': 'application/x-www-form-urlencoded'
                },
                data={
                    'grant_type': 'refresh_token',
                    'refresh_token': refresh_token,
                    'scope': 'pins:write,boards:read'
                },
                timeout=15
            )
            if res.status_code == 200:
                data = res.json()
                new_refresh = data.get('refresh_token')
                if new_refresh:
                    print(f"[Pinterest] New refresh_token (update Render env): {new_refresh}")
                return data.get('access_token')
            print(f"[Pinterest] Token refresh failed: {res.status_code} {res.text[:200]}")
        except Exception as e:
            print(f"[Pinterest] Token refresh error: {e}")
    return os.environ.get('PINTEREST_ACCESS_TOKEN')


def post_pin_to_pinterest(pin_title, description, board_id, link, image_url):
    access_token = get_pinterest_access_token()
    if not access_token or not board_id:
        return False, '環境変数未設定'
    res = requests.post(
        'https://api.pinterest.com/v5/pins',
        headers={'Authorization': f'Bearer {access_token}', 'Content-Type': 'application/json'},
        json={
            'board_id': board_id,
            'title': pin_title,
            'description': description,
            'link': link,
            'media_source': {'source_type': 'image_url', 'url': image_url},
        },
        timeout=15
    )
    if res.status_code == 201:
        return True, res.json().get('id', '')
    return False, f"{res.status_code}: {res.text[:100]}"


def try_post_to_pinterest(title, article_url, content_md, image_url=None):
    """Pinterest投稿を試みる。成功時は投稿完了メッセージ、未設定時はピンテキストを返す"""
    try:
        pin = generate_yakuzen_pin_text(title, article_url, content_md)
        board_id = get_pinterest_board_id(pin['board'])
        if not image_url:
            image_url = fetch_pexels_image_url(generate_pexels_keyword(title))

        if image_url and board_id and os.environ.get('PINTEREST_ACCESS_TOKEN'):
            success, result = post_pin_to_pinterest(
                pin['pin_title'], pin['description'], board_id, article_url, image_url
            )
            if success:
                return f"📌 Pinterestにも投稿しました！\nボード：{pin['board']}"
            print(f"Pinterest post failed: {result}")

        return (
            f"📌 Pinterestピンテキスト：\n"
            f"ボード：{pin['board']}\n"
            f"タイトル：{pin['pin_title']}\n"
            f"説明：{pin['description']}"
        )
    except Exception as e:
        print(f"Pinterest error: {e}")
        return ''



# --- 自動ブログ投稿スケジューラ関数 ---
def auto_blog_new():
    from phases import phase4_write, phase5_quality, phase6_publish
    from pathlib import Path

    kw_file = Path(__file__).parent / "keywords_new.txt"
    lines = [l.strip() for l in kw_file.read_text(encoding='utf-8').splitlines() if l.strip()]
    if not lines:
        return
    keyword = lines[0]
    kw_file.write_text('\n'.join(lines[1:]) + '\n', encoding='utf-8')

    def _run():
        try:
            design = f"# テーマ: {keyword}\n\n共感→原因→改善→薬膳補助→まとめ の構成で執筆してください。"
            draft, _ = phase4_write.run(keyword, design)
            final, score, passed, _ = phase5_quality.run(keyword, draft)
            uid = os.environ.get('LINE_USER_ID', '')
            if passed:
                phase6_publish.run(keyword, final)
            elif score >= 80:
                # 95点未達でも80点以上なら投稿（LINEに通知）
                if uid:
                    line_bot_api.push_message(uid, TextSendMessage(
                        text=f"⚠️ 品質チェック {score}点（95点未達）でも投稿しました\n「{keyword}」"
                    ))
                phase6_publish.run(keyword, final)
            else:
                if uid:
                    line_bot_api.push_message(uid, TextSendMessage(
                        text=f"❌ 自動投稿スキップ（品質{score}点）\nキーワード：「{keyword}」\n手動で確認が必要です"
                    ))
        except Exception as e:
            print(f"auto_blog_new error: {e}")
            uid = os.environ.get('LINE_USER_ID', '')
            if uid:
                try:
                    line_bot_api.push_message(uid, TextSendMessage(
                        text=f"❌ 自動投稿エラー（新規）\n{str(e)[:200]}"
                    ))
                except Exception:
                    pass
    threading.Thread(target=_run, daemon=True).start()

# 水・土 8:00：7stepパイプラインで旧レシピ記事を自動リライト
def auto_blog_rewrite():
    from phases import phase4_rewrite, phase5_quality, phase6_publish
    import requests as req

    wp_url  = os.environ.get('YAKUZEN_WP_URL', 'https://foodmakehealth.com')
    wp_user = os.environ.get('YAKUZEN_WP_USER', 'makiko01035')
    wp_pass = os.environ.get('YAKUZEN_WP_APP_PASSWORD', '')

    def _run():
        try:
            res = req.get(f"{wp_url}/wp-json/wp/v2/posts",
                          auth=(wp_user, wp_pass),
                          params={"categories": 9, "orderby": "date", "order": "asc",
                                  "per_page": 1, "status": "publish",
                                  "_fields": "id,title,link"},
                          timeout=10)
            posts = res.json()
            if not posts:
                return
            post_id = posts[0]["id"]
            title   = posts[0]["title"]["rendered"]
            keyword = ' '.join(title.split()[:5])
            draft, _ = phase4_rewrite.run(keyword, "")
            final, score, passed, _ = phase5_quality.run(keyword, draft)
            uid = os.environ.get('LINE_USER_ID', '')
            if passed:
                phase6_publish.run_update(keyword, final, post_id)
            elif score >= 80:
                if uid:
                    line_bot_api.push_message(uid, TextSendMessage(
                        text=f"⚠️ 品質チェック {score}点（95点未達）でも投稿しました\n「{keyword}」"
                    ))
                phase6_publish.run_update(keyword, final, post_id)
            else:
                if uid:
                    line_bot_api.push_message(uid, TextSendMessage(
                        text=f"❌ 自動リライトスキップ（品質{score}点）\n記事ID:{post_id}「{title}」\n手動で確認が必要です"
                    ))
        except Exception as e:
            print(f"auto_blog_rewrite error: {e}")
            uid = os.environ.get('LINE_USER_ID', '')
            if uid:
                try:
                    line_bot_api.push_message(uid, TextSendMessage(
                        text=f"❌ 自動投稿エラー（リライト）\n{str(e)[:200]}"
                    ))
                except Exception:
                    pass
    threading.Thread(target=_run, daemon=True).start()
