import os
import json
import base64
import datetime
import threading
import time
import requests
from flask import Flask, request, abort, send_from_directory
from linebot.exceptions import InvalidSignatureError
from linebot.models import MessageEvent, TextMessage, TextSendMessage, ImageMessage, AudioMessage, FileMessage
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from apscheduler.schedulers.background import BackgroundScheduler

from clients import line_bot_api, handler, anthropic_client, JST
from ebay_handler import run_ebay_research
from blog_yakuzen import auto_rewrite_yakuzen, process_yakuzen_new_article, process_yakuzen_rewrite, rewrite_yakuzen_by_slug, rewrite_yakuzen_by_keyword, get_pinterest_access_token
from blog_sekisui import suggest_sekisui_themes, process_sekisui_article

app = Flask(__name__)

PENDING_FILE = '/tmp/pending_events.json'
SEKISUI_SESSION_FILE = '/tmp/sekisui_sessions.json'
YAKUZEN_SESSION_FILE = '/tmp/yakuzen_sessions.json'
PRINTS_FILE = '/tmp/school_prints.json'
PRINT_SESSION_FILE = '/tmp/print_sessions.json'


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


def load_prints():
    try:
        with open(PRINTS_FILE, 'r') as f:
            return json.load(f)
    except Exception:
        return {}


def save_prints(data):
    try:
        with open(PRINTS_FILE, 'w') as f:
            json.dump(data, f, ensure_ascii=False)
    except Exception as e:
        print(f"prints save error: {e}")


def load_print_sessions():
    try:
        with open(PRINT_SESSION_FILE, 'r') as f:
            return json.load(f)
    except Exception:
        return {}


def save_print_sessions(data):
    try:
        with open(PRINT_SESSION_FILE, 'w') as f:
            json.dump(data, f, ensure_ascii=False)
    except Exception as e:
        print(f"print_sessions save error: {e}")


# ========== カレンダー ==========

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


@app.route('/test-x-post')
def test_x_post():
    import tweepy
    try:
        client = _get_x_client()
        if not client:
            return 'Error: client is None (keys missing)', 500
        post_text = generate_x_post(0)
        resp = client.create_tweet(text=post_text)
        return f'Success: {resp}', 200
    except tweepy.errors.Unauthorized as e:
        return f'401 Unauthorized - response: {e.response.text if hasattr(e, "response") else str(e)}', 401
    except tweepy.errors.Forbidden as e:
        return f'403 Forbidden - response: {e.response.text if hasattr(e, "response") else str(e)}', 403
    except Exception as e:
        return f'Error ({type(e).__name__}): {e}', 500


@app.route('/debug-x-auth')
def debug_x_auth():
    import requests
    from requests_oauthlib import OAuth1
    api_key = os.environ.get('X_API_KEY', '')
    api_secret = os.environ.get('X_API_SECRET', '')
    access_token = os.environ.get('X_ACCESS_TOKEN', '')
    access_token_secret = os.environ.get('X_ACCESS_TOKEN_SECRET', '')
    try:
        auth = OAuth1(api_key, api_secret, access_token, access_token_secret)
        r = requests.post(
            'https://api.twitter.com/2/tweets',
            json={'text': 'テスト投稿（自動）🤖 #AI副業'},
            auth=auth
        )
        return {'status': r.status_code, 'body': r.json()}, 200
    except Exception as e:
        return {'error': str(e)}, 500


@app.route('/debug-x-keys')
def debug_x_keys():
    def mask(v):
        return (v[:6] + '...' + v[-4:]) if v and len(v) > 10 else ('(empty)' if not v else v)
    return {
        'X_API_KEY': mask(os.environ.get('X_API_KEY')),
        'X_API_SECRET': mask(os.environ.get('X_API_SECRET')),
        'X_ACCESS_TOKEN': mask(os.environ.get('X_ACCESS_TOKEN')),
        'X_ACCESS_TOKEN_SECRET': mask(os.environ.get('X_ACCESS_TOKEN_SECRET')),
    }


def _build_overlay_jpeg(img_url: str, title: str) -> bytes:
    """アイキャッチ画像にタイトルを重ねてJPEGバイト列を返す"""
    from PIL import Image, ImageDraw, ImageFont
    from io import BytesIO

    r = requests.get(img_url, timeout=15, headers={'User-Agent': 'Mozilla/5.0'}, allow_redirects=True)
    r.raise_for_status()
    content_type = r.headers.get('Content-Type', '')
    if 'image' not in content_type:
        raise ValueError(f"Not image: {content_type} | first80: {r.content[:80]}")
    img = Image.open(BytesIO(r.content)).convert('RGBA')
    img = img.resize((1080, 1080), Image.LANCZOS)

    overlay = Image.new('RGBA', (1080, 1080), (0, 0, 0, 0))
    draw_ov = ImageDraw.Draw(overlay)
    for y in range(1080):
        alpha = int(180 * (y / 1080))
        draw_ov.line([(0, y), (1080, y)], fill=(0, 0, 0, alpha))
    img = Image.alpha_composite(img, overlay)

    draw = ImageDraw.Draw(img)
    font_path = os.path.join(os.path.dirname(__file__), 'fonts', 'NotoSansJP-Bold.otf')
    with open(font_path, 'rb') as _f:
        font = ImageFont.truetype(BytesIO(_f.read()), 60)

    max_chars = 14
    lines = []
    t = title
    while len(t) > max_chars:
        lines.append(t[:max_chars])
        t = t[max_chars:]
    lines.append(t)

    line_height = 72
    y_start = 1080 - line_height * len(lines) - 80
    for line in lines:
        bbox = draw.textbbox((0, 0), line, font=font)
        x = (1080 - (bbox[2] - bbox[0])) // 2
        draw.text((x + 2, y_start + 2), line, font=font, fill=(0, 0, 0, 200))
        draw.text((x, y_start), line, font=font, fill=(255, 255, 255, 255))
        y_start += line_height

    buf = __import__('io').BytesIO()
    img.convert('RGB').save(buf, format='JPEG', quality=90)
    return buf.getvalue()


def _upload_to_wp(img_data: bytes, filename: str, wp_url: str, wp_user: str, wp_pass: str):
    """WPメディアライブラリにJPEGをアップロードしてsource_urlを返す"""
    res = requests.post(
        f"{wp_url}/wp-json/wp/v2/media",
        auth=(wp_user, wp_pass),
        headers={'Content-Disposition': f'attachment; filename="{filename}"', 'Content-Type': 'image/jpeg'},
        data=img_data,
    )
    if res.status_code == 201:
        return res.json()['id'], res.json()['source_url']
    raise RuntimeError(f"WP media upload {res.status_code}: {res.text[:300]}")


@app.route('/overlay-image')
def overlay_image():
    """アイキャッチ画像にタイトルを重ねてWPメディアにアップロードしURLを返す"""
    img_url = request.args.get('img_url', '')
    title = request.args.get('title', '')
    wp_url = request.args.get('wp_url', os.environ.get('SEKISUI_WP_URL', ''))
    wp_user = os.environ.get('SEKISUI_WP_USER', '')
    wp_pass = os.environ.get('SEKISUI_WP_APP_PASSWORD', '')
    if not img_url or not title:
        return 'img_url and title are required', 400
    try:
        img_data = _build_overlay_jpeg(img_url, title)
        filename = 'og_' + img_url.split('/')[-1].split('.')[0] + '.jpg'
        _, media_url = _upload_to_wp(img_data, filename, wp_url, wp_user, wp_pass)
        if media_url:
            return {'url': media_url}, 200
        return {'error': 'WP upload failed'}, 500
    except Exception as e:
        return {'error': str(e)}, 500


def _do_overlay_and_update(post_id, wp_url, wp_user, wp_pass):
    """バックグラウンドでオーバーレイ画像を生成してWPのアイキャッチを更新する"""
    try:
        post_res = requests.get(f"{wp_url}/wp-json/wp/v2/posts/{post_id}", auth=(wp_user, wp_pass), timeout=15)
        if post_res.status_code != 200:
            print(f"[overlay] post not found: {post_id}")
            return
        post = post_res.json()
        title = post['title']['rendered']
        featured_media_id = post.get('featured_media', 0)
        if not featured_media_id:
            print(f"[overlay] no featured image: {post_id}")
            return

        media_res = requests.get(f"{wp_url}/wp-json/wp/v2/media/{featured_media_id}", auth=(wp_user, wp_pass), timeout=15)
        if media_res.status_code != 200:
            print(f"[overlay] media not found: {featured_media_id}")
            return
        img_url = media_res.json()['source_url']

        img_data = _build_overlay_jpeg(img_url, title)
        filename = f'ig_{post_id}.jpg'
        new_media_id, media_url = _upload_to_wp(img_data, filename, wp_url, wp_user, wp_pass)

        requests.post(
            f"{wp_url}/wp-json/wp/v2/posts/{post_id}",
            auth=(wp_user, wp_pass),
            json={'featured_media': new_media_id},
            timeout=15,
        )
        print(f"[overlay] done: {post_id} -> {media_url}")
    except Exception as e:
        import traceback
        print(f"[overlay] error: {e}\n{traceback.format_exc()}")


@app.route('/wp-post-published', methods=['POST'])
def wp_post_published():
    """WP Webhooksから記事公開通知を受け取り、バックグラウンドでオーバーレイ画像を更新する"""
    data = request.json or {}

    post_status = data.get('post_status', '')
    post_type = data.get('post_type', 'post')
    if post_status != 'publish' or post_type != 'post':
        return {'status': 'skipped'}, 200

    post_id = data.get('ID') or data.get('post_id')
    wp_url = os.environ.get('SEKISUI_WP_URL', 'https://order-sekisui.com')
    wp_user = os.environ.get('SEKISUI_WP_USER', 'makiko01035')
    wp_pass = os.environ.get('SEKISUI_WP_APP_PASSWORD', '')

    import threading
    threading.Thread(target=_do_overlay_and_update, args=(post_id, wp_url, wp_user, wp_pass), daemon=True).start()
    return {'status': 'accepted'}, 202


@app.route('/rewrite-yakuzen-direct', methods=['POST'])
def rewrite_yakuzen_direct():
    """Claude Codeから直接薬膳記事をSEOリライトするエンドポイント"""
    secret = request.headers.get('X-Secret', '')
    if secret != os.environ.get('LINE_USER_ID', ''):
        return {'error': 'unauthorized'}, 401
    data = request.json or {}
    post_id = data.get('post_id')
    instruction = data.get('instruction', '')
    if not post_id:
        return {'error': 'post_id required'}, 400
    def _do_rewrite(post_id, instruction):
        try:
            from blog_yakuzen import get_yakuzen_wp_creds, generate_yakuzen_rewrite, post_to_yakuzen_wp
            wp_url, wp_user, wp_pass = get_yakuzen_wp_creds()
            r = requests.get(
                f'{wp_url}/wp-json/wp/v2/posts/{post_id}',
                params={'_fields': 'title,content'},
                auth=(wp_user, wp_pass), timeout=30
            )
            post = r.json()
            title = post['title']['rendered']
            content_html = post['content']['rendered']
            new_md = generate_yakuzen_rewrite(title, content_html, instruction)
            lines = new_md.split('\n')
            new_title = lines[0].lstrip('# ').strip()
            new_content = '\n'.join(lines[1:]).lstrip('\n')
            _, new_url = post_to_yakuzen_wp(new_title, new_content, post_id=post_id, status='publish')
            user_id = os.environ.get('LINE_USER_ID', '')
            line_bot_api.push_message(user_id, TextSendMessage(text=f'✅ SEOリライト完了！\n📝 {new_title}\n🔗 {new_url}'))
        except Exception as e:
            import traceback
            user_id = os.environ.get('LINE_USER_ID', '')
            line_bot_api.push_message(user_id, TextSendMessage(text=f'❌ リライトエラー：{str(e)[:200]}'))

    threading.Thread(target=_do_rewrite, args=(post_id, instruction)).start()
    return {'status': 'accepted', 'message': 'リライト開始。完了はLINEに通知します'}, 202


@app.route('/post-sekisui-direct', methods=['POST'])
def post_sekisui_direct():
    """Claude Codeから直接セキスイ記事を投稿するエンドポイント"""
    secret = request.headers.get('X-Secret', '')
    if secret != os.environ.get('LINE_USER_ID', ''):
        return {'error': 'unauthorized'}, 401
    data = request.json or {}
    title = data.get('title', '')
    content_md = data.get('content_md', '')
    if not title or not content_md:
        return {'error': 'title and content_md required'}, 400
    try:
        from blog_sekisui import post_to_sekisui_wp
        post_id, post_url = post_to_sekisui_wp(title, content_md)
        return {'status': 'ok', 'post_id': post_id, 'url': post_url}, 201
    except Exception as e:
        return {'error': str(e)}, 500


@app.route('/debug-image')
def debug_image():
    """画像URLの取得状態をデバッグするエンドポイント"""
    img_url = request.args.get('url', '')
    if not img_url:
        return 'url param required', 400
    try:
        r = requests.get(img_url, timeout=15, headers={'User-Agent': 'Mozilla/5.0'}, allow_redirects=True)
        ct = r.headers.get('Content-Type', 'unknown')
        first_bytes = r.content[:16].hex()
        return {'status': r.status_code, 'content_type': ct, 'size': len(r.content), 'first_bytes_hex': first_bytes}
    except Exception as e:
        return {'error': str(e)}, 500


@app.route('/check-creds')
def check_creds():
    """GOOGLE_CREDENTIALS の形式を確認するデバッグ用エンドポイント"""
    try:
        raw = os.environ.get('GOOGLE_CREDENTIALS', '')
        parsed = json.loads(raw)
        keys = list(parsed.keys())
        scopes = parsed.get('scopes', [])
        has_refresh = bool(parsed.get('refresh_token'))
        return f"OK\nkeys: {keys}\nscopes: {scopes}\nhas_refresh_token: {has_refresh}\nfirst_30_chars: {raw[:30]}"
    except Exception as e:
        raw = os.environ.get('GOOGLE_CREDENTIALS', '')
        return f"JSON parse error: {e}\nfirst_80_chars: {raw[:80]}", 500


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

  /* クイックアクセス */
  .quick-section {{ margin-bottom: 56px; }}
  .quick-grid {{
    display: flex;
    gap: 16px;
    flex-wrap: wrap;
  }}
  .quick-btn {{
    display: flex;
    align-items: center;
    gap: 12px;
    background: var(--card);
    border: 1px solid var(--border);
    border-radius: 2px;
    padding: 20px 28px;
    color: var(--text);
    text-decoration: none;
    font-size: 14px;
    font-family: 'Noto Sans JP', sans-serif;
    font-weight: 300;
    transition: border-color 0.3s, color 0.3s;
    letter-spacing: 1px;
  }}
  .quick-btn:hover {{
    border-color: var(--gold);
    color: var(--gold-light);
  }}
  .quick-btn-icon {{
    font-size: 20px;
    line-height: 1;
  }}

  @media (max-width: 768px) {{
    header {{ padding: 24px; flex-direction: column; align-items: flex-start; gap: 16px; }}
    main {{ padding: 24px; }}
    .departments {{ grid-template-columns: 1fr; }}
    .goal-card {{ flex-direction: column; gap: 24px; align-items: flex-start; }}
    .goal-divider {{ width: 40px; height: 1px; }}
    .line-grid {{ grid-template-columns: 1fr; }}
    .quick-grid {{ flex-direction: column; }}
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

  <!-- クイックアクセス -->
  <div class="quick-section">
    <p class="section-title">Quick Access</p>
    <div class="quick-grid">
      <a class="quick-btn" href="/game" target="_blank">
        <span class="quick-btn-icon">🎮</span>
        <span>まるちゃんワールド</span>
      </a>
      <a class="quick-btn" href="/office" target="_blank">
        <span class="quick-btn-icon">🏢</span>
        <span>会社組織図</span>
      </a>
    </div>
  </div>

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


@app.route('/game')
def game_index():
    return send_from_directory('.', 'index.html')

@app.route('/game/<path:filename>')
def game_files(filename):
    return send_from_directory('.', filename)

@app.route('/office')
def company_office():
    return send_from_directory('.', 'company_office.html')

@app.route('/instagram-roadmap')
def instagram_roadmap():
    return send_from_directory('.', 'instagram_roadmap.html')

@app.route('/x-study-note')
def x_study_note():
    return send_from_directory('.', 'x_study_note.html')

@app.route('/web-marketing')
def web_marketing_notes():
    return send_from_directory('.', 'web_marketing_notes.html')

@app.route('/ebay-guide')
def ebay_guide():
    return send_from_directory('.', 'ebay_guide.html')

@app.route('/ebay-calc')
def ebay_calculator():
    return send_from_directory('.', 'ebay_calculator.html')

@app.route('/game/rhythm')
def game_rhythm():
    return send_from_directory('.', 'maruchan_rhythm.html')


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


def create_rich_menu_image():
    img_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'static', 'richmenu.png')
    with open(img_path, 'rb') as f:
        return f.read()


def setup_rich_menu():
    token = os.environ.get('LINE_CHANNEL_ACCESS_TOKEN', '')
    auth = {'Authorization': f'Bearer {token}'}
    try:
        res = requests.get('https://api.line.me/v2/bot/richmenu/list', headers=auth)
        for rm in res.json().get('richmenus', []):
            requests.delete(f"https://api.line.me/v2/bot/richmenu/{rm['richMenuId']}", headers=auth)
    except Exception:
        pass
    rich_menu = {
        "size": {"width": 2500, "height": 843},
        "selected": True,
        "name": "まきの秘書メニュー",
        "chatBarText": "メニュー",
        "areas": [
            {"bounds": {"x": 0,    "y": 0,   "width": 833, "height": 421},
             "action": {"type": "uri", "uri": "https://www.ebay.com/sh/research"}},
            {"bounds": {"x": 833,  "y": 0,   "width": 834, "height": 421},
             "action": {"type": "uri", "uri": "https://jp.mercari.com"}},
            {"bounds": {"x": 1667, "y": 0,   "width": 833, "height": 421},
             "action": {"type": "uri", "uri": "https://maki-hisho.onrender.com/ebay-calc"}},
            {"bounds": {"x": 0,    "y": 421, "width": 833, "height": 422},
             "action": {"type": "message", "text": "薬膳記事"}},
            {"bounds": {"x": 833,  "y": 421, "width": 834, "height": 422},
             "action": {"type": "message", "text": "セキスイ記事"}},
            {"bounds": {"x": 1667, "y": 421, "width": 833, "height": 422},
             "action": {"type": "message", "text": "今日の予定"}},
        ]
    }
    res = requests.post('https://api.line.me/v2/bot/richmenu',
                        headers={**auth, 'Content-Type': 'application/json'}, json=rich_menu)
    if res.status_code != 200:
        return False, f"作成失敗: {res.status_code} {res.text}"
    rm_id = res.json()['richMenuId']
    image_data = create_rich_menu_image()
    res = requests.post(f'https://api-data.line.me/v2/bot/richmenu/{rm_id}/content',
                        headers={**auth, 'Content-Type': 'image/png'}, data=image_data)
    if res.status_code != 200:
        return False, f"画像アップロード失敗: {res.status_code} {res.text}"
    res = requests.post(f'https://api.line.me/v2/bot/user/all/richmenu/{rm_id}', headers=auth)
    if res.status_code != 200:
        return False, f"デフォルト設定失敗: {res.status_code} {res.text}"
    return True, rm_id


@app.route('/setup-richmenu')
def setup_richmenu_endpoint():
    ok, result = setup_rich_menu()
    if ok:
        return f'<html><head><meta charset="utf-8"></head><body><h2>✅ リッチメニュー登録完了！</h2><p>ID: {result}</p></body></html>'
    return f'<html><head><meta charset="utf-8"></head><body><h2>❌ エラー</h2><p>{result}</p></body></html>', 500


@app.route('/richmenu-preview')
def richmenu_preview():
    from flask import send_file
    import io, traceback
    try:
        data = create_rich_menu_image()
        return send_file(io.BytesIO(data), mimetype='image/png')
    except Exception:
        return f'<pre>{traceback.format_exc()}</pre>', 500


@app.route('/ebay-callback')
def ebay_callback():
    code = request.args.get('code')
    if not code:
        return 'codeパラメータが見つかりません。', 400
    return f'''<html><head><meta charset="utf-8"></head><body>
<h2>eBay認証成功！</h2>
<p>以下のcodeをClaude Codeに貼り付けてください：</p>
<textarea rows="4" cols="80" onclick="this.select()">{code}</textarea>
<br><br>
<p style="color:gray">このページを閉じてOKです。</p>
</body></html>'''


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

    # プリントセッションチェック
    print_sessions = load_print_sessions()
    if user_id in print_sessions and print_sessions[user_id] == 'waiting_for_print_image':
        del print_sessions[user_id]
        save_print_sessions(print_sessions)
        try:
            response = anthropic_client.messages.create(
                model='claude-sonnet-4-6',
                max_tokens=1000,
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
                            'text': """これは学校や習い事から届いたプリント・お知らせです。
以下のJSON形式で情報を抽出してください（情報がない場合はnullにしてください）：
{
  "title": "プリント名・タイトル",
  "category": "カテゴリ（行事/提出物/集金/持ち物/アンケート/連絡/その他）",
  "deadline": "締切・提出期限（YYYY-MM-DD形式、ない場合はnull）",
  "amount": "集金額（例：500円、ない場合はnull）",
  "items": "持ち物・提出物の内容（ない場合はnull）",
  "notes": "その他重要なメモ（ない場合はnull）"
}
JSON形式のみ返してください。"""
                        }
                    ]
                }]
            )
            raw_text = response.content[0].text.strip()
            import re
            if '```' in raw_text:
                match = re.search(r'```(?:json)?\s*([\s\S]*?)```', raw_text)
                if match:
                    raw_text = match.group(1).strip()
            start = raw_text.find('{')
            end = raw_text.rfind('}')
            print_data = json.loads(raw_text[start:end+1])

            prints = load_prints()
            user_prints = prints.get(user_id, [])
            new_id = max([p['id'] for p in user_prints], default=0) + 1
            print_data['id'] = new_id
            print_data['created_at'] = datetime.date.today().isoformat()
            print_data['done'] = False
            user_prints.append(print_data)
            prints[user_id] = user_prints
            save_prints(prints)

            msg = f"📄 プリントを保存しました！（No.{new_id}）\n\n"
            msg += f"📌 {print_data.get('title') or '（タイトル不明）'}\n"
            msg += f"🏷️ {print_data.get('category') or '不明'}\n"
            if print_data.get('deadline'):
                msg += f"⚠️ 締切: {print_data['deadline']}\n"
            if print_data.get('amount'):
                msg += f"💴 集金: {print_data['amount']}\n"
            if print_data.get('items'):
                msg += f"🎒 持ち物: {print_data['items']}\n"
            if print_data.get('notes'):
                msg += f"📝 メモ: {print_data['notes']}\n"

            if print_data.get('deadline'):
                msg += f"\n「プリント登録 {new_id}」で締切をカレンダーに登録できます！"

            line_bot_api.reply_message(event.reply_token, TextSendMessage(text=msg))
        except Exception as e:
            print(f"Print extract error: {e}")
            import traceback; traceback.print_exc()
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text=f"プリントの読み取りに失敗しました😢\nエラー: {str(e)[:100]}"))
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
        # Markdownコードブロックを除去
        if '```' in raw_text:
            import re
            match = re.search(r'```(?:json)?\s*([\s\S]*?)```', raw_text)
            if match:
                raw_text = match.group(1).strip()
        # JSON配列を抽出してパース
        start = raw_text.find('[')
        end = raw_text.rfind(']')
        if start == -1 or end == -1:
            # 配列がない場合はオブジェクトを探す
            start = raw_text.find('{')
            end = raw_text.rfind('}')
            json_str = raw_text[start:end+1]
            extracted_list = [json.loads(json_str)]
        else:
            json_str = raw_text[start:end+1]
            extracted_list = json.loads(json_str)
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


def run_transcription(user_id, audio_data, filename='audio.m4a'):
    try:
        groq_api_key = os.environ.get('GROQ_API_KEY', '')
        ext = filename.rsplit('.', 1)[-1].lower() if '.' in filename else 'm4a'
        mime = {'mp3': 'audio/mpeg', 'mp4': 'audio/mp4', 'wav': 'audio/wav', 'webm': 'audio/webm'}.get(ext, 'audio/m4a')
        files = {'file': (filename, audio_data, mime)}
        data = {'model': 'whisper-large-v3', 'language': 'ja', 'response_format': 'text'}
        resp = requests.post(
            'https://api.groq.com/openai/v1/audio/transcriptions',
            headers={'Authorization': f'Bearer {groq_api_key}'},
            files=files,
            data=data,
            timeout=60
        )
        if resp.status_code != 200:
            raise Exception(f"Groq error {resp.status_code}: {resp.text[:200]}")
        transcript = resp.text.strip()

        summary = anthropic_client.messages.create(
            model='claude-sonnet-4-6',
            max_tokens=1500,
            messages=[{
                'role': 'user',
                'content': f"""以下の音声文字起こしから議事録を作成してください。

【文字起こし】
{transcript}

【議事録フォーマット】
📋 議事録
日時：{datetime.datetime.now(JST).strftime('%Y年%m月%d日 %H:%M')}

■ 話題・内容
（要点を箇条書きで）

■ 決定事項
（決まったことを箇条書き、なければ「なし」）

■ ToDoリスト
（誰が何をするか、なければ「なし」）

---
📝 文字起こし原文
{transcript}"""
            }]
        )
        result = summary.content[0].text
        line_bot_api.push_message(user_id, TextSendMessage(text=result))
    except Exception as e:
        print(f"Transcription error: {e}")
        import traceback; traceback.print_exc()
        line_bot_api.push_message(user_id, TextSendMessage(text=f"文字起こしに失敗しました😢\n{str(e)[:150]}"))


@handler.add(MessageEvent, message=AudioMessage)
def handle_audio(event):
    message_id = event.message.id
    try:
        message_content = line_bot_api.get_message_content(message_id)
        audio_data = b''.join(chunk for chunk in message_content.iter_content())
    except Exception as e:
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=f"音声の取得に失敗しました😢\n{str(e)[:100]}"))
        return
    line_bot_api.reply_message(event.reply_token, TextSendMessage(text="🎤 音声を受け取りました！文字起こし中...少々お待ちください"))
    threading.Thread(target=run_transcription, args=(event.source.user_id, audio_data)).start()


@handler.add(MessageEvent, message=FileMessage)
def handle_file(event):
    filename = event.message.file_name
    ext = filename.rsplit('.', 1)[-1].lower() if '.' in filename else ''
    if ext not in ('mp3', 'mp4', 'm4a', 'wav', 'webm', 'ogg', 'flac'):
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=f"音声ファイル（mp3/m4a/wav等）を送ってください。\n受け取ったファイル: {filename}"))
        return
    message_id = event.message.id
    try:
        message_content = line_bot_api.get_message_content(message_id)
        audio_data = b''.join(chunk for chunk in message_content.iter_content())
    except Exception as e:
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=f"ファイルの取得に失敗しました😢\n{str(e)[:100]}"))
        return
    line_bot_api.reply_message(event.reply_token, TextSendMessage(text=f"🎤 {filename} を受け取りました！文字起こし中...少々お待ちください"))
    threading.Thread(target=run_transcription, args=(event.source.user_id, audio_data, filename)).start()


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
    if user_message == '登録して' and user_id not in pending_events:
        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(text="📋 登録するチラシ画像が見つかりません。\n\nもう一度チラシの画像を送ってから「登録して」と送ってください！")
        )
        return
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
                        if end_dt <= start_dt:
                            end_dt += datetime.timedelta(days=1)
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
            import traceback
            tb = traceback.format_exc()
            print(f"Calendar insert error: {e}\n{tb}")
            # どのステップで失敗したか判別
            err_str = str(e)
            if 'GOOGLE_CREDENTIALS' in tb or ('json' in err_str.lower() and 'double quotes' in err_str.lower()):
                detail = "Google認証情報が壊れています😢\n\nRenderの GOOGLE_CREDENTIALS を credentials_for_render.txt の内容で更新してください。"
            elif 'HttpError' in err_str or 'googleapis' in err_str:
                detail = f"GoogleカレンダーAPIエラー:\n{err_str[:120]}"
            else:
                detail = err_str[:120]
            line_bot_api.reply_message(
                event.reply_token,
                TextSendMessage(text=f"登録中にエラーが発生しました😢\n{detail}")
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

    # ========== プリント管理 ==========

    # プリント一覧
    if user_message in ['プリント一覧', 'プリント確認', 'プリントリスト']:
        prints = load_prints()
        user_prints = [p for p in prints.get(user_id, []) if not p.get('done')]
        if not user_prints:
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text="📭 保存中のプリントはありません！\n「プリント」と送って写真を撮ると保存できます📄"))
        else:
            msg = f"📄 プリント一覧（{len(user_prints)}件）\n\n"
            for p in user_prints:
                msg += f"【No.{p['id']}】{p.get('title') or '（タイトル不明）'}\n"
                msg += f"  🏷️ {p.get('category') or '不明'}"
                if p.get('deadline'):
                    msg += f"  ⚠️ 締切:{p['deadline']}"
                if p.get('amount'):
                    msg += f"  💴{p['amount']}"
                msg += "\n"
            msg += "\n「プリント完了 番号」で完了済みにできます"
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text=msg))
        return

    # プリント完了
    import re as _re
    m = _re.match(r'^プリント完了\s*(\d+)$', user_message.strip())
    if m:
        target_id = int(m.group(1))
        prints = load_prints()
        user_prints = prints.get(user_id, [])
        found = False
        for p in user_prints:
            if p['id'] == target_id:
                p['done'] = True
                found = True
                break
        if found:
            save_prints(prints)
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text=f"✅ No.{target_id} を完了にしました！お疲れさまです🎉"))
        else:
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text=f"No.{target_id} が見つかりませんでした。「プリント一覧」で番号を確認してください。"))
        return

    # プリント締切をカレンダー登録
    m2 = _re.match(r'^プリント登録\s*(\d+)$', user_message.strip())
    if m2:
        target_id = int(m2.group(1))
        prints = load_prints()
        user_prints = prints.get(user_id, [])
        target_print = next((p for p in user_prints if p['id'] == target_id), None)
        if not target_print:
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text=f"No.{target_id} が見つかりませんでした。"))
            return
        if not target_print.get('deadline'):
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text=f"No.{target_id}「{target_print.get('title')}」には締切日がありません。"))
            return
        try:
            service = get_calendar_service()
            cal_id = get_or_create_maybe_calendar(service)
            deadline_event = {
                'summary': f"【プリント締切】{target_print.get('title') or 'プリント'}",
                'description': f"カテゴリ: {target_print.get('category') or ''}\n集金: {target_print.get('amount') or ''}\n持ち物: {target_print.get('items') or ''}",
                'start': {'date': target_print['deadline']},
                'end': {'date': target_print['deadline']},
            }
            service.events().insert(calendarId=cal_id, body=deadline_event).execute()
            line_bot_api.reply_message(event.reply_token, TextSendMessage(
                text=f"✅ カレンダーに登録しました！\n📌 {target_print.get('title')}\n⚠️ 締切: {target_print['deadline']}\n\n1週間前・3日前・前日にお知らせします！"
            ))
        except Exception as e:
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text=f"登録エラー: {str(e)[:100]}"))
        return

    # プリントモード開始（写真を待つ）
    print_trigger_keywords = ['プリント', 'プリントきた', 'プリント撮る', '学校のプリント', 'おたより', 'お知らせ来た']
    if any(kw in user_message for kw in print_trigger_keywords):
        print_sessions = load_print_sessions()
        print_sessions[user_id] = 'waiting_for_print_image'
        save_print_sessions(print_sessions)
        line_bot_api.reply_message(event.reply_token, TextSendMessage(
            text="📄 プリントの写真を送ってください！\n\n撮影のコツ：\n・平らに置いて撮る\n・文字がはっきり見えるように\n・プリント全体が入るように"
        ))
        return

    # SEOレポート即時取得
    seo_keywords = ['SEOレポート', 'seoレポート', '流入確認', '流入みせて', 'ブログ流入', '検索流入']
    if any(kw in user_message for kw in seo_keywords):
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text="📊 Search Consoleを確認中です...少しお待ちください！"))
        threading.Thread(target=send_weekly_seo_report).start()
        return

    # Xレポート即時取得
    x_report_keywords = ['Xレポート', 'xレポート', 'X分析', 'x分析', 'ツイート分析', '投稿分析']
    if any(kw in user_message for kw in x_report_keywords):
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text="📊 X投稿のパフォーマンスを集計中です...少しお待ちください！"))
        threading.Thread(target=send_x_weekly_report).start()
        return

    # eBayリサーチ
    ebay_research_keywords = ['eBayリサーチ', 'ebayリサーチ', 'eBay リサーチ', 'eBayリサーチして', '物販リサーチ', 'リサーチして']
    msg_lower = user_message.lower()
    is_ebay_research = any(kw in user_message for kw in ebay_research_keywords)
    # 「ebay:〇〇」「eBay：〇〇」「ebay 〇〇」形式も検出
    if not is_ebay_research and msg_lower.startswith('ebay'):
        rest = user_message[4:].lstrip('： :　 ')
        if rest:
            is_ebay_research = True
    if is_ebay_research:
        # 「eBayリサーチ：〇〇」「ebay:〇〇」「ebay 〇〇」形式で条件指定があれば抽出
        user_query = None
        for sep in ['：', ':']:
            if sep in user_message:
                parts = user_message.split(sep, 1)
                if len(parts) == 2 and parts[1].strip():
                    user_query = parts[1].strip()
                    break
        # 「ebay 〇〇」形式（コロンなし・スペース区切り）
        if not user_query and msg_lower.startswith('ebay'):
            rest = user_message[4:].lstrip('　 ')
            if rest and not any(kw in rest for kw in ['リサーチ', 'research']):
                user_query = rest.strip()
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text="📦 eBayリサーチを開始します！\n結果が届くまで2〜3分お待ちください🔍"))
        threading.Thread(target=run_ebay_research, args=(user_id, user_query)).start()
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
                save_yakuzen_sessions(yakuzen_sessions)
                line_bot_api.reply_message(event.reply_token, TextSendMessage(
                    text="✍️ 今の季節・人気ワードからテーマを自動選定して記事を作成します！\n少しお待ちください（1〜2分かかります）"
                ))
                threading.Thread(target=process_yakuzen_new_article, args=(user_id,)).start()
            elif any(kw in normalized for kw in ['リライト', '更新', '既存', '2', '②']):
                yakuzen_sessions[user_id] = {'state': 'waiting_for_rewrite_target'}
                save_yakuzen_sessions(yakuzen_sessions)
                line_bot_api.reply_message(event.reply_token, TextSendMessage(
                    text="🌿 リライトします！\n\n・URLを貼り付ける\n・キーワードを入力（例：アーユルヴェーダ、花粉症）\n・「自動」で季節に合った記事を自動選択"
                ))
            elif any(kw in normalized for kw in ['テーマ', '指定', '自分', '3', '③']):
                yakuzen_sessions[user_id] = {'state': 'waiting_for_new_topic'}
                save_yakuzen_sessions(yakuzen_sessions)
                line_bot_api.reply_message(event.reply_token, TextSendMessage(
                    text="✍️ テーマを入力してください！\n例：「更年期のほてりに薬膳」「子どもの風邪予防レシピ」"
                ))
            else:
                line_bot_api.reply_message(event.reply_token, TextSendMessage(
                    text="「1」か「新規作成」、または「2」か「リライト」と送ってください！\n（テーマを自分で決めたい場合は「3」）"
                ))
            return

        elif state == 'waiting_for_rewrite_target':
            del yakuzen_sessions[user_id]
            save_yakuzen_sessions(yakuzen_sessions)
            import unicodedata
            normalized = unicodedata.normalize('NFKC', user_message)
            if '自動' in normalized or normalized.strip() in ['auto', '']:
                line_bot_api.reply_message(event.reply_token, TextSendMessage(
                    text="🌿 季節に合った記事を自動選択してリライトします！\n数分かかります..."
                ))
                threading.Thread(target=auto_rewrite_yakuzen, args=(user_id,)).start()
            elif 'foodmakehealth.com' in user_message:
                slug = user_message.strip().rstrip('/').split('/')[-1]
                line_bot_api.reply_message(event.reply_token, TextSendMessage(
                    text=f"✍️ 「{slug}」の記事をリライト中です...少しお待ちください！"
                ))
                threading.Thread(target=rewrite_yakuzen_by_slug, args=(user_id, slug)).start()
            else:
                keyword = user_message.strip()
                line_bot_api.reply_message(event.reply_token, TextSendMessage(
                    text=f"🔍 「{keyword}」で記事を検索してリライトします...少しお待ちください！"
                ))
                threading.Thread(target=rewrite_yakuzen_by_keyword, args=(user_id, keyword)).start()
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
        msg = "🌿 薬膳ブログ、何をしますか？\n\n1️⃣ 新規作成（季節・人気ワードからテーマ自動決定）\n2️⃣ リライト（既存記事を更新）\n3️⃣ テーマ指定で新規作成\n\n番号か言葉で教えてください！"
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
        service = get_calendar_service()
        now = datetime.datetime.now(JST)
        weekdays = ['月', '火', '水', '木', '金', '土', '日']

        today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        today_end = now.replace(hour=23, minute=59, second=59, microsecond=0)
        tomorrow_start = today_start + datetime.timedelta(days=1)
        tomorrow_end = today_end + datetime.timedelta(days=1)

        calendars = service.calendarList().list().execute().get('items', [])

        def fetch_day_events(start, end):
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

        def format_day(events):
            if not events:
                return "予定なし 🌸\n"
            lines = ""
            for event in events:
                start = event['start'].get('dateTime', event['start'].get('date'))
                if 'T' in start:
                    dt = datetime.datetime.fromisoformat(start).astimezone(JST)
                    time_str = dt.strftime('%H:%M')
                else:
                    time_str = "終日"
                title = event.get('summary', '（タイトルなし）')
                lines += f"⏰ {time_str}  {title}\n"
            return lines

        today_events = fetch_day_events(today_start, today_end)
        tomorrow_events = fetch_day_events(tomorrow_start, tomorrow_end)

        today_str = f"{now.strftime('%m/%d')}({weekdays[now.weekday()]})"
        tomorrow_str = f"{(now + datetime.timedelta(days=1)).strftime('%m/%d')}({weekdays[(now.weekday() + 1) % 7]})"

        msg = f"🌅 おはようございます！\n\n"
        msg += f"📅 今日 {today_str}\n"
        msg += format_day(today_events)
        msg += f"\n📅 明日 {tomorrow_str}\n"
        msg += format_day(tomorrow_events)
        msg += "\n今日も素敵な1日を！✨"

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


def send_ebay_reset_reminder():
    try:
        user_id = os.environ['LINE_USER_ID']
        message = (
            "🎉 eBay月次リセットday！\n\n"
            "今日から出品枠がリフレッシュされました！\n"
            "✅ 無料出品250品 → リセット\n"
            "✅ 出品総額$7,000 → リセット\n\n"
            "たくさん出品するチャンスです！\n"
            "Claude Codeに「出品サポートして」と声かけてね📦\n\n"
            "また5月26日頃にはリミットアップ申請も可能になります！"
        )
        line_bot_api.push_message(user_id, TextSendMessage(text=message))
    except Exception as e:
        print(f"send_ebay_reset_reminder error: {e}")


def send_hsbc_reminder():
    try:
        user_id = os.environ['LINE_USER_ID']
        now = datetime.datetime.now(JST)
        month = now.month

        # USD/JPY レート取得（無料API）
        rate_text = ""
        try:
            res = requests.get("https://api.exchangerate-api.com/v4/latest/USD", timeout=5)
            data = res.json()
            usd_jpy = data["rates"]["JPY"]
            hkd_usd = data["rates"]["HKD"]
            hkd_jpy = usd_jpy / hkd_usd
            rate_text = f"\n📊 今日のレート\nUSD/JPY: {usd_jpy:.1f}円\nHKD/JPY: {hkd_jpy:.2f}円"
        except Exception:
            rate_text = "\n（レート取得失敗）"

        msg = (
            f"🏦 【HSBC {month}月の換金リマインダー】\n"
            f"今月のHKD↔USD換金を忘れずに！\n"
            f"少額でOK。口座維持のための換金です。{rate_text}\n\n"
            f"HSBCアプリ → 外貨両替 → HKD→USD（または逆）"
        )
        line_bot_api.push_message(user_id, TextSendMessage(text=msg))
    except Exception as e:
        print(f"HSBC reminder error: {e}")


def send_zaitage_reminder():
    try:
        user_id = os.environ['LINE_USER_ID']
        msg = (
            "🏠【在宅専門医 週次リマインダー】\n\n"
            "今週も少しだけ進めよう！\n\n"
            "📋 今すぐできること\n"
            "・他施設研修 2か所目の問い合わせ\n"
            "・ポートフォリオのテーマを1つ決める\n"
            "・症例を1〜2例メモする\n\n"
            "⚠️ 2026年10〜11月の受験登録を忘れずに！\n"
            "実践者コースは2027年が実質ラストチャンス。\n\n"
            "Notionで進捗確認👇\n"
            "https://www.notion.so/344f8d6d41de818db44fc33af7cf39e5"
        )
        line_bot_api.push_message(user_id, TextSendMessage(text=msg))
    except Exception as e:
        print(f"Zaitage reminder error: {e}")


def send_x_engage_reminder():
    try:
        user_id = os.environ['LINE_USER_ID']
        msg = (
            "𝕏【X エンゲージメントタイム】\n\n"
            "今日の10分アクション👇\n\n"
            "① 検索：「AI副業」「ワーママ 副業」「ClaudeCode」\n"
            "② フォロワー300〜5000人の投稿に共感リプを3件\n"
            "③ 自分にリプが来てたら返信する\n\n"
            "リポストは不要。リプライだけでOK✨"
        )
        line_bot_api.push_message(user_id, TextSendMessage(text=msg))
    except Exception as e:
        print(f"X engage reminder error: {e}")


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


# ========== X（Twitter）自動投稿 ==========

# 実績ベースのツイートストック（gitコミット履歴から生成）
TWEET_STOCK = [
    # ── チラシ読み取り・カレンダー系 ──
    "1ヶ月前：習い事チラシを見ながら日時・場所を一つずつ手入力してた。今：LINEに写真を送るだけ。AIが読み取ってGoogleカレンダーに自動登録してくれる。毎回10分かかってた作業が0秒になった。ワーママの地味なストレスってこういうところにある。 #AI副業 #ワーママ #LINE",
    "【朗報】「また締切過ぎてた…」が二度と起きなくなった。LINEに「〇〇の期限 5月10日」と送るだけで1週間前・3日前・前日・当日に自動でリマインドが届く。人間の記憶力に頼るのをやめたら、忘れることが物理的にできなくなった。 #AI副業 #自動化 #LINE",
    "チラシに3つイベントが書いてあっても最初の1件しか登録されなかった。1週間悩んで原因を特定→複数イベントを全部抽出する処理に改良した。毎日使うものだから、小さい改善でも毎日0.1%ずつ生活が良くなる感覚がある。 #AI副業 #個人開発 #LINE",
    "【修正済】「登録したのに消えた」問題の原因はサーバー再起動でデータが消えることだった。状態をメモリじゃなくファイルに保存する方式に変えて完全解決。本番運用って、こういう見えないところの作り込みで信頼が生まれると知った。 #AI副業 #個人開発 #Claude",
    "朝の支度中に「今日何がある？」と考えなくて済むようになった。毎朝7時に今日・明日の予定がLINEに届く。スケジュールを確認しに行く手間がゼロ。自分で作ったリマインダーが、今では毎日一番助かってる機能になった。 #AI副業 #LINE #ワーママ",
    # ── 自動化全体・積み上げ系 ──
    "1ヶ月で20以上の自動化が同時に動いてた。医療職×育児で作業時間は1日1〜2時間。それでも毎日少しずつ積み上げたら：チラシ読み取り・ブログ自動投稿・eBayリサーチ・X自動投稿・プリント管理、全部LINEから。全記録はnoteに→ https://note.com/maki_claude_lab/n/n0bf26963cd26 #AI副業 #ClaudeCode",
    "【驚愕】ブログ記事の公開が「書いて」の一言で完結するようになった。テーマ決め→記事作成→アイキャッチ画像選定→WP公開、の全工程を自動化。LINEから指示して10分後に記事が公開されてる。ブログ更新の心理的ハードルが消えた。 #AI副業 #ブログ自動化 #Claude",
    "以前：どの記事をリライトするか選ぶのすら面倒だった。今：LINEに「リライト」と送るだけで、AIが季節に合った記事を選んで・リライトして・公開まで全自動。「何もしなくていい」が副業の最終形だと気づいた。 #AI副業 #ブログ #自動化",
    "以前：帰宅してからPCでeBayリサーチしてた。今：LINEに商品名を送るだけで通勤中にリサーチが終わる。家のPCに縛られた副業から、いつでもどこでも動ける副業に変わった。物理的な制約がなくなると、副業の質が変わる。 #AI副業 #eBay #物販",
    "【重要】「審査中だから何もできない」は嘘だった。Pinterest Trial accessが拒否されてまだ使えない状態。でも待ってる間にOAuth認証の実装は全部完了させた。解禁されたその日から自動投稿が始まる。「動ける準備を整えながら待つ」が正解。 #AI副業 #自動化 #Claude",
    "「今日何投稿しよう」と悩む時間がゼロになった。実体験をストック化→日付ベースで自動選択→朝・昼・夜に自動投稿。副業のSNS運用が完全に「仕組み」になった。意志力に頼らない設計が、続けられる仕組みを作る。 #AI副業 #X #自動化",
    "「あのプリントどこに置いた？」が毎回プチストレスだった。今：LINEに写真を送るだけでAIが内容を読み取って保管してくれる。プリント管理は「仕組みに任せる」でいい。ワーママの小さいストレスを1個ずつ消していくのが副業の醍醐味。 #AI副業 #ワーママ #LINE",
    "【公開】プログラミングゼロからLINE秘書ボットを作るまでの全工程をnoteにまとめた。何から始めたか・どうClaudeに頼んだか・失敗してどう直したか、全部書いた。980円。「自分もできるかも」と思ってもらえたら嬉しい→ https://note.com/maki_claude_lab/n/n0bf26963cd26 #AI副業 #note #Claude",
    "【気づき】忘れっぽい人が続けるコツは「意志力を使わない設計」だった。確認作業を毎週月曜にLINEへ自動通知するようにしたら、通知が来るまで忘れっぱなしだったのが毎週必ず確認できるようになった。人間が覚えるより仕組みが覚える方が100倍確実。 #AI副業 #アフィリエイト #自動化",
    "【断言】習慣化に意志力はいらない。月初の振り返りを毎月1日にLINEに自動で届くようにしたら、忘れず振り返れるようになった。気合いで続けようとしていた頃より、仕組みにした今の方が圧倒的に続いてる。続けるのに必要なのは根性じゃなくて設計。 #AI副業 #習慣化 #自動化",
    "月0円でサーバーを24時間稼働させる方法：①Renderのフリープランを使う②スリープ防止のpingエンドポイントを追加③GitHubにpushするだけで自動デプロイ。これだけ。コストゼロで副業のインフラを作れる時代。 #AI副業 #個人開発 #Render",
    "以前：エラーが出ると手が止まって「詰んだ」と思ってた。今：エラーログをClaudeにそのまま貼るだけで「これが原因、こう直す」と返ってくる。デバッグが怖いスキルから「作業手順」に変わった。エラーを恐れなくなったら開発速度が3倍になった。 #AI副業 #個人開発 #Claude",
    "【マニアック】深夜0時をまたぐイベントの終了日時が翌日にならないバグを修正した。誰も気づかないような細かい仕様。でも自分が毎日使うからこそ「なんか変」が気になる。細部を磨くほど「自分のために作った」感が強くなって、使うたびに気持ちいい。 #AI副業 #個人開発 #自動化",
    "【断言】プログラミングは不要だった。コードは一行も書けない。でも「どうなってほしいか」を言葉で伝えることなら誰でもできる。それだけで動くものが出来上がる。「技術がないから無理」は思い込みだったと知った。全記録→ https://note.com/maki_claude_lab/n/n0bf26963cd26 #AI副業 #ClaudeCode",
    "以前：コードを変更するたびに手動でサーバーにアップロードしてた。今：GitHubにpushするだけで2〜3分後にサーバーに反映される。月0円。個人開発のハードルが下がりまくってる。お金も技術も昔ほど必要じゃない。 #AI副業 #個人開発 #Render",
    "【逆転】時間がないから自動化にハマった。医療職×育児で1日1〜2時間しかない。だから「毎日繰り返す作業」「忘れやすいこと」「ミスが起きやすいこと」から全部自動化する。時間がないことが、最高の自動化設計の先生になった。 #AI副業 #医療職 #副業",
    "以前：薬膳記事を書くたびにCTAを手動で貼ってた。たまに忘れて収益機会を逃してた。今：記事公開時に自動挿入するように設計を変えた。「貼り忘れ」という概念がなくなった。ブログ収益って、こういう地味な積み上げで変わる。 #AI副業 #ブログ #アフィリエイト",
    "以前：売上・記事数・出品数を把握するのに複数サイトを確認してた。今：LINEに「会社」と打つだけで全部が1画面で届く。移動中でも状況を瞬時に把握できる。副業を「見える化」したら、何を優先すべきかが自然とわかるようになった。 #AI副業 #ClaudeCode #自動化",
    "チラシから複数イベントを抽出するとき、たまに1件だけ取れて残りが消える問題があった。パース処理を丸ごと作り直して安定した。見えないバグほど原因特定に時間がかかる。でも解決したとき、そのシステムへの信頼が一気に上がる。 #AI副業 #個人開発 #Claude",
    "【体感】読みやすいコードは開発速度を3倍にする。main.pyが1ファイル3000行になってたので機能ごとに分割した。同じ処理が一瞬でできるようになって「整理は未来への投資」を体感した。コードの質は、作る速さに直結する。 #AI副業 #個人開発 #ClaudeCode",
    "LINEボット1本でできること：ブログ投稿（薬膳・セキスイ）・カレンダー管理・eBayリサーチ・プリント管理・X自動投稿。全部LINEから操作できる。専用アプリを使い分ける必要がなくなった。副業がほぼスマホだけで完結してる。 #AI副業 #LINE #自動化",
    "【設計が全て】AIに「役割」を与えたら使い方が変わった。以前は「これはどのツールで処理する？」と毎回迷ってた。「秘書部に話しかけるだけ」という役割設計にしたら、迷いがゼロになった。AIは「どう使うか」より「どんな役割を与えるか」が大事。 #AI副業 #ClaudeCode #自動化",
    "子どもが寝た後の1〜2時間でできたこと↓ 1ヶ月目：LINEボット 2ヶ月目：ブログ自動化 3ヶ月目：eBay管理ツール 4ヶ月目：ゲームシリーズ開始。コードゼロでも「毎日少し」が積み重なる。全工程→ https://note.com/maki_claude_lab/n/n0bf26963cd26 #AI副業 #ワーママ",
    "【逆説】自動化は「記事が増えるほど楽になる」。薬膳120記事・セキスイ34記事。手動管理なら更新するたびに手間が増える。自動化してあると記事が増えるほど「何もしなくていい分」が増える。仕組みを作る労力は一回、恩恵は永続する。 #AI副業 #ブログ #アフィリエイト",
    "1ヶ月前：「OAuth？認証フロー？意味不明」だった。今：LINEボットに組み込んで毎日使ってる。難しいことも「使い続けると慣れる」は本当だった。逃げずに向き合い続けたら、ある日突然わかる瞬間が来る。 #AI副業 #個人開発 #Google",
    "以前：エラーが出た瞬間「詰んだ」と思ってた。今：Claudeにエラーログを貼るだけで大体解決する。「エラー→貼る→直る」を何十回も繰り返したら、エラーが来ても「どうせ直せる」と思えるようになった。全記録→ https://note.com/maki_claude_lab/n/n0bf26963cd26 #AI副業 #ClaudeCode #初心者",
    "まず手動でやって、繰り返しが生まれたら自動化する。この順番が正解だった。最初から自動化しようとすると「何を自動化するか」がわからない。メルカリ→eBayの物販も、手でフローを覚えてからAPIで繋ぐ予定。 #AI副業 #eBay #物販",
    "毎月6日の更新締切を「また過ぎてた」と繰り返してた。3日前と当日にLINEリマインダーを追加したら完璧に対応できるようになった。忘れるのは意識の問題じゃなくて仕組みの問題。その事実に気づいたら、全部仕組みで解決できるようになった。 #AI副業 #ワーママ #自動化",
    "【UX改善】「登録して」と送っても無言で失敗してたのを「まず画像を送ってください」と案内するように直した。たったこれだけで使い心地が変わった。毎日使うからこそ細部の体験にこだわる。自分のためのツールを磨くのが個人開発の醍醐味。 #AI副業 #個人開発 #LINE",
    "【理由】私がChatGPTじゃなくClaude Codeを使う理由。ChatGPTは会話して→自分でコードを貼る。Claude Codeはファイルを直接読み書きして・コードを実行しながら作ってくれる。「AIに相談した→自分でコードに反映」という手間がない。この差は体験してみないとわからない。 #AI副業 #ClaudeCode #Claude",
    "1ヶ月弱でGitコミット数が50を超えた。「今日は30分しかない」という日でも小さく前進した。30分×30日＝15時間。たったこれだけで動くものが増えていく。続けることの価値は、続けた人にしかわからない。 #AI副業 #ClaudeCode #副業",
    "LINE秘書ボットを作って一番変わったこと。以前：毎朝「今日何の予定あったっけ」とスケジュールを確認しに行ってた。今：毎朝7時に予定が届くから確認しに行く必要すらない。予定を忘れてた自分が、仕組みの中で生活するようになった。 #AI副業 #LINE #ワーママ",
    "以前：記事を書くたびに画像を探して・選んで・設定する時間がかかってた。今：Pexels APIと連携させて、テーマに合った画像を自動で取得・設定してくれる。記事を書いたら全部終わってる状態になった。自動化できると「こんな細かいこともできるのか」と毎回驚く。 #AI副業 #ブログ自動化 #Claude",
    "1ヶ月前：「.envに何を書くか」すら理解できなかった。今：10個以上の環境変数を全部把握してRenderの管理画面でAPIキーを追加・更新できる。理解は突然やってくる。わからなくてもやり続けると、ある日わかる。逃げないのが唯一のルール。 #AI副業 #個人開発 #Render",
    "副業の週次スケジュールを全部自動化した↓ 月曜：審査確認通知 火曜：薬膳ブログリマインド 木曜：セキスイブログリマインド 土曜：eBayチェック通知。「今週何するんだっけ」と考えなくていい。週の行動が仕組みで決まると、迷いなく動ける。 #AI副業 #副業 #習慣化",
    # ── まるちゃんワールド（ゲーム系） ──
    "【衝撃】プログラミングゼロでゲームが作れた。「息子と遊べるゲームがほしい」と思ってClaudeに伝えただけ。コードは一行も書いてない。1日後にタイピングゲームが完成してた。「技術がないから作れない」という思い込みが完全に壊れた瞬間だった。 #AI副業 #ClaudeCode #ゲーム開発",
    "3ヶ月前：ゲームを作れるのはプログラマーだと思ってた。今：コードゼロでタイピング・アクション・音ゲーの3作品を作った。「まるちゃんワールド」というシリーズ名もある。医療職ワーママがゲームシリーズを持つ日が来るとは思ってなかった。 #AI副業 #ClaudeCode",
    "ゲームの公開コスト：サーバー代0円・ドメイン代0円・開発費0円。GitHub Pagesに置いてURLを共有するだけで世界中からアクセスできる。「公開」のハードルが下がった時代に、作れる技術を持つことの価値が上がってる。 #AI副業 #個人開発 #ClaudeCode",
    "【本末転倒】息子のために作ったタイピングゲームに自分がハマった。かんたん・ふつう・むずかしい・1文字モードの4段階。BGMも効果音もAIが生成した。作ることが目的だったのに、遊ぶことが楽しくなった。副産物まで楽しいのが個人開発の醍醐味。 #AI副業 #ワーママ",
    "タイピング・アクションの次に音ゲーを作った。F・G・H・Jキーでノーツをたたく3ステージ構成。Claudeに「もっと難しくして」と言うたびに本当に難しくなった。コードをゼロ行も書かずに音ゲーが完成する。この時代のすごさを毎回感じてる。 #AI副業 #ClaudeCode #ゲーム開発",
    "【正式立ち上げ】AI会社に「ゲーム部」ができた。タイピング・アクション・音ゲーの3作品が完成したので部署化した。プログラミングゼロの医療職ワーママが、AI会社の組織図にゲーム部を追加する日が来るとは。Claudeと作ると「できること」の定義が変わる。 #AI副業",
    "【本末転倒2】自分で作った音ゲーを自分でクリアできない。難しいモードは90秒ずっと集中が必要。「もっと速くして」ってClaudeに頼んだのは私なのに。作る側にいるのに「難しすぎてクリアできない」状況になってる。これが個人開発の楽しさだと思う。 #ClaudeCode #ゲーム開発",
    # ── eBay API連携 ──
    "以前：eBayのマイページをスクロールしながら出品を確認してた。今：LINEから全出品を一覧で見られる。プログラミングゼロでもAPIって繋げるんだと初めて実感した。技術の壁は思ってたより低かった。 #AI副業 #eBay #物販",
    "以前：売上・ウォッチャー数・出品総額を毎回手動で確認してた。今：LINEで一瞬でわかる。同じ作業を何度もやってると気づいたら、それは自動化のサイン。繰り返しに気づく能力が上がると、副業の質が変わる。 #AI副業 #eBay #自動化",
    "【知らなかった】eBayには月間$7,000の出品上限がある。出品しようとして弾かれて初めて気づいた。英語の説明が意味不明だったのをClaudeが解読してくれた。知らないルールでつまずく→AIが一緒に解決する、この体験が積み重なって知識になる。 #AI副業 #eBay #物販",
    "英語が苦手でもeBayで全然問題なかった。サポートチャットへの問い合わせ文も全部Claudeに作ってもらった。「こういう問題が起きてます」と日本語で伝えるだけで適切な英文になる。英語の壁はAIで完全に消えた。 #AI副業 #eBay #物販",
    "【時短】eBay出品タイトルを51件APIで一括変更した。手作業なら半日かかる量が数分で完了した。「Kumano Fude」「Japan Unused New」などの検索キーワードを全タイトルに追加。仕組みを一度作れば次回も同じコスト。タイトル改善が加速した。 #AI副業 #eBay #自動化",
    "以前：出品タイトルを1件ずつ手動で編集してた。今：APIで全品を一括処理→数分で完了。プログラミングゼロでも仕組みを作れば、毎回ラクになる。一度の仕組み作りが、何十時間もの作業を消してくれる。 #AI副業 #eBay #物販",
    # ── Instagram・Zapier系 ──
    "以前：ブログ更新→Instagramにも投稿→2つの作業が必要だった。今：Zapier＋WP Webhooksで記事公開と同時にInstagram投稿まで全自動。しかも画像にタイトル文字を自動合成してる。やることリストから「インスタ投稿」が消えた。 #AI副業 #自動化 #ブログ",
    "【無料でできた】WordPress新記事公開→Instagram自動投稿の設定に30分かかった。Zapierを使えばコードなし・30分で2つのサービスが繋がる。「プログラミングが必要」だと思ってたことが、ツールを組み合わせるだけでできた。 #AI副業 #Zapier #自動化",
    "以前：Instagram用の画像を手動でリサイズして・タイトルを重ねて・保存してた。今：1:1リサイズ→タイトルオーバーレイ→保存まで全部Pythonで自動処理。一回自動化したら二度と手作業に戻れない。 #AI副業 #Python #自動化",
    # ── 音声文字起こし ──
    "【コスト0円】ZOOMの録画が全自動で議事録になった。Groqという無料APIを使えばコスト0円。350MBのMP4でも自動で分割して全部処理してくれる。「会議の録音、どうせ聞き返さない」が解消された。議事録を作るのはもう人間の仕事じゃない。 #AI副業 #自動化 #ClaudeCode",
    "以前：メモを取るのにスマホを開いてキーボードを叩いてた。今：LINEに音声を送るだけでテキストが返ってくる。話すだけでメモが残る。LINEボットに音声入力を追加したら、使い方が想像以上に広がった。 #AI副業 #LINE #自動化",
    "【革命】会議の録音を聞き返す必要がなくなった。ダブルクリックでアプリ起動→ファイル選択→待つだけ。Groq Whisperが文字起こし→AIが要約→議事録がデスクトップに保存される。何時間の録音でも「待つだけ」になった。 #AI副業 #自動化 #時短",
    "文字起こし処理がネット切断で13/18チャンクで止まった。以前なら最初からやり直し。今：前回の続きから再開する機能をその場でClaudeに追加してもらって、14チャンク目から自動スタートできた。トラブルがあっても続けられる仕組みがあれば怖くない。 #AI副業 #ClaudeCode #自動化",
    "176分の会議録音が議事録になるまで：①18分割に自動分割②Groq Whisperで文字起こし③チャプターごと要約④デスクトップに保存。コスト：0円。作業：ファイルを選んで待つだけ。長い会議ほどAIに任せた方がいい。 #AI副業 #自動化 #時短",
    "【失敗談】セキスイブログの記事がInstagramに自動投稿されてたら、住宅写真じゃなくてビルの写真が出てた😂 日本語タイトルをそのままPexels検索に使ってたのが原因。英語キーワードに変換するよう修正したら住宅写真になった。自動化は作って終わりじゃなく改善の繰り返し。 #AI副業 #自動化 #ClaudeCode",
    # ── 薬膳Instagram・SNS自動化 ──
    "以前：薬膳記事ごとに食材写真を自分で選んでた。今：日本語タイトルをAIが英語に変換→Pexelsで完成料理写真を取得→WPに自動設定。「食べたい！」と思う写真になって、クリック率が変わってきた。自動化で品質まで上がった。 #AI副業 #ブログ自動化 #薬膳",
    "【一石三鳥】薬膳記事を書いたら3つの成果物が同時にできる。①ブログ記事②Instagram投稿③料理写真。記事を書くたびにInstagram素材まで揃う。1つの作業から複数の成果物が出るのが、自動化の本当の価値だと思う。 #AI副業 #Instagram #自動化",
    "【開設】薬膳レシピのInstagramアカウントを開設した。コンセプトは「医療職ワーママが実践する薬膳」。医療職という肩書きを前面に出したら、コンセプトが一気に明確になった。「何を発信するか」より「誰が発信するか」の方が刺さると気づいた。 #AI副業 #薬膳 #Instagram",
    "以前：薬膳記事の内容に合ったアフィリエイトリンクを毎回手動で探して貼ってた。今：記事内容を自動判定→子ども向けなら「こども薬膳」・スープ系なら「スープジャー弁当」と内容に合ったリンクを自動挿入。収益機会を逃すのは仕組みの問題だった。 #AI副業 #アフィリエイト #自動化",
    "「この記事だけをリライトしたい」というニーズに対応した。記事URLをLINEに貼るだけで、その記事を選択してリライト・公開まで完結する。小さい要望に答えるほど、自分にとって使いやすくなる。自分のツールを育てる感覚が楽しい。 #AI副業 #ブログ自動化 #LINE",
    "【自動化最高】「何を書くか決める」という一番めんどくさい工程をAIに任せた。4月なら「花粉症・PMS・春の倦怠感」、7月なら「夏バテ・熱中症」と季節×症状で自動決定。執筆開始まで一秒もかからなくなった。「考える」ことすら自動化できる。 #AI副業 #薬膳 #自動化",
    "カルーセル画像の2枚目・3枚目がずっと届かなかった。原因はフォントファイルのバイナリ破損だった。.gitattributesに設定を追加して解決。3時間かかった。地味なバグほど原因特定に時間がかかって、解決したときの安堵感がでかい。 #AI副業 #個人開発 #ClaudeCode",
    "【予想外】AIに任せたらPMSの記事ばかり書かれてた😂 18カテゴリあるのに1つに偏る問題。直近15記事と照合して未使用カテゴリを選ぶ仕組みにしたら均等になった。AIは「シンプルに任せる」と偏る。ちゃんとロジックを設計する必要がある。 #AI副業 #ブログ自動化 #薬膳",
    # ── eBayリサーチAI進化 ──
    "以前：帰宅してからPCでeBayリサーチしてた。今：「ebay 風呂敷」とLINEに送るだけで10個のキーワードを並列検索→結果が届く。通勤中・待ち時間に仕入れ候補を調べられる。物販の制約が「場所」から「時間」だけになった。 #AI副業 #eBay #物販",
    "【穴場の条件】競合が少ない×価格が高い×ウォッチャーが多い＝需要はあるのに出品者が少ない穴場商品。この判定ロジックをeBayリサーチに組み込んだ。勘で仕入れてたのが、数字で判断できるようになった。データで動ける副業は強い。 #AI副業 #eBay #物販",
    "eBay仕入れの判断3ステップ↓ ①AIリサーチで候補を出す②Sold Listingsで絞る③30日の売れ数を確認。AIを信頼しながらも、最後の判断は自分でする。この一手間が仕入れ精度を上げる。自動化と人間判断の最適な分担が大事。 #AI副業 #eBay #物販",
    "以前：1キーワードずつ順番に検索→全部終わるまで待ってた。今：全キーワードを同時に検索→同じ結果が数分の1の時間で届く。「待ち時間を減らす」のも自動化。速くなるだけで使いたい頻度が上がる。 #AI副業 #eBay #ClaudeCode",
    # ── Webマーケティング系 ──
    "以前：LP競合分析を手動でまとめてスプレッドシートに貼ってた。今：Sheets APIで全自動。データ取得→整形→シート書き込みまで全自動。「手作業→自動化」の達成感は何度やっても慣れない。なぜなら毎回「自分の時間が増えた」を実感するから。 #AI副業 #ClaudeCode #自動化",
    "【衝撃】「勉強するための作業」をAIに任せたら、勉強自体に集中できるようになった。LP競合分析→Claudeが3社分のデータ取得→整形→スプレッドシート書き込みまで全部やってくれた。インプットの質を上げるために、インプットの作業を自動化する時代。 #AI副業 #ClaudeCode #Webマーケティング",
    # ── インスタ×SNS自動化 ──
    "【設計】インスタを始めるとき「何を投稿するか」より「誰に届けるか」を先に決めた。顔出しなし・バズ狙いなし・全自動運用のアカウント設計。発信内容より「誰が発信するか」の方が大事だと思ってるから、設計に一番時間をかけた。 #AI副業 #インスタ #自動化",
    "【リール攻略】冒頭2秒が全て。「自分ごと」と感じてもらえるかどうかが勝負。王道は「〇〇で悩んでる人へ」の共感型から入ること。コンテンツの質より「誰が言うか」の方が大事だと、インスタを学んでから確信した。 #AI副業 #インスタ #リール",
    "【衝撃データ】ジャンルを絞らず2年・600投稿でフォロワー50人。絞ったら4ヶ月で5万人。プロから直接聞いた話。「やること」より「やらないこと」を決める方が大事だと思い知らされた。インスタだけじゃなく副業全般に言える教訓。 #AI副業 #インスタ",
    "審査を待ちながら他の部分を全部作り終えた。Meta APIの審査待ちでまだ使えない。でも解禁直後に動ける準備は全部整えた。「動けないときに準備する」が自動化の鉄則。解禁されたその日から全自動投稿が始まる。 #AI副業 #自動化 #ClaudeCode",
    "【逆説】インスタで収益化するには「売らない」が正解だった。価値を提供しまくって、欲しい人だけに案内する。これってLINEボットの設計と全く同じ。プラットフォームが変わっても「先に与える」という本質は変わらない。 #AI副業 #インスタ #マーケティング",
    "SNSは目的で役割を分けるべきだった。X→AI副業の認知、インスタ→薬膳ブログの流入、ブログ→アフィリエイト収益。ごちゃまぜにしたら全部弱くなる。同じSNS運用でも目的が違うと戦略が全然違う。整理したら「何をやるべきか」が迷わなくなった。 #AI副業 #SNS #自動化",
    "インスタ開設したら即「収益化できません」と言われた。手動で真面目に投稿してるのに。原因はアカウントが新しすぎること。「確立されたプレゼンス」というポリシーで新規は全員引っかかる。焦らず育てる一択だと気づいた。 #AI副業 #インスタ",
    "冒頭2秒で自分ごとにする、が全プラットフォーム共通の鉄則だった。インスタ講座で学んで、Xにもそのまま使えると気づいた。最強は共感型：「〇〇で悩んでる人へ」。人の心理はプラットフォームが変わっても変わらない。 #AI副業 #コピーライティング",
    # ── LINEリッチメニュー・物販フロー ──
    "以前：LINEボットを使うたびにキーワードを思い出す必要があった。今：画面下部のボタン6つから一発アクセスできる。使いやすくすると使う頻度が上がる。ツールは「使われてなんぼ」。使いやすさへの投資は、使用頻度という形で返ってくる。 #AI副業 #eBay #物販",
    "仕入れ判断の3ステップをLINEのボタン化した↓ ①Terapeak：売れ筋確認②メルカリ：仕入れ価格確認③利益計算ツール：採算確認。3ステップ全部LINEのボタン1つで飛べる。迷わず動けるフローができると、リサーチのスピードが上がる。 #AI副業 #eBay #物販",
    # ── コピーライティング・note ──
    "【重要】AIで書いても読まれないのはツールの問題じゃない。コピーの問題。「伝えたいことを書く」と「読まれる文章を書く」は全然違う。PASONAの型を知ってから、同じ内容でも反応が変わった。学んだことをnoteにまとめた（無料）→ https://note.com/maki_claude_lab/n/n8f70a6d95f32 #AI副業 #副業ワーママ #コピーライティング",
    # ── 秘書ボット改善 ──
    "毎朝の通知で「これ今日？明日？」と1秒迷ってた。今日・明日でセクション分けして・予定なしの日も明示するように改良した。毎日使うものだからこそその1秒がストレス。細かいUXを磨くほど「自分のために作ってる」感が増す。 #AI副業 #LINE #自動化",
    "【落とし穴】ずっと動いてたと思ってた自動投稿が、最初から動いてなかった。コードを見たらスケジューラーへの登録が1行抜けてただけ。こういう小さいミスを潰すたびに仕組みへの理解が深まる。デバッグは怖いスキルじゃない。 #AI副業 #個人開発 #ClaudeCode",
    "「登録中にエラー」が出た。原因：環境変数のJSONに制御文字が混入してた。解決：/check-credsで診断→クリーンJSON再生成→Renderに貼り直しの3ステップ。エラーの原因が見えると怖くない。診断エンドポイントを自分で作っておくと本当に助かる。 #AI副業 #個人開発 #ClaudeCode",
    # ── 日常・人間味系 ──
    "Claude Codeが面白すぎて気づいたら夜中の2時になってた。翌朝息子に「目が死んでる」と言われた。医療職のはずが不健康。 #ClaudeCode #AI副業",
    "「Claude Codeで毎日何してるの？」と聞かれると毎回うまく答えられない。「えっと…ボット作ったりゲーム作ったりブログ書いたり…」「…なんかすごそうだね」「そうなんですよ」。会話が終わる。 #ClaudeCode",
    "ママ友にClaude Code勧めすぎてひかれた。「無料でできるから！」「コードなしで作れるから！」「本当にすごいから！」3回連続で布教したらLINEの返信が遅くなった。でも諦めない。 #ClaudeCode",
    "ママ友の旦那さんもClaudeCodeでゲーム作ってると聞いて、見せ合いっこした。向こうはRPG、こちらはタイピング・アクション・音ゲーの3作品。「プログラミングできるんですか？」「ゼロです」「えっ」お互いに驚いた。 #ClaudeCode",
    "「やってみたいけど、プログラミングできないし」でずっと止まってたことがある。ゲーム作り、自動化ツール、Webサービス。Claude Codeを使い始めたら全部できた。「無理」の9割は思い込みだった。 #ClaudeCode #AI副業",
    "Claude Codeが楽しすぎて寝る時間が足りない。育児と医療職で毎日くたくたのはずなのに、夜になると触りたくなる。疲れてるのに眠れない理由が「副業のため」じゃなくて「楽しいから」って、自分でも不思議。 #ClaudeCode #AI副業",
    "Claude Codeを触らない日がなくなった。「副業のため」というより「楽しいから」が正直なところ。仕事終わり・子どもが寝た後・隙あらば触ってる。これって副業じゃなくて趣味では？ #ClaudeCode #AI副業",
    "「やってみたかったけど躊躇してた」が全部できるようになった。Claude Codeのおかげで。躊躇の理由はだいたい「技術がないから」だった。技術がなくても作れると知ってから、躊躇する理由がなくなった。 #ClaudeCode #AI副業",
    "ママ友に「なんか最近楽しそうだね」と言われた。「Claude Codeで色々作ってるんだよね」と答えたら「何それ」と聞かれた。説明しようとしたら30分経ってた。布教が止まらない。 #ClaudeCode #AI副業",
    "子どもが「ママのパソコン、いつも同じ画面だね」と言った。Claude Codeの画面のことだ。毎日開いてるから確かに同じ画面。「何してるの？」「色々作ってる」「ふーん」。これが今の私の副業のリアル。 #ClaudeCode #AI副業",
    # ── eBayタイトル・リサーチ改善 ──
    "eBay57品のタイトルをAIで一括改善した。「Funny Erasers」→「Iwako Japanese Puzzle Eraser Japan Kawaii Novelty」みたいな感じで。ウォッチゼロが続いてた理由が分かった。検索に引っかかってなかっただけ。タイトルは商品の顔。 #AI副業 #eBay #物販",
    "利益計算ツールを「仕入れ→売値計算」から「Terapeak売値→仕入れ上限逆算」に作り直した。以前は仕入れ¥400→eBay推奨$22と出てたが相場は$9。ツールが甘かった。逆算にしたら「$9なら¥339以下で仕入れろ」と一瞬で分かるようになった。 #AI副業 #eBay #物販",
    "eBayリサーチの結果を毎回忘れて同じカテゴリを何度も調べてた。解決策：利益計算ツールに「リサーチメモ保存」機能を追加。今はボタン1つで調べた日・仕入れ上限・判定が残る。記憶に頼る作業は全部仕組み化するに限る。 #AI副業 #eBay #物販",
    "Teapeakで調べたらHakuhodo化粧筆はメルカリ仕入れでは利益が出ないと判明。逆にサンリオ靴下は¥400以下で仕入れれば利益が出る。リサーチ前と後で仕入れ判断が全然違う。感覚で動いてたのがデータで動けるようになった。 #AI副業 #eBay #物販",
]

# 引用RT用テンプレート（バズってる投稿に引用するとき使う）
QUOTE_TWEET_TEMPLATES = [
    # AI技術・ツール系への引用
    "私もやってます。違いはプログラミングゼロ×医療職ワーママという条件。それでも1ヶ月で20以上の自動化が動いた。「技術がないから無理」は思い込みだったと知った。 #AI副業 #ClaudeCode",
    "Claude Codeで同じことを試してみた。ChatGPTと違ってファイルを直接読み書きしてくれるから「相談→自分でコードを反映」という手間がない。この差は体験してみないとわからない。 #AI副業 #ClaudeCode",
    "コスト0円でここまでできる。RenderのフリープランとGitHubを組み合わせたら月0円でサーバーが24時間稼働してる。お金をかけなくても本番運用できる時代になってる。 #AI副業 #個人開発",
    "1日1〜2時間でも積み重なる。医療職×育児で自由時間が限られてる分、「繰り返す作業は全部自動化する」と決めてる。時間がないことが最高の自動化設計の先生になった。 #AI副業 #ワーママ",
    # note・収益化系への引用
    "私も980円のnoteを公開してます。まず体験があって初めて書けるものがある。LINEボット・ブログ自動化・eBayを全部作ってから初めて書けた記事だった。→ https://note.com/maki_claude_lab/n/n0bf26963cd26 #AI副業 #note",
    "「売らない」が正解だと思ってます。私のnoteも最初は全部の実体験を無料でXに公開してた。価値を先に渡すから、欲しい人が自然に集まる。プラットフォームが変わっても「先に与える」の本質は変わらない。 #AI副業 #note",
    "私の場合はプログラミングゼロからスタートした。技術がなくてもClaudeがあれば作れる。「何ができるか」より「何を作りたいか」が明確なら前に進める。全記録→ https://note.com/maki_claude_lab/n/n0bf26963cd26 #AI副業 #ClaudeCode",
    # X運用・SNS系への引用
    "X投稿の自動化、私もやってます。実体験をストックして→日付ベースで自動選択→朝・昼・夜に自動投稿。「今日何投稿しよう」と考える時間がゼロになった。SNS運用が完全に仕組みになった。 #AI副業 #X #自動化",
    "フォロワーより「毎日続けられる仕組み」を先に作るべきだと思ってます。私はまず自動投稿の仕組みを完成させてから、内容の改善に集中できるようになった。土台ができてから伸ばす戦略が効く。 #AI副業 #X",
    "私がXを続けられてる理由は全自動化したから。手動で毎日投稿してたら続かなかったと思う。仕組みがあると「副業がある日もない日も」投稿が止まらない。継続の仕組みを作ることが最初の一歩。 #AI副業 #X #自動化",
    # ワーママ副業系への引用
    "育児×仕事×副業、全部やってる人の本音。時間がないのは全員同じ。でも「繰り返し作業を自動化する」だけで1日に余白が生まれる。私は医療職×育児で1日1〜2時間しかない。それでも仕組みは作れる。 #ワーママ #AI副業",
    "「副業は時間がある人のもの」は嘘だった。子どもが寝た後の1〜2時間を4ヶ月積み上げたら、LINEボット・ブログ自動化・eBay管理・ゲームシリーズが全部揃った。時間がないからこそ自動化を選んだ。 #ワーママ #AI副業",
    "ストレスの正体は「毎日の小さな手作業」だった。チラシ入力・締切管理・ブログ更新・eBayチェック。全部自動化したら、ストレスの発生源が消えた。ワーママの副業は「自動化できる仕組み」が命。 #ワーママ #AI副業 #自動化",
    # 初心者・挑戦系への引用
    "コードは一行も書いてないのに動くものが作れる。Claudeに「こうしたい」と伝えるだけでいい。半年前の私に「プログラミングがなくても副業できる」と伝えたら信じなかったと思う。 #AI副業 #ClaudeCode #初心者",
    "エラーが出てもClaudeにログを貼るだけで直る。それを何十回も繰り返したら「エラーが来ても怖くない」に変わった。「技術的な壁」は、何度も越えるうちに壁じゃなくなる。 #AI副業 #ClaudeCode",
    # 失敗談・デバッグ系への引用
    "失敗も全部ネタになる。自動化でバグる・APIが弾かれる・サーバーが落ちる。全部「解決した実体験」としてXに投稿できる。副業で失敗すると「ネタが増えた」と思えるようになった。 #AI副業 #個人開発",
    "「詰んだ」と思うたびに仕組みの理解が深まってた。デバッグが怖くなくなったのは、怖いのに続けたからだと思う。逃げなかっただけで、ある日突然「全部わかる」瞬間が来る。 #AI副業 #ClaudeCode",
    # 自動化設計系への引用
    "「仕組みを作る労力は一回、恩恵は永続する」。これが副業で自動化にこだわる理由。1度作れば2度目のコストはゼロ。積み上げるほど「何もしなくていい分」が増える設計になってる。 #AI副業 #自動化",
    "AIに「役割」を与えると使い方が変わる。私のClaude Codeは「秘書部の司令塔」という役割。何でも話しかければ適切な処理が走る。AIは「どう使うか」より「どんな役割を与えるか」が大事。 #AI副業 #ClaudeCode #自動化",
    "「繰り返してる」と気づいたら自動化のサイン。毎日同じ作業をしてると「これ自動化できそう」がわかるようになる。最初は何を自動化すべきか分からなかった。今は手作業が気になって仕方ない。 #AI副業 #自動化",
    # ── ブログ×Instagram自動化 ──
    "ブログ記事を投稿したら、Instagramにも自動で上がるようになった。Claude Codeがタイトル入り画像を自動生成→WPに設定→ZapierがInstagramに投稿。私がやることはMarkdownファイルを渡すだけ。全部つながると感動する。 #AI副業 #ブログ自動化 #Instagram",
    "バグとの格闘3時間→「フォントの読み込み方を変えたら治った」。RenderサーバーでPillowがTTFフォントを読めなかっただけ。解決策は2行。プログラミングって9割は原因特定で、解決自体は一瞬。 #AI副業 #ClaudeCode #個人開発",
    "セキスイブログに記事を投稿するとき、タイトル入りのアイキャッチ画像が自動生成されるようになった。Pexelsから写真を取得→Pillowでタイトル文字を合成→WPにアップロードまで全自動。見た目のクオリティが一気に上がった。 #AI副業 #ブログ自動化 #自動化",
]


def get_tweet_for_slot(slot):
    """slot: 0=朝(8:30), 1=昼(12:30), 2=夜(19:30)"""
    start = datetime.datetime(2026, 4, 16, tzinfo=JST)
    now = datetime.datetime.now(JST)
    days = max(0, (now - start).days)
    index = (days * 3 + slot) % len(TWEET_STOCK)
    return TWEET_STOCK[index]


def generate_x_post(slot=0):
    return get_tweet_for_slot(slot)


def get_google_creds():
    raw = os.environ.get('GOOGLE_CREDENTIALS', '')
    if not raw:
        return None
    try:
        data = json.loads(raw)
        return Credentials(
            token=data.get('token'),
            refresh_token=data.get('refresh_token'),
            client_id=data.get('client_id'),
            client_secret=data.get('client_secret'),
            token_uri='https://oauth2.googleapis.com/token',
            scopes=data.get('scopes', []),
        )
    except Exception:
        return None


def fetch_search_console(creds, site_url, days=28):
    try:
        service = build('searchconsole', 'v1', credentials=creds)
        end_date = datetime.date.today()
        start_date = end_date - datetime.timedelta(days=days)
        body = {
            'startDate': start_date.isoformat(),
            'endDate': end_date.isoformat(),
            'dimensions': ['query'],
            'rowLimit': 10,
            'orderBy': [{'fieldName': 'impressions', 'sortOrder': 'DESCENDING'}],
        }
        result = service.searchanalytics().query(siteUrl=site_url, body=body).execute()
        return result.get('rows', [])
    except Exception as e:
        print(f"Search Console error ({site_url}): {e}")
        return None


def fetch_x_weekly_metrics():
    try:
        import tweepy
        client = _get_x_client()
        if not client:
            return None
        me = client.get_me()
        if not me.data:
            return None
        user_id = me.data.id
        tweets = client.get_users_tweets(
            id=user_id,
            max_results=10,
            tweet_fields=['public_metrics', 'created_at'],
        )
        if not tweets.data:
            return None
        results = []
        for t in tweets.data:
            m = t.public_metrics
            results.append({
                'text': t.text[:40],
                'impressions': m.get('impression_count', 0),
                'likes': m.get('like_count', 0),
                'retweets': m.get('retweet_count', 0),
            })
        results.sort(key=lambda x: x['impressions'], reverse=True)
        return results[:3]
    except Exception as e:
        print(f"X metrics error: {e}")
        return None


def send_weekly_seo_report():
    try:
        user_id = os.environ['LINE_USER_ID']
        creds = get_google_creds()
        lines = ['📊 週次レポート\n']

        for label, site_url in [('薬膳ブログ', 'https://foodmakehealth.com/'), ('セキスイブログ', 'https://order-sekisui.com/')]:
            lines.append(f'【{label}】')
            if creds:
                rows = fetch_search_console(creds, site_url)
                if rows:
                    lines.append('🔍 検索キーワード TOP5')
                    for i, row in enumerate(rows[:5], 1):
                        query = row['keys'][0]
                        clicks = int(row.get('clicks', 0))
                        impressions = int(row.get('impressions', 0))
                        position = round(row.get('position', 0), 1)
                        lines.append(f'{i}. {query}')
                        lines.append(f'   表示{impressions}回 / クリック{clicks}回 / 順位{position}位')
                    low_ctr = [r for r in rows if r.get('impressions', 0) >= 20 and r.get('ctr', 1) < 0.03]
                    if low_ctr:
                        lines.append('📝 リライト候補（表示多いのにクリック少ない）')
                        for r in low_ctr[:2]:
                            lines.append(f'・{r["keys"][0]}（表示{int(r["impressions"])}回）')
                else:
                    lines.append('（データなし or 認証スコープ未更新）')
            else:
                lines.append('（Google認証未設定）')
            lines.append('')

        x_data = fetch_x_weekly_metrics()
        lines.append('【X（@maki_claude_lab）】')
        if x_data:
            lines.append('🐦 直近10投稿TOP3')
            for i, t in enumerate(x_data, 1):
                lines.append(f'{i}. {t["text"]}…')
                lines.append(f'   👁{t["impressions"]} ❤️{t["likes"]} 🔁{t["retweets"]}')
        else:
            lines.append('（X APIデータ取得できず）')

        message = '\n'.join(lines)
        line_bot_api.push_message(user_id, TextSendMessage(text=message))
    except Exception as e:
        print(f"Weekly SEO report error: {e}")


def send_note_reminder():
    try:
        user_id = os.environ['LINE_USER_ID']
        line_bot_api.push_message(user_id, TextSendMessage(
            text="📝 【noteリマインド】\n今月もX投稿が溜まりました！\n\nそろそろnote記事が書けそうなネタはありますか？\nClaude Codeに「note書きたい」と話しかけてみてね✨"
        ))
    except Exception as e:
        print(f"Note reminder error: {e}")


def send_x_weekly_report():
    """過去7日間のX投稿パフォーマンスをLINEに送信"""
    try:
        client = _get_x_client()
        if not client:
            return

        me = client.get_me()
        user_id_x = me.data.id

        now = datetime.datetime.now(datetime.timezone.utc)
        start_time = now - datetime.timedelta(days=7)

        tweets = client.get_users_tweets(
            id=user_id_x,
            max_results=100,
            start_time=start_time,
            tweet_fields=['public_metrics', 'non_public_metrics', 'created_at', 'text'],
            exclude=['retweets', 'replies']
        )

        line_uid = os.environ['LINE_USER_ID']

        if not tweets.data:
            line_bot_api.push_message(line_uid, TextSendMessage(
                text="📊 今週のXレポート\n\n先週の投稿データがありませんでした。"
            ))
            return

        def get_imp(tweet):
            nm = tweet.non_public_metrics or {}
            return nm.get('impression_count')

        def get_score(tweet):
            imp = get_imp(tweet)
            if imp is not None:
                return imp
            pm = tweet.public_metrics or {}
            return pm.get('like_count', 0) * 3 + pm.get('retweet_count', 0) * 5 + pm.get('reply_count', 0) * 2

        sorted_tweets = sorted(tweets.data, key=get_score, reverse=True)
        total = len(sorted_tweets)
        use_imp = get_imp(sorted_tweets[0]) is not None
        metric_label = "imp" if use_imp else "エンゲージ"

        def fmt(tweet, rank):
            score = get_imp(tweet) if use_imp else get_score(tweet)
            pm = tweet.public_metrics or {}
            likes = pm.get('like_count', 0)
            rts = pm.get('retweet_count', 0)
            text_prev = tweet.text[:25] + '…' if len(tweet.text) > 25 else tweet.text
            return f"{rank}位 {score:,}{metric_label}「{text_prev}」❤{likes} RT{rts}"

        top3 = sorted_tweets[:min(3, total)]
        worst3 = sorted_tweets[max(0, total - 3):]

        lines = [
            f"📊 今週のXレポート（{start_time.strftime('%m/%d')}〜{now.strftime('%m/%d')}）",
            f"投稿数：{total}本\n",
            "🏆 トップ3",
        ]
        for i, t in enumerate(top3, 1):
            lines.append(fmt(t, i))
        if total > 3:
            lines.append("\n📉 ワースト3")
            for i, t in enumerate(worst3, 1):
                lines.append(fmt(t, total - len(worst3) + i))

        # トップ投稿をAIで分析して型と改善提案を生成
        top_texts = '\n'.join([f"{i+1}位: {t.text}" for i, t in enumerate(top3)])
        worst_texts = '\n'.join([f"{t.text}" for t in worst3]) if total > 3 else "なし"
        analysis_prompt = (
            "あなたはXアカウント（@maki_claude_lab：ワーママ×医療職×AI副業実験中）の投稿分析者です。\n"
            "以下のパフォーマンスデータを見て、簡潔に分析してください。\n\n"
            f"【今週のトップ投稿】\n{top_texts}\n\n"
            f"【今週のワースト投稿】\n{worst_texts}\n\n"
            "以下の形式で答えてください（全体で100文字以内）：\n"
            "今週の傾向：〇〇型が強い（例：Before→After型、数字まとめ型、実体験型、共感型、断言型）\n"
            "来週やること：〇〇（具体的に1行で）"
        )
        try:
            analysis_resp = anthropic_client.messages.create(
                model="claude-haiku-4-5-20251001",
                max_tokens=150,
                messages=[{"role": "user", "content": analysis_prompt}]
            )
            analysis_text = analysis_resp.content[0].text.strip()
            lines.append(f"\n📌 AI分析\n{analysis_text}")
        except Exception:
            pass

        line_bot_api.push_message(line_uid, TextSendMessage(text='\n'.join(lines)))
    except Exception as e:
        print(f"X weekly report error: {e}")


def _get_x_client():
    import tweepy
    api_key = (os.environ.get('X_API_KEY') or '').strip()
    api_secret = (os.environ.get('X_API_SECRET') or '').strip()
    access_token = (os.environ.get('X_ACCESS_TOKEN') or '').strip()
    access_token_secret = (os.environ.get('X_ACCESS_TOKEN_SECRET') or '').strip()
    if not all([api_key, api_secret, access_token, access_token_secret]):
        return None
    return tweepy.Client(
        consumer_key=api_key,
        consumer_secret=api_secret,
        access_token=access_token,
        access_token_secret=access_token_secret
    )


def _post_tweet(slot):
    client = _get_x_client()
    if not client:
        print("X API keys not configured, skipping post")
        return
    post_text = generate_x_post(slot)
    client.create_tweet(text=post_text)
    print(f"X post (slot={slot}) successful: {post_text[:50]}...")


def post_to_x_daily():
    """毎朝8:30 投稿（slot=0）"""
    try:
        _post_tweet(0)
    except Exception as e:
        print(f"X post error (morning): {e}")


def post_to_x_noon():
    """毎日12:30 投稿（slot=1）— 奇数日のみ実行して2〜3本/日を交互に"""
    try:
        if datetime.datetime.now(JST).day % 2 == 0:
            return
        _post_tweet(1)
    except Exception as e:
        print(f"X post error (noon): {e}")


def post_to_x_evening():
    """毎日19:30 投稿（slot=2）"""
    try:
        _post_tweet(2)
    except Exception as e:
        print(f"X post error (evening): {e}")


scheduler = BackgroundScheduler(timezone='Asia/Tokyo')
scheduler.add_job(send_morning_message, 'cron', hour=7, minute=0)
scheduler.add_job(send_preparation_reminder, 'cron', hour=20, minute=0, day_of_week='sun')
scheduler.add_job(check_deadline_reminders, 'cron', hour=8, minute=0)
# 毎月1日朝8時30分：HSBC換金リマインダー
scheduler.add_job(send_hsbc_reminder, 'cron', day=1, hour=8, minute=30)
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
# 毎週月曜朝9時10分：在宅専門医 取得プロジェクト週次リマインダー
scheduler.add_job(send_zaitage_reminder, 'cron', day_of_week='mon', hour=9, minute=10)
# 毎日朝8:30・昼12:30（奇数日のみ）・夜19:30：X（Twitter）自動投稿（2〜3本/日）
scheduler.add_job(post_to_x_daily, 'cron', hour=8, minute=30)
scheduler.add_job(post_to_x_noon, 'cron', hour=12, minute=30)
scheduler.add_job(post_to_x_evening, 'cron', hour=19, minute=30)
# 毎週月・水・金 朝9時20分：X エンゲージメントリマインダー
scheduler.add_job(send_x_engage_reminder, 'cron', day_of_week='mon,wed,fri', hour=9, minute=20)
# 毎週月曜朝9時30分：週次SEOレポート（薬膳・セキスイ・X）
scheduler.add_job(send_weekly_seo_report, 'cron', day_of_week='mon', hour=9, minute=30)
# 毎月末日朝9時：noteリマインド
scheduler.add_job(send_note_reminder, 'cron', day='last', hour=9, minute=0)
# 毎週月曜9時40分：週次Xパフォーマンスレポート（PDCA用）
scheduler.add_job(send_x_weekly_report, 'cron', day_of_week='mon', hour=9, minute=40)
# 5月1日朝9時45分：eBay月次リセット＆リミットアップ案内
scheduler.add_job(send_ebay_reset_reminder, 'date', run_date='2026-05-01 09:45:00', timezone='Asia/Tokyo')
scheduler.start()

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
