import os
import json
import base64
import datetime
import threading
import time
import pytz
import requests
import markdown as md_lib
from flask import Flask, request, abort
from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import MessageEvent, TextMessage, TextSendMessage, ImageMessage
from anthropic import Anthropic
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from apscheduler.schedulers.background import BackgroundScheduler

app = Flask(__name__)

line_bot_api = LineBotApi(os.environ['LINE_CHANNEL_ACCESS_TOKEN'])
handler = WebhookHandler(os.environ['LINE_CHANNEL_SECRET'])
anthropic_client = Anthropic(api_key=os.environ['ANTHROPIC_API_KEY'])

JST = pytz.timezone('Asia/Tokyo')

PENDING_FILE = '/tmp/pending_events.json'
SEKISUI_SESSION_FILE = '/tmp/sekisui_sessions.json'
YAKUZEN_SESSION_FILE = '/tmp/yakuzen_sessions.json'


def load_pending_events():
    try:
        with open(PENDING_FILE, 'r') as f:
            return json.load(f)
    except Exception:
        return {}


def save_pending_events(data):
    try:
        with open(PENDING_FILE, 'w') as f:
            json.dump(data, f)
    except Exception as e:
        print(f"pending_events save error: {e}")


def load_sekisui_sessions():
    try:
        with open(SEKISUI_SESSION_FILE, 'r') as f:
            return json.load(f)
    except Exception:
        return {}


def save_sekisui_sessions(data):
    try:
        with open(SEKISUI_SESSION_FILE, 'w') as f:
            json.dump(data, f, ensure_ascii=False)
    except Exception as e:
        print(f"sekisui_sessions save error: {e}")


def load_yakuzen_sessions():
    try:
        with open(YAKUZEN_SESSION_FILE, 'r') as f:
            return json.load(f)
    except Exception:
        return {}


def save_yakuzen_sessions(data):
    try:
        with open(YAKUZEN_SESSION_FILE, 'w') as f:
            json.dump(data, f, ensure_ascii=False)
    except Exception as e:
        print(f"yakuzen_sessions save error: {e}")


def get_yakuzen_wp_creds():
    return (
        os.environ.get('YAKUZEN_WP_URL', 'https://foodmakehealth.com'),
        os.environ.get('YAKUZEN_WP_USER', 'makiko01035'),
        os.environ['YAKUZEN_WP_APP_PASSWORD']
    )


# ========== eBayリサーチ ==========

EBAY_APP_ID = os.environ.get('EBAY_APP_ID', '')
EBAY_CERT_ID = os.environ.get('EBAY_CERT_ID', '')
EBAY_TOKEN_URL = "https://api.ebay.com/identity/v1/oauth2/token"
EBAY_BROWSE_URL = "https://api.ebay.com/buy/browse/v1/item_summary/search"

EBAY_KEYWORDS = [
    "Japan vintage kanzashi hair pin",
    "Japan vintage brooch",
    "Japan vintage kimono accessory",
    "Japan tenugui vintage",
    "Japan furoshiki vintage",
    "Japan vintage handkerchief",
    "Japan vintage coin purse",
    "Japan vintage fan sensu",
    "Japan washi tape",
    "Japan vintage eraser iwako",
    "Japan vintage badge pin",
    "Japan vintage patch embroidered",
    "Japan vintage incense holder",
    "Japan vintage chopsticks lacquer",
]


def get_ebay_token():
    credentials = base64.b64encode(f"{EBAY_APP_ID}:{EBAY_CERT_ID}".encode()).decode()
    headers = {
        "Authorization": f"Basic {credentials}",
        "Content-Type": "application/x-www-form-urlencoded",
    }
    data = "grant_type=client_credentials&scope=https://api.ebay.com/oauth/api_scope"
    res = requests.post(EBAY_TOKEN_URL, headers=headers, data=data, timeout=10)
    if res.status_code == 200:
        return res.json().get("access_token")
    return None


def ebay_search(token, keyword, min_price=10, max_price=100):
    headers = {
        "Authorization": f"Bearer {token}",
        "X-EBAY-C-MARKETPLACE-ID": "EBAY_US",
    }
    params = {
        "q": keyword,
        "filter": f"price:[{min_price}..{max_price}],priceCurrency:USD,buyingOptions:{{FIXED_PRICE}}",
        "limit": 50,
    }
    try:
        res = requests.get(EBAY_BROWSE_URL, headers=headers, params=params, timeout=10)
        if res.status_code == 200:
            data = res.json()
            return data.get("itemSummaries", []), data.get("total", 0)
    except Exception:
        pass
    return [], 0


def run_ebay_research(user_id):
    try:
        line_bot_api.push_message(user_id, TextSendMessage(text="🔍 eBayリサーチ中です...2〜3分かかります、そのままお待ちください！"))

        token = get_ebay_token()
        if not token:
            line_bot_api.push_message(user_id, TextSendMessage(text="❌ eBay APIの認証に失敗しました"))
            return

        results = []
        for keyword in EBAY_KEYWORDS:
            items, total = ebay_search(token, keyword)
            if not items:
                time.sleep(0.5)
                continue
            prices = []
            for item in items:
                try:
                    prices.append(float(item["price"]["value"]))
                except Exception:
                    pass
            if not prices:
                time.sleep(0.5)
                continue
            avg = sum(prices) / len(prices)
            score = (avg * len(items)) / max(total, 1)

            if total <= 30 and avg >= 25:
                judge = "◎超おすすめ"
            elif total <= 80 and avg >= 20:
                judge = "○おすすめ"
            elif total <= 150 and avg >= 15:
                judge = "△要検討"
            else:
                judge = None

            if judge:
                results.append({
                    "keyword": keyword,
                    "total": total,
                    "avg": round(avg, 1),
                    "score": round(score, 2),
                    "judge": judge,
                })
            time.sleep(0.5)

        results.sort(key=lambda x: x["score"], reverse=True)

        if not results:
            line_bot_api.push_message(user_id, TextSendMessage(text="今回はおすすめ候補が見つかりませんでした😢\nキーワードを変えて再試行します。"))
            return

        msg = "📦 eBayリサーチ結果（軽量・小物カテゴリ）\n\n"
        for i, r in enumerate(results[:5], 1):
            msg += f"{i}位 {r['keyword']}\n"
            msg += f"   競合: {r['total']}件 / 平均${r['avg']}\n"
            msg += f"   {r['judge']}\n\n"

        msg += "💡 メルカリで仕入れてみましょう！\neBayタイトルは「eBayタイトル作って：商品名」で作れます。"
        line_bot_api.push_message(user_id, TextSendMessage(text=msg))

    except Exception as e:
        print(f"eBay research error: {e}")
        line_bot_api.push_message(user_id, TextSendMessage(text=f"❌ リサーチ中にエラーが発生しました: {str(e)[:100]}"))


# ========== 薬膳ブログ ==========

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
    import re as _re
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
    match = _re.search(r'\{.*\}', raw, _re.DOTALL)
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

        _, link = post_to_yakuzen_wp(new_title, new_content, post_id=post_id, status='publish')
        pin_msg = try_post_to_pinterest(new_title, link, new_content)

        msg = f"✅ リライト・更新完了！\n\n📝 {new_title}\n🔗 {link}"
        if pin_msg:
            msg += f"\n\n{pin_msg}"
        line_bot_api.push_message(user_id, TextSendMessage(text=msg))

    except Exception as e:
        print(f"Auto rewrite error: {e}")
        import traceback; traceback.print_exc()
        line_bot_api.push_message(user_id, TextSendMessage(text=f"😢 エラーが発生しました。\n{str(e)[:150]}"))


def generate_yakuzen_article(topic):
    response = anthropic_client.messages.create(
        model='claude-sonnet-4-6',
        max_tokens=3000,
        messages=[{
            'role': 'user',
            'content': f"""薬膳ブログ（foodmakehealth.com）の記事を書いてください。

テーマ・情報：{topic}

条件：
- 1500〜2500文字程度
- 健康・美容に関心のある女性向け
- 薬膳の考え方を分かりやすく説明する
- 「体を温める」「気を補う」など薬膳的な視点を盛り込む
- 見出し（##）を使って3〜4セクションに分ける
- Markdown形式で出力
- 最初の行は「# タイトル」形式
- 記事末尾に「<!-- yakuzen-affiliate-cta -->」を1行追加"""
        }]
    )
    return response.content[0].text.strip()


def generate_yakuzen_rewrite(title, original_html, instruction=''):
    import re
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


def post_to_yakuzen_wp(title, content_md, post_id=None, status='draft'):
    wp_url, wp_user, wp_pass = get_yakuzen_wp_creds()
    html = md_lib.markdown(content_md, extensions=['tables', 'nl2br'])
    html = html.replace('<!-- yakuzen-affiliate-cta -->', '')
    data = {'title': title, 'content': html, 'status': status}
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


def process_yakuzen_new_article(user_id, topic):
    try:
        article_md = generate_yakuzen_article(topic)
        lines = article_md.split('\n')
        title = lines[0].lstrip('# ').strip()
        content = '\n'.join(lines[1:]).lstrip('\n')
        post_id, link = post_to_yakuzen_wp(title, content, status='publish')
        pin_msg = try_post_to_pinterest(title, link, content)
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
        _, link = post_to_yakuzen_wp(new_title, new_content, post_id=post_id, status='publish')
        pin_msg = try_post_to_pinterest(new_title, link, new_content)
        msg = f"✅ 薬膳記事をリライト・更新しました！\n\n📝 {new_title}\n🔗 {link}"
        if pin_msg:
            msg += f"\n\n{pin_msg}"
        line_bot_api.push_message(user_id, TextSendMessage(text=msg))
    except Exception as e:
        print(f"Yakuzen rewrite error: {e}")
        line_bot_api.push_message(user_id, TextSendMessage(text=f"😢 リライト中にエラーが発生しました。\n{str(e)[:150]}"))


def suggest_sekisui_themes():
    response = anthropic_client.messages.create(
        model='claude-sonnet-4-6',
        max_tokens=400,
        messages=[{
            'role': 'user',
            'content': """セキスイハイムで家を建てた施主が書くブログ向けに、
注文住宅を検討中の読者に役立つ記事テーマを3つ提案してください。
施主の実体験を盛り込める具体的なテーマにしてください。
以下の形式だけ返してください：
1. テーマ名
2. テーマ名
3. テーマ名"""
        }]
    )
    return response.content[0].text.strip()


def generate_sekisui_article(user_input):
    response = anthropic_client.messages.create(
        model='claude-sonnet-4-6',
        max_tokens=3000,
        messages=[{
            'role': 'user',
            'content': f"""セキスイハイムで家を建てた施主のブログ記事を書いてください。

施主からの情報：{user_input}

条件：
- 1500〜2000文字程度
- 注文住宅を検討中の方向け
- 実体験を自然に盛り込む（「私の場合は〜」「実際に〜でした」など）
- 見出し（##）を使って3〜4セクションに分ける
- Markdown形式で出力
- 最初の行は「# タイトル」形式
- 記事末尾に「<!-- sekisui-affiliate-cta -->」を1行追加"""
        }]
    )
    return response.content[0].text.strip()


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


YAKUZEN_BOARD_RULES = {
    '季節': '季節の養生', '養生': '季節の養生', '花粉': '季節の養生',
    '春': '季節の養生', '夏': '季節の養生', '秋': '季節の養生', '冬': '季節の養生',
    'レシピ': '薬膳レシピ', '食材': '薬膳レシピ', '効能': '薬膳レシピ',
    '基礎': '薬膳の基礎知識', '中医': '薬膳の基礎知識', '体質': '薬膳の基礎知識',
    '資格': '薬膳資格', '講座': '薬膳資格',
}


def guess_yakuzen_board(title):
    for keyword, board in YAKUZEN_BOARD_RULES.items():
        if keyword in title:
            return board
    return '薬膳の基礎知識'


def generate_yakuzen_pin_text(title, url, content_md):
    board = guess_yakuzen_board(title)
    import re as _re
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
    match = _re.search(r'\{.*\}', raw, _re.DOTALL)
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


def try_post_to_pinterest(title, article_url, content_md):
    """Pinterest投稿を試みる。成功時は投稿完了メッセージ、未設定時はピンテキストを返す"""
    try:
        pin = generate_yakuzen_pin_text(title, article_url, content_md)
        board_id = get_pinterest_board_id(pin['board'])
        image_url = fetch_pexels_image_url(title)

        if image_url and board_id and os.environ.get('PINTEREST_ACCESS_TOKEN'):
            success, result = post_pin_to_pinterest(
                pin['pin_title'], pin['description'], board_id, article_url, image_url
            )
            if success:
                return f"📌 Pinterestにも投稿しました！\nボード：{pin['board']}"
            print(f"Pinterest post failed: {result}")

        # 未設定 or 失敗時 → ピンテキストをLINEに送る
        return (
            f"📌 Pinterestピンテキスト：\n"
            f"ボード：{pin['board']}\n"
            f"タイトル：{pin['pin_title']}\n"
            f"説明：{pin['description']}"
        )
    except Exception as e:
        print(f"Pinterest error: {e}")
        return ''


def fetch_pexels_image_for_wp(keyword):
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
        if not photos:
            return None
        img_res = requests.get(photos[0]['src']['large2x'], timeout=10)
        if img_res.status_code != 200:
            return None
        return img_res.content, f"pexels_{photos[0]['id']}.jpg"
    except Exception as e:
        print(f"Pexels error: {e}")
        return None


def upload_image_to_wp(wp_url, wp_user, wp_pass, img_data, filename):
    try:
        res = requests.post(
            f'{wp_url}/wp-json/wp/v2/media',
            auth=(wp_user, wp_pass),
            headers={
                'Content-Disposition': f'attachment; filename="{filename}"',
                'Content-Type': 'image/jpeg',
            },
            data=img_data,
            timeout=30
        )
        if res.status_code == 201:
            return res.json()['id']
    except Exception as e:
        print(f"Image upload error: {e}")
    return None


SEKISUI_CTA_BOX = '''<div style="background:#fdf8f3;border:2px solid #d4956a;border-radius:8px;padding:20px 24px;margin:32px 0;">
  <p style="margin:0 0 16px;font-weight:bold;font-size:16px;color:#333;">&#127968; 家づくりで私が実際に検討したサービス</p>
  <div style="margin-bottom:12px;padding:14px 16px;background:#fff;border-radius:6px;border-left:3px solid #e8730a;">
    <p style="margin:0 0 6px;font-size:13px;color:#777;">後悔しない家づくりのために ／ 無料相談</p>
    <a href="https://px.a8.net/svt/ejp?a8mat=4AZS0Q+G0X2QI+5OGA+5YJRM" rel="nofollow" style="display:inline-block;background:#e8730a;color:#fff;padding:9px 20px;border-radius:4px;text-decoration:none;font-size:14px;font-weight:bold;">家づくり相談所で無料相談する &#8594;</a>
    <img border="0" width="1" height="1" src="https://www14.a8.net/0.gif?a8mat=4AZS0Q+G0X2QI+5OGA+5YJRM" alt="">
  </div>
  <div style="margin-bottom:12px;padding:14px 16px;background:#fff;border-radius:6px;border-left:3px solid #2a7dc9;">
    <p style="margin:0 0 6px;font-size:13px;color:#777;">太陽光発電の費用を無料で一括比較</p>
    <a href="https://px.a8.net/svt/ejp?a8mat=3BMB3B+DQ5TNE+3LME+5YJRM" rel="nofollow" style="display:inline-block;background:#2a7dc9;color:#fff;padding:9px 20px;border-radius:4px;text-decoration:none;font-size:14px;font-weight:bold;">ソーラーパートナーズで無料見積り &#8594;</a>
    <img border="0" width="1" height="1" src="https://www11.a8.net/0.gif?a8mat=3BMB3B+DQ5TNE+3LME+5YJRM" alt="">
  </div>
  <div style="padding:14px 16px;background:#fff;border-radius:6px;border-left:3px solid #4caf50;">
    <p style="margin:0 0 6px;font-size:13px;color:#777;">家さがし・家づくりの情報を無料でまとめて入手</p>
    <a href="https://px.a8.net/svt/ejp?a8mat=4AZS0Q+FZ4RX6+5V18+5YJRM" rel="nofollow" style="display:inline-block;background:#4caf50;color:#fff;padding:9px 20px;border-radius:4px;text-decoration:none;font-size:14px;font-weight:bold;">すまいのいろはPlusで無料相談 &#8594;</a>
    <img border="0" width="1" height="1" src="https://www11.a8.net/0.gif?a8mat=4AZS0Q+FZ4RX6+5V18+5YJRM" alt="">
  </div>
</div>'''


def post_to_sekisui_wp(title, content_md):
    wp_url = os.environ.get('SEKISUI_WP_URL', 'https://order-sekisui.com')
    wp_user = os.environ.get('SEKISUI_WP_USER', 'makiko01035')
    wp_pass = os.environ['SEKISUI_WP_APP_PASSWORD']

    html = md_lib.markdown(content_md, extensions=['tables', 'nl2br'])
    html = html.replace('<!-- sekisui-affiliate-cta -->', SEKISUI_CTA_BOX)
    data = {'title': title, 'content': html, 'status': 'publish'}

    img_result = fetch_pexels_image_for_wp(title)
    if img_result:
        img_data, filename = img_result
        media_id = upload_image_to_wp(wp_url, wp_user, wp_pass, img_data, filename)
        if media_id:
            data['featured_media'] = media_id

    res = requests.post(
        f'{wp_url}/wp-json/wp/v2/posts',
        auth=(wp_user, wp_pass),
        json=data,
        timeout=30
    )
    if res.status_code == 201:
        post = res.json()
        return post['id'], post['link']
    raise Exception(f"WP投稿エラー: {res.status_code}")


def process_sekisui_article(user_id, user_input):
    try:
        article_md = generate_sekisui_article(user_input)
        lines = article_md.split('\n')
        title = lines[0].lstrip('# ').strip()
        content = '\n'.join(lines[1:]).lstrip('\n')
        post_id, _ = post_to_sekisui_wp(title, content)
        edit_url = f"https://order-sekisui.com/wp-admin/post.php?post={post_id}&action=edit"
        msg = f"✅ セキスイ記事を下書き保存しました！\n\n📝 {title}\n\n確認・公開はこちら：\n{edit_url}"
        line_bot_api.push_message(user_id, TextSendMessage(text=msg))
    except Exception as e:
        print(f"Sekisui article error: {e}")
        line_bot_api.push_message(user_id, TextSendMessage(text=f"😢 記事作成中にエラーが発生しました。\n{str(e)[:100]}"))


def get_calendar_service():
    from google.auth.transport.requests import Request
    creds_info = json.loads(os.environ['GOOGLE_CREDENTIALS'])
    creds = Credentials(
        token=None,
        refresh_token=creds_info.get('refresh_token'),
        token_uri='https://oauth2.googleapis.com/token',
        client_id=creds_info.get('client_id'),
        client_secret=creds_info.get('client_secret'),
        scopes=creds_info.get('scopes'),
    )
    creds.refresh(Request())
    return build('calendar', 'v3', credentials=creds)


def get_or_create_maybe_calendar(service):
    """「気になるイベント」カレンダーを取得または作成"""
    calendars = service.calendarList().list().execute().get('items', [])
    for cal in calendars:
        if cal.get('summary') == '気になるイベント':
            return cal['id']
    new_cal = service.calendars().insert(body={
        'summary': '気になるイベント',
        'timeZone': 'Asia/Tokyo'
    }).execute()
    return new_cal['id']


def get_upcoming_events(days=7):
    service = get_calendar_service()
    now = datetime.datetime.now(JST)
    start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    end = now + datetime.timedelta(days=days)

    calendars = service.calendarList().list().execute().get('items', [])
    all_events = []
    for cal in calendars:
        try:
            result = service.events().list(
                calendarId=cal['id'],
                timeMin=start.isoformat(),
                timeMax=end.isoformat(),
                maxResults=20,
                singleEvents=True,
                orderBy='startTime'
            ).execute()
            for event in result.get('items', []):
                event['_calendar_name'] = cal.get('summary', '')
            all_events.extend(result.get('items', []))
        except Exception:
            pass

    all_events.sort(key=lambda e: e['start'].get('dateTime', e['start'].get('date', '')))
    return all_events


def format_events(events):
    if not events:
        return "予定はありません。"
    lines = []
    for event in events:
        start = event['start'].get('dateTime', event['start'].get('date'))
        if 'T' in start:
            dt = datetime.datetime.fromisoformat(start).astimezone(JST)
            start_str = dt.strftime('%m/%d %H:%M')
        else:
            start_str = start
        cal_name = event.get('_calendar_name', '')
        cal_label = f"[{cal_name}] " if cal_name else ""
        lines.append(f"・{start_str} {cal_label}{event['summary']}")
    return '\n'.join(lines)


def check_deadline_reminders():
    """申込期限の1週間前・3日前・前日・当日にLINE通知を送る"""
    try:
        service = get_calendar_service()
        cal_id = get_or_create_maybe_calendar(service)
        now = datetime.datetime.now(JST)
        today = now.date()
        user_id = os.environ['LINE_USER_ID']

        notify_days = {
            0: ("⚠️", "今日", "まだ申し込んでいなければ急いでください！"),
            1: ("📢", "明日", "忘れずに申し込んでください！"),
            3: ("📌", "3日後", "早めに準備を始めてください！"),
            7: ("📅", "1週間後", "早めに確認しておきましょう！"),
        }

        for days_ahead, (icon, label, action) in notify_days.items():
            target_date = today + datetime.timedelta(days=days_ahead)
            start = datetime.datetime.combine(target_date, datetime.time.min).astimezone(JST)
            end = datetime.datetime.combine(target_date, datetime.time.max).astimezone(JST)

            result = service.events().list(
                calendarId=cal_id,
                timeMin=start.isoformat(),
                timeMax=end.isoformat(),
                maxResults=20,
                singleEvents=True,
                orderBy='startTime'
            ).execute()

            for e in result.get('items', []):
                if '【申込期限】' not in e.get('summary', ''):
                    continue
                title = e['summary'].replace('【申込期限】', '').strip()
                msg = f"{icon} 【申込期限 {label}！】\n「{title}」の申し込み期限は{label}です！\n{action}"
                line_bot_api.push_message(user_id, TextSendMessage(text=msg))
    except Exception as e:
        print(f"Deadline reminder error: {e}")


@app.route('/ping')
def ping():
    return 'OK'


@app.route('/company')
def company_dashboard():
    now = datetime.datetime.now(JST)
    return f'''<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>まきの会社 | Company Dashboard</title>
<link href="https://fonts.googleapis.com/css2?family=Noto+Sans+JP:wght@300;400;700&family=Playfair+Display:wght@400;700&display=swap" rel="stylesheet">
<style>
  :root {{
    --gold: #c9a84c;
    --gold-light: #e8c96a;
    --black: #0a0a0a;
    --dark: #111111;
    --card: #1a1a1a;
    --border: #2a2a2a;
    --text: #e0e0e0;
    --muted: #888888;
  }}
  * {{ margin: 0; padding: 0; box-sizing: border-box; }}
  body {{
    background: var(--black);
    color: var(--text);
    font-family: 'Noto Sans JP', sans-serif;
    font-weight: 300;
    min-height: 100vh;
  }}

  /* ヘッダー */
  header {{
    border-bottom: 1px solid var(--border);
    padding: 40px 48px 32px;
    display: flex;
    justify-content: space-between;
    align-items: flex-end;
  }}
  .logo {{
    font-family: 'Playfair Display', serif;
    font-size: 28px;
    color: var(--gold);
    letter-spacing: 2px;
  }}
  .logo span {{
    display: block;
    font-family: 'Noto Sans JP', sans-serif;
    font-size: 11px;
    font-weight: 300;
    color: var(--muted);
    letter-spacing: 4px;
    text-transform: uppercase;
    margin-top: 4px;
  }}
  .timestamp {{
    font-size: 12px;
    color: var(--muted);
    letter-spacing: 1px;
    text-align: right;
  }}
  .timestamp strong {{
    display: block;
    font-size: 20px;
    color: var(--gold-light);
    font-weight: 400;
  }}

  /* メインコンテンツ */
  main {{ padding: 48px; }}

  /* ミッション */
  .mission {{
    border-left: 2px solid var(--gold);
    padding-left: 24px;
    margin-bottom: 56px;
  }}
  .mission h2 {{
    font-family: 'Playfair Display', serif;
    font-size: 14px;
    letter-spacing: 4px;
    text-transform: uppercase;
    color: var(--gold);
    margin-bottom: 12px;
  }}
  .mission p {{
    font-size: 22px;
    font-weight: 300;
    line-height: 1.8;
    color: var(--text);
  }}
  .mission p em {{
    font-style: normal;
    color: var(--gold-light);
    font-weight: 400;
  }}

  /* セクションタイトル */
  .section-title {{
    font-size: 11px;
    letter-spacing: 5px;
    text-transform: uppercase;
    color: var(--gold);
    margin-bottom: 24px;
  }}

  /* 目標メーター */
  .goal-section {{ margin-bottom: 56px; }}
  .goal-card {{
    background: var(--card);
    border: 1px solid var(--border);
    border-radius: 2px;
    padding: 32px 40px;
    display: flex;
    align-items: center;
    gap: 48px;
  }}
  .goal-label {{
    font-size: 12px;
    color: var(--muted);
    letter-spacing: 2px;
    text-transform: uppercase;
    margin-bottom: 8px;
  }}
  .goal-amount {{
    font-family: 'Playfair Display', serif;
    font-size: 48px;
    color: var(--gold);
    line-height: 1;
  }}
  .goal-amount span {{ font-size: 18px; color: var(--muted); }}
  .goal-divider {{
    width: 1px;
    height: 60px;
    background: var(--border);
    flex-shrink: 0;
  }}
  .goal-vision {{
    font-size: 14px;
    color: var(--muted);
    line-height: 2;
  }}
  .goal-vision strong {{ color: var(--gold-light); font-weight: 400; }}

  /* 部署カード */
  .departments {{
    display: grid;
    grid-template-columns: repeat(3, 1fr);
    gap: 20px;
    margin-bottom: 56px;
  }}
  .dept-card {{
    background: var(--card);
    border: 1px solid var(--border);
    border-radius: 2px;
    padding: 32px;
    position: relative;
    overflow: hidden;
    transition: border-color 0.3s;
  }}
  .dept-card:hover {{ border-color: var(--gold); }}
  .dept-card::before {{
    content: '';
    position: absolute;
    top: 0; left: 0; right: 0;
    height: 2px;
    background: linear-gradient(90deg, var(--gold), transparent);
  }}
  .dept-priority {{
    font-size: 10px;
    letter-spacing: 3px;
    text-transform: uppercase;
    color: var(--gold);
    margin-bottom: 16px;
  }}
  .dept-name {{
    font-family: 'Playfair Display', serif;
    font-size: 22px;
    color: var(--text);
    margin-bottom: 8px;
  }}
  .dept-target {{
    font-size: 12px;
    color: var(--muted);
    margin-bottom: 20px;
    padding-bottom: 20px;
    border-bottom: 1px solid var(--border);
  }}
  .dept-target strong {{ color: var(--gold-light); font-weight: 400; }}
  .dept-items {{ list-style: none; }}
  .dept-items li {{
    font-size: 13px;
    color: var(--muted);
    padding: 5px 0;
    padding-left: 14px;
    position: relative;
    line-height: 1.6;
  }}
  .dept-items li::before {{
    content: '—';
    position: absolute;
    left: 0;
    color: var(--border);
  }}

  /* 週次スケジュール */
  .schedule-section {{ margin-bottom: 56px; }}
  .schedule-table {{
    width: 100%;
    border-collapse: collapse;
  }}
  .schedule-table th, .schedule-table td {{
    padding: 16px 20px;
    text-align: left;
    font-size: 13px;
    border-bottom: 1px solid var(--border);
  }}
  .schedule-table th {{
    font-size: 10px;
    letter-spacing: 3px;
    text-transform: uppercase;
    color: var(--muted);
    font-weight: 400;
  }}
  .schedule-table td {{ color: var(--text); font-weight: 300; }}
  .schedule-table td:first-child {{
    color: var(--gold);
    font-weight: 400;
    width: 120px;
  }}
  .schedule-table td:nth-child(2) {{
    color: var(--muted);
    width: 140px;
    font-size: 12px;
  }}

  /* LINE機能一覧 */
  .line-section {{ margin-bottom: 56px; }}
  .line-grid {{
    display: grid;
    grid-template-columns: repeat(2, 1fr);
    gap: 1px;
    background: var(--border);
    border: 1px solid var(--border);
    border-radius: 2px;
    overflow: hidden;
  }}
  .line-item {{
    background: var(--card);
    padding: 20px 24px;
    display: flex;
    gap: 16px;
    align-items: flex-start;
  }}
  .line-keyword {{
    font-family: monospace;
    font-size: 12px;
    background: #0f0f0f;
    border: 1px solid var(--border);
    color: var(--gold);
    padding: 4px 10px;
    border-radius: 2px;
    white-space: nowrap;
    flex-shrink: 0;
  }}
  .line-desc {{
    font-size: 13px;
    color: var(--muted);
    line-height: 1.6;
    padding-top: 2px;
  }}

  /* フッター */
  footer {{
    border-top: 1px solid var(--border);
    padding: 24px 48px;
    display: flex;
    justify-content: space-between;
    align-items: center;
  }}
  footer p {{ font-size: 11px; color: var(--muted); letter-spacing: 1px; }}
  .status-dot {{
    display: inline-block;
    width: 6px; height: 6px;
    background: #4caf50;
    border-radius: 50%;
    margin-right: 6px;
    animation: pulse 2s infinite;
  }}
  @keyframes pulse {{
    0%, 100% {{ opacity: 1; }}
    50% {{ opacity: 0.4; }}
  }}

  @media (max-width: 768px) {{
    header {{ padding: 24px; flex-direction: column; align-items: flex-start; gap: 16px; }}
    main {{ padding: 24px; }}
    .departments {{ grid-template-columns: 1fr; }}
    .goal-card {{ flex-direction: column; gap: 24px; align-items: flex-start; }}
    .goal-divider {{ width: 40px; height: 1px; }}
    .line-grid {{ grid-template-columns: 1fr; }}
    footer {{ flex-direction: column; gap: 8px; padding: 24px; }}
  }}
</style>
</head>
<body>

<header>
  <div class="logo">
    Maki &amp; Co.
    <span>Private Company Dashboard</span>
  </div>
  <div class="timestamp">
    <strong>{now.strftime('%Y.%m.%d')}</strong>
    {now.strftime('%H:%M')} JST
  </div>
</header>

<main>

  <!-- ミッション -->
  <div class="mission">
    <h2>Mission</h2>
    <p>副業収入を<em>月50万円</em>以上に育て、<br>
    <em>海外移住・開業</em>という未来の自由を手に入れる。</p>
  </div>

  <!-- 月収目標 -->
  <div class="goal-section">
    <p class="section-title">Revenue Target</p>
    <div class="goal-card">
      <div>
        <div class="goal-label">Monthly Goal</div>
        <div class="goal-amount">50<span>万円 / 月</span></div>
      </div>
      <div class="goal-divider"></div>
      <div class="goal-vision">
        <strong>物販部</strong>　40〜50万円（最優先）<br>
        <strong>ブログ部</strong>　数万円（蓄積型・長期資産）<br>
        <strong>秘書部</strong>　時間節約・業務自動化
      </div>
    </div>
  </div>

  <!-- 部署 -->
  <div class="departments">
    <div class="dept-card">
      <div class="dept-priority">★★★ 最優先 — 物販部</div>
      <div class="dept-name">eBay Sales</div>
      <div class="dept-target">月収目標 <strong>40〜50万円</strong></div>
      <ul class="dept-items">
        <li>メルカリ仕入れ → eBay販売</li>
        <li>無在庫→有在庫移行中</li>
        <li>4月目標：250品出品</li>
        <li>利益目標：1〜5万円</li>
      </ul>
    </div>
    <div class="dept-card">
      <div class="dept-priority">★★ — ブログ部</div>
      <div class="dept-name">Blog &amp; SEO</div>
      <div class="dept-target">月収目標 <strong>数万円（蓄積型）</strong></div>
      <ul class="dept-items">
        <li>薬膳ブログ：約120記事</li>
        <li>セキスイブログ：34記事</li>
        <li>アフィリエイト収益化進行中</li>
        <li>Search Console流入増が目標</li>
      </ul>
    </div>
    <div class="dept-card">
      <div class="dept-priority">★★ — 秘書部</div>
      <div class="dept-name">Secretary</div>
      <div class="dept-target">役割 <strong>時間節約・完全自動化</strong></div>
      <ul class="dept-items">
        <li>LINEボット稼働中</li>
        <li>毎朝7時：予定通知</li>
        <li>Googleカレンダー管理</li>
        <li>ブログ自動投稿</li>
      </ul>
    </div>
  </div>

  <!-- 週次スケジュール -->
  <div class="schedule-section">
    <p class="section-title">Weekly Schedule</p>
    <table class="schedule-table">
      <thead>
        <tr>
          <th>Day</th>
          <th>Department</th>
          <th>Task</th>
        </tr>
      </thead>
      <tbody>
        <tr><td>毎朝 7:00</td><td>秘書部</td><td>今日の予定をLINEに自動送信</td></tr>
        <tr><td>毎週日曜 20:00</td><td>秘書部</td><td>3日以内の予定リマインド</td></tr>
        <tr><td>火曜日</td><td>ブログ部</td><td>薬膳ブログ リライト2本 + Pinterestピン</td></tr>
        <tr><td>木曜日</td><td>ブログ部</td><td>セキスイブログ 記事投稿</td></tr>
        <tr><td>随時</td><td>物販部</td><td>eBayタイトル生成・出品サポート</td></tr>
      </tbody>
    </table>
  </div>

  <!-- LINE機能 -->
  <div class="line-section">
    <p class="section-title">LINE Functions — @296wjwwj</p>
    <div class="line-grid">
      <div class="line-item">
        <span class="line-keyword">薬膳記事</span>
        <span class="line-desc">薬膳ブログメニュー表示（新規作成 / リライト）</span>
      </div>
      <div class="line-item">
        <span class="line-keyword">セキスイ記事</span>
        <span class="line-desc">セキスイブログ記事作成フロー起動</span>
      </div>
      <div class="line-item">
        <span class="line-keyword">画像送信</span>
        <span class="line-desc">チラシからイベント情報を読み取り</span>
      </div>
      <div class="line-item">
        <span class="line-keyword">登録して</span>
        <span class="line-desc">読み取ったイベントをGoogleカレンダーに登録</span>
      </div>
      <div class="line-item">
        <span class="line-keyword">〇〇の期限 4月10日</span>
        <span class="line-desc">申込期限をカレンダーに登録＋リマインド設定</span>
      </div>
      <div class="line-item">
        <span class="line-keyword">自由入力</span>
        <span class="line-desc">Claudeが返答（カレンダー情報も参照）</span>
      </div>
    </div>
  </div>

</main>

<footer>
  <p><span class="status-dot"></span>All systems operational — Render / maki-hisho.onrender.com</p>
  <p>© 2026 Maki &amp; Co. — Built with Claude Code</p>
</footer>

</body>
</html>'''


@app.route('/auth/pinterest')
def auth_pinterest():
    app_id = os.environ.get('PINTEREST_APP_ID', '')
    if not app_id:
        return 'PINTEREST_APP_ID が設定されていません。Renderに設定してください。', 400
    redirect_uri = 'https://maki-hisho.onrender.com/auth/pinterest/callback'
    auth_url = (
        f"https://www.pinterest.com/oauth/"
        f"?client_id={app_id}"
        f"&redirect_uri={redirect_uri}"
        f"&response_type=code"
        f"&scope=pins:write,boards:read"
        f"&state=maki"
    )
    return f'''<html><body>
<h2>Pinterest認証</h2>
<p><a href="{auth_url}" style="font-size:20px;padding:10px;background:#e60023;color:#fff;text-decoration:none;border-radius:6px;">
Pinterestで認証する</a></p>
</body></html>'''


@app.route('/auth/pinterest/callback')
def auth_pinterest_callback():
    code = request.args.get('code')
    if not code:
        return f'エラー: codeが取得できませんでした。{request.args}', 400
    app_id = os.environ.get('PINTEREST_APP_ID')
    app_secret = os.environ.get('PINTEREST_APP_SECRET')
    redirect_uri = 'https://maki-hisho.onrender.com/auth/pinterest/callback'
    import base64
    creds = base64.b64encode(f"{app_id}:{app_secret}".encode()).decode()
    res = requests.post(
        'https://api.pinterest.com/v5/oauth/token',
        headers={'Authorization': f'Basic {creds}', 'Content-Type': 'application/x-www-form-urlencoded'},
        data={'grant_type': 'authorization_code', 'code': code, 'redirect_uri': redirect_uri},
        timeout=15
    )
    if res.status_code == 200:
        data = res.json()
        return f'''<html><body>
<h2>✅ 認証成功！</h2>
<p>以下をRenderの環境変数にコピペしてください：</p>
<p><b>PINTEREST_REFRESH_TOKEN:</b><br>
<textarea rows="3" cols="80">{data.get('refresh_token', '')}</textarea></p>
<p><small>（access_tokenは自動更新されるので不要です）</small></p>
<p>次に <a href="/auth/pinterest/boards">ボードIDを確認する</a></p>
</body></html>'''
    return f'エラー: {res.status_code} {res.text}', 400


@app.route('/auth/pinterest/boards')
def auth_pinterest_boards():
    access_token = get_pinterest_access_token()
    if not access_token:
        return 'PINTEREST_REFRESH_TOKEN または PINTEREST_ACCESS_TOKEN が設定されていません。', 400
    res = requests.get(
        'https://api.pinterest.com/v5/boards',
        headers={'Authorization': f'Bearer {access_token}'},
        params={'page_size': 25},
        timeout=15
    )
    if res.status_code == 200:
        boards = res.json().get('items', [])
        rows = ''.join(
            f"<tr><td>{b['name']}</td><td><code>{b['id']}</code></td></tr>"
            for b in boards
        )
        return f'''<html><body>
<h2>Pinterestボード一覧</h2>
<table border="1" cellpadding="6">
<tr><th>ボード名</th><th>ID（Renderに設定する値）</th></tr>
{rows}
</table>
<p>対応する環境変数：<br>
季節の養生 → PINTEREST_BOARD_SEASONAL<br>
薬膳レシピ → PINTEREST_BOARD_RECIPE<br>
薬膳の基礎知識 → PINTEREST_BOARD_BASICS<br>
薬膳資格 → PINTEREST_BOARD_QUALIF</p>
</body></html>'''
    return f'エラー: {res.status_code} {res.text}', 400


@app.route('/trigger/morning', methods=['GET', 'POST'])
def trigger_morning():
    threading.Thread(target=send_morning_message).start()
    return 'OK'


@app.route('/callback', methods=['POST'])
def callback():
    signature = request.headers['X-Line-Signature']
    body = request.get_data(as_text=True)
    try:
        handler.handle(body, signature)
    except InvalidSignatureError:
        abort(400)
    return 'OK'


@handler.add(MessageEvent, message=ImageMessage)
def handle_image(event):
    user_id = event.source.user_id
    message_id = event.message.id

    try:
        # LINEから画像をダウンロード
        message_content = line_bot_api.get_message_content(message_id)
        image_data = b''.join(chunk for chunk in message_content.iter_content())
        image_base64 = base64.standard_b64encode(image_data).decode('utf-8')
        if image_data[:4] == b'\x89PNG':
            media_type = 'image/png'
        elif image_data[:4] == b'RIFF':
            media_type = 'image/webp'
        elif image_data[:6] in (b'GIF87a', b'GIF89a'):
            media_type = 'image/gif'
        else:
            media_type = 'image/jpeg'
    except Exception as e:
        print(f"Image download error: {e}")
        import traceback; traceback.print_exc()
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=f"画像の取得に失敗しました😢\nエラー: {str(e)[:100]}"))
        return

    # Claudeで画像からイベント情報を抽出
    try:
        response = anthropic_client.messages.create(
            model='claude-sonnet-4-6',
            max_tokens=2000,
            messages=[{
                'role': 'user',
                'content': [
                    {
                        'type': 'image',
                        'source': {
                            'type': 'base64',
                            'media_type': media_type,
                            'data': image_base64
                        }
                    },
                    {
                        'type': 'text',
                        'text': """このチラシやプリントから全てのイベント・日程情報を抽出してください。
複数の日程がある場合は全て抽出してください。
以下のJSON配列形式のみ返してください（情報がない場合はnullにしてください）：
[
  {
    "title": "イベント名",
    "date": "YYYY-MM-DD",
    "start_time": "HH:MM",
    "end_time": "HH:MM",
    "location": "場所",
    "description": "その他メモ",
    "application_deadline": "YYYY-MM-DD"
  }
]
application_deadlineは申込締切・申込期限・締切日などの日付です。ない場合はnullにしてください。
必ずJSON配列（[...]）で返してください。"""
                    }
                ]
            }]
        )

        raw_text = response.content[0].text.strip()
        start = raw_text.find('[')
        decoder = json.JSONDecoder()
        extracted_list, _ = decoder.raw_decode(raw_text, start)
        if isinstance(extracted_list, dict):
            extracted_list = [extracted_list]
        pending_events = load_pending_events()
        pending_events[user_id] = extracted_list
        save_pending_events(pending_events)

        msg = f"📋 {len(extracted_list)}件読み取れました！\n\n"
        for i, ev in enumerate(extracted_list, 1):
            msg += f"【{i}】📌 {ev.get('title') or '（タイトル不明）'}\n"
            if ev.get('date'):
                msg += f"　📅 {ev['date']}\n"
            if ev.get('start_time'):
                time_str = ev['start_time']
                if ev.get('end_time'):
                    time_str += f"〜{ev['end_time']}"
                msg += f"　🕐 {time_str}\n"
            if ev.get('location'):
                msg += f"　📍 {ev['location']}\n"
            if ev.get('application_deadline'):
                msg += f"　⚠️ 申込期限: {ev['application_deadline']}\n"
            msg += "\n"
        msg += "「登録して」と送ってくれたら全て保存します！"

        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=msg))

    except Exception as e:
        print(f"Image extract error: {e}")
        import traceback; traceback.print_exc()
        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(text=f"画像の読み取りに失敗しました😢\nエラー: {str(e)[:150]}")
        )


@handler.add(MessageEvent, message=TextMessage)
def handle_message(event):
    user_message = event.message.text
    user_id = event.source.user_id

    # ユーザーIDを確認するコマンド
    if user_message == 'myid':
        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(text=f'あなたのユーザーID:\n{user_id}')
        )
        return

    # 社内ダッシュボード
    if user_message in ['会社', 'ダッシュボード']:
        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(text='📊 まきの会社 ダッシュボード\nhttps://maki-hisho.onrender.com/company')
        )
        return

    # カレンダー一覧を確認するコマンド
    if user_message == 'カレンダー一覧':
        try:
            service = get_calendar_service()
            calendars = service.calendarList().list().execute().get('items', [])
            cal_list = '\n'.join([f"・{c.get('summary', '')} ({c.get('accessRole', '')})" for c in calendars])
            line_bot_api.reply_message(
                event.reply_token,
                TextSendMessage(text=f'取得できているカレンダー:\n{cal_list}')
            )
        except Exception as e:
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text=f'エラー: {e}'))
        return

    # 手動期限登録（「〇〇の期限 4月10日」など）
    if any(kw in user_message for kw in ['の期限', 'の締切', 'の締め切り', 'の申込期限', 'の手続き期限']):
        try:
            parse_response = anthropic_client.messages.create(
                model='claude-sonnet-4-6',
                max_tokens=300,
                messages=[{
                    'role': 'user',
                    'content': f"""以下のメッセージから手続き名と期限日を抽出してください。
今日の日付: {datetime.datetime.now(JST).strftime('%Y-%m-%d')}
メッセージ: {user_message}
以下のJSON形式のみ返してください（他の文字は不要）:
{{"title": "手続き名", "deadline": "YYYY-MM-DD"}}
日付が不明な場合はdeadlineをnullにしてください。"""
                }]
            )
            parsed = json.loads(parse_response.content[0].text.strip())
            title = parsed.get('title')
            deadline = parsed.get('deadline')

            if title and deadline:
                service = get_calendar_service()
                cal_id = get_or_create_maybe_calendar(service)
                deadline_event = {
                    'summary': f"【申込期限】{title}",
                    'description': '申込期限です。忘れずに！',
                    'start': {'date': deadline},
                    'end': {'date': deadline},
                }
                service.events().insert(calendarId=cal_id, body=deadline_event).execute()
                line_bot_api.reply_message(
                    event.reply_token,
                    TextSendMessage(text=f"✅ 登録しました！\n📌 {title}\n⚠️ 期限: {deadline}\n\n1週間前・3日前・前日・当日にお知らせします！")
                )
            else:
                line_bot_api.reply_message(
                    event.reply_token,
                    TextSendMessage(text="手続き名か期限日が読み取れませんでした😢\n例：「〇〇の期限 4月10日」のように送ってください！")
                )
        except Exception as e:
            print(f"Manual deadline error: {e}")
            line_bot_api.reply_message(
                event.reply_token,
                TextSendMessage(text=f"登録中にエラーが発生しました😢\n{str(e)[:100]}")
            )
        return

    # 画像確認後の「登録して」コマンド
    pending_events = load_pending_events()
    if user_message == '登録して' and user_id in pending_events:
        extracted_list = pending_events.pop(user_id)
        save_pending_events(pending_events)
        try:
            service = get_calendar_service()
            cal_id = get_or_create_maybe_calendar(service)
            registered = []
            deadline_count = 0

            for extracted in extracted_list:
                if extracted.get('date') and extracted.get('start_time'):
                    start_dt = datetime.datetime.fromisoformat(f"{extracted['date']}T{extracted['start_time']}:00")
                    start_dt = JST.localize(start_dt)
                    if extracted.get('end_time'):
                        end_dt = datetime.datetime.fromisoformat(f"{extracted['date']}T{extracted['end_time']}:00")
                        end_dt = JST.localize(end_dt)
                    else:
                        end_dt = start_dt + datetime.timedelta(hours=1)
                    event_body = {
                        'summary': extracted.get('title') or 'イベント',
                        'location': extracted.get('location') or '',
                        'description': extracted.get('description') or '',
                        'start': {'dateTime': start_dt.isoformat(), 'timeZone': 'Asia/Tokyo'},
                        'end': {'dateTime': end_dt.isoformat(), 'timeZone': 'Asia/Tokyo'},
                    }
                elif extracted.get('date'):
                    event_body = {
                        'summary': extracted.get('title') or 'イベント',
                        'location': extracted.get('location') or '',
                        'description': extracted.get('description') or '',
                        'start': {'date': extracted['date']},
                        'end': {'date': extracted['date']},
                    }
                else:
                    continue

                service.events().insert(calendarId=cal_id, body=event_body).execute()
                registered.append(extracted.get('title') or 'イベント')

                deadline = extracted.get('application_deadline')
                if deadline:
                    deadline_event = {
                        'summary': f"【申込期限】{extracted.get('title') or 'イベント'}",
                        'description': '申込期限です。忘れずに！',
                        'start': {'date': deadline},
                        'end': {'date': deadline},
                    }
                    service.events().insert(calendarId=cal_id, body=deadline_event).execute()
                    deadline_count += 1

            reply = f"✅ {len(registered)}件を「気になるイベント」に登録しました！\n\n"
            for title in registered:
                reply += f"📌 {title}\n"
            if deadline_count:
                reply += f"\n⚠️ 申込期限{deadline_count}件も登録しました！\n1週間前・3日前・前日・当日にお知らせします！"
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply))

        except Exception as e:
            print(f"Calendar insert error: {e}")
            line_bot_api.reply_message(
                event.reply_token,
                TextSendMessage(text=f"登録中にエラーが発生しました😢\n{str(e)[:100]}")
            )
        return

    # セキスイブログ：作成中セッションチェック
    sekisui_sessions = load_sekisui_sessions()
    if user_id in sekisui_sessions and sekisui_sessions[user_id] == 'waiting_for_content':
        del sekisui_sessions[user_id]
        save_sekisui_sessions(sekisui_sessions)
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text="✍️ 記事を作成中です...少しお待ちください！（1〜2分かかります）"))
        threading.Thread(target=process_sekisui_article, args=(user_id, user_message)).start()
        return

    # eBayリサーチ
    ebay_research_keywords = ['eBayリサーチ', 'ebayリサーチ', 'eBay リサーチ', 'eBayリサーチして', '物販リサーチ', 'リサーチして']
    if any(kw in user_message for kw in ebay_research_keywords):
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text="📦 eBayリサーチを開始します！\n結果が届くまで2〜3分お待ちください🔍"))
        threading.Thread(target=run_ebay_research, args=(user_id,)).start()
        return

    # セキスイブログ：キーワード検出 → テーマ提案
    sekisui_keywords = ['セキスイ記事', 'セキスイブログ', 'セキスイ 記事', 'order-sekisui']
    if any(kw in user_message for kw in sekisui_keywords):
        themes = suggest_sekisui_themes()
        sekisui_sessions[user_id] = 'waiting_for_content'
        save_sekisui_sessions(sekisui_sessions)
        msg = f"🏠 セキスイブログ記事を作りましょう！\n\nテーマ候補：\n{themes}\n\n番号と実体験・エピソードを一緒に教えてください！\n例：「2番で。先月の電気代が想像より安くて驚いた」"
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=msg))
        return

    # 薬膳ブログ：セッションチェック
    yakuzen_sessions = load_yakuzen_sessions()
    if user_id in yakuzen_sessions:
        session = yakuzen_sessions[user_id]
        state = session.get('state')

        if state == 'waiting_for_mode':
            import unicodedata
            normalized = unicodedata.normalize('NFKC', user_message)
            if any(kw in normalized for kw in ['新規', '作成', '新しい', '1', '①']):
                del yakuzen_sessions[user_id]
                yakuzen_sessions[user_id] = {'state': 'waiting_for_new_topic'}
                save_yakuzen_sessions(yakuzen_sessions)
                line_bot_api.reply_message(event.reply_token, TextSendMessage(
                    text="✍️ 薬膳記事を新規作成します！\n\nテーマや書きたいことを教えてください。\n例：「春の花粉症に効く食材」「更年期のほてりに薬膳」"
                ))
            elif any(kw in normalized for kw in ['リライト', '更新', '既存', '2', '②']):
                del yakuzen_sessions[user_id]
                save_yakuzen_sessions(yakuzen_sessions)
                line_bot_api.reply_message(event.reply_token, TextSendMessage(
                    text="🌿 今の季節に合った記事を自動選択してリライトします！\n数分かかります、そのままお待ちください..."
                ))
                threading.Thread(target=auto_rewrite_yakuzen, args=(user_id,)).start()
            else:
                # セッションはそのまま残して再入力を促す
                line_bot_api.reply_message(event.reply_token, TextSendMessage(
                    text="「1」か「新規作成」、または「2」か「リライト」と送ってください！"
                ))
            return

        elif state == 'waiting_for_new_topic':
            del yakuzen_sessions[user_id]
            save_yakuzen_sessions(yakuzen_sessions)
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text="✍️ 薬膳記事を作成中です...少しお待ちください！（1〜2分かかります）"))
            threading.Thread(target=process_yakuzen_new_article, args=(user_id, user_message)).start()
            return


    # 薬膳ブログ：キーワード検出
    yakuzen_keywords = ['薬膳記事', '薬膳ブログ', '薬膳 記事', '薬膳リライト', 'foodmakehealth', '薬膳の記事']
    if any(kw in user_message for kw in yakuzen_keywords):
        yakuzen_sessions[user_id] = {'state': 'waiting_for_mode'}
        save_yakuzen_sessions(yakuzen_sessions)
        msg = "🌿 薬膳ブログ、何をしますか？\n\n1️⃣ 新規作成（新しい記事を書く）\n2️⃣ リライト（既存記事を更新する）\n\n番号か言葉で教えてください！"
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=msg))
        return

    try:
        events = get_upcoming_events(days=14)
        events_text = format_events(events)
    except Exception as e:
        print(f"Calendar error: {e}")
        import traceback
        traceback.print_exc()
        events_text = f"（カレンダー取得エラー: {str(e)[:100]}）"

    now_str = datetime.datetime.now(JST).strftime('%Y年%m月%d日 %H:%M')

    response = anthropic_client.messages.create(
        model='claude-sonnet-4-6',
        max_tokens=1000,
        system=f"""あなたはまきさんの個人秘書「まきの秘書」です。
現在時刻: {now_str}

【今後2週間の予定】
{events_text}

役割:
- スケジュール確認・整理
- やるべきことのリマインド
- 事前準備が必要なことの提案
- 親切で簡潔に日本語で返答する

予定の追加・変更はGoogleカレンダーを直接操作するよう案内してください。""",
        messages=[{'role': 'user', 'content': user_message}]
    )

    reply_text = response.content[0].text
    line_bot_api.reply_message(
        event.reply_token,
        TextSendMessage(text=reply_text)
    )


def send_morning_message():
    try:
        events = get_upcoming_events(days=1)
        now = datetime.datetime.now(JST)
        weekdays = ['月', '火', '水', '木', '金', '土', '日']
        today_str = f"{now.strftime('%m月%d日')}({weekdays[now.weekday()]})"

        msg = f"🌅 おはようございます！\n📅 {today_str}の予定\n"

        if events:
            msg += "\n"
            for event in events:
                start = event['start'].get('dateTime', event['start'].get('date'))
                if 'T' in start:
                    dt = datetime.datetime.fromisoformat(start).astimezone(JST)
                    time_str = dt.strftime('%H:%M')
                else:
                    time_str = "終日"
                title = event.get('summary', '（タイトルなし）')
                cal_name = event.get('_calendar_name', '')
                msg += f"⏰ {time_str}  {title}\n"
                if cal_name:
                    msg += f"   📂 {cal_name}\n"
                msg += "\n"
            msg += "今日も素敵な1日を！✨"
        else:
            msg += "\n予定はありません 🌸\nゆっくり過ごせそうですね！"

        user_id = os.environ['LINE_USER_ID']
        line_bot_api.push_message(user_id, TextSendMessage(text=msg))
    except Exception as e:
        print(f"Morning message error: {e}")


def send_preparation_reminder():
    try:
        events = get_upcoming_events(days=3)
        if not events:
            return

        user_id = os.environ['LINE_USER_ID']
        msg = "【3日以内の予定】事前準備は大丈夫ですか？\n\n"
        msg += format_events(events)
        msg += "\n\n準備が必要なことがあれば教えてください！"
        line_bot_api.push_message(user_id, TextSendMessage(text=msg))
    except Exception as e:
        print(f"Preparation reminder error: {e}")


def send_famm_reminder():
    try:
        user_id = os.environ['LINE_USER_ID']
        line_bot_api.push_message(user_id, TextSendMessage(
            text="📸 【Famm更新】今月のFammの更新をお忘れなく！\n期限は今月9日です。"
        ))
    except Exception as e:
        print(f"Famm reminder error: {e}")


def send_famm_deadline_reminder():
    try:
        user_id = os.environ['LINE_USER_ID']
        line_bot_api.push_message(user_id, TextSendMessage(
            text="⚠️ 【Famm期限まで3日！】\nFammの更新期限は9日です。まだの方はお早めに！"
        ))
    except Exception as e:
        print(f"Famm deadline reminder error: {e}")


def send_yakuzen_blog_reminder():
    try:
        user_id = os.environ['LINE_USER_ID']
        line_bot_api.push_message(user_id, TextSendMessage(
            text="✍️ 【薬膳ブログ更新日・火曜日】\n今日は薬膳ブログ＋Pinterest投稿の日です！\n\nClaudeに👇と声かけしてね\n「Pinterest今週分お願い」"
        ))
    except Exception as e:
        print(f"Yakuzen blog reminder error: {e}")


def send_sekisui_blog_reminder():
    try:
        user_id = os.environ['LINE_USER_ID']
        line_bot_api.push_message(user_id, TextSendMessage(
            text="🏠 【セキスイブログ更新日・木曜日】\n今日はセキスイブログの投稿日です！\n\n① 音声入力でネタを話す\n② テキストをClaudeに貼って👇\n「セキスイの記事投稿して」"
        ))
    except Exception as e:
        print(f"Sekisui blog reminder error: {e}")


def send_ebay_check_reminder():
    try:
        user_id = os.environ['LINE_USER_ID']
        line_bot_api.push_message(user_id, TextSendMessage(
            text="📦 【eBayチェック日・土曜日】\n今日はeBayの確認日です！\n\nClaudeに👇と声かけしてね\n「eBay状況確認して、次やること教えて」"
        ))
    except Exception as e:
        print(f"eBay check reminder error: {e}")


def send_a8_check_reminder():
    try:
        user_id = os.environ['LINE_USER_ID']
        line_bot_api.push_message(user_id, TextSendMessage(
            text="📧 【A8審査確認】\nA8の審査結果メールが届いていませんか？\n\n審査が通っていたらClaudeに👇\n「○○のA8審査通った。リンク追加して」\n\n確認待ち：\n・ユーキャン\n・がくぶん\n・ヒューマンアカデミー"
        ))
    except Exception as e:
        print(f"A8 check reminder error: {e}")


def send_monthly_review_reminder():
    try:
        user_id = os.environ['LINE_USER_ID']
        line_bot_api.push_message(user_id, TextSendMessage(
            text="📊 【月初振り返り】\n今日は先月の振り返り日です！\n\nClaudeに👇と声かけしてね\n「先月の副業収支まとめて」\n「薬膳・セキスイの今月進捗教えて」\n「eBay今月の売上と反省点まとめて」"
        ))
    except Exception as e:
        print(f"Monthly review reminder error: {e}")


scheduler = BackgroundScheduler(timezone='Asia/Tokyo')
scheduler.add_job(send_preparation_reminder, 'cron', hour=20, minute=0, day_of_week='sun')
scheduler.add_job(check_deadline_reminders, 'cron', hour=8, minute=0)
# 毎月1日朝9時：Famm更新リマインダー
scheduler.add_job(send_famm_reminder, 'cron', day=1, hour=9, minute=0)
# 毎月6日朝9時：Famm期限3日前リマインダー
scheduler.add_job(send_famm_deadline_reminder, 'cron', day=6, hour=9, minute=0)
# 毎週火曜朝9時：薬膳ブログ更新リマインダー
scheduler.add_job(send_yakuzen_blog_reminder, 'cron', day_of_week='tue', hour=9, minute=0)
# 毎週木曜朝9時：セキスイブログ更新リマインダー
scheduler.add_job(send_sekisui_blog_reminder, 'cron', day_of_week='thu', hour=9, minute=0)
# 毎週土曜朝9時：eBayチェックリマインダー
scheduler.add_job(send_ebay_check_reminder, 'cron', day_of_week='sat', hour=9, minute=0)
# 毎月1日朝9時30分：月初振り返りリマインダー（Fammリマインダーの30分後）
scheduler.add_job(send_monthly_review_reminder, 'cron', day=1, hour=9, minute=30)
# 毎週月曜朝9時：A8審査確認リマインダー（全審査通過後に削除してOK）
scheduler.add_job(send_a8_check_reminder, 'cron', day_of_week='mon', hour=9, minute=0)
scheduler.start()

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
