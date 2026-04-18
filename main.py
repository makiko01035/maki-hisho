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
from blog_yakuzen import auto_rewrite_yakuzen, process_yakuzen_new_article, process_yakuzen_rewrite, get_pinterest_access_token
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

    r = requests.get(img_url, timeout=15)
    img = Image.open(BytesIO(r.content)).convert('RGBA')
    img = img.resize((1080, 1080), Image.LANCZOS)

    overlay = Image.new('RGBA', (1080, 1080), (0, 0, 0, 0))
    draw_ov = ImageDraw.Draw(overlay)
    for y in range(1080):
        alpha = int(180 * (y / 1080))
        draw_ov.line([(0, y), (1080, y)], fill=(0, 0, 0, alpha))
    img = Image.alpha_composite(img, overlay)

    draw = ImageDraw.Draw(img)
    font_path = os.path.join(os.path.dirname(__file__), 'fonts', 'NotoSansJP-Bold.ttf')
    font = ImageFont.truetype(font_path, 60)

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
    return None, None


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


@app.route('/wp-post-published', methods=['POST'])
def wp_post_published():
    """WP Webhooksから記事公開通知を受け取り、オーバーレイ画像を作成してfeatured_mediaを更新する"""
    data = request.json or {}

    post_status = data.get('post_status', '')
    post_type = data.get('post_type', 'post')
    if post_status != 'publish' or post_type != 'post':
        return {'status': 'skipped'}, 200

    post_id = data.get('ID') or data.get('post_id')
    wp_url = os.environ.get('SEKISUI_WP_URL', '')
    wp_user = os.environ.get('SEKISUI_WP_USER', '')
    wp_pass = os.environ.get('SEKISUI_WP_APP_PASSWORD', '')

    try:
        post_res = requests.get(f"{wp_url}/wp-json/wp/v2/posts/{post_id}", auth=(wp_user, wp_pass))
        if post_res.status_code != 200:
            return {'error': 'post not found'}, 404
        post = post_res.json()
        title = post['title']['rendered']
        featured_media_id = post.get('featured_media', 0)
        if not featured_media_id:
            return {'status': 'skipped', 'reason': 'no featured image'}, 200

        media_res = requests.get(f"{wp_url}/wp-json/wp/v2/media/{featured_media_id}", auth=(wp_user, wp_pass))
        if media_res.status_code != 200:
            return {'error': 'media not found'}, 404
        img_url = media_res.json()['source_url']

        img_data = _build_overlay_jpeg(img_url, title)
        filename = f'ig_{post_id}.jpg'
        new_media_id, media_url = _upload_to_wp(img_data, filename, wp_url, wp_user, wp_pass)
        if not new_media_id:
            return {'error': 'upload failed'}, 500

        requests.post(
            f"{wp_url}/wp-json/wp/v2/posts/{post_id}",
            auth=(wp_user, wp_pass),
            json={'featured_media': new_media_id},
        )
        return {'status': 'ok', 'new_image': media_url}, 200
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
    "子どもの習い事チラシをLINEに送ったら、AIが日時を読み取ってGoogleカレンダーに自動登録してくれた🙌 もう手入力しなくていい。 #AI副業 #子育て中ママ #LINE",
    "申込期限をLINEに送るだけで、1週間前・3日前・前日に自動でリマインドが来る仕組みを作った✨ 締切忘れがゼロになった。 #AI副業 #自動化 #LINE",
    "チラシに複数のイベントが書いてあっても全部拾ってカレンダーに入れてくれる。1個しか対応してなかったのを改良した😄 #AI副業 #LINE #自動化",
    "Renderが再起動しても登録待ちデータが消えない仕組みを追加。「登録したのに消えた」が起きなくなった🔧 #AI副業 #個人開発 #Claude",
    "毎朝7時に今日の予定がLINEに届く🌅 自分で作ったリマインダー。地味だけど毎日助かってる。 #AI副業 #LINE #自動化",
    "医療職で働きながらLINEボットを作り続けて1ヶ月弱。気づいたら20個以上の自動化が動いてた😳 全部noteにまとめた→ https://note.com/maki_claude_lab/n/n0bf26963cd26 #AI副業 #医療職 #ClaudeCode",
    "セキスイブログの記事、LINEで「書いて」と送るだけで書いてくれて即公開まで全自動になった✍️ #AI副業 #ブログ自動化 #Claude",
    "薬膳ブログのリライトも自動化。季節に合った記事をAIが選んで、リライトして、公開まで全部やってくれる🌿 #AI副業 #ブログ #自動化",
    "eBayのリサーチをLINEから起動できるようにした。出先でもスマホから商品調査が始められる📱 #AI副業 #eBay #物販",
    "Pinterest連携のOAuth認証を実装した。審査待ちで今はまだ使えないけど、通った瞬間に動き出せる準備は完了🔧 #AI副業 #自動化 #Claude",
    "Xに毎日自動投稿する仕組みを作った。投稿ネタを考えなくていいって本当にラク😌 #AI副業 #X #自動化",
    "学校のプリント管理もLINEボットに追加。写真送ったら内容を読み取って管理してくれる📄 #AI副業 #子育て中ママ #LINE",
    "noteに「プログラミングゼロからClaudeで秘書ボットを作るまで」の記事を公開した📝 1,480円で全工程まとめてます→ https://note.com/maki_claude_lab/n/n0bf26963cd26 #AI副業 #note #Claude",
    "A8.netの審査確認リマインダーを追加。毎週月曜に自動で「確認した？」と届く。忘れっぽい自分に最適 #AI副業 #アフィリエイト #自動化",
    "月初の振り返りリマインダー、毎月1日に自動で届くように設定。習慣化ってこうやって作るんだと気づいた #AI副業 #習慣化 #自動化",
    "無料サーバーRenderがスリープしないようにpingエンドポイントを追加。月0円で24時間稼働を維持してる #AI副業 #個人開発 #Render",
    "タイムアウトエラーをバックグラウンド処理に変えて解決。エラーログ見ながらClaudeと一緒にデバッグするの得意になってきた🔍 #AI副業 #個人開発 #Claude",
    "22時開始のイベントが翌0時で終わるとき、終了日時が翌日になる処理を追加。細かいバグこそちゃんと潰す🗓️ #AI副業 #個人開発 #自動化",
    "プログラミング経験ゼロでもClaude Codeがあれば本当に作れる。コードは書けないけど「どうなってほしいか」を伝えることならできる😊 体験記はこちら→ https://note.com/maki_claude_lab/n/n0bf26963cd26 #AI副業 #ClaudeCode #初心者",
    "月0円で24時間動き続けるサーバーを運用中。RenderとGitHubを組み合わせたら無料でデプロイまで全自動になった #AI副業 #個人開発 #Render",
    "医療職×子育て中で時間がない分、「自動化するか/しないか」の判断が早くなった。作って損したことが一度もない #AI副業 #医療職 #副業",
    "薬膳ブログにアフィリエイトのCTAを記事末尾に自動挿入する機能を追加✨ 記事書くたびに忘れてたのがゼロになった #AI副業 #ブログ #アフィリエイト",
    "LINEから「会社」と送るだけで自分のAI会社の状況が一覧で見られるダッシュボードを作った👀 #AI副業 #ClaudeCode #自動化",
    "画像から複数イベントを抽出するJSONパースが不安定だったのを修正。「なんか登録されなかった」が起きなくなった #AI副業 #個人開発 #Claude",
    "main.pyが大きくなったのでモジュール分割してリファクタリング。コードが読みやすくなると次の機能追加がスムーズ #AI副業 #個人開発 #ClaudeCode",
    "LINEボット1本で、ブログ投稿・カレンダー管理・eBayリサーチ・プリント管理が全部できる。スマホだけで副業が完結しつつある📱 #AI副業 #LINE #自動化",
    "「秘書部が司令塔」という概念を導入した。どこに何を頼めばいいか迷わなくなった。AIに役割を持たせると全然違う😮 #AI副業 #ClaudeCode #自動化",
    "子どもが寝た後の1〜2時間でここまで作れるようになった。コードが書けなくても「作りたいもの」が明確なら進める。全工程note→ https://note.com/maki_claude_lab/n/n0bf26963cd26 #AI副業 #子育て中ママ #副業",
    "薬膳ブログ120記事・セキスイブログ34記事。記事数が増えるほど自動化の恩恵が大きくなってきた📈 #AI副業 #ブログ #アフィリエイト",
    "Google CalendarのOAuth認証をLINEボットに組み込んだ。最初は一番難しかったのに今では当たり前に使いこなしてる🔑 #AI副業 #個人開発 #Google",
    "エラーが出たらログを貼ってClaudeに聞く。それだけで大体解決する。デバッグが怖くなくなった💪 ゼロからの全記録→ https://note.com/maki_claude_lab/n/n0bf26963cd26 #AI副業 #ClaudeCode #初心者",
    "eBayとメルカリを組み合わせた物販も始めてる。仕入れ→出品の流れをいずれ自動化したい #AI副業 #eBay #物販",
    "Famm締切リマインダーを追加。毎月忘れてたのがゼロになった。小さい自動化ほど日常が楽になる #AI副業 #子育て中ママ #自動化",
    "「登録して」と送ってもデータがないときのエラーメッセージを改善。使う人のことを考えたUXを少しずつ磨いてる #AI副業 #個人開発 #LINE",
    "ChatGPTじゃなくてClaude Codeを使ってる理由：ファイルを直接読み書きできて、コードを実行しながら作れるから。会話型との差がでかい #AI副業 #ClaudeCode #Claude",
    "1ヶ月弱でコミット数50以上。毎日少しずつでも積み上げるとこんなに変わる #AI副業 #ClaudeCode #副業",
    "LINE秘書ボットを作って一番変わったこと：「あれ、今日何の予定あったっけ」がゼロになった。地味だけど毎日助かってる #AI副業 #LINE #子育て中ママ",
    "セキスイブログとPexels APIを連携。記事のアイキャッチ画像も自動で取得するようにした🖼️ #AI副業 #ブログ自動化 #Claude",
    "Renderの環境変数を10個以上管理してる。最初は何が何かわからなかったのに今は全部把握できてる #AI副業 #個人開発 #Render",
    "週次ルーティン通知を設定：月曜A8確認、火曜薬膳ブログ、木曜セキスイブログ、土曜eBayチェック。週の動きが自然に決まってきた #AI副業 #副業 #習慣化",
    # ── まるちゃんワールド（ゲーム系） ──
    "Claude Codeでブラウザゲームを作った🎮 プログラミングゼロなのにタイピングゲームが完成。息子と一緒に遊んでる笑 #AI副業 #ClaudeCode #ゲーム開発",
    "「まるちゃんワールド」というゲームシリーズを作り始めた。タイピング練習ゲームとアクションゲームをHTMLファイル1枚で。コードは全部Claudeが書いてくれた #AI副業 #ClaudeCode",
    "AIで作ったゲームをGitHub Pagesで無料公開した📡 URLを共有するだけでどこからでも遊べる。サーバー代0円。 #AI副業 #個人開発 #ClaudeCode",
    "子どものタイピング練習用にゲームを作ったら自分もハマった😂 かんたん・ふつう・むずかしい・1文字モードの4段階。BGMも効果音も全部AIが生成 #AI副業 #子育て中ママ",
    "まるちゃんワールドに音ゲーを追加した🎵 F・G・H・Jキーでノーツをたたくやつ。ステージ3段階でむずかしいは本当にむずかしい笑 コードは全部Claude製 #AI副業 #ClaudeCode #ゲーム開発",
    "AIで「ゲーム部」を立ち上げた🎮 タイピング・アクション・音ゲーと3作品できたので正式に部署化。プログラミングゼロの医療職ママが会社組織図にゲーム部を追加する日が来るとは #AI副業",
    "まるちゃんワールドの音ゲー、むずかしいモードはノーツがめちゃ速い😂 90秒間ずっと集中しないといけない。Claudeに「もっと難しくして」って言ったら本当に難しくなった #ClaudeCode #ゲーム開発",
    # ── eBay API連携 ──
    "eBayのAPIをClaude Codeと連携させた📦 自分の出品120品がリストで見られるようになった。プログラミングゼロでもAPIって繋げるんだと気づいた #AI副業 #eBay #物販",
    "eBayの出品状況をAPIで自動取得できるようにした。売上・ウォッチャー数・出品総額が一瞬でわかる。手動でマイページ確認しなくていい🙌 #AI副業 #eBay #自動化",
    "eBayの出品上限（金額制限）の存在を知らなかった😳 $7,000/月の壁。Claudeが一緒にSeller Hubを解読してくれて助かった。5月1日にリセットされるから待機中 #AI副業 #eBay #物販",
    "eBayリミットアップ申請の方法もClaudeに教えてもらった。英語でのサポートチャットの文面まで全部作ってもらえる。英語苦手でも全然平気 #AI副業 #eBay #物販",
    "ブログ記事を公開したら自動でInstagramに投稿される仕組みを作った📸 Zapier＋WP Webhooksで完全無料。しかも画像にタイトル文字まで合成してる #AI副業 #自動化 #ブログ",
    "Zapierって無料でもかなりのことができる。WordPressの記事→Instagramの自動投稿、設定30分でできた🙌 プログラミングゼロでも繋がるものだと実感 #AI副業 #Zapier #自動化",
    "Pythonで画像にテキストを重ねる処理を作った🖼️ ブログのアイキャッチにタイトルを自動合成してInstagram用にリサイズ。Claudeにコード書いてもらったら思ったより簡単だった #AI副業 #Python #自動化",
    # ── 音声文字起こし ──
    "ZOOMの録画をAIで自動文字起こし＋議事録にする仕組みを作った🎤 Groqという無料APIを使えばコスト0円。350MBのMP4でも自動で分割して全部処理してくれる #AI副業 #自動化 #ClaudeCode",
    "LINEに音声メッセージを送ると自動で文字起こしして返ってくる仕組みを作った✨ 短い録音はLINE、長い会議録音はPCツールで、って使い分けができるようになった #AI副業 #LINE #自動化",
    "会議の録音、もう自分で聞き返さなくていい😌 ダブルクリックでアプリ起動→ファイル選択→議事録がテキストで保存される。Groq Whisperが無料で文字起こししてくれる #AI副業 #自動化 #時短",
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


def send_note_reminder():
    try:
        user_id = os.environ['LINE_USER_ID']
        line_bot_api.push_message(user_id, TextSendMessage(
            text="📝 【noteリマインド】\n今月もX投稿が溜まりました！\n\nそろそろnote記事が書けそうなネタはありますか？\nClaude Codeに「note書きたい」と話しかけてみてね✨"
        ))
    except Exception as e:
        print(f"Note reminder error: {e}")


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
# 毎月末日朝9時：noteリマインド
scheduler.add_job(send_note_reminder, 'cron', day='last', hour=9, minute=0)
# 5月1日朝9時45分：eBay月次リセット＆リミットアップ案内
scheduler.add_job(send_ebay_reset_reminder, 'date', run_date='2026-05-01 09:45:00', timezone='Asia/Tokyo')
scheduler.start()

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
