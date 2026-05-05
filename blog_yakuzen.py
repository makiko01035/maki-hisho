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

YAKUZEN_BOARD_RULES = {
    '季節': '季節の養生', '養生': '季節の養生', '花粉': '季節の養生',
    '春': '季節の養生', '夏': '季節の養生', '秋': '季節の養生', '冬': '季節の養生',
    'レシピ': '薬膳レシピ', '食材': '薬膳レシピ', '効能': '薬膳レシピ',
    '基礎': '薬膳の基礎知識', '中医': '薬膳の基礎知識', '体質': '薬膳の基礎知識',
    '資格': '薬膳資格', '講座': '薬膳資格',
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
        sns_msg = build_sns_message(new_title, link, image_url, new_content)
        msg = f"✅ リライト・更新完了！\n\n📝 {new_title}\n🔗 {link}\n\n{sns_msg}"
        line_bot_api.push_message(user_id, TextSendMessage(text=msg))

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


# 症状カテゴリのローテーションリスト（この順番で均等に使う）
YAKUZEN_CATEGORY_ROTATION = [
    "冷え",
    "むくみ",
    "疲労・だるさ",
    "肌荒れ・美肌",
    "便秘",
    "PMS・生理痛",
    "不眠",
    "胃腸不調・食欲不振",
    "花粉症・アレルギー",
    "頭痛・肩こり",
    "貧血",
    "免疫力アップ",
    "子ども・家族向け",
    "目の疲れ・ドライアイ",
    "乾燥・保湿",
    "五月病・気分の落ち込み",
    "夏バテ",
    "冬の乾燥・風邪予防",
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

    seasonal_food = {
        1: "大根・ごぼう・ねぎ・生姜",
        2: "菜の花・ほうれん草・いちご",
        3: "菜の花・たけのこ・春キャベツ",
        4: "たけのこ・春キャベツ・豆類・いちご",
        5: "そら豆・アスパラ・新玉ねぎ",
        6: "梅・きゅうり・トマト・枝豆",
        7: "ゴーヤ・とうもろこし・なす・トマト",
        8: "オクラ・冬瓜・桃・スイカ",
        9: "さつまいも・栗・梨・きのこ",
        10: "さつまいも・柿・れんこん・きのこ",
        11: "ごぼう・大根・柚子・さつまいも",
        12: "大根・白菜・ゆず・くるみ",
    }.get(month, "旬の野菜")

    response = anthropic_client.messages.create(
        model='claude-haiku-4-5-20251001',
        max_tokens=120,
        messages=[{
            'role': 'user',
            'content': f"""薬膳ブログの記事タイトルを1つ作ってください。

テーマカテゴリ：{next_cat}
今日：{today}（{month}月）／旬の食材：{seasonal_food}

条件：
- 上記カテゴリにまつわる薬膳レシピ・食材・食事法の記事
- 旬の食材を1〜2つ組み合わせる
- 20〜40代女性が検索しそうなSEOタイトル
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


AFFILIATE_BOOKS = {
    'kids': {
        'url': 'https://amzn.asia/d/07txS5CF',
        'title': '薬に頼らずのびのび育てる！こども薬膳',
        'desc': 'お子さんの体質改善・風邪予防・食欲不振など、日常のごはんで対応できる薬膳レシピを紹介。',
    },
    'soup': {
        'url': 'https://amzn.asia/d/09tQHZKL',
        'title': '薬膳スープジャー弁当 朝10分で作れる',
        'desc': '忙しい朝でも10分で完成。体を温めて整えるスープジャーレシピが満載。',
    },
    'ayurveda': {
        'raw_html': '<a href="//af.moshimo.com/af/c/click?a_id=5429137&p_id=4140&pc_id=10486&pl_id=56829" rel="nofollow" referrerpolicy="no-referrer-when-downgrade" attributionsrc><img src="//image.moshimo.com/af-img/3597/000000056829.jpg" width="240" height="120" style="border:none;"></a><img src="//i.moshimo.com/af/i/impression?a_id=5429137&p_id=4140&pc_id=10486&pl_id=56829" width="1" height="1" style="border:none;" loading="lazy">',
    },
    'default': {
        'url': 'https://amzn.asia/d/0bkhnDrf',
        'title': '「まいにちのごはん」で健康になっちゃう！ずぼら薬膳',
        'desc': '特別な食材は不要。いつものごはんに薬膳の考え方をプラスするだけで体が変わる一冊。',
    },
}


def _select_affiliate_book(title, content_md):
    ayurveda_keywords = ['アーユルヴェーダ', 'スパイス検定', 'アーユル']
    kids_keywords = ['子ども', 'こども', '子育て', '育児', '小児', 'キッズ']
    soup_keywords = ['スープ', '鍋', '温活', '温め', 'シチュー', 'お粥', '粥']
    text = title + content_md[:500]
    if any(k in text for k in ayurveda_keywords):
        return AFFILIATE_BOOKS['ayurveda']
    if any(k in text for k in kids_keywords):
        return AFFILIATE_BOOKS['kids']
    if any(k in text for k in soup_keywords):
        return AFFILIATE_BOOKS['soup']
    return AFFILIATE_BOOKS['default']


def _build_affiliate_cta(title, content_md):
    book = _select_affiliate_book(title, content_md)
    if 'raw_html' in book:
        return f'<div style="margin:30px 0;">{book["raw_html"]}</div>'
    return f'''<div style="background:#f9f6f0;border-left:4px solid #8b6914;padding:20px;margin:30px 0;border-radius:4px;">
<p style="font-weight:bold;margin:0 0 8px;">📚 もっと薬膳を日常に取り入れたい方へ</p>
<p style="margin:0 0 4px;font-weight:bold;">{book["title"]}</p>
<p style="margin:0 0 15px;font-size:0.95em;">{book["desc"]}</p>
<a href="{book["url"]}" target="_blank" rel="nofollow" style="display:inline-block;background:#ff9900;color:white;padding:10px 24px;border-radius:4px;text-decoration:none;font-weight:bold;">Amazonで見る →</a>
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
        }
        if RAKUTEN_ACCESS_KEY:
            params['accessKey'] = RAKUTEN_ACCESS_KEY
        res = requests.get(
            'https://openapi.rakuten.co.jp/ichibams/api/IchibaItem/Search/20260401',
            params=params,
            headers={'Referer': 'http://foodmakehealth.com'},
            timeout=10
        )
        print(f"[楽天API] status={res.status_code} keys={list(res.json().keys())}")
        data = res.json()
        if 'errors' in data:
            print(f"[楽天API] errors={data['errors']}")
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


def _build_rakuten_section(title, content_md=''):
    keyword = _extract_rakuten_keyword(title, content_md)
    print(f"[楽天] キーワード: {keyword}")
    items = search_rakuten_items(keyword)
    print(f"[楽天] 取得件数: {len(items)}")
    if not items:
        return ''
    intro = _build_rakuten_natural_intro(title, content_md, keyword)
    cards = ''
    for item in items:
        name = item['name'][:50] + ('...' if len(item['name']) > 50 else '')
        cards += f'''<div style="display:flex;gap:12px;margin-bottom:12px;padding:12px;border:1px solid #e8d5c5;border-radius:6px;background:#fff;">
  <a href="{item['url']}" target="_blank" rel="nofollow" style="flex-shrink:0;"><img src="{item['image']}" alt="" style="width:70px;height:70px;object-fit:cover;border-radius:4px;"></a>
  <div>
    <a href="{item['url']}" target="_blank" rel="nofollow" style="font-size:0.9em;font-weight:bold;color:#333;text-decoration:none;">{name}</a>
    <p style="margin:4px 0 8px;color:#bf0000;font-weight:bold;">¥{item['price']:,}</p>
    <a href="{item['url']}" target="_blank" rel="nofollow" style="background:#bf0000;color:#fff;padding:4px 12px;border-radius:4px;text-decoration:none;font-size:0.85em;font-weight:bold;">楽天で見る →</a>
  </div>
</div>'''
    return f'''<div style="background:#fff5f5;border-left:4px solid #bf0000;padding:20px;margin:30px 0;border-radius:4px;">
{intro}
<p style="font-weight:bold;margin:12px 0 16px;">🛒 楽天市場で探す（{keyword}）</p>
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
        sns_msg = build_sns_message(title, link, image_url, content)
        msg = f"✅ 薬膳記事を公開しました！\n\n📝 {title}\n🔗 {link}\n\n{sns_msg}"
        line_bot_api.push_message(user_id, TextSendMessage(text=msg))
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
        sns_msg = build_sns_message(new_title, link, image_url, new_content)
        msg = f"✅ 薬膳記事をリライト・更新しました！\n\n📝 {new_title}\n🔗 {link}\n\n{sns_msg}"
        line_bot_api.push_message(user_id, TextSendMessage(text=msg))
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
            'content': f"""薬膳料理研究家のInstagram（@foodmakehealth）用キャプションを作成してください。

記事タイトル：{title}
記事冒頭：{content_md[:300]}

条件：
- 1行目：共感を呼ぶ一言（絵文字1個＋症状への共感）
- 2〜4行目：レシピのポイントを箇条書き（絵文字付き）
- 最後：「詳しいレシピはプロフのリンクから🔗」
- ハッシュタグ5〜8個（#薬膳 #スーパーで買える #女性の健康 など）
- 全体150文字以内
- キャプションのみ出力"""
        }]
    )
    return response.content[0].text.strip()


def extract_slide_content(title, content_md):
    """記事から2枚目（材料）・3枚目（効能）用テキストを抽出"""
    response = anthropic_client.messages.create(
        model='claude-haiku-4-5-20251001',
        max_tokens=400,
        messages=[{
            'role': 'user',
            'content': f"""薬膳ブログ記事から以下を抽出してください。

タイトル：{title}
本文：{content_md[:2000]}

以下のJSON形式のみで回答（説明不要）：
{{
  "ingredients": ["食材1（効能一言）", "食材2（効能一言）", "食材3（効能一言）"],
  "effects": ["効能まとめ1", "効能まとめ2", "効能まとめ3"]
}}

条件：
- ingredients：3〜4個、「生姜（体を温める）」のような形式
- effects：3個、「〜を改善する」「〜に効く」などシンプルに"""
        }]
    )
    raw = response.content[0].text.strip()
    match = re.search(r'\{.*\}', raw, re.DOTALL)
    if match:
        return json.loads(match.group())
    return {'ingredients': [], 'effects': []}


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
    """2枚目・3枚目のスライド画像を生成してURLリストを返す。エラー文字列も返す"""
    import traceback
    errors = []
    urls = [slide1_url] if slide1_url else []
    try:
        data = extract_slide_content(title, content_md)
        slug = re.sub(r'[^a-z0-9-]', '-', title[:20].encode('ascii', 'ignore').decode())[:20] or 'yakuzen'
    except Exception as e:
        errors.append(f"extract: {e}")
        return urls, errors

    for label, filename_prefix in [("使う食材", "slide2"), ("体への効能", "slide3")]:
        try:
            img = build_slide_image(label, data.get('ingredients' if label == "使う食材" else 'effects', []))
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
    """Instagram・Pinterest用の投稿セットをまとめてLINEメッセージ化"""
    ig_caption = generate_instagram_caption(title, content_md, link)
    pin = generate_yakuzen_pin_text(title, link, content_md)
    carousel_urls, carousel_errors = build_carousel_images(title, content_md, image_url)

    img_lines = ""
    for i, url in enumerate(carousel_urls, 1):
        img_lines += f"📸 {i}枚目：{url}\n"

    error_note = ""
    if carousel_errors:
        error_note = f"\n⚠️ スライドエラー：\n" + "\n".join(carousel_errors[:2]) + "\n"

    return (
        f"━━━━━━━━━━━━━━\n"
        f"【Instagram @foodmakehealth】\n"
        f"↓3枚保存してカルーセル投稿\n\n"
        f"{img_lines}{error_note}\n"
        f"【キャプション】\n"
        f"{ig_caption}\n\n"
        f"━━━━━━━━━━━━━━\n"
        f"【Pinterest】\n"
        f"ボード：{pin['board']}\n"
        f"タイトル：{pin['pin_title']}\n"
        f"説明：{pin['description']}\n"
        f"画像：{carousel_urls[0] if carousel_urls else 'なし'}"
    )


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
            params={'query': keyword, 'per_page': 1, 'orientation': 'landscape'},
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
    return '薬膳の基礎知識'


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
        '季節の養生': 'PINTEREST_BOARD_SEASONAL',
        '薬膳レシピ': 'PINTEREST_BOARD_RECIPE',
        '薬膳の基礎知識': 'PINTEREST_BOARD_BASICS',
        '薬膳資格': 'PINTEREST_BOARD_QUALIF',
    }
    env_key = board_env.get(board_name, 'PINTEREST_BOARD_BASICS')
    return os.environ.get(env_key, '')


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
