import os
import re
import json
import datetime
import requests
import markdown as md_lib
from linebot.models import TextSendMessage
from clients import line_bot_api, anthropic_client

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
        _, link = post_to_yakuzen_wp(new_title, new_content, post_id=post_id, status='publish', featured_media_id=media_id)
        pin_msg = try_post_to_pinterest(new_title, link, new_content, image_url=image_url)

        msg = f"✅ リライト・更新完了！\n\n📝 {new_title}\n🔗 {link}"
        if pin_msg:
            msg += f"\n\n{pin_msg}"
        line_bot_api.push_message(user_id, TextSendMessage(text=msg))

    except Exception as e:
        print(f"Auto rewrite error: {e}")
        import traceback; traceback.print_exc()
        line_bot_api.push_message(user_id, TextSendMessage(text=f"😢 エラーが発生しました。\n{str(e)[:150]}"))


def select_yakuzen_topic():
    """今月・季節・人気検索ワードを考慮して記事テーマを1つ自動選定"""
    today = datetime.date.today()
    month = today.month
    seasonal = {
        1:  "インフルエンザ予防・冷え・免疫低下・乾燥肌",
        2:  "インフルエンザ予防・冷え・花粉症準備・むくみ",
        3:  "花粉症・春の疲れ・デトックス・肝機能",
        4:  "花粉症・PMS・春の倦怠感・気の巡り",
        5:  "五月病・疲労感・胃腸疲れ・頭痛",
        6:  "梅雨のむくみ・だるさ・湿気による不調・冷え",
        7:  "夏バテ・熱中症対策・食欲不振・冷え",
        8:  "夏バテ・夏の冷え・不眠・疲労回復",
        9:  "秋の乾燥・肌荒れ・免疫低下・便秘",
        10: "乾燥・便秘・秋冷え・肌荒れ",
        11: "インフルエンザ予防・冷え・貧血・疲れ",
        12: "冷え・年末疲れ・冬の免疫・むくみ",
    }.get(month, "冷え・疲れ・免疫")

    response = anthropic_client.messages.create(
        model='claude-haiku-4-5-20251001',
        max_tokens=100,
        messages=[{
            'role': 'user',
            'content': f"""薬膳ブログの記事テーマを1つ決めてください。今日は{today}（{month}月）です。

優先すること：
- 今月の季節ワード：{seasonal}
- 20〜40代女性の検索頻度が高い症状（冷え・むくみ・生理痛・PMS・疲労・肌荒れ・便秘・不眠）
- 旬の食材と組み合わせる
- 子ども・家族向けテーマを月1〜2回程度混ぜる

テーマのみ出力（説明不要）。例：「花粉症の季節に試したい！鼻炎を和らげる旬の薬膳レシピ」"""
        }]
    )
    return response.content[0].text.strip()


def generate_yakuzen_article(topic):
    response = anthropic_client.messages.create(
        model='claude-sonnet-4-6',
        max_tokens=3000,
        messages=[{
            'role': 'user',
            'content': f"""薬膳ブログ（foodmakehealth.com）の記事を書いてください。

テーマ・情報：{topic}

条件：
- ターゲット：20〜40代の健康意識が高い女性（子育て中・働く女性）
- タイトルに必ず症状キーワードを入れる（例：「〜に悩む女性へ」「〜を改善する」）
- 1500〜2500文字程度
- 薬膳の考え方を分かりやすく説明する（専門用語は噛み砕く）
- 使いやすい・手に入りやすい食材を使う
- 見出し（##）を使って3〜4セクションに分ける（見出しにも症状ワードを入れる）
- Markdown形式で出力
- 最初の行は「# タイトル」形式
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
            'content': f"""以下の薬膳ブログ記事をリライトしてください。

元のタイトル：{title}
元の記事内容：
{plain[:3000]}
{extra}

リライト条件：
- SEOを意識した自然な文章にする
- 読みやすさを改善する
- 薬膳的な視点・情報はさらに充実させる
- 文字数は元記事以上（1500〜2500文字目安）
- 見出し（##）を使って3〜4セクションに分ける
- Markdown形式で出力
- 最初の行は「# タイトル」形式（タイトルも改善してOK）
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
    'default': {
        'url': 'https://amzn.asia/d/0bkhnDrf',
        'title': '「まいにちのごはん」で健康になっちゃう！ずぼら薬膳',
        'desc': '特別な食材は不要。いつものごはんに薬膳の考え方をプラスするだけで体が変わる一冊。',
    },
}


def _select_affiliate_book(title, content_md):
    kids_keywords = ['子ども', 'こども', '子育て', '育児', '小児', 'キッズ']
    soup_keywords = ['スープ', '鍋', '温活', '温め', 'シチュー', 'お粥', '粥']
    text = title + content_md[:500]
    if any(k in text for k in kids_keywords):
        return AFFILIATE_BOOKS['kids']
    if any(k in text for k in soup_keywords):
        return AFFILIATE_BOOKS['soup']
    return AFFILIATE_BOOKS['default']


def _build_affiliate_cta(title, content_md):
    book = _select_affiliate_book(title, content_md)
    return f'''<div style="background:#f9f6f0;border-left:4px solid #8b6914;padding:20px;margin:30px 0;border-radius:4px;">
<p style="font-weight:bold;margin:0 0 8px;">📚 もっと薬膳を日常に取り入れたい方へ</p>
<p style="margin:0 0 4px;font-weight:bold;">{book["title"]}</p>
<p style="margin:0 0 15px;font-size:0.95em;">{book["desc"]}</p>
<a href="{book["url"]}" target="_blank" rel="nofollow" style="display:inline-block;background:#ff9900;color:white;padding:10px 24px;border-radius:4px;text-decoration:none;font-weight:bold;">Amazonで見る →</a>
</div>'''


def post_to_yakuzen_wp(title, content_md, post_id=None, status='draft', featured_media_id=None):
    wp_url, wp_user, wp_pass = get_yakuzen_wp_creds()
    html = md_lib.markdown(content_md, extensions=['tables', 'nl2br'])
    html = html.replace('<!-- yakuzen-affiliate-cta -->', _build_affiliate_cta(title, content_md))
    data = {'title': title, 'content': html, 'status': status}
    if featured_media_id:
        data['featured_media'] = featured_media_id
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
        pin_msg = try_post_to_pinterest(title, link, content, image_url=image_url)
        msg = f"✅ 薬膳記事を公開しました！\n\n📝 {title}\n🔗 {link}"
        if pin_msg:
            msg += f"\n\n{pin_msg}"
        line_bot_api.push_message(user_id, TextSendMessage(text=msg))
    except Exception as e:
        print(f"Yakuzen new article error: {e}")
        line_bot_api.push_message(user_id, TextSendMessage(text=f"😢 記事作成中にエラーが発生しました。\n{str(e)[:150]}"))


def process_yakuzen_rewrite(user_id, post_id, post_title, post_content, instruction=''):
    try:
        article_md = generate_yakuzen_rewrite(post_title, post_content, instruction)
        lines = article_md.split('\n')
        new_title = lines[0].lstrip('# ').strip()
        new_content = '\n'.join(lines[1:]).lstrip('\n')
        keyword = generate_pexels_keyword(new_title)
        image_url = fetch_pexels_image_url(keyword)
        media_id = upload_image_to_yakuzen_wp(image_url, new_title) if image_url else None
        _, link = post_to_yakuzen_wp(new_title, new_content, post_id=post_id, status='publish', featured_media_id=media_id)
        pin_msg = try_post_to_pinterest(new_title, link, new_content, image_url=image_url)
        msg = f"✅ 薬膳記事をリライト・更新しました！\n\n📝 {new_title}\n🔗 {link}"
        if pin_msg:
            msg += f"\n\n{pin_msg}"
        line_bot_api.push_message(user_id, TextSendMessage(text=msg))
    except Exception as e:
        print(f"Yakuzen rewrite error: {e}")
        line_bot_api.push_message(user_id, TextSendMessage(text=f"😢 リライト中にエラーが発生しました。\n{str(e)[:150]}"))


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
