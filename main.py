import os
import json
import base64
import datetime
import threading
import time
import requests
from flask import Flask, request, abort, send_from_directory, jsonify
from linebot.exceptions import InvalidSignatureError
from linebot.models import MessageEvent, TextMessage, TextSendMessage, ImageMessage, AudioMessage, FileMessage
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from apscheduler.schedulers.background import BackgroundScheduler

from clients import line_bot_api, handler, anthropic_client, JST
from ebay_handler import run_ebay_research, send_daily_purchase_candidates, check_seller_now
from sns_engine_koharu import (
    run_researcher as koharu_researcher,
    run_writer    as koharu_writer,
    run_poster_morning as koharu_poster_morning,
    run_poster_aff     as koharu_poster_aff,
    run_collector as koharu_collector,
    run_analyst   as koharu_analyst,
    run_monitor   as koharu_monitor,
    handle_approval as koharu_handle_approval,
)
from sns_engine_mako import (
    run_researcher as mako_researcher,
    run_writer     as mako_writer,
    run_poster_info as mako_poster_info,
    run_poster_aff  as mako_poster_aff,
    run_collector   as mako_collector,
    run_analyst     as mako_analyst,
    run_monitor     as mako_monitor,
    handle_mako_approval,
)
from blog_yakuzen import auto_rewrite_yakuzen, process_yakuzen_new_article, process_yakuzen_rewrite, rewrite_yakuzen_by_slug, rewrite_yakuzen_by_keyword, get_pinterest_access_token, check_old_yakuzen_post, delete_yakuzen_post, kw_auto_rewrite, kw_auto_new_article
from blog_sekisui import suggest_sekisui_themes, process_sekisui_article

app = Flask(__name__)

PENDING_FILE = '/tmp/pending_events.json'
SEKISUI_SESSION_FILE = '/tmp/sekisui_sessions.json'
YAKUZEN_SESSION_FILE = '/tmp/yakuzen_sessions.json'
PRINTS_FILE = '/tmp/school_prints.json'
PRINT_SESSION_FILE = '/tmp/print_sessions.json'
MORNING_SENT_FILE = '/tmp/morning_sent_date.txt'
NOTE_SESSION_FILE = '/tmp/note_sessions.json'
ROOM_TAG_SESSION_FILE = '/tmp/room_tag_sessions.json'
NEWSLETTER_SESSION_FILE = '/tmp/newsletter_sessions.json'

_morning_sent_date = None
_morning_sent_lock = threading.Lock()


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


def load_note_sessions():
    try:
        with open(NOTE_SESSION_FILE, 'r') as f:
            return json.load(f)
    except Exception:
        return {}


def save_note_sessions(data):
    try:
        with open(NOTE_SESSION_FILE, 'w') as f:
            json.dump(data, f, ensure_ascii=False)
    except Exception as e:
        print(f"note_sessions save error: {e}")


def load_newsletter_sessions():
    try:
        with open(NEWSLETTER_SESSION_FILE, 'r') as f:
            return json.load(f)
    except Exception:
        return {}


def save_newsletter_sessions(data):
    try:
        with open(NEWSLETTER_SESSION_FILE, 'w') as f:
            json.dump(data, f, ensure_ascii=False)
    except Exception as e:
        print(f"newsletter_sessions save error: {e}")


def save_newsletter_to_notion(email):
    """メルマガ1件をNotionのメルマガDBに保存"""
    notion_token = os.environ.get('NOTION_TOKEN', '')
    headers = {
        "Authorization": f"Bearer {notion_token}",
        "Notion-Version": "2022-06-28",
        "Content-Type": "application/json"
    }
    today = datetime.datetime.now(JST).strftime('%Y-%m-%d')
    title = f"[{email.get('category', '')}] {email.get('from_name', email.get('from', ''))}"
    content = f"件名：{email.get('subject', '')}\n\n{email.get('summary', '')}"
    body = {
        "after": "323f8d6d-41de-809d-9e98-f9a5da8556a8",
        "children": [{
            "object": "block",
            "type": "to_do",
            "to_do": {
                "rich_text": [{"type": "text", "text": {"content": f"📧 {today} {title}\n{content}"}}],
                "checked": False
            }
        }]
    }
    requests.patch(
        "https://api.notion.com/v1/blocks/323f8d6d41de80dea66efad500806f69/children",
        headers=headers,
        json=body
    )


def load_room_tag_sessions():
    try:
        with open(ROOM_TAG_SESSION_FILE, 'r') as f:
            return json.load(f)
    except Exception:
        return {}


def save_room_tag_sessions(data):
    try:
        with open(ROOM_TAG_SESSION_FILE, 'w') as f:
            json.dump(data, f, ensure_ascii=False)
    except Exception as e:
        print(f"room_tag_sessions save error: {e}")


def generate_room_tags(text=None, image_base64=None, media_type=None):
    """楽天Room用ハッシュタグを生成する"""
    content = []
    if image_base64:
        content.append({
            'type': 'image',
            'source': {'type': 'base64', 'media_type': media_type, 'data': image_base64}
        })
    prompt = (
        f"商品名：{text}\n\n" if text else "この商品画像を見て、\n\n"
    ) + (
        "楽天Roomの投稿に使うハッシュタグを15個生成してください。\n"
        "条件：\n"
        "- #を先頭につけたハッシュタグ形式\n"
        "- 日本語で\n"
        "- 楽天Roomで検索されやすいキーワード（商品カテゴリ・用途・ブランド・特徴など）\n"
        "- 1行に並べてスペース区切りで出力\n"
        "- ハッシュタグのみ出力（説明文不要）"
    )
    content.append({'type': 'text', 'text': prompt})
    response = anthropic_client.messages.create(
        model='claude-sonnet-4-6',
        max_tokens=300,
        messages=[{'role': 'user', 'content': content}]
    )
    return response.content[0].text.strip()


def send_long_message(user_id, text, chunk_size=4000):
    """長いテキストを分割してLINEにpush送信"""
    chunks = [text[i:i+chunk_size] for i in range(0, len(text), chunk_size)]
    for i, chunk in enumerate(chunks):
        prefix = f"【{i+1}/{len(chunks)}】\n" if len(chunks) > 1 else ""
        line_bot_api.push_message(user_id, TextSendMessage(text=prefix + chunk))


def generate_note_draft_async(user_id, note_type, target=None, worry=None, experience=None):
    """note下書きをClaude APIで生成してLINEに分割送信"""
    try:
        type_label = "有料" if note_type == "paid" else "無料"
        if note_type == "paid":
            type_instruction = (
                "有料記事として書いてください。\n"
                "- 無料部分：導入・共感・この記事でわかること（全体の1/3程度）\n"
                "- 「ここから有料記事です（300円）」という区切りを入れる\n"
                "- 有料部分：再現性のある具体的な手順・プロンプト・実例を含める\n"
                "- 目標文字数：3,000〜4,000文字"
            )
        else:
            type_instruction = (
                "無料記事として書いてください。\n"
                "- 読みごたえがあり、SNSでシェアされるような内容\n"
                "- 体験談ベースで共感を呼ぶ構成\n"
                "- 目標文字数：1,500〜2,000文字"
            )

        design_info = ""
        if target:
            design_info += f"\n【届けたい読者】{target}"
        if worry:
            design_info += f"\n【読者の悩み】{worry}"
        if experience:
            design_info += f"\n【まきの体験エピソード】{experience}"

        response = anthropic_client.messages.create(
            model='claude-sonnet-4-6',
            max_tokens=4000,
            system=(
                "あなたはAI×日常生活×ワーママ実体験を発信する「まき」として書きます。\n"
                "プロフィール：医療職・3児ワンオペ・夫急逝後にプログラミングゼロからAIで毎日を仕組み化した\n"
                "読者：「AIって何に使うの？」と思っているワーママ・AI初心者・日常を楽にしたい人\n"
                "文体：ですます調・親しみやすい・体験談ベース・専門用語を使わない\n"
                "重要：読者の悩みへの共感から入り、まきの実体験を通じて「私にもできる」と感じてもらう構成にする\n"
                + type_instruction
            ),
            messages=[{
                'role': 'user',
                'content': f'以下の設計情報に基づいてnote{type_label}記事の下書きを書いてください。タイトルも含めて。\n{design_info}'
            }]
        )

        draft = response.content[0].text
        line_bot_api.push_message(user_id, TextSendMessage(
            text=f"📝 note{type_label}記事の下書きができました！\n↓をそのままnoteにコピペしてください👇"
        ))
        send_long_message(user_id, draft)
        if note_type == "paid":
            line_bot_api.push_message(user_id, TextSendMessage(
                text="✅ コピペ後、noteで「有料ライン」を「ここから有料記事です」の行に設定してから公開してください🎉"
            ))

    except Exception as e:
        line_bot_api.push_message(user_id, TextSendMessage(text=f"❌ 下書き生成エラー: {str(e)[:200]}"))


def _start_old_check(user_id, skip_ids):
    """古い記事チェックを開始してセッションにpost_idを保存"""
    yakuzen_sessions = load_yakuzen_sessions()
    post_id = check_old_yakuzen_post(user_id, skip_ids)
    if post_id:
        yakuzen_sessions[user_id] = {
            'state': 'waiting_for_old_rewrite_confirm',
            'post_id': post_id,
            'skip_ids': skip_ids
        }
        save_yakuzen_sessions(yakuzen_sessions)


def rewrite_yakuzen_by_post_id(user_id, post_id):
    """post_idを指定して記事をリライト"""
    import html as html_lib
    from blog_yakuzen import (get_yakuzen_wp_creds, generate_yakuzen_rewrite,
                               generate_pexels_keyword, fetch_pexels_image_url,
                               upload_image_to_yakuzen_wp, detect_category_id,
                               post_to_yakuzen_wp, try_post_to_pinterest, send_sns_messages)
    try:
        wp_url, wp_user, wp_pass = get_yakuzen_wp_creds()
        res = requests.get(f'{wp_url}/wp-json/wp/v2/posts/{post_id}',
                           auth=(wp_user, wp_pass),
                           params={'_fields': 'id,title,content'}, timeout=15)
        post = res.json()
        post_title = html_lib.unescape(post['title']['rendered'])
        post_content = post['content']['rendered']
        article_md = generate_yakuzen_rewrite(post_title, post_content)
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
        line_bot_api.push_message(user_id, TextSendMessage(text=f"✅ リライト完了！\n\n📝 {new_title}\n🔗 {link}"))
        send_sns_messages(user_id, new_title, link, image_url, new_content)
    except Exception as e:
        print(f"rewrite_yakuzen_by_post_id error: {e}")
        line_bot_api.push_message(user_id, TextSendMessage(text=f"😢 エラーが発生しました。\n{str(e)[:150]}"))


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
    """申込期限の1週間前・3日前・前日・当日、申込開始日の当日にLINE通知を送る"""
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
                summary = e.get('summary', '')
                if '【申込期限】' in summary:
                    title = summary.replace('【申込期限】', '').strip()
                    msg = f"{icon} 【申込期限 {label}！】\n「{title}」の申し込み期限は{label}です！\n{action}"
                    line_bot_api.push_message(user_id, TextSendMessage(text=msg))
                elif '【申込開始】' in summary and days_ahead == 0:
                    title = summary.replace('【申込開始】', '').strip()
                    msg = f"🟢 【申込開始 今日！】\n「{title}」の申し込みが今日から始まりました！\n忘れずに申し込んでください！"
                    line_bot_api.push_message(user_id, TextSendMessage(text=msg))
    except Exception as e:
        print(f"Deadline reminder error: {e}")


@app.route('/rakuten-room-rss')
def rakuten_room_rss():
    import feedparser
    import re
    from flask import Response
    try:
        headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36'}
        raw = requests.get('https://room.rakuten.co.jp/makiko01035/items/feed/rss', headers=headers, timeout=10)
        feed = feedparser.parse(raw.text)
        items_xml = ''
        for entry in feed.entries:
            title = entry.get('title', '')
            link = entry.get('link', '')
            summary = entry.get('summary', entry.get('description', ''))
            img_match = re.search(r'<img[^>]+src=["\']([^"\']+)["\']', summary)
            img_url = img_match.group(1) if img_match else ''
            summary_clean = re.sub(r'<[^>]+>', '', summary).strip()
            items_xml += f'''  <item>
    <title><![CDATA[{title}]]></title>
    <link>{link}</link>
    <description><![CDATA[{summary_clean}]]></description>
    <enclosure url="{img_url}" type="image/jpeg" length="0"/>
  </item>\n'''
        rss_xml = f'''<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <title>まきの楽天room</title>
    <link>https://room.rakuten.co.jp/makiko01035/items</link>
    <description>まきの楽天roomコレクション</description>
{items_xml}  </channel>
</rss>'''
        return Response(rss_xml, mimetype='application/rss+xml; charset=utf-8')
    except Exception as e:
        return str(e), 500



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


@app.route('/test-threads')
def test_threads():
    """Threads接続テスト＆テスト投稿"""
    access_token = os.environ.get('THREADS_ACCESS_TOKEN', '')
    user_id = os.environ.get('THREADS_USER_ID', '')
    if not access_token or not user_id:
        missing = []
        if not access_token:
            missing.append('THREADS_ACCESS_TOKEN')
        if not user_id:
            missing.append('THREADS_USER_ID')
        return f'❌ Render環境変数が未設定です: {", ".join(missing)}', 400
    post_id = post_to_threads('【テスト投稿】まきの秘書ボットからThreads連携テスト中🧵')
    if post_id:
        import time; time.sleep(3)
        reply_to_threads(post_id, '🛒 コチラ！\nhttps://room.rakuten.co.jp/makiko01035\n[楽天PR]')
        return '✅ Threads投稿成功！（本文＋コメントURLの2段構え）Threadsアプリで確認してください。'
    return '❌ 投稿失敗。Renderのログを確認してください。', 500


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


@app.route('/set-yakuzen-image', methods=['POST'])
def set_yakuzen_image():
    """既存の薬膳記事にPexelsアイキャッチ画像を設定する"""
    secret = request.headers.get('X-Secret', '')
    if secret != os.environ.get('LINE_USER_ID', ''):
        return {'error': 'unauthorized'}, 401
    data = request.json or {}
    post_id = data.get('post_id')
    keyword = data.get('keyword', 'pillow mattress sleep bedroom')
    title = data.get('title', keyword)
    if not post_id:
        return {'error': 'post_id required'}, 400
    try:
        from blog_yakuzen import fetch_pexels_image_url, upload_image_to_yakuzen_wp
        image_url = fetch_pexels_image_url(keyword)
        if not image_url:
            return {'error': 'Pexels画像が見つかりませんでした'}, 404
        media_id = upload_image_to_yakuzen_wp(image_url, title)
        wp_url = os.environ.get('YAKUZEN_WP_URL', 'https://foodmakehealth.com')
        wp_user = os.environ.get('YAKUZEN_WP_USER', 'makiko01035')
        wp_pass = os.environ['YAKUZEN_WP_APP_PASSWORD']
        res = requests.post(
            f'{wp_url}/wp-json/wp/v2/posts/{post_id}',
            auth=(wp_user, wp_pass),
            json={'featured_media': media_id},
            timeout=30
        )
        if res.status_code in (200, 201):
            return {'status': 'ok', 'media_id': media_id, 'image_url': image_url}
        return {'error': res.text[:200]}, 500
    except Exception as e:
        return {'error': str(e)}, 500


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


@app.route('/notify-ig', methods=['POST'])
def notify_ig():
    """記事タイトルとURLを受け取りInstagramネタをLINEに送信"""
    secret = request.headers.get('X-Secret', '')
    if secret != os.environ.get('LINE_USER_ID', ''):
        return {'error': 'unauthorized'}, 401
    data = request.json or {}
    title = data.get('title', '')
    url = data.get('url', '')
    content_md = data.get('content_md', '')
    image_url = data.get('image_url', None)
    if not title or not url:
        return {'error': 'title and url required'}, 400
    _notify_line_ig(title, url, content_md, image_url)
    return {'status': 'ok', 'message': f'LINEにInstagramネタを送信中: {title}'}


def _fetch_wp_post_info(post_url):
    """記事URLからWP REST APIで本文・アイキャッチURLを取得。(content_md, featured_url)を返す"""
    import re as _re
    try:
        slug = post_url.rstrip('/').split('/')[-1]
        yakuzen_wp_url = os.environ.get('YAKUZEN_WP_URL', 'https://foodmakehealth.com')
        wp_user = os.environ.get('YAKUZEN_WP_USER', 'makiko01035')
        wp_pass = os.environ.get('YAKUZEN_WP_APP_PASSWORD', '')
        r = requests.get(
            f'{yakuzen_wp_url}/wp-json/wp/v2/posts',
            params={'slug': slug, '_embed': 1},
            auth=(wp_user, wp_pass),
            timeout=15
        )
        if r.status_code == 200 and r.json():
            post = r.json()[0]
            content_html = post.get('content', {}).get('rendered', '')
            content_md = _re.sub(r'<[^>]+>', '', content_html)[:3000]
            featured_url = None
            for media in post.get('_embedded', {}).get('wp:featuredmedia', []):
                featured_url = media.get('source_url')
                break
            return content_md, featured_url
    except Exception as e:
        print(f"WP post info fetch error: {e}")
    return '', None


def _notify_line_ig(title, post_url, content_md='', image_url=None):
    """薬膳記事公開後にInstagramキャプションをLINEにバックグラウンド送信"""
    import threading
    def _send():
        try:
            actual_content = content_md
            actual_image = image_url
            if not actual_content or not actual_image:
                fetched_content, fetched_image = _fetch_wp_post_info(post_url)
                actual_content = actual_content or fetched_content
                actual_image = actual_image or fetched_image
            from blog_yakuzen import send_sns_messages
            user_id = os.environ.get('LINE_USER_ID', '')
            if user_id:
                send_sns_messages(user_id, title, post_url, actual_image, actual_content)
        except Exception as e:
            print(f"LINE Instagram通知エラー: {e}")
    threading.Thread(target=_send, daemon=True).start()


@app.route('/post-yakuzen-direct', methods=['POST'])
def post_yakuzen_direct():
    """Claude Codeから直接薬膳記事を投稿するエンドポイント"""
    secret = request.headers.get('X-Secret', '')
    if secret != os.environ.get('LINE_USER_ID', ''):
        return {'error': 'unauthorized'}, 401
    data = request.json or {}
    title = data.get('title', '')
    content_md = data.get('content_md', '')
    slug = data.get('slug', '')
    if not title or (not content_md and not data.get('content_html', '')):
        return {'error': 'title and content required'}, 400
    try:
        update_id = data.get('update_id', '')
        pid = int(update_id) if update_id else None
        content_html = data.get('content_html', '')
        if content_html:
            # ローカルで変換済みHTMLをそのままWPに投稿
            wp_url = os.environ.get('YAKUZEN_WP_URL', 'https://foodmakehealth.com')
            wp_user = os.environ.get('YAKUZEN_WP_USER', 'makiko01035')
            wp_pass = os.environ['YAKUZEN_WP_APP_PASSWORD']
            post_data = {'title': title, 'content': content_html, 'status': 'publish'}
            if slug:
                post_data['slug'] = slug
            # カテゴリー・タグのサポート
            categories = data.get('categories', [])
            if categories:
                post_data['categories'] = categories
            tag_names = data.get('tags', [])
            if tag_names:
                # タグ名→IDに変換（なければ作成）
                tag_ids = []
                for tag_name in tag_names:
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
                    post_data['tags'] = tag_ids
            if pid:
                res = requests.post(f'{wp_url}/wp-json/wp/v2/posts/{pid}',
                                    auth=(wp_user, wp_pass), json=post_data, timeout=30)
            else:
                res = requests.post(f'{wp_url}/wp-json/wp/v2/posts',
                                    auth=(wp_user, wp_pass), json=post_data, timeout=30)
            if res.status_code in (200, 201):
                post = res.json()
                post_url = post['link']
                _notify_line_ig(title, post_url, content_md)
                return {'status': 'ok', 'post_id': post['id'], 'url': post_url}, res.status_code
            return {'error': res.text[:200]}, 500
        from blog_yakuzen import post_to_yakuzen_wp
        post_id, post_url = post_to_yakuzen_wp(title, content_md, post_id=pid, status='publish')
        if slug:
            wp_url = os.environ.get('YAKUZEN_WP_URL', 'https://foodmakehealth.com')
            wp_user = os.environ.get('YAKUZEN_WP_USER', 'makiko01035')
            wp_pass = os.environ.get('YAKUZEN_WP_APP_PASSWORD', '')
            import requests as req
            req.post(f'{wp_url}/wp-json/wp/v2/posts/{post_id}',
                     auth=(wp_user, wp_pass), json={'slug': slug}, timeout=15)
        _notify_line_ig(title, post_url, content_md)
        return {'status': 'ok', 'post_id': post_id, 'url': post_url}, 201
    except Exception as e:
        return {'error': str(e)}, 500


@app.route('/update-yakuzen-meta', methods=['POST'])
def update_yakuzen_meta():
    """既存記事のカテゴリー・タグ・スラッグを更新する（コンテンツ変更なし）"""
    secret = request.headers.get('X-Secret', '')
    if secret != os.environ.get('LINE_USER_ID', ''):
        return {'error': 'unauthorized'}, 401
    data = request.json or {}
    post_ids = data.get('post_ids', [])
    if not post_ids:
        return {'error': 'post_ids required'}, 400
    wp_url = os.environ.get('YAKUZEN_WP_URL', 'https://foodmakehealth.com')
    wp_user = os.environ.get('YAKUZEN_WP_USER', 'makiko01035')
    wp_pass = os.environ['YAKUZEN_WP_APP_PASSWORD']
    update_data = {}
    if 'categories' in data:
        update_data['categories'] = data['categories']
    tag_names = data.get('tags', [])
    if tag_names:
        tag_ids = []
        for tag_name in tag_names:
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
        update_data['tags'] = tag_ids
    results = []
    for pid in post_ids:
        try:
            res = requests.post(f'{wp_url}/wp-json/wp/v2/posts/{pid}',
                                auth=(wp_user, wp_pass), json=update_data, timeout=30)
            results.append({'post_id': pid, 'status': res.status_code})
        except Exception as e:
            results.append({'post_id': pid, 'error': str(e)})
    return {'results': results}, 200


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
        <span class="line-keyword">睡眠記事</span>
        <span class="line-desc">睡眠ブログメニュー表示（新規作成 / リライト）</span>
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


DASHBOARD_PASSWORD = os.environ.get('DASHBOARD_PASSWORD', 'maki1234')

@app.route('/dashboard', methods=['GET', 'POST'])
def dashboard():
    pw = request.args.get('pw') or request.form.get('pw', '')
    if pw != DASHBOARD_PASSWORD:
        err = '<p class="err">パスワードが違います</p>' if request.method == 'POST' else ''
        return f'''<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Dashboard — Login</title>
<link href="https://fonts.googleapis.com/css2?family=Noto+Sans+JP:wght@300;400&display=swap" rel="stylesheet">
<style>
  body {{ background: #f4f0eb; display: flex; align-items: center; justify-content: center; min-height: 100vh; font-family: 'Noto Sans JP', sans-serif; margin: 0; }}
  .box {{ background: #fff; border: 1px solid #e2dbd3; border-radius: 10px; padding: 40px 36px; width: 320px; text-align: center; }}
  h2 {{ font-size: 16px; font-weight: 400; color: #3d3530; margin-bottom: 24px; letter-spacing: 1px; }}
  input {{ width: 100%; padding: 10px 14px; border: 1px solid #e2dbd3; border-radius: 6px; font-size: 14px; margin-bottom: 14px; box-sizing: border-box; }}
  button {{ width: 100%; padding: 10px; background: #7a9e6e; border: none; border-radius: 6px; color: #fff; font-size: 14px; cursor: pointer; font-family: inherit; }}
  button:hover {{ background: #5c7d52; }}
  .err {{ color: #b56b5e; font-size: 12px; margin-top: 10px; }}
</style>
</head>
<body>
<div class="box">
  <h2>Maki &amp; Co. Dashboard</h2>
  <form method="POST" action="/dashboard">
    <input type="password" name="pw" placeholder="パスワード" autofocus>
    <button type="submit">ログイン</button>
    {err}
  </form>
</div>
</body>
</html>'''

    try:
        events = get_upcoming_events(days=7)
    except Exception:
        events = []

    now = datetime.datetime.now(JST)
    today = now.date()
    weekdays = ["月", "火", "水", "木", "金", "土", "日"]
    today_str = f"{today.month}月{today.day}日（{weekdays[today.weekday()]}）"

    today_events = []
    future_events_by_date = {}
    for event in events:
        start_raw = event['start'].get('date') or event['start'].get('dateTime', '')[:10]
        try:
            evt_date = datetime.date.fromisoformat(start_raw)
        except Exception:
            continue
        title = event.get('summary', '（タイトルなし）')
        if evt_date == today:
            if 'T' in event['start'].get('dateTime', ''):
                dt = datetime.datetime.fromisoformat(event['start']['dateTime']).astimezone(JST)
                today_events.append(f"{dt.strftime('%H:%M')} {title}")
            else:
                today_events.append(title)
        elif evt_date > today:
            if evt_date not in future_events_by_date:
                future_events_by_date[evt_date] = []
            future_events_by_date[evt_date].append(title)

    today_html = ''.join(f'<li>{e}</li>' for e in today_events) or '<li class="empty">予定なし</li>'
    future_html = ''
    for d in sorted(future_events_by_date.keys()):
        future_html += f'<div class="date-header">{d.month}月{d.day}日（{weekdays[d.weekday()]}）</div>'
        for t in future_events_by_date[d]:
            future_html += f'<div class="event-item">📌 {t}</div>'
    if not future_html:
        future_html = '<div class="empty">今後7日間の予定なし</div>'

    link_groups = [
        ('Blog', [
            ('WordPress（薬膳）', 'https://foodmakehealth.com/wp-admin/', False),
            ('WordPress（セキスイ）', 'https://order-sekisui.com/wp-admin/', False),
            ('Notion', 'https://notion.so/', True),
        ]),
        ('Medical', [
            ('CareNet', 'https://www.carenet.com/', False),
            ('MedPeer', 'https://medpeer.jp/keymessage/list/point3', False),
        ]),
        ('Sales', [
            ('メルハント', 'https://auction2024.com/admin/main.php', True),
            ('物販ブースター', 'https://buppan-booster.com/list-sell', True),
            ('メルカリ', 'https://jp.mercari.com/', False),
            ('eBay出品中', 'https://www.ebay.com/mys/active', True),
        ]),
        ('まきの会社', [
            ('まるちゃんワールド', 'https://maki-hisho.onrender.com/game', True),
            ('会社組織図', 'https://maki-hisho.onrender.com/office', False),
            ('会社LP', 'https://maki-hisho.onrender.com/company', False),
        ]),
    ]
    links_rows = ''
    for label, items in link_groups:
        btns = ''.join(
            f'<a href="{url}" target="_blank" class="link-btn {"link-btn-hl" if hl else ""}">{name}</a>'
            for name, url, hl in items
        )
        links_rows += f'<div class="links-row"><span class="links-category">{label}</span>{btns}</div>'

    return f'''<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<meta http-equiv="refresh" content="3600">
<title>Maki &amp; Co. | Dashboard</title>
<link href="https://fonts.googleapis.com/css2?family=Noto+Sans+JP:wght@300;400;500&family=Lato:wght@300;400&display=swap" rel="stylesheet">
<style>
  :root {{
    --bg: #f4f0eb; --surface: #faf8f5; --card: #ffffff;
    --border: #e2dbd3; --border-light: #ede8e2;
    --text: #3d3530; --text-sub: #7a6f68; --muted: #b0a89f;
    --accent: #7a9e6e; --accent-dark: #5c7d52; --accent-red: #b56b5e;
    --link-bg: #f0ece7;
  }}
  * {{ margin: 0; padding: 0; box-sizing: border-box; }}
  body {{ background: var(--bg); color: var(--text); font-family: 'Noto Sans JP', sans-serif; font-weight: 300; min-height: 100vh; font-size: 14px; line-height: 1.7; }}
  header {{ background: var(--surface); border-bottom: 1px solid var(--border); padding: 20px 40px; display: flex; justify-content: space-between; align-items: center; }}
  .logo {{ font-family: 'Lato', sans-serif; font-size: 18px; font-weight: 300; color: var(--text); letter-spacing: 3px; text-transform: uppercase; }}
  .logo span {{ display: block; font-size: 10px; color: var(--muted); letter-spacing: 2px; margin-top: 2px; }}
  .date-display {{ text-align: right; }}
  .date-display strong {{ display: block; font-size: 18px; font-weight: 400; }}
  .date-display small {{ font-size: 11px; color: var(--muted); letter-spacing: 1px; }}
  .links-bar {{ background: var(--surface); border-bottom: 2px solid var(--border); padding: 0 40px; }}
  .links-row {{ display: flex; align-items: center; flex-wrap: wrap; padding: 7px 0; border-bottom: 1px solid var(--border-light); gap: 5px; }}
  .links-row:last-child {{ border-bottom: none; }}
  .links-category {{ font-size: 10px; letter-spacing: 2px; text-transform: uppercase; color: var(--muted); min-width: 60px; margin-right: 6px; flex-shrink: 0; }}
  .link-btn {{ display: inline-block; background: var(--link-bg); border: 1px solid var(--border); color: var(--text-sub); padding: 3px 10px; border-radius: 4px; text-decoration: none; font-size: 11px; transition: background 0.15s, color 0.15s; }}
  .link-btn:hover {{ background: var(--accent); border-color: var(--accent); color: #fff; }}
  .link-btn-hl {{ background: #fff3e0; border-color: #ff8c00; color: #d05000; font-weight: 600; }}
  .link-btn-hl:hover {{ background: #ff8c00; border-color: #ff8c00; color: #fff; }}
  main {{ padding: 28px 40px; }}
  .grid {{ display: grid; grid-template-columns: 1fr 1fr; gap: 18px; }}
  .card {{ background: var(--card); border: 1px solid var(--border); border-radius: 8px; overflow: hidden; }}
  .card-header {{ background: var(--link-bg); border-bottom: 1px solid var(--border); padding: 10px 18px; font-size: 11px; letter-spacing: 2px; text-transform: uppercase; color: var(--text-sub); font-weight: 400; }}
  .card-body {{ padding: 16px 18px; }}
  ul {{ list-style: none; }}
  li {{ padding: 6px 0; border-bottom: 1px solid var(--border-light); font-size: 13px; }}
  li:last-child {{ border-bottom: none; }}
  .empty {{ color: var(--muted); font-style: italic; }}
  .date-header {{ font-size: 11px; font-weight: 500; color: var(--accent-dark); margin: 12px 0 4px; letter-spacing: 1px; }}
  .event-item {{ padding: 4px 0 4px 10px; font-size: 13px; border-left: 3px solid var(--accent); margin-left: 2px; border-bottom: 1px solid var(--border-light); }}
  .updated {{ text-align: right; font-size: 11px; color: var(--muted); padding: 12px 40px; border-top: 1px solid var(--border); background: var(--surface); }}
  @media (max-width: 760px) {{
    header, .links-bar, main, .updated {{ padding-left: 16px; padding-right: 16px; }}
    .grid {{ grid-template-columns: 1fr; }}
    header {{ flex-direction: column; align-items: flex-start; gap: 8px; }}
  }}
</style>
</head>
<body>
<header>
  <div class="logo">Maki &amp; Co.<span>Daily Operations Dashboard</span></div>
  <div class="date-display"><strong>{today_str}</strong><small>Today</small></div>
</header>
<div class="links-bar">{links_rows}</div>
<main>
  <div class="grid">
    <div class="card">
      <div class="card-header">Today&#x27;s Schedule</div>
      <div class="card-body"><ul>{today_html}</ul></div>
    </div>
    <div class="card">
      <div class="card-header">Upcoming — Next 7 Days</div>
      <div class="card-body">{future_html}</div>
    </div>
  </div>
</main>
<div class="updated">最終更新: {now.strftime('%Y-%m-%d %H:%M')}</div>
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

@app.route('/threads-guide')
def threads_guide():
    return send_from_directory('.', 'threads_guide.html')

@app.route('/check-kvision')
def check_kvision():
    """KVISION X APIキーの設定状況を確認"""
    keys = {
        'KVISION_X_API_KEY': os.environ.get('KVISION_X_API_KEY', ''),
        'KVISION_X_API_SECRET': os.environ.get('KVISION_X_API_SECRET', ''),
        'KVISION_X_ACCESS_TOKEN': os.environ.get('KVISION_X_ACCESS_TOKEN', ''),
        'KVISION_X_ACCESS_TOKEN_SECRET': os.environ.get('KVISION_X_ACCESS_TOKEN_SECRET', ''),
    }
    result = []
    for k, v in keys.items():
        status = f'✅ 設定済み（{v[:6]}...）' if v.strip() else '❌ 未設定'
        result.append(f'{k}: {status}')
    return '<br>'.join(result)



@app.route('/post-kvision-now')
def post_kvision_now():
    """今すぐ@kvision_mにアフィスレッドを1本送る（手動テスト用）"""
    import random
    slot = random.randint(0, len(TRAVEL_GENRES) - 1)
    client = _get_kvision_x_client()
    if not client:
        return '❌ KVISION X APIキーが未設定です。Renderの環境変数を確認してください。', 500
    try:
        post_kvision_travel_aff(slot)
        return f'✅ @kvision_m スレッド投稿完了！ジャンル：{TRAVEL_GENRES[slot]["name"]}　Xアプリで確認してください。'
    except Exception as e:
        return f'❌ エラー: {e}', 500


@app.route('/post-kvision-morning-now')
def post_kvision_morning_now():
    """今すぐ@kvision_mに朝つぶやきを送る（手動テスト用）"""
    try:
        post_kvision_morning_tweet()
        return '✅ @kvision_m 朝つぶやき投稿完了！Xアプリで確認してください。'
    except Exception as e:
        return f'❌ エラー: {e}', 500


@app.route('/post-kvision-card-now')
def post_kvision_card_now():
    """今すぐ楽天カード誘導ツイートを送る（手動テスト用）"""
    try:
        post_kvision_card_tweet()
        return '✅ @kvision_m 楽天カード誘導ツイート完了！（スレッド形式・URLランダム）'
    except Exception as e:
        return f'❌ エラー: {e}', 500


@app.route('/test-line-send')
def test_line_send():
    """LINEにテストメッセージを送信して動作確認"""
    try:
        user_id = os.environ.get('LINE_USER_ID', '')
        if not user_id:
            return '❌ LINE_USER_ID が未設定', 500
        line_bot_api.push_message(user_id, TextSendMessage(text='🔔 LINEテスト送信成功！こはるままエンジンからのテストメッセージです。'))
        return f'✅ LINE送信成功（宛先: {user_id[:8]}...）'
    except Exception as e:
        return f'❌ LINE送信エラー: {e}', 500


@app.route('/koharu-stock-status')
def koharu_stock_status():
    """こはるまま承認待ち・承認済みストックの状態確認"""
    import json as _json
    pending_path  = '/tmp/koharu_stock_pending.json'
    approved_path = '/tmp/koharu_stock_approved.json'
    def _read(path):
        try:
            if os.path.exists(path):
                with open(path, encoding='utf-8') as f:
                    return _json.load(f)
        except Exception:
            pass
        return {}
    pending  = _read(pending_path)
    approved = _read(approved_path)
    p_posts  = pending.get('posts', [])
    a_posts  = approved.get('posts', [])
    return jsonify({
        'pending': {
            'count':        len(p_posts),
            'created_at':   pending.get('created_at'),
            'weekly_theme': pending.get('weekly_theme'),
            'preview': [{'type': p.get('type'), 'body': p.get('body','')[:50], 'score': p.get('score')} for p in p_posts[:5]],
        },
        'approved': {
            'count':    len(a_posts),
            'unposted': len([p for p in a_posts if not p.get('posted')]),
        },
    })


@app.route('/koharu-engine-writer-debug')
def koharu_engine_writer_debug():
    """ライターをフォアグラウンドで実行してエラーを直接確認（デバッグ用）"""
    import traceback, io, sys
    buf = io.StringIO()
    old_stdout = sys.stdout
    sys.stdout = buf
    try:
        koharu_writer()
        output = buf.getvalue()
        sys.stdout = old_stdout
        return f'<pre>✅ 完了\n\n{output}</pre>'
    except Exception as e:
        output = buf.getvalue()
        sys.stdout = old_stdout
        return f'<pre>❌ エラー: {e}\n\n{traceback.format_exc()}\n\nログ:\n{output}</pre>', 500


@app.route('/koharu-engine-writer-now')
def koharu_engine_writer_now():
    """こはるままエンジン：②ライターを今すぐ実行（手動テスト）"""
    try:
        import threading
        threading.Thread(target=koharu_writer, daemon=True).start()
        return '✅ こはるままライター起動！数分後にLINEに投稿案が届きます。'
    except Exception as e:
        return f'❌ {e}', 500


@app.route('/koharu-engine-researcher-now')
def koharu_engine_researcher_now():
    """こはるままエンジン：①リサーチャーを今すぐ実行（手動テスト）"""
    try:
        koharu_researcher()
        return '✅ こはるままリサーチャー完了！'
    except Exception as e:
        return f'❌ {e}', 500


@app.route('/koharu-engine-analyst-now')
def koharu_engine_analyst_now():
    """こはるままエンジン：⑤アナリストを今すぐ実行（手動テスト）"""
    try:
        import threading
        threading.Thread(target=koharu_analyst, daemon=True).start()
        return '✅ こはるままアナリスト起動！数分後にLINEにレポートが届きます。'
    except Exception as e:
        return f'❌ {e}', 500


@app.route('/mako-engine-writer-now')
def mako_engine_writer_now():
    """MAKOエンジン：②ライターを今すぐ実行（手動テスト）"""
    try:
        import threading
        threading.Thread(target=mako_writer, daemon=True).start()
        return '✅ MAKOライター起動！数分後にLINEに投稿案が届きます。'
    except Exception as e:
        return f'❌ {e}', 500


@app.route('/mako-engine-researcher-now')
def mako_engine_researcher_now():
    """MAKOエンジン：①リサーチャーを今すぐ実行（手動テスト）"""
    try:
        mako_researcher()
        return '✅ MAKOリサーチャー完了！'
    except Exception as e:
        return f'❌ {e}', 500


@app.route('/mako-stock-status')
def mako_stock_status():
    """MAKO承認待ち・承認済みストックの状態確認"""
    import json as _json
    pending_path  = '/tmp/mako_stock_pending.json'
    approved_path = '/tmp/mako_stock_approved.json'
    def _read(path):
        try:
            with open(path, 'r', encoding='utf-8') as f:
                return _json.load(f)
        except Exception:
            return None
    pending  = _read(pending_path)
    approved = _read(approved_path)
    return _json.dumps({
        'pending':  {'count': len((pending  or {}).get('posts', [])), 'created_at': (pending  or {}).get('created_at')},
        'approved': {'count': len((approved or {}).get('posts', [])), 'created_at': (approved or {}).get('created_at')},
    }, ensure_ascii=False, indent=2)


@app.route('/koharu-writer-log')
def koharu_writer_log():
    """こはるままライターの直近エラーログを表示"""
    try:
        with open('/tmp/koharu_writer_error.log', 'r', encoding='utf-8') as f:
            return f'<pre>{f.read()}</pre>'
    except FileNotFoundError:
        return '✅ エラーログなし（ライターは正常終了しています）'
    except Exception as e:
        return f'❌ ログ読み取りエラー: {e}', 500


@app.route('/post-koharu-threads-now')
def post_koharu_threads_now():
    """今すぐこはるままのThreadsにアフィ投稿を送る（手動テスト用）"""
    try:
        post_koharu_threads_aff_auto()
        return '✅ こはるまま Threads アフィ投稿完了！Threadsアプリで確認してください。'
    except Exception as e:
        return f'❌ エラー: {e}', 500


@app.route('/post-koharu-threads-morning-now')
def post_koharu_threads_morning_now():
    """今すぐこはるままのThreadsに朝つぶやきを送る（手動テスト用）"""
    try:
        post_koharu_threads_morning()
        return '✅ こはるまま Threads 朝つぶやき投稿完了！Threadsアプリで確認してください。'
    except Exception as e:
        return f'❌ エラー: {e}', 500


@app.route('/post-mako-threads-now')
def post_mako_threads_now():
    """今すぐMAKOのThreadsにアフィ投稿を送る（手動テスト用）"""
    try:
        post_mako_threads_aff_auto()
        return '✅ MAKO Threads アフィ投稿完了！Threadsアプリで確認してください。'
    except Exception as e:
        return f'❌ エラー: {e}', 500


@app.route('/post-mako-threads-morning-now')
def post_mako_threads_morning_now():
    """今すぐMAKOのThreadsに朝の共感投稿を送る（手動テスト用）"""
    try:
        post_mako_threads_morning()
        return '✅ MAKO Threads 朝投稿完了！Threadsアプリで確認してください。'
    except Exception as e:
        return f'❌ エラー: {e}', 500


@app.route('/post-threads-now')
def post_threads_now():
    """今すぐThreadsに楽天アフィ投稿を1本送る（手動トリガー）"""
    import random
    slot = random.randint(0, len(ROOM_GENRES) - 1)
    try:
        send_room_suggestion_slot(slot)
        return f'✅ Threads投稿完了！ジャンル：{ROOM_GENRES[slot]["name"]}　Threadsアプリで確認してください。'
    except Exception as e:
        return f'❌ エラー: {e}', 500

@app.route('/newsletter-summary', methods=['POST'])
def newsletter_summary():
    """GASからメルマガ要約を受け取り、LINEに送信＋セッション保存"""
    data = request.json or {}
    secret = data.get('secret')
    if secret != os.environ.get('NOTIFY_SECRET', ''):
        return jsonify({'error': 'Unauthorized'}), 401

    summary = data.get('summary', '')
    emails = data.get('emails', [])
    user_id = os.environ.get('LINE_USER_ID')

    sessions = load_newsletter_sessions()
    sessions[user_id] = {
        'emails': emails,
        'timestamp': datetime.datetime.now(JST).isoformat()
    }
    save_newsletter_sessions(sessions)

    message = f"📧 今週のメルマガまとめ（{len(emails)}件）\n\n{summary}\n\n━━━━━━━━━━\n保存したいものは番号で返信してください\n例：「①③保存して」"
    line_bot_api.push_message(user_id, TextSendMessage(text=message))
    return jsonify({'status': 'ok', 'count': len(emails)})


@app.route('/add-task', methods=['POST'])
def add_task():
    """Power Automateからメール検知時に呼ばれる：LINE通知 + Notionにタスク追加"""
    data = request.json or {}
    secret = data.get('secret')
    if secret != os.environ.get('NOTIFY_SECRET', ''):
        return jsonify({'error': 'Unauthorized'}), 401

    line_message = data.get('message', '')
    task_title = data.get('task', '')
    results = {}

    # LINEに通知
    if line_message:
        try:
            line_bot_api.push_message(
                os.environ.get('LINE_USER_ID'),
                TextSendMessage(text=line_message)
            )
            results['line'] = 'sent'
        except Exception as e:
            results['line_error'] = str(e)

    # Notionの「今週やること」にto_doを追加
    if task_title:
        try:
            notion_token = os.environ.get('NOTION_TOKEN', '')
            notion_headers = {
                "Authorization": f"Bearer {notion_token}",
                "Notion-Version": "2022-06-28",
                "Content-Type": "application/json"
            }
            body = {
                "after": "323f8d6d-41de-809d-9e98-f9a5da8556a8",  # 「今週やること」heading直後
                "children": [{
                    "object": "block",
                    "type": "to_do",
                    "to_do": {
                        "rich_text": [{"type": "text", "text": {"content": task_title}}],
                        "checked": False
                    }
                }]
            }
            r = requests.patch(
                "https://api.notion.com/v1/blocks/323f8d6d41de80dea66efad500806f69/children",
                headers=notion_headers,
                json=body
            )
            results['notion'] = r.status_code
        except Exception as e:
            results['notion_error'] = str(e)

    return jsonify(results)


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

# ===== eBay 売上管理ダッシュボード =====
EBAY_MGMT_SHEET_ID   = "1pPAVYxeETPq6VVtg7Jd7eapXZf8lgTttndRN6Cd4wqI"
EBAY_MGMT_SHEET_NAME = "売上管理"
EBAY_MGMT_HEADERS    = [
    "item_id", "order_id", "title", "sale_price_usd", "sale_date",
    "purchase_price_jpy", "shipping_method", "shipping_cost_jpy",
    "fx_rate", "ebay_fee_jpy", "profit_jpy", "completed", "notes",
]

def get_ebay_user_token():
    import base64, urllib.parse
    refresh = os.environ.get('EBAY_REFRESH_TOKEN', '')
    app_id  = os.environ.get('EBAY_APP_ID', '')
    cert_id = os.environ.get('EBAY_CERT_ID', '')
    if not (refresh and app_id and cert_id):
        return None
    creds = base64.b64encode(f"{app_id}:{cert_id}".encode()).decode()
    resp  = requests.post(
        "https://api.ebay.com/identity/v1/oauth2/token",
        headers={"Authorization": f"Basic {creds}", "Content-Type": "application/x-www-form-urlencoded"},
        data=f"grant_type=refresh_token&refresh_token={urllib.parse.quote(refresh)}",
        timeout=10,
    )
    return resp.json().get("access_token")

def get_sheets_creds():
    """google-authライブラリを使わず直接HTTPでアクセストークンを取得してCredentialsを返す"""
    from google.oauth2.credentials import Credentials as GCreds
    raw = os.environ.get('GOOGLE_SHEETS_TOKEN', '')
    try:
        import re
        clean = re.sub(r'[\x00-\x1f\x7f]', '', raw) if raw else ''
        data = json.loads(clean) if clean else json.load(open('token_sheets.json', encoding='utf-8'))
        resp = requests.post(
            'https://oauth2.googleapis.com/token',
            data={
                'client_id':     data['client_id'],
                'client_secret': data['client_secret'],
                'refresh_token': data['refresh_token'],
                'grant_type':    'refresh_token',
            },
            timeout=10,
        )
        result = resp.json()
        token = result.get('access_token')
        if not token:
            print(f"[get_sheets_creds] token取得失敗: {result}")
            return None
        return GCreds(token=token)
    except Exception as e:
        print(f"[get_sheets_creds error] {e}")
        return None

def ensure_ebay_mgmt_sheet(service):
    meta   = service.spreadsheets().get(spreadsheetId=EBAY_MGMT_SHEET_ID).execute()
    titles = [s["properties"]["title"] for s in meta["sheets"]]
    if EBAY_MGMT_SHEET_NAME not in titles:
        service.spreadsheets().batchUpdate(
            spreadsheetId=EBAY_MGMT_SHEET_ID,
            body={"requests": [{"addSheet": {"properties": {"title": EBAY_MGMT_SHEET_NAME}}}]},
        ).execute()
        service.spreadsheets().values().update(
            spreadsheetId=EBAY_MGMT_SHEET_ID,
            range=f"{EBAY_MGMT_SHEET_NAME}!A1",
            valueInputOption="RAW",
            body={"values": [EBAY_MGMT_HEADERS]},
        ).execute()

@app.route('/ebay-dashboard')
def ebay_dashboard_page():
    return send_from_directory('.', 'ebay_dashboard.html')

@app.route('/api/ebay/debug')
def ebay_debug():
    import traceback
    result = {
        'GOOGLE_SHEETS_TOKEN_set': bool(os.environ.get('GOOGLE_SHEETS_TOKEN', '')),
        'EBAY_REFRESH_TOKEN_set':  bool(os.environ.get('EBAY_REFRESH_TOKEN', '')),
        'EBAY_APP_ID_set':         bool(os.environ.get('EBAY_APP_ID', '')),
        'EBAY_CERT_ID_set':        bool(os.environ.get('EBAY_CERT_ID', '')),
    }
    # Sheetsの詳細テスト（直接HTTPリクエスト）
    try:
        import re
        raw = os.environ.get('GOOGLE_SHEETS_TOKEN', '')
        clean = re.sub(r'[\x00-\x1f\x7f]', '', raw)
        data = json.loads(clean)
        result['token_keys'] = list(data.keys())
        result['refresh_token_prefix'] = data.get('refresh_token', '')[:15]
        resp = requests.post(
            'https://oauth2.googleapis.com/token',
            data={
                'client_id':     data['client_id'],
                'client_secret': data['client_secret'],
                'refresh_token': data['refresh_token'],
                'grant_type':    'refresh_token',
            },
            timeout=10,
        )
        r = resp.json()
        result['google_token_response'] = {k: v for k, v in r.items() if k != 'access_token'}
        result['sheets_creds_ok'] = 'access_token' in r
    except Exception as e:
        result['sheets_creds_ok'] = False
        result['sheets_error'] = traceback.format_exc()
    try:
        token = get_ebay_user_token()
        result['ebay_token_ok'] = token is not None
    except Exception as e:
        result['ebay_token_ok'] = False
        result['ebay_token_error'] = str(e)
    return jsonify(result)

@app.route('/api/ebay/data')
def ebay_data_api():
    try:
        creds = get_sheets_creds()
        if not creds:
            return jsonify({"error": "Google Sheets認証エラー"}), 500
        service = build("sheets", "v4", credentials=creds)
        ensure_ebay_mgmt_sheet(service)
        result = service.spreadsheets().values().get(
            spreadsheetId=EBAY_MGMT_SHEET_ID,
            range=f"{EBAY_MGMT_SHEET_NAME}!A2:M1000",
            valueRenderOption="UNFORMATTED_VALUE",
        ).execute()
        rows   = result.get("values", [])
        orders = []
        for i, row in enumerate(rows):
            obj = {h: (row[j] if j < len(row) else "") for j, h in enumerate(EBAY_MGMT_HEADERS)}
            obj["row_num"] = i + 2
            orders.append(obj)
        return jsonify({"orders": orders})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/ebay/update', methods=['POST'])
def ebay_update_api():
    try:
        data          = request.json
        row_num       = data.get("row_num")
        sale_usd      = float(data.get("sale_price_usd", 0))
        purchase      = float(data.get("purchase_price_jpy", 0))
        ship_method   = data.get("shipping_method", "CPASS")
        ship_cost     = float(data.get("shipping_cost_jpy", 0))
        fx            = float(data.get("fx_rate", 155))
        notes         = data.get("notes", "")
        sales_jpy     = round(sale_usd * fx)
        ebay_fee      = round(sales_jpy * 0.1325)
        profit        = sales_jpy - round(purchase) - round(ship_cost) - ebay_fee

        creds = get_sheets_creds()
        if not creds:
            return jsonify({"error": "Google Sheets認証エラー"}), 500
        service = build("sheets", "v4", credentials=creds)
        service.spreadsheets().values().update(
            spreadsheetId=EBAY_MGMT_SHEET_ID,
            range=f"{EBAY_MGMT_SHEET_NAME}!F{row_num}:M{row_num}",
            valueInputOption="RAW",
            body={"values": [[purchase, ship_method, ship_cost, fx, ebay_fee, profit, "TRUE", notes]]},
        ).execute()
        return jsonify({"success": True, "profit": profit, "ebay_fee": ebay_fee})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/ebay/sync')
def ebay_sync_api():
    try:
        token = get_ebay_user_token()
        if not token:
            return jsonify({"error": "EBAY_REFRESH_TOKENが未設定です。Renderの環境変数を確認してください"}), 500

        resp = requests.get(
            "https://api.ebay.com/sell/fulfillment/v1/order",
            headers={"Authorization": f"Bearer {token}"},
            params={"limit": 200, "filter": "creationdate:[2026-01-01T00:00:00.000Z..]"},
            timeout=20,
        )
        ebay_orders = resp.json().get("orders", [])

        creds = get_sheets_creds()
        if not creds:
            return jsonify({"error": "Google Sheets認証エラー"}), 500
        service = build("sheets", "v4", credentials=creds)
        ensure_ebay_mgmt_sheet(service)

        existing = service.spreadsheets().values().get(
            spreadsheetId=EBAY_MGMT_SHEET_ID,
            range=f"{EBAY_MGMT_SHEET_NAME}!B2:B1000",
        ).execute()
        existing_ids = {row[0] for row in existing.get("values", []) if row}

        new_rows = []
        for order in ebay_orders:
            order_id = order.get("orderId", "")
            if order_id in existing_ids:
                continue
            sale_date   = (order.get("creationDate") or "")[:10]
            total_price = (order.get("pricingSummary") or {}).get("total", {}).get("value", "0")
            for item in order.get("lineItems", []):
                new_rows.append([
                    item.get("legacyItemId", ""),
                    order_id,
                    item.get("title", ""),
                    total_price,
                    sale_date,
                    "", "", "", "155", "", "", "FALSE", "",
                ])

        if new_rows:
            service.spreadsheets().values().append(
                spreadsheetId=EBAY_MGMT_SHEET_ID,
                range=f"{EBAY_MGMT_SHEET_NAME}!A2",
                valueInputOption="RAW",
                insertDataOption="INSERT_ROWS",
                body={"values": new_rows},
            ).execute()

        return jsonify({"added": len(new_rows), "fetched": len(ebay_orders)})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/note-roadmap')
def note_roadmap():
    import markdown
    with open('note_roadmap_pome.md', encoding='utf-8') as f:
        content = f.read()
    html_body = markdown.markdown(content, extensions=['tables'])
    html = f"""<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>noteロードマップまとめ</title>
<style>
  body {{ font-family: -apple-system, BlinkMacSystemFont, 'Hiragino Sans', sans-serif; max-width: 800px; margin: 40px auto; padding: 0 20px; line-height: 1.8; color: #333; }}
  h1 {{ color: #2c3e50; border-bottom: 3px solid #e74c3c; padding-bottom: 10px; }}
  h2 {{ color: #2c3e50; border-left: 4px solid #e74c3c; padding-left: 12px; margin-top: 40px; }}
  h3 {{ color: #555; }}
  table {{ border-collapse: collapse; width: 100%; margin: 16px 0; }}
  th {{ background: #2c3e50; color: white; padding: 10px; text-align: left; }}
  td {{ border: 1px solid #ddd; padding: 10px; }}
  tr:nth-child(even) {{ background: #f9f9f9; }}
  code {{ background: #f4f4f4; padding: 2px 6px; border-radius: 3px; font-family: monospace; }}
  pre {{ background: #f4f4f4; padding: 16px; border-radius: 6px; overflow-x: auto; }}
  blockquote {{ border-left: 4px solid #e74c3c; margin: 0; padding-left: 16px; color: #666; }}
  ul, ol {{ padding-left: 24px; }}
  li {{ margin: 6px 0; }}
</style>
</head>
<body>
{html_body}
</body>
</html>"""
    return html

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


@app.route('/check-threads-app')
def check_threads_app():
    """THREADS_APP_IDが正しく設定されているか確認するデバッグ用"""
    app_id = os.environ.get('THREADS_APP_ID', '')
    app_secret = os.environ.get('THREADS_APP_SECRET', '')
    return {
        'THREADS_APP_ID_先頭6桁': app_id[:6] + '...' if len(app_id) > 6 else f'({len(app_id)}文字)',
        'THREADS_APP_ID_桁数': len(app_id),
        'THREADS_APP_ID_数字のみか': app_id.isdigit(),
        'THREADS_APP_SECRET_先頭4文字': app_secret[:4] + '...' if len(app_secret) > 4 else f'({len(app_secret)}文字)',
    }


@app.route('/auth/threads')
def auth_threads():
    app_id = os.environ.get('THREADS_APP_ID', '')
    if not app_id:
        return 'THREADS_APP_ID が設定されていません。Renderに設定してください。', 400
    redirect_uri = 'https://maki-hisho.onrender.com/auth/threads/callback'
    auth_url = (
        f"https://www.threads.net/oauth/authorize"
        f"?client_id={app_id}"
        f"&redirect_uri={redirect_uri}"
        f"&scope=threads_basic,threads_content_publish"
        f"&response_type=code"
    )
    return f'''<html><body>
<h2>Threads認証</h2>
<p><a href="{auth_url}" style="font-size:20px;padding:10px;background:#000;color:#fff;text-decoration:none;border-radius:6px;">
Threadsで認証する</a></p>
</body></html>'''


@app.route('/auth/threads/callback')
def auth_threads_callback():
    code = request.args.get('code')
    if not code:
        return f'エラー: codeが取得できませんでした。{request.args}', 400
    app_id = os.environ.get('THREADS_APP_ID')
    app_secret = os.environ.get('THREADS_APP_SECRET')
    redirect_uri = 'https://maki-hisho.onrender.com/auth/threads/callback'
    res = requests.post(
        'https://graph.threads.net/oauth/access_token',
        data={
            'client_id': app_id,
            'client_secret': app_secret,
            'grant_type': 'authorization_code',
            'redirect_uri': redirect_uri,
            'code': code,
        },
        timeout=15
    )
    if res.status_code != 200:
        return f'短期トークン取得エラー: {res.status_code} {res.text}', 400
    short_token = res.json().get('access_token')
    long_res = requests.get(
        'https://graph.threads.net/access_token',
        params={
            'grant_type': 'th_exchange_token',
            'client_secret': app_secret,
            'access_token': short_token,
        },
        timeout=15
    )
    if long_res.status_code != 200:
        return f'長期トークン取得エラー: {long_res.status_code} {long_res.text}', 400
    long_token = long_res.json().get('access_token')
    user_id_val = res.json().get('user_id', '（取得できませんでした）')
    account = request.args.get('account', 'koharu')
    if account == 'mako':
        token_key = 'MAKO_THREADS_ACCESS_TOKEN'
        uid_key = 'MAKO_THREADS_USER_ID'
        label = 'MAKO'
    else:
        token_key = 'KOHARU_THREADS_ACCESS_TOKEN'
        uid_key = 'KOHARU_THREADS_USER_ID'
        label = 'こはるまま'
    return f'''<html><body>
<h2>✅ {label} Threads認証成功！</h2>
<p>以下2つをRenderの環境変数にコピペしてください：</p>
<p><b>{token_key}:</b><br>
<textarea rows="4" cols="80">{long_token}</textarea></p>
<p><b>{uid_key}:</b><br>
<textarea rows="1" cols="80">{user_id_val}</textarea></p>
<p><small>トークンは60日間有効。期限切れになったら /auth/threads?account={account} に再アクセスしてください。</small></p>
</body></html>'''


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
             "action": {"type": "message", "text": "roomタグ"}},
            {"bounds": {"x": 1667, "y": 0,   "width": 833, "height": 421},
             "action": {"type": "uri", "uri": "https://maki-hisho.onrender.com/ebay-calc"}},
            {"bounds": {"x": 0,    "y": 421, "width": 833, "height": 422},
             "action": {"type": "message", "text": "睡眠記事"}},
            {"bounds": {"x": 833,  "y": 421, "width": 834, "height": 422},
             "action": {"type": "message", "text": "セキスイ記事"}},
            {"bounds": {"x": 1667, "y": 421, "width": 833, "height": 422},
             "action": {"type": "uri", "uri": "https://maki-hisho.onrender.com/ebay-dashboard"}},
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
    return 'Disabled', 410


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

    # 楽天Roomタグセッションチェック
    room_tag_sessions = load_room_tag_sessions()
    if user_id in room_tag_sessions and room_tag_sessions[user_id] == 'waiting':
        del room_tag_sessions[user_id]
        save_room_tag_sessions(room_tag_sessions)
        try:
            tags = generate_room_tags(image_base64=image_base64, media_type=media_type)
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text=f"🏷️ 楽天Roomタグ\n\n{tags}"))
        except Exception as e:
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text=f"タグ生成エラー: {str(e)[:100]}"))
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
                        'text': f"""このチラシやプリントから全てのイベント・日程情報を抽出してください。
複数の日程がある場合は全て抽出してください。
今日の日付: {datetime.datetime.now(JST).strftime('%Y-%m-%d')}
年が書かれていない日付は今日の年（{datetime.datetime.now(JST).year}年）を使ってください。ただし、今日の日付より前になる場合は翌年にしてください。
以下のJSON配列形式のみ返してください（情報がない場合はnullにしてください）：
[
  {{
    "title": "イベント名",
    "date": "YYYY-MM-DD",
    "start_time": "HH:MM",
    "end_time": "HH:MM",
    "location": "場所",
    "description": "その他メモ",
    "application_start": "YYYY-MM-DD",
    "application_deadline": "YYYY-MM-DD"
  }}
]
application_startは申込開始日・受付開始日・予約開始日などの日付です。ない場合はnullにしてください。
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
            if ev.get('application_start'):
                msg += f"　🟢 申込開始: {ev['application_start']}\n"
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


def _sanitize_text(text: str) -> str:
    # 孤立サロゲート文字を除去（Anthropic APIのJSON serialization失敗を防ぐ）
    return text.encode('utf-16', 'surrogatepass').decode('utf-16', errors='replace')


@handler.add(MessageEvent, message=TextMessage)
def handle_message(event):
    user_message = _sanitize_text(event.message.text)
    user_id = event.source.user_id

    # こはるまま投稿承認コマンド
    if user_message.startswith('こはるまま'):
        if koharu_handle_approval(user_message):
            return
        # 未対応の場合はそのまま通常処理へ

    # MAKO投稿承認コマンド
    if user_message.upper().startswith('MAKO') or user_message.startswith('ＭＡＫＯ') or user_message.startswith('ｍａｋｏ'):
        if handle_mako_approval(user_message):
            return
        # 未対応の場合はそのまま通常処理へ

    # メルマガ保存コマンド（「①③保存して」など）
    if '保存' in user_message and any(c in user_message for c in '①②③④⑤⑥⑦⑧⑨⑩0123456789'):
        sessions = load_newsletter_sessions()
        session = sessions.get(user_id)
        if not session:
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text='保存できるメルマガが見つかりませんでした。\nまずメルマガまとめを受け取ってから返信してください。'))
            return
        emails = session.get('emails', [])
        circle_map = {'①': 1, '②': 2, '③': 3, '④': 4, '⑤': 5, '⑥': 6, '⑦': 7, '⑧': 8, '⑨': 9, '⑩': 10}
        numbers = set()
        for char, num in circle_map.items():
            if char in user_message:
                numbers.add(num)
        import re as _re
        for m in _re.findall(r'\d+', user_message):
            n = int(m)
            if 1 <= n <= len(emails):
                numbers.add(n)
        if not numbers:
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text='番号が見つかりませんでした。\n例：「①③保存して」'))
            return
        saved = []
        circle_chars = '①②③④⑤⑥⑦⑧⑨⑩'
        for n in sorted(numbers):
            if n <= len(emails):
                save_newsletter_to_notion(emails[n - 1])
                saved.append(circle_chars[n - 1])
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=f'{"・".join(saved)}をNotionの今週やることに保存しました✅'))
        return

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
            start_count = 0

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

                app_start = extracted.get('application_start')
                if app_start:
                    start_event = {
                        'summary': f"【申込開始】{extracted.get('title') or 'イベント'}",
                        'description': '申込開始日です。忘れずに申し込みを！',
                        'start': {'date': app_start},
                        'end': {'date': app_start},
                    }
                    service.events().insert(calendarId=cal_id, body=start_event).execute()
                    start_count += 1

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
            if start_count:
                reply += f"\n🟢 申込開始日{start_count}件も登録しました！\n当日にお知らせします！"
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

    # 業務ログ即時取得
    work_log_keywords = ['業務ログ', '今日のログ', '今日の作業', '作業ログ']
    if any(kw in user_message for kw in work_log_keywords):
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text="📋 今日の業務ログを確認中です..."))
        threading.Thread(target=send_daily_work_log).start()
        return

    # 日記メモ（「メモ」改行形式 → Notionの今日の日記ページに追記）
    if user_message.startswith('メモ\n'):
        memo_text = user_message.split('\n', 1)[1].strip()
        if memo_text:
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text="📓 日記に追記中..."))
            def _add_diary():
                ok = add_diary_memo(memo_text)
                uid = os.environ.get('LINE_USER_ID', '')
                if uid:
                    msg = "✅ 日記に追記しました！" if ok else "❌ 追記に失敗しました（NOTION_TOKENを確認）"
                    line_bot_api.push_message(uid, TextSendMessage(text=msg))
            threading.Thread(target=_add_diary).start()
        else:
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text="📝 2行目にメモ内容を書いてください\n例：\nメモ\n今日の外来、更年期が多かった"))
        return

    # 勉強ノートへのメモ追加
    if user_message.startswith('メモ：') or user_message.startswith('メモ:'):
        memo_text = user_message.split('：', 1)[-1].split(':', 1)[-1].strip()
        if memo_text:
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text="✏️ 勉強ノートに追加中...2〜3分で反映されます"))
            def _add_memo():
                ok = add_study_memo(memo_text)
                uid = os.environ.get('LINE_USER_ID', '')
                if uid:
                    msg = "✅ 勉強ノートにメモを追加しました！" if ok else "❌ メモ追加に失敗しました"
                    line_bot_api.push_message(uid, TextSendMessage(text=msg))
            threading.Thread(target=_add_memo).start()
        return

    # Threadsネタ生成
    if any(kw in user_message for kw in ['スレッズネタ', 'threadsネタ', 'Threadsネタ', 'スレッドネタ']):
        try:
            import random
            genre = random.choice(['育児・子育て', '美容・スキンケア', '収納・暮らし', '睡眠・健康', '節約・お買い物'])
            prompt = (
                f"あなたは3人の子どもを育てるワーママ（医療職・副業中）です。\n"
                f"今日のThreadsネタジャンル：{genre}\n\n"
                "以下の3パターンのThreads投稿文を作ってください。\n"
                "それぞれ120字以内・ですます調NG・体言止めや口語OK・ハッシュタグなし・リアルな体験談ベースで。\n\n"
                "①【共感型】育児・生活のあるあるや気づき（共感を呼ぶ）\n"
                "②【レビュー型】買って使ってみた正直な感想（購買意欲を高める）\n"
                "③【日常型】今日あったこと・思ったこと（親しみやすさ・フォロワー獲得）\n\n"
                "出力形式：\n"
                "①（共感型）\n投稿文\n\n②（レビュー型）\n投稿文\n\n③（日常型）\n投稿文\n\n"
                "余計な説明不要。投稿文だけ出力。"
            )
            resp = anthropic_client.messages.create(
                model='claude-haiku-4-5-20251001',
                max_tokens=600,
                messages=[{'role': 'user', 'content': prompt}]
            )
            ideas = resp.content[0].text.strip()
            reply = f"🧵 今日のThreadsネタ（{genre}）\n\n{ideas}\n\n👆コピペして投稿してみて！\nいいね・コメントきたら教えてね📊"
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply))
        except Exception as e:
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text=f"ネタ生成エラー: {str(e)[:100]}"))
        return

    # 楽天Roomタグ生成
    room_tag_sessions = load_room_tag_sessions()
    if any(kw in user_message for kw in ['roomタグ', 'Roomタグ', 'ルームタグ', 'roomハッシュ']):
        keyword = None
        for sep in ['roomタグ', 'Roomタグ', 'ルームタグ', 'roomハッシュ']:
            if sep in user_message:
                rest = user_message.split(sep, 1)[1].strip()
                if rest:
                    keyword = rest
                break
        if keyword:
            try:
                tags = generate_room_tags(text=keyword)
                line_bot_api.reply_message(event.reply_token, TextSendMessage(text=f"🏷️ 楽天Roomタグ\n\n{tags}"))
            except Exception as e:
                line_bot_api.reply_message(event.reply_token, TextSendMessage(text=f"タグ生成エラー: {str(e)[:100]}"))
        else:
            room_tag_sessions[user_id] = 'waiting'
            save_room_tag_sessions(room_tag_sessions)
            line_bot_api.reply_message(event.reply_token, TextSendMessage(
                text="🏷️ 商品名を送るか、商品の写真を送ってください！\nハッシュタグを作ります📦"
            ))
        return

    if user_id in room_tag_sessions and room_tag_sessions[user_id] == 'waiting':
        del room_tag_sessions[user_id]
        save_room_tag_sessions(room_tag_sessions)
        try:
            tags = generate_room_tags(text=user_message)
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text=f"🏷️ 楽天Roomタグ\n\n{tags}"))
        except Exception as e:
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text=f"タグ生成エラー: {str(e)[:100]}"))
        return

    # 仕入れ候補 即時実行（テスト・デバッグ兼用）
    if user_message in ['仕入れ候補', '仕入れ候補テスト', '仕入れリサーチ']:
        threading.Thread(
            target=send_daily_purchase_candidates, args=(user_id,), daemon=True
        ).start()
        return

    # セラーチェック（即時）
    seller_check_prefixes = ['セラーチェック：', 'セラーチェック:', 'セラー確認：', 'セラー確認:']
    for prefix in seller_check_prefixes:
        if user_message.startswith(prefix):
            seller_name = user_message[len(prefix):].strip()
            if seller_name:
                threading.Thread(target=check_seller_now, args=(user_id, seller_name), daemon=True).start()
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
    # note下書き：セッションチェック
    note_sessions = load_note_sessions()
    if user_id in note_sessions:
        session = note_sessions[user_id]
        state = session.get('state')

        if state == 'waiting_for_note_type':
            import unicodedata
            normalized = unicodedata.normalize('NFKC', user_message)
            if any(kw in normalized for kw in ['有料', '1', '①']):
                note_sessions[user_id] = {'state': 'waiting_for_note_target', 'type': 'paid'}
                save_note_sessions(note_sessions)
                line_bot_api.reply_message(event.reply_token, TextSendMessage(
                    text="💰 有料記事ですね！\n\n【Step1】誰に届けたい記事ですか？\n\n例：「AIって何に使うかわからないワーママ」「忙しくて新しいことを始める余裕がない人」\n（「おまかせ」でもOK）"
                ))
            elif any(kw in normalized for kw in ['無料', '2', '②']):
                note_sessions[user_id] = {'state': 'waiting_for_note_target', 'type': 'free'}
                save_note_sessions(note_sessions)
                line_bot_api.reply_message(event.reply_token, TextSendMessage(
                    text="📖 無料記事ですね！\n\n【Step1】誰に届けたい記事ですか？\n\n例：「AIって何に使うかわからないワーママ」「忙しくて新しいことを始める余裕がない人」\n（「おまかせ」でもOK）"
                ))
            else:
                del note_sessions[user_id]
                save_note_sessions(note_sessions)
                line_bot_api.reply_message(event.reply_token, TextSendMessage(text="キャンセルしました。"))
            return

        if state == 'waiting_for_note_target':
            if user_message in ['おまかせ', 'おまかせで']:
                target = 'AIって何に使うの？と思っているワーママ・AI初心者'
            else:
                target = user_message
            note_sessions[user_id] = {
                'state': 'waiting_for_note_worry',
                'type': session.get('type', 'paid'),
                'target': target
            }
            save_note_sessions(note_sessions)
            line_bot_api.reply_message(event.reply_token, TextSendMessage(
                text=f"【Step2】その人の一番の悩みは何ですか？\n\n例：「毎日パンクしてるけどAIは難しそう」「何から始めればいいかわからない」\n（「おまかせ」でもOK）"
            ))
            return

        if state == 'waiting_for_note_worry':
            if user_message in ['おまかせ', 'おまかせで']:
                worry = 'AIは難しそう・何に使えばいいかわからない・でも何か変えたい'
            else:
                worry = user_message
            note_sessions[user_id] = {
                'state': 'waiting_for_note_experience',
                'type': session.get('type', 'paid'),
                'target': session.get('target', ''),
                'worry': worry
            }
            save_note_sessions(note_sessions)
            line_bot_api.reply_message(event.reply_token, TextSendMessage(
                text=f"【Step3】まきさんのどんな体験エピソードとつながりますか？\n\n例：「夫急逝後にYohanaを試してAIに行き着いた話」「先生に教えてもらって1か月課金してみた話」\n（「おまかせ」でもOK）"
            ))
            return

        if state == 'waiting_for_note_experience':
            if user_message in ['おまかせ', 'おまかせで']:
                experience = (
                    "以下のエピソードから記事テーマに合うものを選んでください：\n"
                    "・毎朝7時にLINEで今日の予定が届く。Googleカレンダーを確認しに行く手間がゼロ\n"
                    "・学校のプリントをLINEに写真で送るだけで日時・場所・申込期限が自動でカレンダーに登録される\n"
                    "・LINEで「〇〇の期限 5月10日」と打つだけで1週間前〜当日まで自動リマインド\n"
                    "・プログラミングゼロでも3日後に動くものができた（エラーはコピペして「直して」と言うだけ）\n"
                    "・APIとかRenderとかGitHubとか全部意味不明のまま進めたが、言われた通りにやったら動いた\n"
                    "・Googleカレンダーの通知は別のことをしていると流れてしまう→LINEは届くから忘れない\n"
                    "・家にも秘書がいてくれたらと思っていたが、AIで月ほぼ0円で実現した\n"
                    "・Yohanaを試したが続かなかった→AIは言い直しやすくて相性が良かった\n"
                    "・不動産投資の講師の先生にClaude Codeを教えてもらって「困っていることをそのまま話すだけ」という言葉で試してみた\n"
                    "重要：AI副業・収益化・コード技術の話は控えめに。「日常が楽になった」視点を中心にしてください。"
                )
            else:
                experience = user_message
            note_type = session.get('type', 'paid')
            target = session.get('target', '')
            worry = session.get('worry', '')
            del note_sessions[user_id]
            save_note_sessions(note_sessions)
            type_label = "有料" if note_type == "paid" else "無料"
            line_bot_api.reply_message(event.reply_token, TextSendMessage(
                text=f"✍️ note{type_label}記事の下書きを作成中です...\n少しお待ちください（1〜2分かかります）"
            ))
            threading.Thread(target=generate_note_draft_async, args=(user_id, note_type, target, worry, experience)).start()
            return

    # note公開済み報告 → NOTE_PUBLISHED_TITLESを次のラインナップ記事で自動更新
    if user_message in ['note公開した', 'note公開', '公開した']:
        published_count = len(NOTE_PUBLISHED_TITLES)
        if published_count < len(NOTE_LINEUP):
            next_item = NOTE_LINEUP[published_count]
            type_label = "無料" if next_item["type"] == "free" else "有料"
            line_bot_api.reply_message(event.reply_token, TextSendMessage(
                text=f"🎉 お疲れ様でした！\n\n次の記事はこれです👇\n▶ [{type_label}] {next_item['title']}\n\n「note書きたい」で下書きを作れます✨\n（NOTE_PUBLISHED_TITLESへの記録はClaude Codeで更新します）"
            ))
        else:
            line_bot_api.reply_message(event.reply_token, TextSendMessage(
                text="🎉 全ラインナップ公開完了！すごい！\n次のシリーズ設計はClaude Codeに相談してください✨"
            ))
        return

    # noteキーワード
    note_keywords = ['note書きたい', 'note下書き', 'note記事', 'note作りたい', 'noteかきたい']
    if any(kw in user_message for kw in note_keywords):
        note_sessions[user_id] = {'state': 'waiting_for_note_type'}
        save_note_sessions(note_sessions)
        line_bot_api.reply_message(event.reply_token, TextSendMessage(
            text="📝 note記事を作りましょう！\n\nどちらにしますか？\n\n1️⃣ 有料記事（300〜500円・テクニック系）\n2️⃣ 無料記事（体験談・共感系）\n\n番号か言葉で教えてください！"
        ))
        return

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
                    text="✍️ テーマを入力してください！\n例：「更年期の不眠」「寝つきが悪い30代女性」「なつめで睡眠改善」"
                ))
            elif any(kw in normalized for kw in ['古い', '4', '④', '古い記事']):
                del yakuzen_sessions[user_id]
                save_yakuzen_sessions(yakuzen_sessions)
                line_bot_api.reply_message(event.reply_token, TextSendMessage(
                    text="🔍 一番古い記事を確認しています...少しお待ちください！"
                ))
                threading.Thread(target=_start_old_check, args=(user_id, [])).start()
            elif any(kw in normalized for kw in ['KW選定', 'KWリライト', 'kw選定', '5', '⑤']):
                del yakuzen_sessions[user_id]
                save_yakuzen_sessions(yakuzen_sessions)
                line_bot_api.reply_message(event.reply_token, TextSendMessage(
                    text="🔍 Search ConsoleでKW分析→リライト全自動で開始します！\n少しお待ちください！"
                ))
                creds = get_google_creds()
                threading.Thread(target=kw_auto_rewrite, args=(user_id, creds)).start()
            elif any(kw in normalized for kw in ['KW新規', 'kw新規', '6', '⑥']):
                del yakuzen_sessions[user_id]
                save_yakuzen_sessions(yakuzen_sessions)
                line_bot_api.reply_message(event.reply_token, TextSendMessage(
                    text="🔍 Search ConsoleでKW分析→新規記事全自動で開始します！\n少しお待ちください！"
                ))
                creds = get_google_creds()
                threading.Thread(target=kw_auto_new_article, args=(user_id, creds)).start()
            else:
                line_bot_api.reply_message(event.reply_token, TextSendMessage(
                    text="「1」か「新規作成」、または「2」か「リライト」と送ってください！\n（テーマ指定は「3」、古い記事チェックは「4」、KW選定リライトは「5」、KW選定新規は「6」）"
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
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text="✍️ 睡眠記事を作成中です...少しお待ちください！（1〜2分かかります）"))
            threading.Thread(target=process_yakuzen_new_article, args=(user_id, user_message)).start()
            return

        elif state == 'waiting_for_old_rewrite_confirm':
            import unicodedata
            normalized = unicodedata.normalize('NFKC', user_message)
            post_id = session.get('post_id')
            skip_ids = session.get('skip_ids', [])

            if any(kw in normalized for kw in ['リライト', '1', '①']):
                del yakuzen_sessions[user_id]
                save_yakuzen_sessions(yakuzen_sessions)
                line_bot_api.reply_message(event.reply_token, TextSendMessage(
                    text="✍️ リライト中です...少しお待ちください！"
                ))
                threading.Thread(target=rewrite_yakuzen_by_post_id, args=(user_id, post_id)).start()

            elif any(kw in normalized for kw in ['スキップ', '次', '2', '②']):
                new_skip = skip_ids + [post_id]
                del yakuzen_sessions[user_id]
                save_yakuzen_sessions(yakuzen_sessions)
                line_bot_api.reply_message(event.reply_token, TextSendMessage(
                    text="⏭ スキップして次の記事を確認します..."
                ))
                threading.Thread(target=_start_old_check, args=(user_id, new_skip)).start()

            elif any(kw in normalized for kw in ['削除', '3', '③']):
                from blog_yakuzen import delete_yakuzen_post
                delete_yakuzen_post(post_id)
                new_skip = skip_ids
                del yakuzen_sessions[user_id]
                save_yakuzen_sessions(yakuzen_sessions)
                line_bot_api.reply_message(event.reply_token, TextSendMessage(
                    text="🗑 削除しました。次の記事を確認します..."
                ))
                threading.Thread(target=_start_old_check, args=(user_id, new_skip)).start()

            elif any(kw in normalized for kw in ['やめる', '終わり', '4', '④', 'やめ', '終了']):
                del yakuzen_sessions[user_id]
                save_yakuzen_sessions(yakuzen_sessions)
                line_bot_api.reply_message(event.reply_token, TextSendMessage(
                    text="✅ 古い記事チェックを終了しました！お疲れ様でした。"
                ))
            else:
                line_bot_api.reply_message(event.reply_token, TextSendMessage(
                    text="1️⃣ リライト / 2️⃣ スキップ / 3️⃣ 削除 / 4️⃣ やめる\nで返答してください！"
                ))
            return


    # 睡眠記事（薬膳ブログ）：キーワード検出
    yakuzen_keywords = ['睡眠記事', '薬膳記事', '薬膳ブログ', '薬膳 記事', '薬膳リライト', 'foodmakehealth', '薬膳の記事']
    if any(kw in user_message for kw in yakuzen_keywords):
        yakuzen_sessions[user_id] = {'state': 'waiting_for_mode'}
        save_yakuzen_sessions(yakuzen_sessions)
        msg = "🌙 睡眠記事、何をしますか？\n\n1️⃣ 新規作成（季節・人気ワードからテーマ自動決定）\n2️⃣ リライト（既存記事を更新）\n3️⃣ テーマ指定で新規作成\n4️⃣ 古い記事チェック＆リライト\n5️⃣ KW選定→リライト（Search Console分析→全自動）\n6️⃣ KW選定→新規記事（Search Console分析→全自動）\n\n番号か言葉で教えてください！"
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
    global _morning_sent_date
    today = datetime.datetime.now(JST).date()
    today_str = today.isoformat()

    with _morning_sent_lock:
        if _morning_sent_date == today:
            print(f"Morning: already sent today (memory), skipping.")
            return
        try:
            if os.path.exists(MORNING_SENT_FILE):
                with open(MORNING_SENT_FILE, 'r') as f:
                    if f.read().strip() == today_str:
                        _morning_sent_date = today
                        print(f"Morning: already sent today (file), skipping.")
                        return
        except Exception:
            pass
        _morning_sent_date = today
        try:
            with open(MORNING_SENT_FILE, 'w') as f:
                f.write(today_str)
        except Exception:
            pass

    print(f"Morning: sending for {today_str}")
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


def send_threads_api_reminder():
    try:
        user_id = os.environ['LINE_USER_ID']
        line_bot_api.push_message(user_id, TextSendMessage(
            text="🧵 【Threadsリマインド】\nブログ→Threads自動投稿の実装をやる予定でした！\n\n準備できたらClaudeに「Threads API実装して」と声かけしてね✅"
        ))
    except Exception as e:
        print(f"Threads API reminder error: {e}")


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


def send_may25_reminder():
    try:
        user_id = os.environ['LINE_USER_ID']
        msg = (
            "📱【5/25 実装リマインド】\n\n"
            "以下の2つを進めましょう！\n\n"
            "① MAKOのX自動投稿の実装\n"
            "　→ クレカ届いてたら「MAKO X実装して」\n\n"
            "② こはるままのThreads連携\n"
            "　→ トークン取得してRenderに設定\n"
            "　→ 「こはるままThreads連携して」"
        )
        line_bot_api.push_message(user_id, TextSendMessage(text=msg))
    except Exception as e:
        print(f"May25 reminder error: {e}")


def send_may30_reminder():
    try:
        user_id = os.environ['LINE_USER_ID']
        msg = (
            "📱【5/30 実装リマインド】\n\n"
            "MAKOのThreads連携を進めましょう！\n\n"
            "→ トークン取得してRenderに設定\n"
            "→ 「MAKOのThreads連携して」"
        )
        line_bot_api.push_message(user_id, TextSendMessage(text=msg))
    except Exception as e:
        print(f"May30 reminder error: {e}")



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

# ========== note ラインナップ管理 ==========

# noteの投稿計画（セット単位：無料1〜2本＋有料1本 = 1セット、月2セット）
NOTE_LINEUP = [
    # セット1
    {"set": 1, "type": "free", "title": "毎朝7時に予定が届くようになって、朝が変わった話"},
    {"set": 1, "type": "free", "title": "学校のプリントに追われなくなった話"},
    {"set": 1, "type": "paid", "title": "毎朝7時のLINE通知を作るまでの全手順（300円）"},
    # セット2
    {"set": 2, "type": "free", "title": "忘れるのは意志の問題じゃなかった話"},
    {"set": 2, "type": "free", "title": "エラーが怖くなくなった日のこと"},
    {"set": 2, "type": "paid", "title": "チラシ写真→カレンダー自動登録の作り方（300円）"},
    # セット3
    {"set": 3, "type": "free", "title": "AIに役割を与えたら使い方が変わった話"},
    {"set": 3, "type": "paid", "title": "締切リマインダーを月0円で作る方法（300円）"},
]

# 公開済みnote記事（公開したら末尾に追加する）
NOTE_PUBLISHED_TITLES = [
    "秘書が欲しかった私が、プログラミングゼロでAIに話しかけたら毎日が変わった話（300円）",  # 2026-05-16
]

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
    # ── Threads×楽天アフィ自動投稿 ──
    "楽天アフィのThreads自動投稿が動き始めた。毎日5本・商品画像付き・URLはコメント欄・[楽天PR]表記も全自動。1日中何もしなくても朝から夜まで投稿が流れてる。仕組みを作る1日と、仕組みが動き続ける毎日。この差が積み重なっていく。 #AI副業 #楽天アフィ #自動化",
    "詰まったら外を検索する、を学んだ。Threads連携で変数名を間違えたり、不要なOAuthフローに誘導されたりした。AIだけ頼ってたら半日以上かかってた。自分で見つけた記事で一発解決。AIと外部情報を組み合わせるのが正解だった。 #AI副業 #ClaudeCode",
    "楽天アフィのThreads攻略を調べたら、冒頭1行目で9割決まると知った。『事件です。』『正直ノーマークだった。』みたいなフックが重要。この型をAIが35種のフックからランダムで作って毎日5本投稿してくれる。戦略を学んで仕組みに落とす、これが自動化の醍醐味。 #AI副業 #楽天アフィ #ClaudeCode",
    # ── eBayリサーチAI進化 ──
    "以前：帰宅してからPCでeBayリサーチしてた。今：「ebay 風呂敷」とLINEに送るだけで10個のキーワードを並列検索→結果が届く。通勤中・待ち時間に仕入れ候補を調べられる。物販の制約が「場所」から「時間」だけになった。 #AI副業 #eBay #物販",
    "【穴場の条件】競合が少ない×価格が高い×ウォッチャーが多い＝需要はあるのに出品者が少ない穴場商品。この判定ロジックをeBayリサーチに組み込んだ。勘で仕入れてたのが、数字で判断できるようになった。データで動ける副業は強い。 #AI副業 #eBay #物販",
    "eBay仕入れの判断3ステップ↓ ①AIリサーチで候補を出す②Sold Listingsで絞る③30日の売れ数を確認。AIを信頼しながらも、最後の判断は自分でする。この一手間が仕入れ精度を上げる。自動化と人間判断の最適な分担が大事。 #AI副業 #eBay #物販",
    "以前：1キーワードずつ順番に検索→全部終わるまで待ってた。今：全キーワードを同時に検索→同じ結果が数分の1の時間で届く。「待ち時間を減らす」のも自動化。速くなるだけで使いたい頻度が上がる。 #AI副業 #eBay #ClaudeCode",
    # ── 古い記事チェック＆リライト ──
    "120記事ある薬膳ブログの「古い記事から順番に書き直したい」が「古い記事」の一言で動くようになった。一番古い記事をAIが取得→今のブログ方針に合うか判断→「リライト/スキップ/削除」で返答するだけ。ブログのリニューアル作業がLINEだけで完結する。 #AI副業 #ブログ自動化 #ClaudeCode",
    "【実体験】エラーが出てから10分で直った。LINEで新機能を試したらエラーメッセージが届いた→そのまま画面を貼っただけ→原因特定＆修正＆再デプロイまで全部やってもらえた。「エラー＝詰み」じゃなくて「エラー＝報告するだけ」になった。 #AI副業 #ClaudeCode #自動化",
    # ── Playwright MCP・WP自動修正 ──
    "サイトの表示崩れを「確認して」一言で全部直してもらえた。メニューが空のページを指してた→正しい記事URLに修正→不要ページ2つを削除。WP管理画面を一度も開かずAPIで全部完結。自分の作業：0秒。 #AI副業 #ClaudeCode #WordPress",
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
    "子どもの寝かしつけ中にスマホで医療記事を読んでたら、その知見がAIプロンプトになった。細切れ時間を「ながら学習」として使うと、ワーママの制約が専門性の武器に変わる。 #AI副業 #ワーママ",
    "ClaudeCodeで自分の業務フロー自動化してたら、手作業で30分かかってたタスクが3分で終わるように。「これ医療現場でも使えるのでは」って思ったけど、導入の壁の高さを改めて実感した。 #AI副業 #ClaudeCode",
    "毎朝のLINEBOT診断で得たユーザーの悩みパターンを、翌日のプロンプト改善に反映させてる。ワンウェイじゃなく、ユーザーの反応を見て小さく何度も直す。その繰り返しがプロダクトの質を上げてる気がする。 #AI副業 #ClaudeCode",
    "「Xを自動化してもマネタイズできない」って聞いて最初は意味が分からなかった。自動投稿してるのに何が足りないの？答えは「PDCA」。何が刺さるか分析して改善する仕組みがなかった。今日やっとそれを作った。 #AI副業 #ClaudeCode",
    "XのPDCA、全部自動化した↓\n・毎週月曜LINEでパフォーマンスレポート届く\n・AIが投稿の型を自動判定（今週は実体験型が強い、など）\n・同じ型の投稿を3本自動生成\n・そのままストックに追加・デプロイ\nプログラミングゼロのワーママが Claude Codeで全部作った。 #AI副業 #ClaudeCode",
    "1週間前：X投稿は自動だけど分析は全手動（そもそもやってなかった）。今日：投稿→分析→改善まで全自動。AIが「実体験型が強い」と判定して、同じ型の投稿を勝手に追加してくれる。PDCAを回すのに何もしなくていい。 #AI副業 #ClaudeCode",
    "XはPDCAなしで伸びない。でも手動でやる必要はない。AIに任せればいい。分析→型判定→投稿生成→ストック追加、全部自動化した。私がやることはリプライの返信だけ。プログラミングゼロでもできた。 #AI副業 #ワーママ #ClaudeCode",
    "今日初めてXのパフォーマンスデータを見た。「実体験型が強い」「リプライが弱い」AIが一言で教えてくれた。今まで感覚で投稿してたのが、データで動けるようになった。これがPDCAを回すってことか。 #AI副業 #ClaudeCode",
    "「スマホからPCに指示→Claudeが仕事する」機能が出た。すごいと思った。でも冷静に考えたら私のLINEボットで同じことやってる。「LINEで指示→RenderのClaudeが動く→WPに記事公開」。技術はすでに手の中にあった。 #AI副業 #ClaudeCode",
    "XはPDCAを回すのが大事と聞いて、全部自動化した。「Xレポート」とLINEに送る→AIが型を分析→同じ型の投稿を自動生成→ストックに追加。人間がやることはリプライの返信だけ。 #AI副業 #ClaudeCode #ワーママ",
    "「Claude Codeはエンジニアだけのもの」は嘘だった。プログラミングゼロの医療職ワーママでも、ブログ自動投稿・X自動投稿・カレンダー管理・eBay管理・ゲーム開発まで全部作れた。使い方を知るか知らないかの差しかない。 #AI副業 #ClaudeCode #ワーママ",
    "Claude Codeを使う前に/planを打つようにした。「まず計画を出して」から動かすだけで失敗が激減した。AIに任せる前に「どうやるか」を確認するのは人間を使うときと同じ。 #AI副業 #ClaudeCode",
    "ブログ記事1本からX投稿・インスタキャプション・note導入文を同時に作れる時代。書くのは1回、使い回しは無限。コンテンツは量産するものじゃなく、1本を多方向に展開するもの。 #AI副業 #ClaudeCode #ブログ",
    "以前：毎日メールで届く問い合わせを手動でスプレッドシートに入力してた。今：Gmailフィルター＋Zapierで自動入力→入力ミスゼロ。5分の仕組み作りが、毎月3時間を返してくれた。 #AI副業 #ワーママ",
    "以前：患者さんの問い合わせメールに毎回同じ内容をテンプレから手打ちコピペしてた。今：Claudeで返信文を自動生成→内容確認だけで送信。トーン調整も一瞬。「メール業務」が業務じゃなくなった。 #AI副業 #ClaudeCode",
    "以前：動画の字幕を手作業で30分かけてつけてた。今：APIで音声抽出→Claude音声認識→字幕自動生成。同じ作業が3分で終わる。難しい設定なしで実装できた。仕組みって本当に便利。 #ワーママ #ClaudeCode",
    # ── 2026-05-07追加 ──
    "AI副業でやってること・やってないこと【2026年5月版】\n\nやってること↓\n・毎朝7時：予定通知（自動）\n・X投稿3本/日（自動）\n・ブログ記事公開（LINEから1ワード）\n・eBayリサーチ（LINEから）\n\nやってないこと↓\n・毎日SNSを手動チェック\n・画像を手動で選ぶ\n・週次レポートを手動でまとめる\n\n仕組みが育つと「やること」が減る。 #AI副業 #自動化",
    "【修正済み】毎朝2通届くバグを直した。原因：コードを更新するたびに新旧サーバーが一瞬並行起動→両方が7時に通知を送る仕組みだった。解決：ファイルに「今日送信済み」と記録→再起動後もスキップする処理を追加。自動化の保守って「見えない構造」を1個ずつ潰す地道な作業。 #AI副業 #ClaudeCode",
    "薬膳ブログの収益化が一歩進んだ。記事を書く→AIが関連キーワードを抽出→楽天APIで商品3件を自動取得→カード形式で記事末尾に自動挿入→公開。「書いた瞬間にアフィリエイトリンクが入っている」状態になった。稼ぐ仕組みを先に作ると、書くことに集中できる。 #AI副業 #ブログ自動化 #アフィリエイト",
    "AI副業を続けて気づいた「やること・やらないこと」\n\n✅やること\n・実体験を即ツイート\n・バグを直すたびに仕組みを理解する\n・リプライに返信する\n\n❌やらないこと\n・完璧になるまで公開しない\n・手動でできることを手動でやり続ける\n・「忙しいから」を理由にしない\n\nワーママが続けられる副業の唯一のコツは「仕組みに任せること」。 #AI副業 #ワーママ",
    "おはようございます☀️",
    "ChatGPTの無料枠で物販リサーチやってたら精度ガタ落ち😅Claudeに切り替えたら商品判定の正確性上がった。やっぱりツール選びが副業の生産性左右するんだ。 #AI副業 #ワーママ",
    "午前中に子どもの宿題見守りながらAIコード生成→デバッグ→本業。バッファ時間がないから失敗できない。ではなく「小さく失敗して即修正」が時短のコツだった。完璧目指すより回転数。 #ClaudeCode #AI副業",
    # ── 2026-05-14追加 ──
    "仕事先のPCからでも会社ダッシュボードを見れるようにした📊 ローカルで動いてたのをRenderに移行してパスワード認証つけて外部公開。自宅・勤務先・スマホどこからでも予定とリンクを確認できる。Claudeに頼んだら30分かからなかった。 #AI副業 #ClaudeCode",
    # ── 2026-05-12追加 ──
    "AIで作った自動投稿、Threadsに入れたら即アカBANされた。MetaがAPI投稿を厳しく制限してるらしい。同じ仕組みをXに移したら何事もなく動いてる。使えなくなった仕組みをすぐ代替に変えられるのが、自分でツールを作れる強さだと思った。 #AI副業 #ClaudeCode #自動化",
    "X APIが動かない原因、アクセストークンの権限が『読み取り専用』になってた。投稿するには『Read and Write』が必要で、権限を変えたらトークンの再生成も必須。1時間かけてデバッグして、原因が1行の設定ミスだと分かったときの気持ち、複雑。でも次は迷わず直せる。 #AI副業 #ClaudeCode",
    # ── 2026-05-09追加 ──
    "LINEに届くInstagramキャプション、1通にまとめてたら自分で「切り分けるのが面倒」と気づいた。キャプション・タイトル・説明文を別々のメッセージに分割したら、コピペが一瞬に。ツールは作って終わりじゃなく、使いながら直すもの。自分のツールを育てる感覚が好きだ。 #AI副業 #ClaudeCode #LINE",
    "LINEメニューの「メルカリ」ボタンを「楽天Roomタグ」に入れ替えた。商品写真を送るだけでハッシュタグ15個が届く機能。使わないボタンより毎日使う機能を置く。たったそれだけで使い心地が全然違う。自分のツールの「使いにくい」を放置しないのが、長く続けられる秘訣だと思う。 #AI副業 #楽天Room #自動化",
    # ── 2026-05-14追加 ──
    "薬膳記事を公開→LINEに3枚の画像が届く仕組みを作った。1枚目：タイトル画像、2枚目：こんな悩みに、3枚目：今夜から試せること。全部AIが記事を読んで自動生成。インスタ投稿の「何を作るか」を考える作業がゼロになった。 #AI副業 #インスタ #自動化",
    "睡眠記事にはカモミールや温熱アイマスクが自動で入るようにした。AIが記事内容を読んで関連商品を楽天APIで取得→末尾にカード形式で自動挿入。レシピ記事には料理本、睡眠記事には寝具グッズ。記事に合ったアフィリエイトが自動で切り替わる。 #AI副業 #アフィリエイト #ブログ自動化",
    # ── 日常が楽になった（AI×ワーママ実体験） ──
    "毎朝7時に今日の予定がLINEで届く。前日どんなに疲れて寝ても、翌朝には今日やることが待っている。Googleカレンダーを確認しに行かなくていい。この安心感が、一番生活を変えてくれた。 #ワーママ #AI秘書",
    "Googleカレンダーの通知、気づいたら消えてた、が何度あったか。通知が来た瞬間に別のことをしてると、そのまま流れてしまう。今はLINEで届く。届くなら忘れない。それだけで全然違う。 #ワーママ #AI",
    "学校のプリントをLINEに写真で送るだけで、日時・場所・申込期限がカレンダーに入る。3人分の行事を1枚ずつ手入力してた頃と比べたら、別の生活。「あのプリントどこ？」が日常から消えた。 #ワーママ #子育て",
    "LINEに「〇〇の期限 5月10日」と打つだけで、1週間前・3日前・前日・当日に通知が届く。忘れるのは気のゆるみじゃなくて、仕組みの問題だったんだと気づいた。仕組みを変えたら、忘れなくなった。 #ワーママ #子育て",
    "子ども3人分の学校行事・バイトのシフト・本業の予定。全部頭に入れておかなきゃいけなくて、毎日どこかでパンクしてた。今はLINEが覚えてくれてる。頭の外に出したら、こんなに楽だった。 #ワーママ #ワンオペ",
    "「AIって何に使うの？」とずっと思ってた。副業ツールかコード書くものでしょ、と。でも「困っていることをそのまま話すだけでいい」と聞いて試してみた。予定管理の悩みを話したら、LINEボットを提案してくれた。 #ワーママ #AI",
    "プログラミングゼロでも動くものができた。「次は何をすればいいですか？」と聞いて、言われた通りにやる。エラーが出たらそのままコピペして「直してください」と言うだけ。3日後に動いてた。 #ClaudeCode #AI",
    "APIって何？Renderって何？GitHubって何？全部意味不明のまま進めた。でも言われた通りにやったら、3日後に毎朝7時のLINE通知が動いてた。わからなくても前に進めるんだ、を体で知った。 #AI #ClaudeCode",
    "本業ではアシスタントさんが「今日のスケジュールはこれです」と教えてくれる。家では全部自分。その差がずっとキツかった。今はAIが毎朝教えてくれる。月ほぼ0円で。 #ワーママ #AI秘書",
    "秘書サービスを試したことがある。でも続かなかった。アプリが重い、人間だから言い直しにくい。AIは24時間・即返答・何度でも言い直せる。求めてたのはこっちだったんだと気づいた。 #ワーママ #AI秘書",
    "「AIは難しそう」「自分には関係ない」と思ってた私が、今は毎朝LINEで予定が届く生活をしている。必要だったのは技術じゃなくて、困っていることを話すことだけだった。 #ワーママ #AI",
    "家事・育児・本業・バイト・習い事の送迎。頭の中がいつもパンクしてた。AIに仕組みを作ってもらってから、「覚えておかなきゃ」のプレッシャーが消えた。軽くなった、という感覚が一番正確かもしれない。 #ワーママ #ワンオペ",
    "締切を過ぎてしまったことが何度もある。意識の問題だと思ってた。でも今思うと、通知が来ても「見に行かないと気づけない」仕組みが問題だった。届く仕組みに変えたら、過ぎなくなった。 #ワーママ #子育て #AI",
    "「家にも秘書がいたら」と本気で思ってた。仕事にはアシスタントがいる。でも家のこと・子育てのことは全部自分。その非対称さが毎日じわじわキツかった。今は違う。 #ワーママ #ワンオペ #AI秘書",
    # ── 発信・方向性系 ──
    "note記事を設計から書き直した。同じ体験談でも、誰に届けるかを先に決めてから書くと全然違うものになった。「AIって何に使うの？」と思っているワーママに届けたい、と決めたら言葉が変わった。 #ワーママ #AI",
    "LINEに日常をメモするだけで、自動でX投稿のネタになる仕組みを作った。メモ→AI変換→ストック追加→自動投稿まで全部つながった。やること：メモするだけ。 #ClaudeCode #ワーママ",
    "X投稿の方向性を整理した。AI副業系のストックがいっぱいあったけど、本当に届けたい人は『AIって何に使うの？』と思っているワーママだった。発信する前に『誰に届けたいか』を決めることの大切さを改めて実感した。 #ワーママ #AI",
    "「困っていることをそのまま話すだけでいい」という言葉で、AIを使い始めた。技術の話じゃなくて、困りごとの話でよかった。それが一番の入口だったと思う。 #ワーママ #AI #ClaudeCode",
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
    # ── eBay物販・リサーチ系 ──
    "「なんとなく売れそう」で仕入れてた頃、売れない在庫が積み上がった。今はSTR 2%以上・セラー20人以上・需給率10%以上の3条件を全部クリアした商品だけ仕入れる。数字で判断するようにしたら、赤字在庫がゼロになった。 #eBay #物販 #副業",
    "評価1万人のトップセラーを参考にしていた頃、全然真似できなかった。理由がわかった。トップセラーは仕入れルートも配送方法も特殊で、同じことができない。評価100〜1000人の「手が届く規模」のセラーを分析したら、真似できるポイントだらけだった。 #eBay #物販 #副業",
    "eBayのリサーチ、Teapeakで「90日間で2個以上売れてる商品」を探す。ただし50〜100個以上売れてるものは避ける。売れすぎてる＝大手セラーが占領してる。ほどほどに売れてて激戦じゃない商品が、初心者には一番稼ぎやすい。 #eBay #物販 #副業",
    "月10万円の逆算をしたら「今週30品出品する」という行動に変わった。利益2,800円/品 × 36品 = 月10万円。出品数の4%が売れるとして900品必要。フェーズを分けると4ヶ月で届く計算。曖昧な目標が、今週やることに変わった瞬間が好き。 #eBay #副業 #逆算思考",
    "eBayリサーチのスプレッドシートに自動判定をつけた。数字を入力するだけで「✅ 仕入れOK」「❌ NG」が出て行の色が変わる。ClaudeCodeに頼んで30分でできた。判断に迷う時間がゼロになったのが一番の効果。 #AI副業 #eBay #自動化",
    # ── 楽天アフィリエイト自動挿入 ──
    "薬膳記事のアフィ機会損失がゼロになった。記事を公開するたびにAIがテーマを読み取り→楽天APIで関連商品を検索→カード形式で自動挿入。「なつめ記事」を書いたらなつめ商品が並ぶ。手動でリンクを探す手間も貼り忘れもなくなった。 #AI副業 #ブログ #楽天アフィリエイト",
    "「Refererヘッダー1行」が2時間の詰まりを解決した。楽天API連携で403が出続けてた原因がそれだった。APIキーを確認して・エンドポイントを変えて・テストコードを書いて…全部試したあと最後の1行で動いた。デバッグは「シンプルな見落とし」で終わることが多い。 #AI副業 #ClaudeCode #個人開発",
    # ── X・noteプロフィール整備 ──
    "Xのピン止めが空白だった。プロフィールを見に来た人に自己紹介が届いてなかった。夫が急逝してワンオペになった話・ゼロから始めた話・0→1まだこれからという正直な状況。全部書いたらようやく「私らしいアカウント」になった気がした。 #AI副業 #ワーママ #X運用",
    "ピン止め・Bio・URLを整えて、Xからnoteへの導線を初めてちゃんと作った。「プロフィールを見に来た人が次に何をするか」を設計すると、バラバラだった発信が1本の線につながる。発信は量より設計だと気づいた。 #AI副業 #note #X運用",
    "副業収益ゼロのまま発信していくことにした。0→1はまだこれから。でも「成功した人の話」より「今まさに同じ状況にいる人の話」の方が刺さることもある。リアルな過程を見せることが、今の自分にできる発信だと思ってる。 #AI副業 #ワーママ #note",
    # ── 楽天room→Instagram自動化チャレンジ ──
    "楽天room→Instagram自動化、断念した😅楽天roomはJavaScriptで動くSPAでRSSが商品データを返さない仕様だったでも副産物でInstagramビジネスアカウントの設定・FBページ連携を完全把握壁にぶつかっても必ず何か得るものがある #AI副業 #ワーママ副業",
    # ── Higgsfield MCP・AI画像生成 ──
    "Claude CodeからAIイラストを直接作れるようになった🎨 コマンド1行でMCP連携して、チャットで「白衣の女性を描いて」と指示するだけ。note用のアイコン画像が数秒で出てくる。ゼロから始めたAI副業、できることが着実に増えてきてる。 #AI副業 #ClaudeCode #自動化",
    "AIイラスト、無料でも毎日10枚作れると知った✨ ただし無料プランは透かし入り。「透かしが嫌なら他のツールも出すよ」とClaudeが提案してくれた。ツール名を覚えるより、適切に選んでくれる秘書がいる方が楽。 #AI副業 #ワーママ副業",
    # ── 楽天API・外部API保守 ──
    "外部APIは突然動かなくなる。楽天APIのエンドポイントがいつの間にか旧仕様→廃止になってた。気づいたのはデバッグ中。新エンドポイントに1行変えたら動いた。自動化の仕組みは「作って終わり」じゃなくて、たまに動作確認が必要だと学んだ。 #AI副業 #ClaudeCode #自動化",
    "複数のXアカウントをClaude Codeで自動管理できるようになった。キー4つ・関数1本・スケジューラー1行。アカウントが増えても同じパターンで追加できる。仕組みを作るのは最初だけで、あとは全部自動。 #AI副業 #X運用 #自動化",
    # ── eBay初売れ・仕入れ自動化 ──
    "eBay初売れした。メルカリで仕入れてeBayで出品、初めての海外への販売。ドキドキしながら通知を確認したら本当に売れてた。ゼロから積み上げてきた感じがちゃんとある。 #AI副業 #eBay #ワーママ副業",
    "毎朝LINEに『今日の仕入れ候補』が届くようにした。eBayで日本人セラーが売れた商品を自動リサーチして、メルカリ相場確認URLと仕入れ上限も一緒に届く。朝起きたら見るだけ。 #Claude #AI副業 #自動化",
    "気になるeBayセラーをLINEで即追跡できるようにした。セラーIDを送ると売れた商品・価格・仕入れ上限をすぐ返してくれる。競合調査が秒で終わる。 #Claude #AI副業 #eBay",
    # ── ファイル整理・PC管理 ──
    "ダウンロードフォルダ1,492件→1,113件。Claude Codeに頼んだら20分で終わった。重複削除・野球写真除外・不動産フォルダ分け・監修原稿一括削除。自分でやったら半日かかってたやつ。AIに任せると『整理しなきゃ』というストレスごと消える。 #ClaudeCode #AI副業 #時短",
    "「野球の写真はいらない」の一言でフォルダ丸ごと削除できる時代になった。Claude Codeが画像を1枚ずつ開いて内容を確認して、野球写真だけ選んで消してくれた。パソコン整理もAIに丸投げでいい。 #ClaudeCode #AI副業 #時短術",
    "不動産書類58件をリリア成増・板橋区計画・松戸市岩瀬・検討物件…に自動分類してもらった。フォルダ作成から移動まで全部AIがやってくれる。自分でやったら30分の作業が30秒。Claude Codeはコードだけじゃなくてファイル整理もできる。 #AI副業 #ClaudeCode",
    # ── 複数キャラSNS展開 ──
    "1人なのに3キャラで動いてる。まき（AI副業）・こはるまま（旅行アフィ）・MAKO（医師×睡眠）。全部Claudeが自動投稿を担当。SNSの運用を人格ごとに分けると刺さる層が変わる。AIカンパニーってこういうことだと思う。 #AI副業 #Claude #自動化",
    "医師キャラのSNS投稿は『言い切りNG』という制約をClaudeに設定した。『〜です』じゃなく『〜かもしれません』『〜という方もいます』のトーンで自動生成させる。制約があるほど信頼感が出る。キャラ設計ってそういうもの。 #AI副業 #Claude #SNS運用",
    "PowerShellを開くたびに『どの部署？』と毎回選択してたのをやめた。秘書部しか使わないから直接入るように変えた。こういう小さな摩擦を一つずつ消していくのがClaudeとの正しい付き合い方。 #ClaudeCode #AI副業 #自動化",
    # ── 2026-05-15追加 ──
    "Hotmailに届いたeBayの購入通知、今まで埋もれて見逃してた。対応した？してない？の管理もゼロ。make.comを設定したら：メール受信→LINE通知→Notionにタスク自動追加、まで全自動。埋もれていた重要メールがタスクに変わった。 #AI副業 #自動化 #eBay",
    "メルマガが1000件溜まってた。全部読むのは無理・全部消すのはもったいない。週2回・未読30件ずつ・Claudeが要約→LINEに届く仕組みを作った。「副業に使えそう」「不動産案件あり」だけが届く。読む必要がなくなった。 #AI副業 #自動化 #メール管理",
    "「あのメール確認したっけ」から解放された。eBay・楽天アフィの重要メールが来たら、LINEに通知が届いてNotionのタスクリストに自動で追加される。対応漏れがゼロになった。メールを管理するのをやめて、仕組みに管理させるのが正解だった。 #AI副業 #自動化 #ClaudeCode",
    "メルマガ要約、全部Notionに保存してたらすぐパンクしそうだった。半自動にした。LINEに要約が届く→気になった番号を「①③保存して」と返信する→その分だけNotionに入る。完全自動より「人が選んだものだけ残す」の方が使える情報になる。 #AI副業 #自動化 #Notion",
    # ── Threads自動投稿・API格闘 ──
    "Threads自動投稿の設定に一晩かかった。何度もエラーが出て、最終的に今日は諦めた。でも「どこで詰まったか・次回の手順・参考記事」をAIに全部引き継がせた。完璧に終わらせなくていい。続きを渡せる仕組みがあれば十分。 #AI副業 #Claude #自動化",
    "MetaのAPIは沼だった。OAuthのエラーコードを調べ続けて一晩。原因は「アプリの作り方が違った」という単純なこと。試行錯誤の記録をAIに残しておいたら、次回は同じ失敗をしない。失敗もちゃんと資産になる。 #AI副業 #Claude #SNS運用",
    "こはるまま・MAKOのThreads自動投稿、コードは全部書いた。あとはトークン設定するだけ。完成してから報告じゃなくて、7割の状態でもプロセスを投稿できるのがAI副業の強みだと思う。完璧じゃなくていい。 #AI副業 #Claude #Threads",
    # ── SNSデザイン・複数キャラ運用 ──
    "XのヘッダーもAIに作ってもらった🌙夜空×三日月×薬草のデザイン、PythonとPillowで自動生成。Canvaもデザインツールも不要、コードで1500×500pxの画像が完成する。SNS運用の細かい作業がどんどんAI化されていく。 #AI副業 #ClaudeCode #自動化",
    # ── 楽天アフィ自動化・こはるまま展開 ──
    "旅行アカウントの楽天アフィ投稿を全自動化した。固定ジャンル13種＋月替わり特集2種をローテーション。3日に1回は楽天ROOMの商品も混ぜる。投稿文はClaudeが生成、スケジューラーが毎日20:30に投稿。仕込みが終わったら何もしなくていい。 #AI副業 #楽天アフィ #自動化",
    "楽天アフィのジャンルは『1〜2ヶ月前シフト』で組むのが正解だった。8月のお盆需要のために6月から帰省グッズを投稿する。季節のタイムラグを読んで投稿ネタをJSON管理したら、年間の戦略が1ファイルで完結した。 #AI副業 #楽天アフィ #自動化",
    "Threadsのコードも書き終わった。あとはトークンをRenderに設定するだけで、朝と夜の投稿が全自動で動き出す。仕組みを作ってしまえば、プラットフォームが増えても追加コストがほぼゼロになる。 #AI副業 #自動化 #Threads",
    # ── eBay売上管理ダッシュボード（2026-05-16追加） ──
    "eBayの注文管理、今まで手入力でやってた。注文が増えるほど追いつかなくなるのは明らか。APIで注文データを自動取得→Google Sheetsに記録→仕入れ値と送料だけ入力すれば利益が自動計算される仕組みを作った。入力する手間が劇的に減った。 #AI副業 #eBay #自動化",
    "【バグ発見】損益が出たとき利益が「¥-1」と表示されてた。正しくは「-¥1,500」。原因はGoogle SheetsがAPIで数字を「-1,500」という文字列で返すせいで、parseFloatがカンマの手前で止まってた。設定を1行変えたら解決。小さいバグほど原因が面白い。 #AI副業 #個人開発 #ClaudeCode",
    "eBay売上ダッシュボードをLINEリッチメニューのボタンに置いた。スマホからワンタップで開ける。注文・仕入れ・送料・為替・利益まで1画面に全部入ってる。副業の数字管理がスマホだけで完結するのは想像以上に気持ちいい。 #AI副業 #eBay #物販",
    "Google APIのトークンをローカルで動作確認してからRenderに貼ったら「認証エラー」になった。調べたら原因はトークンが使うたびに新しいものに入れ替わる仕様だった。正解は「生成→即Renderに貼る・ローカルでは絶対使わない」。ハマって初めてわかるAPI仕様の罠。 #AI副業 #個人開発 #Claude",
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
            text="📝 【noteリマインド】\n今月もX投稿が溜まりました！\n\nそろそろnote記事が書けそうなネタはありますか？\nLINEに「note書きたい」と送るだけで下書きが作れます✨"
        ))
    except Exception as e:
        print(f"Note reminder error: {e}")


def send_note_weekly_reminder():
    """毎週木曜9:05：noteネタ提案＋ラインナップ俯瞰リマインダー"""
    try:
        user_id = os.environ['LINE_USER_ID']
        published_count = len(NOTE_PUBLISHED_TITLES)
        next_item = NOTE_LINEUP[published_count] if published_count < len(NOTE_LINEUP) else None

        # ラインナップ進捗
        progress_lines = []
        for i, item in enumerate(NOTE_LINEUP):
            type_label = "無料" if item["type"] == "free" else "有料"
            if i < published_count:
                mark = "✅"
            elif i == published_count:
                mark = "👉"
            else:
                mark = "⬜"
            progress_lines.append(f"{mark}[{type_label}] {item['title']}")

        progress_text = "\n".join(progress_lines)

        if next_item:
            type_label = "無料" if next_item["type"] == "free" else "有料"
            msg = (
                f"📝 今週のnoteネタ提案\n\n"
                f"▶ 次に書く記事（{type_label}）\n「{next_item['title']}」\n\n"
                f"📊 ラインナップ進捗\n{progress_text}\n\n"
                f"「note書きたい」で下書きが作れます✨\n"
                f"公開したら「note公開した」と送ってください"
            )
        else:
            msg = (
                f"📝 ラインナップ全記事公開完了！おめでとうございます🎉\n\n"
                f"{progress_text}\n\n"
                f"次のラインナップを設計しましょう✨"
            )

        line_bot_api.push_message(user_id, TextSendMessage(text=msg))
    except Exception as e:
        print(f"Note weekly reminder error: {e}")


def send_x_weekly_report():
    """過去7日間のX投稿パフォーマンスをLINEに送信"""
    try:
        client = _get_x_client()
        if not client:
            return

        me = client.get_me(user_auth=True)
        user_id_x = me.data.id

        now = datetime.datetime.now(datetime.timezone.utc)
        start_time = now - datetime.timedelta(days=7)

        tweets = client.get_users_tweets(
            id=user_id_x,
            max_results=10,
            tweet_fields=['public_metrics', 'text'],
            user_auth=True
        )

        line_uid = os.environ['LINE_USER_ID']

        if not tweets.data:
            line_bot_api.push_message(line_uid, TextSendMessage(
                text="📊 今週のXレポート\n\n先週の投稿データがありませんでした。"
            ))
            return

        def get_score(tweet):
            pm = tweet.public_metrics or {}
            return pm.get('like_count', 0) * 3 + pm.get('retweet_count', 0) * 5 + pm.get('reply_count', 0) * 2

        sorted_tweets = sorted(tweets.data, key=get_score, reverse=True)
        total = len(sorted_tweets)

        def fmt(tweet, rank):
            pm = tweet.public_metrics or {}
            likes = pm.get('like_count', 0)
            rts = pm.get('retweet_count', 0)
            replies = pm.get('reply_count', 0)
            text_prev = tweet.text[:25] + '…' if len(tweet.text) > 25 else tweet.text
            return f"{rank}位「{text_prev}」❤{likes} RT{rts} 返{replies}"

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
            "あなたはXアカウント（@maki_claude_lab：医療職×3児ワンオペ×AIで日常が楽になった実体験を発信）の投稿分析者です。\n"
            "アカウント方針：「AIって何に使うの？」と思っているワーママ・AI初心者に、日常が楽になった実体験を届ける。副業・収益化より『日常の変化』軸を重視。\n"
            "以下のパフォーマンスデータを見て、簡潔に分析してください。\n\n"
            f"【今週のトップ投稿】\n{top_texts}\n\n"
            f"【今週のワースト投稿】\n{worst_texts}\n\n"
            "以下の形式で答えてください（全体で100文字以内）：\n"
            "今週の傾向：〇〇型が強い（例：Before→After型、日常共感型、実体験型、安心感型、断言型）\n"
            "来週やること：〇〇（具体的に1行で）"
        )
        analysis_text = ""
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

        # TWEET_STOCKを自動改善（バックグラウンド）
        if analysis_text:
            threading.Thread(
                target=auto_improve_tweet_stock,
                args=(top_texts, analysis_text)
            ).start()

        # Notionの日記メモからもツイート生成（バックグラウンド）
        threading.Thread(target=auto_tweet_from_diary_memos).start()

    except Exception as e:
        print(f"X weekly report error: {e}")
        try:
            line_uid = os.environ.get('LINE_USER_ID', '')
            if line_uid:
                line_bot_api.push_message(line_uid, TextSendMessage(
                    text=f"❌ Xレポートエラー：\n{str(e)[:200]}"
                ))
        except Exception:
            pass


def send_daily_work_log():
    """毎日18時：今日のコミット履歴＋X投稿数をLINEに送信"""
    try:
        import requests as req_lib
        now = datetime.datetime.now(JST)
        today_str = now.strftime('%Y-%m-%d')
        line_uid = os.environ['LINE_USER_ID']
        lines = [f"📋 今日の業務ログ（{now.strftime('%m/%d')}）\n"]

        # GitHubから今日のコミットを取得
        github_token = (os.environ.get('GITHUB_TOKEN') or '').strip()
        if github_token:
            headers_gh = {
                'Authorization': f'token {github_token}',
                'Accept': 'application/vnd.github.v3+json'
            }
            r = req_lib.get(
                'https://api.github.com/repos/makiko01035/maki-hisho/commits',
                headers=headers_gh,
                params={
                    'since': f'{today_str}T00:00:00+09:00',
                    'until': f'{today_str}T23:59:59+09:00',
                    'per_page': 10
                }
            )
            if r.status_code == 200 and r.json():
                commits = r.json()
                lines.append(f"🔨 今日のコミット（{len(commits)}件）")
                for c in commits[:5]:
                    msg = c['commit']['message'].split('\n')[0][:40]
                    lines.append(f"  ・{msg}")
                lines.append("")
            else:
                lines.append("🔨 今日のコミット：なし\n")

        # X投稿数
        x_count = 3 if now.day % 2 == 1 else 2
        lines.append(f"📱 X投稿：{x_count}本 自動投稿済み\n")

        # AIの一言
        try:
            ai_resp = anthropic_client.messages.create(
                model="claude-haiku-4-5-20251001",
                max_tokens=60,
                messages=[{"role": "user", "content": f"ワーママAI副業家まきさんへ、今日もお疲れ様の一言を30文字以内で。明るく背中を押す一言で。"}]
            )
            lines.append(f"💌 {ai_resp.content[0].text.strip()}")
        except Exception:
            lines.append("💌 今日もお疲れ様！明日もコツコツいこう。")

        line_bot_api.push_message(line_uid, TextSendMessage(text='\n'.join(lines)))
    except Exception as e:
        print(f"Daily work log error: {e}")


def add_study_memo(memo_text):
    """LINEからのメモをx_study_note.htmlに追記してGitHub APIでコミット"""
    import base64
    import requests as req_lib

    github_token = (os.environ.get('GITHUB_TOKEN') or '').strip()
    if not github_token:
        return False

    repo = 'makiko01035/maki-hisho'
    file_path = 'x_study_note.html'
    headers = {
        'Authorization': f'token {github_token}',
        'Accept': 'application/vnd.github.v3+json'
    }

    r = req_lib.get(f'https://api.github.com/repos/{repo}/contents/{file_path}', headers=headers)
    if r.status_code != 200:
        return False

    data = r.json()
    sha = data['sha']
    content = base64.b64decode(data['content']).decode('utf-8')

    now = datetime.datetime.now(JST)
    date_str = now.strftime('%m/%d %H:%M')
    memo_html = (
        f'  <div class="memo-item"><span class="memo-date">{date_str}</span>{memo_text}</div>\n'
        f'  <!-- MEMO_INSERT_POINT -->'
    )
    new_content = content.replace('  <!-- MEMO_INSERT_POINT -->', memo_html)

    update_payload = {
        'message': f'勉強ノート：自分メモ追加（{date_str}）',
        'content': base64.b64encode(new_content.encode('utf-8')).decode('utf-8'),
        'sha': sha,
        'branch': 'main'
    }
    r2 = req_lib.put(
        f'https://api.github.com/repos/{repo}/contents/{file_path}',
        headers=headers, json=update_payload
    )
    return r2.status_code in (200, 201)


def find_or_create_diary_page(notion_token, today_str):
    """今日の日記ページをNotionで探す。なければ作成して返す"""
    import requests as req
    headers = {
        "Authorization": f"Bearer {notion_token}",
        "Notion-Version": "2022-06-28",
        "Content-Type": "application/json"
    }
    # 検索APIで今日の日記ページを探す
    r = req.post("https://api.notion.com/v1/search",
        headers=headers,
        json={"query": "日記", "filter": {"value": "page", "property": "object"}, "page_size": 20}
    )
    if r.status_code == 200:
        for page in r.json().get("results", []):
            props = page.get("properties", {})
            date_val = props.get("日付", {}).get("date") or {}
            if date_val.get("start") == today_str:
                return page["id"]

    # 見つからなければDBに新規作成
    db_id = "323f8d6d-41de-8082-9c88-e476d05c2a0a"
    r2 = req.post("https://api.notion.com/v1/pages",
        headers=headers,
        json={
            "parent": {"database_id": db_id},
            "properties": {
                "今日やること": {"title": [{"text": {"content": "日記"}}]},
                "日付": {"date": {"start": today_str}}
            }
        }
    )
    if r2.status_code == 200:
        return r2.json()["id"]
    return None


def fetch_diary_memos_from_notion(days=7):
    """Notionから直近N日間の日記メモを取得してテキストで返す"""
    import requests as req
    notion_token = os.environ.get('NOTION_TOKEN', '')
    if not notion_token:
        return ""
    headers = {
        "Authorization": f"Bearer {notion_token}",
        "Notion-Version": "2022-06-28",
        "Content-Type": "application/json"
    }
    # 直近N日間の日付リストを作成
    today = datetime.date.today()
    target_dates = [(today - datetime.timedelta(days=i)).strftime('%Y-%m-%d') for i in range(days)]

    # Notionから「日記」ページを検索
    r = req.post("https://api.notion.com/v1/search",
        headers=headers,
        json={"query": "日記", "filter": {"value": "page", "property": "object"}, "page_size": 30}
    )
    if r.status_code != 200:
        return ""

    all_memos = []
    for page in r.json().get("results", []):
        props = page.get("properties", {})
        date_val = props.get("日付", {}).get("date") or {}
        page_date = date_val.get("start", "")
        if page_date not in target_dates:
            continue
        # そのページのブロック（メモ）を取得
        page_id = page["id"]
        rb = req.get(f"https://api.notion.com/v1/blocks/{page_id}/children",
            headers=headers, params={"page_size": 100}
        )
        if rb.status_code != 200:
            continue
        for block in rb.json().get("results", []):
            if block.get("type") == "bulleted_list_item":
                texts = block["bulleted_list_item"].get("rich_text", [])
                text = "".join(t.get("plain_text", "") for t in texts).strip()
                if text:
                    all_memos.append(f"[{page_date}] {text}")

    return "\n".join(all_memos)


def auto_tweet_from_diary_memos():
    """Notionの日記メモをX投稿に変換してTWEET_STOCKに自動追加"""
    import base64
    import requests as req_lib

    memos_text = fetch_diary_memos_from_notion(days=7)
    if not memos_text:
        print("diary memos: nothing to process")
        return

    github_token = (os.environ.get('GITHUB_TOKEN') or '').strip()
    if not github_token:
        return

    # Claudeに渡してX投稿3本を生成
    gen_prompt = (
        "あなたはXアカウント（@maki_claude_lab：医療職×3児ワンオペ×AIで日常が楽になった実体験を発信）の投稿担当です。\n"
        "以下は本人が日常の中でLINEにメモした内容です。\n"
        "これをX投稿（140文字以内）に変換してください。3本作成してください。\n\n"
        f"【日記メモ（直近7日）】\n{memos_text[:1500]}\n\n"
        "条件：\n"
        "- 「AIって何に使うの？」と思っているワーママ・AI初心者に刺さる書き方\n"
        "- 副業・収益化より『日常の変化』『楽になった』『気づき』軸で書く\n"
        "- ですます調・等身大のトーン\n"
        "- ハッシュタグは #ワーママ #AI #子育て #AI秘書 #時短 のいずれか1〜2個\n"
        "- 3本を1行ずつ、番号なし・余計な説明なしで出力\n"
        "- メモがAI・副業と無関係でも日常の共感ネタとして活かしてOK"
    )
    try:
        gen_resp = anthropic_client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=500,
            messages=[{"role": "user", "content": gen_prompt}]
        )
        new_tweets = [
            t.strip() for t in gen_resp.content[0].text.strip().split('\n')
            if t.strip() and len(t.strip()) > 10
        ][:3]
    except Exception as e:
        print(f"diary tweet generate error: {e}")
        return

    if not new_tweets:
        return

    # GitHub APIでTWEET_STOCKに追加
    repo = 'makiko01035/maki-hisho'
    file_path = 'main.py'
    headers = {
        'Authorization': f'token {github_token}',
        'Accept': 'application/vnd.github.v3+json'
    }
    r = req_lib.get(f'https://api.github.com/repos/{repo}/contents/{file_path}', headers=headers)
    if r.status_code != 200:
        return

    data = r.json()
    sha = data['sha']
    content = base64.b64decode(data['content']).decode('utf-8')
    stock_start = content.find('TWEET_STOCK = [')
    if stock_start == -1:
        return
    stock_end = content.find('\n]', stock_start)
    if stock_end == -1:
        return

    insert_lines = '\n'.join([f'    "{t}",' for t in new_tweets])
    new_content = content[:stock_end] + '\n' + insert_lines + content[stock_end:]

    today_str = datetime.date.today().strftime('%Y-%m-%d')
    commit_msg = f"広報部PDCA：{today_str} 日記メモからツイート{len(new_tweets)}本自動追加"
    update_payload = {
        'message': commit_msg,
        'content': base64.b64encode(new_content.encode('utf-8')).decode('utf-8'),
        'sha': sha,
        'branch': 'main'
    }
    r2 = req_lib.put(
        f'https://api.github.com/repos/{repo}/contents/{file_path}',
        headers=headers, json=update_payload
    )
    line_uid = os.environ.get('LINE_USER_ID', '')
    if r2.status_code in (200, 201) and line_uid:
        preview = '\n'.join([f'・{t[:30]}…' for t in new_tweets])
        line_bot_api.push_message(line_uid, TextSendMessage(
            text=f"📓 日記メモからX投稿を追加しました！\n\n{preview}\n\n2〜3分でRenderに反映されます。"
        ))
    else:
        print(f"diary tweet stock update error: {r2.status_code}")


def add_diary_memo(memo_text):
    """LINEからのメモを今日の日記ページに時刻付きで追記する"""
    import requests as req
    notion_token = os.environ.get('NOTION_TOKEN', '')
    if not notion_token:
        return False
    now = datetime.datetime.now(JST)
    today_str = now.strftime('%Y-%m-%d')
    time_str = now.strftime('%H:%M')
    page_id = find_or_create_diary_page(notion_token, today_str)
    if not page_id:
        return False
    r = req.patch(
        f"https://api.notion.com/v1/blocks/{page_id}/children",
        headers={
            "Authorization": f"Bearer {notion_token}",
            "Notion-Version": "2022-06-28",
            "Content-Type": "application/json"
        },
        json={"children": [{
            "object": "block",
            "type": "bulleted_list_item",
            "bulleted_list_item": {
                "rich_text": [{"type": "text", "text": {"content": f"{time_str} {memo_text}"}}]
            }
        }]}
    )
    return r.status_code == 200


def auto_improve_tweet_stock(top_tweets_text, analysis_text):
    """トップ型の投稿を3本生成→GitHub APIでTWEET_STOCKに自動追加→Renderが自動デプロイ"""
    import base64
    import requests as req_lib

    github_token = (os.environ.get('GITHUB_TOKEN') or '').strip()
    if not github_token:
        return

    repo = 'makiko01035/maki-hisho'
    file_path = 'main.py'
    headers = {
        'Authorization': f'token {github_token}',
        'Accept': 'application/vnd.github.v3+json'
    }
    line_uid = os.environ.get('LINE_USER_ID', '')

    # 新投稿3本を生成
    gen_prompt = (
        "あなたはXアカウント（@maki_claude_lab：医療職×3児ワンオペ×AIで日常が楽になった実体験を発信）の投稿担当です。\n"
        "アカウント方針：「AIって何に使うの？」と思っているワーママ・AI初心者に、日常が楽になった実体験を届ける。副業色より『毎朝LINEで予定が届く』『プリント1枚で自動登録』『忘れなくなった』などの日常変化を中心に。\n"
        "以下の分析を参考に、同じ型の新しい投稿を3本作成してください。\n\n"
        f"【先週のトップ投稿（参考）】\n{top_tweets_text}\n\n"
        f"【分析結果】\n{analysis_text}\n\n"
        "条件：\n"
        "- 各投稿は140文字以内\n"
        "- ハッシュタグは #ワーママ #AI秘書 #子育て #AI #ClaudeCode #時短 のいずれか1〜2個\n"
        "- まきの等身大の言葉・ですます調で書く\n"
        "- 3本を1行ずつ、番号なし・余計な説明なしで出力"
    )
    try:
        gen_resp = anthropic_client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=500,
            messages=[{"role": "user", "content": gen_prompt}]
        )
        new_tweets = [
            t.strip() for t in gen_resp.content[0].text.strip().split('\n')
            if t.strip() and len(t.strip()) > 10
        ][:3]
    except Exception as e:
        print(f"auto_improve generate error: {e}")
        return

    if not new_tweets:
        return

    # GitHub APIでmain.pyを取得
    r = req_lib.get(f'https://api.github.com/repos/{repo}/contents/{file_path}', headers=headers)
    if r.status_code != 200:
        print(f"GitHub get error: {r.status_code}")
        return

    data = r.json()
    sha = data['sha']
    content = base64.b64decode(data['content']).decode('utf-8')

    # TWEET_STOCKの末尾（最初の ^\] の位置）に追加
    stock_start = content.find('TWEET_STOCK = [')
    if stock_start == -1:
        return
    stock_end = content.find('\n]', stock_start)
    if stock_end == -1:
        return

    insert_lines = '\n'.join([f'    "{t}",' for t in new_tweets])
    new_content = content[:stock_end] + '\n' + insert_lines + content[stock_end:]

    # GitHub APIでコミット
    today = datetime.date.today().strftime('%Y-%m-%d')
    commit_msg = f"広報部PDCA：{today} トップ型投稿を{len(new_tweets)}本自動追加"
    update_payload = {
        'message': commit_msg,
        'content': base64.b64encode(new_content.encode('utf-8')).decode('utf-8'),
        'sha': sha,
        'branch': 'main'
    }
    r2 = req_lib.put(
        f'https://api.github.com/repos/{repo}/contents/{file_path}',
        headers=headers, json=update_payload
    )
    if r2.status_code in (200, 201):
        preview = '\n'.join([f'・{t[:35]}…' for t in new_tweets])
        if line_uid:
            line_bot_api.push_message(line_uid, TextSendMessage(
                text=f"✅ TWEET_STOCKを自動更新！\n\n追加した投稿（{len(new_tweets)}本）：\n{preview}\n\n2〜3分でRenderに反映されます。"
            ))
    else:
        print(f"GitHub put error: {r2.status_code} {r2.text[:200]}")


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


# --- 楽天アフィ：Threads自動投稿（毎日5本 朝1・昼1・夕1・夜2）---
# 投稿ルール：本文にURLなし・URLはコメント欄・PR表記必須・1時間以上間隔

ROOM_GENRES = [
    # 5月〜夏向け高料率ジャンル優先
    {'name': 'UV・日焼け止め', 'keyword': '日焼け止め UV SPF ママ 子ども おすすめ'},
    {'name': '冷感寝具', 'keyword': '冷感 敷きパッド 枕パッド 夏 快眠'},
    {'name': '父の日ギフト', 'keyword': '父の日 ギフト プレゼント おすすめ 人気'},
    {'name': '虫除け', 'keyword': '虫除け 虫よけ 子ども アウトドア スプレー'},
    {'name': '美容サプリ', 'keyword': 'サプリ コラーゲン 美容 飲む 女性 おすすめ'},
    {'name': '育児', 'keyword': 'ベビー 育児グッズ ママ おすすめ 人気'},
    {'name': 'スキンケア', 'keyword': 'スキンケア 化粧水 美容液 夏 毛穴 おすすめ'},
]

LINK_PHRASES = [
    # 権威・社会的証明型
    "楽天1位、納得🙂‍↕️\n",
    "SNSでバズってた理由が分かる...\n",
    "美容マニアの友達に激プッシュされたやつ\n",
    # 価格型
    "値段バグなんよ...\n",
    "価格見て二度見した\n",
    "マラソンで価格おかしいことになってる\n",
    # 口コミ型
    "口コミ見たら泣いた\n",
    "★4.8の理由、見ればわかる\n",
    "口コミに『もっと早く買えば』多すぎ\n",
    "低評価レビューが参考になる...\n",
    # 緊急性型
    "前回は3日で売り切れてた😭\n",
    "再販待ってた人、今出てる\n",
    "在庫残りわずかって出てる\n",
]

HOOKS = [
    # 驚き・発見系
    "正直ノーマークだった。",
    "完全ノーマークだった、",
    "事件です。",
    "大変なことが起こりました",
    "天才やん...",
    "信じられないんですが、",
    "センスいい人しかきづいてないけど、",
    # 共感・呼びかけ系
    "出産前に知りたかった...",
    "男の子ママ...聞こえますか......",
    "3年悩んだアレ、1日で解決した。",
    "寝かしつけ1時間かかってる人へ。",
    "夫へ。",
    "100回以上言ってるけど、",
    "勘違いしている人が多いですが、",
    "今すぐやめて！！",
    # 愛用・推し系
    "私が愛してやまない、",
    "私の推しの、",
    "浮気します...",
    "これ内緒にして欲しいのですが、",
    "批判されそうですが...",
    "ずっと我慢してたけど買った。",
    # 価格・お得系
    "値段バグなんよ...",
    "これで1,000円台？",
    "価格見て二度見した",
    "マラソンで価格おかしいことになってる",
    # 口コミ・実績系
    "楽天1位、納得🙂‍↕️",
    "SNSでバズってた理由が分かる...",
    "口コミ見たら泣いた",
    "口コミに『もっと早く買えば』多すぎ",
    "★4.8の理由、見ればわかる",
    # 緊急性系
    "前回は3日で売り切れてた😭",
    "再販待ってた人、今出てる",
    "在庫残りわずかって出てる",
]


def post_to_threads(text, image_url=None):
    """Threads APIに投稿する。image_urlがあれば画像付き投稿。成功時は投稿IDを返す、失敗時はNone"""
    access_token = os.environ.get('THREADS_ACCESS_TOKEN', '')
    user_id = os.environ.get('THREADS_USER_ID', '')
    if not access_token or not user_id:
        print("Threads credentials not set")
        return None
    try:
        params = {'text': text, 'access_token': access_token}
        if image_url:
            params['media_type'] = 'IMAGE'
            params['image_url'] = image_url
        else:
            params['media_type'] = 'TEXT'
        container_res = requests.post(
            f'https://graph.threads.net/v1.0/{user_id}/threads',
            params=params,
            timeout=15
        )
        if container_res.status_code != 200:
            print(f"Threads container error: {container_res.status_code} {container_res.text}")
            return None
        creation_id = container_res.json().get('id')
        publish_res = requests.post(
            f'https://graph.threads.net/v1.0/{user_id}/threads_publish',
            params={'creation_id': creation_id, 'access_token': access_token},
            timeout=15
        )
        if publish_res.status_code != 200:
            print(f"Threads publish error: {publish_res.status_code} {publish_res.text}")
            return None
        post_id = publish_res.json().get('id')
        print(f"Threads posted: {post_id}")
        return post_id
    except Exception as e:
        print(f"post_to_threads error: {e}")
        return None


def reply_to_threads(post_id, text):
    """Threads投稿にリプライ（URLをコメント欄に貼る用）"""
    access_token = os.environ.get('THREADS_ACCESS_TOKEN', '')
    user_id = os.environ.get('THREADS_USER_ID', '')
    if not access_token or not user_id:
        return False
    try:
        container_res = requests.post(
            f'https://graph.threads.net/v1.0/{user_id}/threads',
            params={
                'media_type': 'TEXT',
                'text': text,
                'reply_to_id': post_id,
                'access_token': access_token,
            },
            timeout=15
        )
        if container_res.status_code != 200:
            print(f"Threads reply container error: {container_res.status_code} {container_res.text}")
            return False
        creation_id = container_res.json().get('id')
        publish_res = requests.post(
            f'https://graph.threads.net/v1.0/{user_id}/threads_publish',
            params={'creation_id': creation_id, 'access_token': access_token},
            timeout=15
        )
        return publish_res.status_code == 200
    except Exception as e:
        print(f"reply_to_threads error: {e}")
        return False


def _fetch_room_suggestion(genre):
    """楽天APIで商品1件取得 → Claude投稿文生成。(本文, url, image_url) のタプルを返す"""
    import random
    from blog_yakuzen import search_rakuten_items
    items = search_rakuten_items(genre['keyword'], hits=5)
    if not items:
        return None, None, None
    item = random.choice(items)
    name = item['name'][:40]
    price = item['price']
    url = item['url']
    image_url = item.get('image', '')
    hook = random.choice(HOOKS)
    prompt = (
        f"商品名：{name}\n価格：{price}円\nジャンル：{genre['name']}\n\n"
        f"Threads投稿文を作ってください。\n"
        f"冒頭は必ず「{hook}」で始める。\n"
        "ルール：\n"
        "・全体3行以内（改行込み）\n"
        "・URLもハッシュタグも含めない（どちらも別途対応）\n"
        "・ですます調NG・口語・体言止めOK\n"
        "・主語は「私」ではなく「あなた」視点で書く\n"
        "・機能ではなく『使った後の未来・変化』を伝える\n"
        "  例）「保温機能付き」→「朝淹れたコーヒー昼でも温かい」\n"
        "  例）「防水仕様」→「子どもとのお風呂時間が快適に」\n"
        "・30〜40代ワーママに刺さる言葉を使う\n\n"
        "余計な説明不要。投稿文だけ出力。"
    )
    try:
        resp = anthropic_client.messages.create(
            model='claude-haiku-4-5-20251001',
            max_tokens=300,
            messages=[{'role': 'user', 'content': prompt}]
        )
        body = resp.content[0].text.strip()
    except Exception as e:
        print(f"room suggestion generate error ({genre['name']}): {e}")
        body = f"{hook}\n{name}"
    return body, url, image_url


def send_room_suggestion_slot(slot_index):
    """指定スロットのジャンルをThreadsに投稿（商品画像付き本文→コメントにURL+PR）"""
    genre = ROOM_GENRES[slot_index % len(ROOM_GENRES)]
    try:
        body, url, image_url = _fetch_room_suggestion(genre)
        if not body or not url:
            return
        post_id = post_to_threads(body)
        if post_id:
            import time
            time.sleep(3)
            import random as _random
            phrase = _random.choice(LINK_PHRASES)
            reply_to_threads(post_id, f"{phrase}{url}\n[楽天PR]")
    except Exception as e:
        print(f"send_room_suggestion_slot({slot_index}) error: {e}")


def send_threads_token_reminder():
    try:
        line_bot_api.push_message(os.environ['LINE_USER_ID'], TextSendMessage(
            text="🧵【Threadsトークン期限切れ注意】\nThreads自動投稿のトークンが期限切れになる時期です！\n\nMeta開発者ダッシュボード→Graph APIエクスプローラーで新しいトークンを取得して、RenderのTHREADS_ACCESS_TOKENを更新してね✅"
        ))
    except Exception as e:
        print(f"send_threads_token_reminder error: {e}")


# --- こはるまま：@kvision_m 旅行×楽天アフィ X自動投稿（1日2本）---

TRAVEL_GENRES = [
    # 通年固定
    {'name': 'シアーパーカー・UVカット羽織り', 'keyword': 'シアーパーカー UVカット 羽織り レディース 冷房対策 薄手'},
    {'name': '折りたたみ日傘', 'keyword': '日傘 折りたたみ 完全遮光 軽量 UVカット レディース'},
    {'name': 'アウトドアワゴン', 'keyword': 'キャリーワゴン アウトドア 折りたたみ 大容量 子連れ レジャー'},
    {'name': 'メッシュトート・おでかけバッグ', 'keyword': 'トートバッグ メッシュ 大容量 アウトドア 軽量 レディース'},
    {'name': 'ハンディファン・暑さ対策', 'keyword': 'ハンディファン 携帯扇風機 軽量 USB 旅行 暑さ対策'},
    {'name': 'ブラトップ・機能性インナー', 'keyword': 'ブラトップ 機能性インナー 旅行 快適 レディース'},
    {'name': '旅行ポーチ・トラベルコスメ', 'keyword': '旅行ポーチ トラベル コスメ 防水 コンパクト 化粧品'},
    # ロングシーズン追加
    {'name': 'キャンプ・BBQグッズ', 'keyword': 'キャンプ BBQ グッズ 子連れ アウトドア 便利 コンパクト'},
    {'name': 'キャンプ・ランタン・焚き火', 'keyword': 'ランタン 焚き火台 キャンプ アウトドア おしゃれ コンパクト'},
    {'name': 'スーツケース・旅行バッグ', 'keyword': 'スーツケース 軽量 旅行 キャリー 子連れ レディース'},
    {'name': '子ども旅行グッズ・新幹線の暇つぶし', 'keyword': '子ども 旅行 暇つぶし 新幹線 おもちゃ 知育 コンパクト'},
    {'name': '時短・便利キッチングッズ', 'keyword': '時短 便利 キッチン グッズ ワーママ おすすめ'},
    {'name': 'お取り寄せグルメ・手土産', 'keyword': 'お取り寄せ グルメ 手土産 旅行 ご当地 人気 ギフト'},
]

TRAVEL_HOOKS = [
    "先週の旅行で大活躍したのが",
    "子連れ旅でこれ持ってくよかった",
    "旅行前日に気づいた神アイテム",
    "ホテルで「買ってきてよかった」ってなったのが",
    "子どもがぐずりだしたときに救われたのが",
    "旅行バッグに必ず入れてるの、これ",
    "去年の旅行で後悔して今年買ったのが",
    "旅先で肌がボロボロになってから使い始めたのが",
    "子連れ旅行、荷物減らすために買ったのが",
    "夏の旅行に絶対持っていきたいのが",
    "キャンプ行くとき絶対持っていくのが",
    "おでかけバッグに毎回入れてるのが",
    "庭ピクのときに大活躍してるのが",
    "BBQ前日に「これ買っといてよかった」ってなったのが",
    "子どもと一緒に使えて買ってよかったのが",
    "ワーママ的に時間が減ったきっかけになったのが",
    "帰省のときに持っていって正解だったのが",
    "旅行のお土産じゃなくて、旅行前に買いたいのが",
]

MAKO_THREADS_MORNING = [
    "夜中に何度も目が覚める…\n\nそういう方、思った以上に多いんです。\n\n「眠れない」より「眠りが浅い」という感覚。\n\n原因の一つに、就寝後の体温調節がうまくいっていないことがあるかもしれません。\n\n入浴で体を温めてから自然に冷ます流れが、深い眠りに入りやすくなる方もいます。",
    "夜になると考えすぎてしまう、という方いませんか？\n\n頭が静まらないまま布団に入ると、なかなか眠れないことも。\n\n東洋医学的には「心」の気が乱れている状態かもしれません。\n\nゆっくり吐く呼吸を意識するだけで、少し落ち着く方もいます。",
    "疲れているのに眠れない…\n\nこれって結構つらいですよね。\n\n「疲労」と「眠気」は別物で、体は疲れていても脳が興奮していると眠れないことがあります。\n\n就寝1時間前にブルーライトを避けると、改善した方もいます。\n\n小さなことですが、試してみる価値はあるかもしれません。",
    "更年期に入ってから眠りが浅くなった、という声を聞くことがあります。\n\nエストロゲンの変動が自律神経に影響し、体温調節が乱れやすくなることが関係しているかもしれません。\n\n薬膳的には、血を補う食材（なつめ・クコの実・黒ごまなど）が助けになるという方もいます。",
    "睡眠は「量」より「質」という話があります。\n\n6時間でも深く眠れる方もいれば、8時間眠っても疲れが取れない方もいます。\n\n睡眠の質に関わる要素は体温・光・音・寝具・ストレスなど様々。\n\n一つずつ試してみるのが遠回りに見えて近道かもしれません。",
    "子育て中のママって、眠れない理由が本当に多いですよね。\n\n子どもの夜泣き・翌日の段取りへの不安・自分だけの時間への渇望…\n\n眠れない夜に「何かできることはないか」と考えてしまう気持ち、わかります。\n\nまず「眠れなくてもOK」と思えると、少し体の力が抜けることもあるかもしれません。",
    "漢方や薬膳に興味はあるけど、どこから始めればいいか分からない…\n\nそういう声、よく聞きます。\n\nスーパーで買えるなつめ・黒豆・くるみ・黒ごまは、東洋医学で「腎」を補い、睡眠と深く関わるとされています。\n\n日々の料理に少し取り入れるだけでも、変化を感じる方もいます。",
    "「疲れているのに眠れない」「眠っても疲れが取れない」\n\nこの2つはちょっと違う問題かもしれません。\n\n前者は睡眠に入れないこと、後者は睡眠の質の問題です。\n\nどちらも辛いですが、対策も少し違うことがあります。\n\nブログでも詳しく書いています。",
    "マグネシウムが睡眠に関係している、という話があります。\n\n神経の興奮を抑え、体をリラックスさせる働きがあるとされています。\n\n海藻・ナッツ・ほうれん草などに含まれています。\n\n食事から意識するのも一つかもしれません。",
    "眠る前のスマホ、なんとなく分かっていてもやめられない…\n\nブルーライトがメラトニン（眠気を作るホルモン）の分泌を抑えるという研究があります。\n\n完全にやめなくても、画面を暗くする・ナイトモードにするだけで変わる方もいます。\n\n小さな工夫から始めてみませんか？",
]

MAKO_THREADS_AFF_GENRES = [
    {'name': '睡眠サプリ（GABA）', 'keyword': 'GABA 睡眠 サプリ'},
    {'name': 'マグネシウムサプリ', 'keyword': 'マグネシウム サプリ 睡眠'},
    {'name': 'なつめ薬膳食材', 'keyword': 'なつめ 薬膳 乾燥'},
    {'name': '睡眠アイマスク', 'keyword': 'アイマスク 睡眠 遮光'},
    {'name': 'クコの実', 'keyword': 'クコの実 薬膳 乾燥'},
    {'name': '漢方（睡眠）', 'keyword': '漢方 睡眠 改善'},
    {'name': '睡眠枕', 'keyword': '枕 睡眠 低反発'},
]

TRAVEL_MORNING_TWEETS = [
    "子連れ旅行の荷物、毎回多すぎて笑う。でもこれが楽しいんだよな",
    "旅行前夜のパッキングが一番楽しい説、わかる人いる？",
    "キャンプって準備が9割だと思ってる。道具選びが趣味になってきた",
    "旅先でお気に入りの日傘壊れたとき、あれは本当に悲しかった",
    "子どもと旅行するとき「これ荷物になるかな」って毎回悩む",
    "夏のおでかけは暑さ対策グッズを制した人が勝つ",
    "家族でBBQ、準備と片付けが大変すぎる問題。でも楽しい",
    "旅先で日焼けしすぎてヒリヒリしながら「なんで対策しなかったんだ」って毎年思う",
    "子連れで荷物多いのに、バッグの中がぐちゃぐちゃになるのをなんとかしたい",
    "旅行中、子どもが「暑い暑い」って言い始めるタイミングが毎回同じ",
]

KOHARU_ROOM_URL = "https://room.rakuten.co.jp/makiko01035/items"

ROOM_INTRO_TWEETS = [
    "私が実際に買ってよかったもの、ROOMにまとめてます\n子連れ旅行・アウトドア・日常使いのグッズを厳選してるので参考にしてみて↓",
    "旅行グッズ・キャンプ道具・便利グッズ、使ってよかったものを楽天ROOMにまとめてます\n購入前の参考にどうぞ↓",
    "子連れ旅行で本当に使えたものだけROOMに残してる\n301件あるのでお気に入り登録しておいてもらえると嬉しい↓",
    "ワーママ目線で厳選した旅行・生活グッズ、楽天ROOMにまとめてます\n気になるものあったら見てみて↓",
    "子ども連れのおでかけに役立つグッズ、全部ここにまとめてます\n使ってよかったものだけ厳選↓",
]


def post_kvision_room_intro():
    """週1回（木曜）夜19時：楽天ROOM誘導投稿（X）"""
    import random, time as _time
    try:
        client = _get_kvision_x_client()
        if not client:
            print("KVISION X API keys not configured, skipping room intro")
            return
        text = random.choice(ROOM_INTRO_TWEETS)
        resp = client.create_tweet(text=text)
        tweet_id = resp.data['id']
        _time.sleep(3)
        client.create_tweet(
            text=KOHARU_ROOM_URL,
            in_reply_to_tweet_id=tweet_id
        )
        print("kvision room intro tweet successful")
    except Exception as e:
        print(f"post_kvision_room_intro error: {e}")


def post_koharu_threads_room_intro():
    """週1回（木曜）夜19時：楽天ROOM誘導投稿（Threads）"""
    import random, time as _time
    try:
        text = random.choice(ROOM_INTRO_TWEETS)
        post_id = _post_to_koharu_threads(text)
        if post_id:
            _time.sleep(5)
            _post_to_koharu_threads(KOHARU_ROOM_URL, reply_to_id=post_id)
        print("koharu threads room intro successful")
    except Exception as e:
        print(f"post_koharu_threads_room_intro error: {e}")


def _get_kvision_x_client():
    import tweepy
    api_key = (os.environ.get('KVISION_X_API_KEY') or '').strip()
    api_secret = (os.environ.get('KVISION_X_API_SECRET') or '').strip()
    access_token = (os.environ.get('KVISION_X_ACCESS_TOKEN') or '').strip()
    access_token_secret = (os.environ.get('KVISION_X_ACCESS_TOKEN_SECRET') or '').strip()
    if not all([api_key, api_secret, access_token, access_token_secret]):
        return None
    return tweepy.Client(
        consumer_key=api_key,
        consumer_secret=api_secret,
        access_token=access_token,
        access_token_secret=access_token_secret
    )


def _fetch_travel_suggestion(genre):
    """楽天APIで旅行グッズ1件取得 → Claude投稿文生成。(本文, url) のタプルを返す"""
    import random
    from blog_yakuzen import search_rakuten_items
    items = search_rakuten_items(genre['keyword'], hits=5)
    if not items:
        return None, None
    item = random.choice(items)
    name = item['name'][:40]
    price = item['price']
    url = item['url']
    hook = random.choice(TRAVEL_HOOKS)
    prompt = (
        f"商品名：{name}\n価格：{price}円\nジャンル：{genre['name']}\n\n"
        f"X（旧Twitter）投稿文を作ってください。\n"
        f"冒頭は必ず「{hook}」で始める。\n"
        "ルール：\n"
        "・全体3行以内（改行込み）\n"
        "・URLもハッシュタグも含めない（どちらも別途対応）\n"
        "・ですます調NG・口語・体言止めOK\n"
        "・機能ではなく『使った後の未来・変化』を伝える\n"
        "・30〜40代子連れ旅行好きワーママに刺さる言葉を使う\n\n"
        "余計な説明不要。投稿文だけ出力。"
    )
    try:
        resp = anthropic_client.messages.create(
            model='claude-haiku-4-5-20251001',
            max_tokens=300,
            messages=[{'role': 'user', 'content': prompt}]
        )
        body = resp.content[0].text.strip()
    except Exception as e:
        print(f"travel suggestion generate error ({genre['name']}): {e}")
        body = f"{hook}\n{name}"
    return body, url


def post_kvision_morning_tweet():
    """@kvision_m 朝9:00：テキストのみ・旅あるあるつぶやき"""
    import random
    try:
        client = _get_kvision_x_client()
        if not client:
            print("KVISION X API keys not configured, skipping")
            return
        text = random.choice(TRAVEL_MORNING_TWEETS)
        client.create_tweet(text=text)
        print(f"kvision morning tweet successful: {text[:30]}...")
    except Exception as e:
        print(f"post_kvision_morning_tweet error: {e}")


def post_kvision_travel_aff(slot_index):
    """@kvision_mに旅行×楽天アフィをXにスレッド形式で投稿（本文→リプライにURL）"""
    import time as _time
    all_genres = _get_all_kvision_genres()
    genre = all_genres[slot_index % len(all_genres)]
    try:
        body, url = _fetch_travel_suggestion(genre)
        if not body or not url:
            return
        client = _get_kvision_x_client()
        if not client:
            print("KVISION X API keys not configured, skipping")
            return
        resp = client.create_tweet(text=body)
        tweet_id = resp.data['id']
        _time.sleep(3)
        client.create_tweet(
            text=f"↓ 商品はこちら\n{url}\n[楽天PR]",
            in_reply_to_tweet_id=tweet_id
        )
        print(f"kvision X thread post ({genre['name']}) successful")
    except Exception as e:
        print(f"post_kvision_travel_aff({slot_index}) error: {e}")


# ========== 月替わりジャンル・ジャンル管理 ==========

def _get_monthly_kvision_genres():
    """今月の特集ジャンルをkvision_monthly_genres.jsonから取得"""
    import json
    month_key = datetime.now().strftime('%m')
    try:
        with open('kvision_monthly_genres.json', 'r', encoding='utf-8') as f:
            data = json.load(f)
        return data.get(month_key, [])
    except Exception as e:
        print(f"monthly kvision genres read error: {e}")
        return []


def _get_all_kvision_genres():
    """固定ジャンル7種 + 今月の特集ジャンルを合わせたリスト"""
    return TRAVEL_GENRES + _get_monthly_kvision_genres()


# まきが選んだ固定アフィ商品ストック（URL直指定・投稿文もまきのコメントベース）
FIXED_AFF_POSTS = [
    {
        'name': 'ポケットチェア',
        'body': "庭ピクのときとかも大活躍\n折りたためるポケットチェア、アウトドアも運動会も持って行けるサイズ感がちょうどいい",
        'url': 'https://a.r10.to/hXxfyW',
    },
    {
        'name': 'ネッククーラー',
        'body': "これからの季節あったら絶対便利\n熱中症予防に早めに用意しておきたいやつ。子連れのお出かけ前の準備リストに入れてる",
        'url': 'https://a.r10.to/hgY7sa',
    },
    {
        'name': 'ピクニックマット',
        'body': "庭ピクするのにもいつも使ってる\n天気のいい休みの日は庭でピクニックが定番。1枚あると出番がとにかく多い",
        'url': 'https://a.r10.to/hkV4yh',
    },
]


def _post_kvision_fixed_aff(post, client, time_module):
    """固定アフィ商品をXにスレッド形式で投稿"""
    resp = client.create_tweet(text=post['body'])
    tweet_id = resp.data['id']
    time_module.sleep(3)
    client.create_tweet(
        text=f"↓ 商品はこちら\n{post['url']}\n[楽天PR]",
        in_reply_to_tweet_id=tweet_id
    )
    print(f"kvision fixed aff post ({post['name']}) successful")


def post_kvision_travel_aff_auto():
    """日付ベースでローテーション。3日に1回は固定アフィストックから投稿"""
    import time as _time
    day = datetime.now().day
    if FIXED_AFF_POSTS and day % 3 == 0:
        post = FIXED_AFF_POSTS[(day // 3) % len(FIXED_AFF_POSTS)]
        client = _get_kvision_x_client()
        if not client:
            print("KVISION X API keys not configured, skipping")
            return
        try:
            _post_kvision_fixed_aff(post, client, _time)
        except Exception as e:
            print(f"post_kvision_fixed_aff error: {e}")
    else:
        all_genres = _get_all_kvision_genres()
        slot = day % len(all_genres)
        post_kvision_travel_aff(slot)


# ========== 楽天カード誘導ツイート ==========

CARD_TWEETS = [
    "楽天トラベルで旅行予約するとき、楽天カードで払うとポイントが3〜4倍になる話、旅行前の自分に教えてあげたかった",
    "旅行費用の積立を楽天カードに変えてから、気づいたら年間けっこうなポイントに。子どもの旅行代金に充当してる",
    "楽天プレミアムカード、持ってると空港ラウンジが無料で使える。子連れ旅行の出発前の休憩にこれが地味に最高",
    "楽天マラソン×楽天カードの組み合わせ、旅行グッズのまとめ買いするなら知っておいたほうがいいやつ",
    "海外旅行の荷物リストに「楽天カード」を追加してから、旅先での買い物ポイントが馬鹿にならない",
    "楽天カードの旅行保険、カードで予約すれば付帯されるのに使ってない人多すぎる。子連れ旅行なら確認して",
    "旅行貯金を楽天カードのポイントで賄ってる。現金じゃないから心理的ハードルが低くて続いてる",
]

RAKUTEN_CARD_AFF_URLS = [
    "https://a.r10.to/hkZjJw",
    "https://a.r10.to/h5E1Na",
    "https://a.r10.to/h5yRmz",
    "https://a.r10.to/h5v161",
    "https://a.r10.to/h55SLT",
    "https://a.r10.to/hksjy7",
    "https://a.r10.to/h5MZJX",
    "https://a.r10.to/hYrRdd",
    "https://a.r10.to/h5aOuJ",
    "https://a.r10.to/h57LDq",
]

CARD_AFF_TWEETS_WITH_URL = [
    "楽天カード、旅行好きには正直マストだと思ってる。楽天トラベルのポイント還元が段違い\n\n詳細はこちら↓",
    "子連れ旅行のコスト、楽天カードのポイントで少し軽くできてる。年会費無料でこの恩恵は大きい\n\n詳細はこちら↓",
    "楽天プレミアムカードの空港ラウンジ特典、旅行好きなら元が取れる。子連れ出発前の待機場所として最高\n\n詳細はこちら↓",
    "旅行前にとりあえず楽天カードで予約する癖をつけてから、ポイントがどんどん貯まるようになった\n\n詳細はこちら↓",
    "楽天カードの旅行保険、カードで予約するだけで付帯されるの知ってた？子連れ旅行なら絶対確認して\n\n詳細はこちら↓",
    "子ども連れの旅費って地味にかさむ。楽天カードのポイント還元で少しでも圧縮するのがマイルール\n\n詳細はこちら↓",
    "楽天マラソン前に楽天カード作っておくと、まとめ買いのポイントが倍以上変わる話\n\n詳細はこちら↓",
]


def _pick_card_url():
    """楽天カードアフィURLをランダムに1つ返す"""
    import random
    return random.choice(RAKUTEN_CARD_AFF_URLS)


def post_kvision_card_tweet():
    """週2回（水・土）昼12:30：楽天カード誘導ツイート（スレッド形式）"""
    import random, time as _time
    try:
        client = _get_kvision_x_client()
        if not client:
            print("KVISION X API keys not configured, skipping card tweet")
            return
        text = random.choice(CARD_AFF_TWEETS_WITH_URL)
        resp = client.create_tweet(text=text)
        tweet_id = resp.data['id']
        _time.sleep(3)
        client.create_tweet(
            text=f"{_pick_card_url()}\n[楽天PR]",
            in_reply_to_tweet_id=tweet_id
        )
        print("kvision card tweet successful")
    except Exception as e:
        print(f"post_kvision_card_tweet error: {e}")


# ========== こはるまま Threads自動投稿 ==========

def _post_to_koharu_threads(text, reply_to_id=None):
    """こはるままのThreads APIに投稿。成功時はpost_idを返す、失敗時はNone"""
    access_token = os.environ.get('KOHARU_THREADS_ACCESS_TOKEN', '').strip()
    user_id = os.environ.get('KOHARU_THREADS_USER_ID', '').strip()
    if not access_token or not user_id:
        print("KOHARU_THREADS tokens not configured, skipping")
        return None
    try:
        params = {
            'media_type': 'TEXT',
            'text': text,
            'access_token': access_token,
        }
        if reply_to_id:
            params['reply_to_id'] = reply_to_id
        container_res = requests.post(
            f'https://graph.threads.net/v1.0/{user_id}/threads',
            params=params,
            timeout=15
        )
        if container_res.status_code != 200:
            print(f"koharu threads container error: {container_res.status_code} {container_res.text}")
            return None
        creation_id = container_res.json().get('id')
        import time as _time
        _time.sleep(5)
        publish_res = requests.post(
            f'https://graph.threads.net/v1.0/{user_id}/threads_publish',
            params={'creation_id': creation_id, 'access_token': access_token},
            timeout=15
        )
        if publish_res.status_code != 200:
            print(f"koharu threads publish error: {publish_res.status_code} {publish_res.text}")
            return None
        post_id = publish_res.json().get('id')
        print(f"koharu threads posted: {post_id}")
        return post_id
    except Exception as e:
        print(f"_post_to_koharu_threads error: {e}")
        return None


def post_koharu_threads_morning():
    """こはるまま Threads朝7:30：旅あるあるテキスト投稿"""
    import random
    try:
        text = random.choice(TRAVEL_MORNING_TWEETS)
        _post_to_koharu_threads(text)
        print(f"koharu threads morning post successful: {text[:30]}...")
    except Exception as e:
        print(f"post_koharu_threads_morning error: {e}")


def post_koharu_threads_aff_auto():
    """こはるまま Threads夜20:00：旅行グッズアフィ投稿（日付ローテーション・Xとずらす）3日に1回は固定ストック"""
    import time as _time
    day = datetime.now().day
    if FIXED_AFF_POSTS and (day + 1) % 3 == 0:
        post = FIXED_AFF_POSTS[((day + 1) // 3) % len(FIXED_AFF_POSTS)]
        try:
            post_id = _post_to_koharu_threads(post['body'])
            if post_id:
                _time.sleep(5)
                _post_to_koharu_threads(f"↓ 商品はこちら\n{post['url']}\n[楽天PR]", reply_to_id=post_id)
            print(f"koharu threads fixed aff post ({post['name']}) successful")
        except Exception as e:
            print(f"koharu threads fixed aff error: {e}")
        return
    all_genres = _get_all_kvision_genres()
    slot = (day + 3) % len(all_genres)
    genre = all_genres[slot]
    try:
        body, url = _fetch_travel_suggestion(genre)
        if not body or not url:
            return
        post_id = _post_to_koharu_threads(body)
        if post_id and url:
            _time.sleep(5)
            _post_to_koharu_threads(f"↓ 商品はこちら\n{url}\n[楽天PR]", reply_to_id=post_id)
        print(f"koharu threads aff post ({genre['name']}) successful")
    except Exception as e:
        print(f"post_koharu_threads_aff_auto error: {e}")


def post_koharu_threads_card():
    """こはるまま Threads週2回（水・土）12:30：楽天カード誘導（スレッド形式）"""
    import random, time as _time
    try:
        text = random.choice(CARD_AFF_TWEETS_WITH_URL)
        post_id = _post_to_koharu_threads(text)
        if post_id:
            _time.sleep(5)
            _post_to_koharu_threads(f"{_pick_card_url()}\n[楽天PR]", reply_to_id=post_id)
        print("koharu threads card post successful")
    except Exception as e:
        print(f"post_koharu_threads_card error: {e}")


# ========== MAKO Threads自動投稿 ==========

def _post_to_mako_threads(text, reply_to_id=None):
    """MAKOのThreads APIに投稿。成功時はpost_idを返す、失敗時はNone"""
    import time as _time
    access_token = os.environ.get('MAKO_THREADS_ACCESS_TOKEN', '').strip()
    user_id = os.environ.get('MAKO_THREADS_USER_ID', '').strip()
    if not access_token or not user_id:
        print("MAKO_THREADS tokens not configured, skipping")
        return None
    try:
        params = {
            'media_type': 'TEXT',
            'text': text,
            'access_token': access_token,
        }
        if reply_to_id:
            params['reply_to_id'] = reply_to_id
        container_res = requests.post(
            f'https://graph.threads.net/v1.0/{user_id}/threads',
            params=params,
            timeout=15
        )
        if container_res.status_code != 200:
            print(f"mako threads container error: {container_res.status_code} {container_res.text}")
            return None
        creation_id = container_res.json().get('id')
        _time.sleep(5)
        publish_res = requests.post(
            f'https://graph.threads.net/v1.0/{user_id}/threads_publish',
            params={'creation_id': creation_id, 'access_token': access_token},
            timeout=15
        )
        if publish_res.status_code != 200:
            print(f"mako threads publish error: {publish_res.status_code} {publish_res.text}")
            return None
        post_id = publish_res.json().get('id')
        print(f"mako threads posted: {post_id}")
        return post_id
    except Exception as e:
        print(f"_post_to_mako_threads error: {e}")
        return None


def _fetch_mako_sleep_suggestion(genre):
    """楽天APIで睡眠グッズ・サプリ1件取得 → Claude投稿文生成（MAKOトーン）。(本文, url) を返す"""
    import random
    from blog_yakuzen import search_rakuten_items
    items = search_rakuten_items(genre['keyword'], hits=5)
    if not items:
        return None, None
    item = random.choice(items)
    name = item['name'][:40]
    price = item['price']
    url = item['url']
    prompt = (
        f"商品名：{name}\n価格：{price}円\nジャンル：{genre['name']}\n\n"
        "Threads投稿文を作ってください（本文のみ。URLなし）。\n"
        "ルール：\n"
        "・医師として発信しているため「売る」方向NG\n"
        "・悩みへの共感から始める\n"
        "・医学・薬膳の知識を淡々と伝える\n"
        "・「〜です」「〜効果があります」などの言い切り表現は使わない\n"
        "・「〜かもしれません」「〜という方もいます」「試してみる価値はあります」などの柔らかい表現を使う\n"
        "・商品への言及は最後の1行で「気になる方はこちら」程度\n"
        "・ハッシュタグなし\n"
        "・全体150文字以内\n\n"
        "余計な説明不要。投稿文だけ出力。"
    )
    try:
        resp = anthropic_client.messages.create(
            model='claude-haiku-4-5-20251001',
            max_tokens=400,
            messages=[{'role': 'user', 'content': prompt}]
        )
        body = resp.content[0].text.strip()
    except Exception as e:
        print(f"mako sleep suggestion generate error ({genre['name']}): {e}")
        body = f"眠れない夜が続いているという方もいるかもしれません。\n\n気になる方はこちら"
    return body, url


def post_mako_threads_morning():
    """MAKO Threads朝8:00：睡眠共感ツイート"""
    import random
    try:
        text = random.choice(MAKO_THREADS_MORNING)
        _post_to_mako_threads(text)
        print(f"mako threads morning post successful: {text[:30]}...")
    except Exception as e:
        print(f"post_mako_threads_morning error: {e}")


def post_mako_threads_aff_auto():
    """MAKO Threads夜21:00：睡眠グッズ・サプリアフィ投稿（日付ローテーション）"""
    import time as _time
    slot = datetime.now().day % len(MAKO_THREADS_AFF_GENRES)
    genre = MAKO_THREADS_AFF_GENRES[slot]
    try:
        body, url = _fetch_mako_sleep_suggestion(genre)
        if not body or not url:
            return
        post_id = _post_to_mako_threads(body)
        if post_id and url:
            _time.sleep(5)
            _post_to_mako_threads(
                f"気になる方はこちら\n{url}\n[楽天PR]",
                reply_to_id=post_id
            )
        print(f"mako threads aff post ({genre['name']}) successful")
    except Exception as e:
        print(f"post_mako_threads_aff_auto error: {e}")


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
# 月・木 8:00：7stepパイプラインで新規記事を自動作成
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
            if passed:
                phase6_publish.run(keyword, final)
        except Exception as e:
            print(f"auto_blog_new error: {e}")
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
            if passed:
                # 新規投稿ではなく既存記事を上書き更新＋カテゴリ変更
                phase6_publish.run_update(keyword, final, post_id)
        except Exception as e:
            print(f"auto_blog_rewrite error: {e}")
    threading.Thread(target=_run, daemon=True).start()

scheduler.add_job(auto_blog_new,     'cron', day_of_week='mon,thu', hour=8, minute=0)
scheduler.add_job(auto_blog_rewrite, 'cron', day_of_week='wed,sat', hour=8, minute=0)
# 毎週火曜朝9時：薬膳ブログ更新リマインダー（手動対応用）
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
# 2026-05-25 朝9時：MAKOのX実装 + こはるままThreads連携リマインド（一回限り）
scheduler.add_job(send_may25_reminder, 'date', run_date='2026-05-25 09:00:00')
# 2026-05-30 朝9時：MAKOのThreads連携リマインド（一回限り）
scheduler.add_job(send_may30_reminder, 'date', run_date='2026-05-30 09:00:00')
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
scheduler.add_job(send_note_weekly_reminder, 'cron', day_of_week='thu', hour=9, minute=5)
# 毎週月曜9時40分：週次Xパフォーマンスレポート（PDCA用）
scheduler.add_job(send_x_weekly_report, 'cron', day_of_week='mon', hour=9, minute=40)
# 毎日18時：業務ログ（今日のコミット・X投稿・AIの一言）
scheduler.add_job(send_daily_work_log, 'cron', hour=18, minute=0)
# 毎日5本：楽天アフィ→Threads自動投稿（朝1・昼1・夕1・夜2、1時間以上間隔）
# ジャンルは7種ローテーション（slot % 7）
scheduler.add_job(send_room_suggestion_slot, 'cron', hour=7, minute=30, args=[0])   # UV・日焼け止め
scheduler.add_job(send_room_suggestion_slot, 'cron', hour=12, minute=30, args=[1])  # 冷感寝具
scheduler.add_job(send_room_suggestion_slot, 'cron', hour=17, minute=30, args=[2])  # 父の日ギフト
scheduler.add_job(send_room_suggestion_slot, 'cron', hour=20, minute=0, args=[4])   # 美容サプリ（ゴールデンタイム）
scheduler.add_job(send_room_suggestion_slot, 'cron', hour=22, minute=0, args=[6])   # スキンケア
# 5月1日朝9時45分：eBay月次リセット＆リミットアップ案内
scheduler.add_job(send_ebay_reset_reminder, 'date', run_date='2026-05-01 09:45:00', timezone='Asia/Tokyo')
# 7月7日朝9時：Threadsトークン更新リマインド（60日期限）
scheduler.add_job(send_threads_token_reminder, 'date', run_date='2026-07-07 09:00:00', timezone='Asia/Tokyo')
# @kvision_m（こはるまま）旅行×楽天アフィ X：1日2本 + 楽天カード週2
# 朝9:00 旅あるあるつぶやき、夜20:30 固定＋月替わりジャンルをローテーションしてアフィスレッド
# 水・土曜12:30 楽天カード誘導ツイート（RAKUTEN_CARD_AFF_URL設定済みならURL付き）
scheduler.add_job(post_kvision_morning_tweet, 'cron', hour=9, minute=0)
scheduler.add_job(post_kvision_travel_aff_auto, 'cron', hour=20, minute=30)
scheduler.add_job(post_kvision_card_tweet, 'cron', day_of_week='wed,sat', hour=12, minute=0, jitter=3600)
# 木曜夜19時：楽天ROOM誘導投稿（前後30分ランダム）
scheduler.add_job(post_kvision_room_intro, 'cron', day_of_week='thu', hour=19, minute=0, jitter=1800)
# ========== こはるまま SNSエンジン 6ロール ==========
# ① リサーチャー：月曜 05:00（今週のテーマ生成）
scheduler.add_job(koharu_researcher, 'cron', day_of_week='mon', hour=5, minute=0)
# ② ライター：月曜 06:00（投稿案生成→AI採点→LINEで確認依頼）
scheduler.add_job(koharu_writer, 'cron', day_of_week='mon', hour=6, minute=0)
# ③ ポスター：承認済みストックから投稿（ストック切れ時はフォールバック自動生成）
scheduler.add_job(koharu_poster_morning, 'cron', hour=6, minute=30, jitter=7200)   # 6:30〜8:30のランダム
scheduler.add_job(koharu_poster_aff,     'cron', hour=19, minute=0,  jitter=7200)   # 19:00〜21:00のランダム
# カード・ROOM誘導は既存関数を継続
scheduler.add_job(post_koharu_threads_card,      'cron', day_of_week='wed,sat', hour=12, minute=0, jitter=3600)
scheduler.add_job(post_koharu_threads_room_intro,'cron', day_of_week='thu', hour=19, minute=0, jitter=1800)
# ④ コレクター：毎日 23:00（Threads APIでパフォーマンスデータ取得）
scheduler.add_job(koharu_collector, 'cron', hour=23, minute=0)
# ⑤ アナリスト：日曜 20:00（週次分析→LINEレポート）
scheduler.add_job(koharu_analyst, 'cron', day_of_week='sun', hour=20, minute=0)
# ⑥ モニター：毎日 07:00 / 13:00 / 22:00（正常稼働・凍結チェック）
scheduler.add_job(koharu_monitor, 'cron', hour=7,  minute=0)
scheduler.add_job(koharu_monitor, 'cron', hour=13, minute=0)
scheduler.add_job(koharu_monitor, 'cron', hour=22, minute=0)
# ========== MAKO SNSエンジン 6ロール（x:10 オフセット・こはるままと衝突回避）==========
# ① リサーチャー：月曜 05:10
scheduler.add_job(mako_researcher, 'cron', day_of_week='mon', hour=5, minute=10)
# ② ライター：月曜 06:10
scheduler.add_job(mako_writer, 'cron', day_of_week='mon', hour=6, minute=10)
# ③ ポスター：承認済みストックから投稿（ストック切れ時はリアルタイム生成）
scheduler.add_job(mako_poster_info, 'cron', hour=6, minute=40, jitter=7200)  # 6:40〜8:40のランダム
scheduler.add_job(mako_poster_aff,  'cron', hour=19, minute=10, jitter=7200)  # 19:10〜21:10のランダム
# ④ コレクター：毎日 23:10
scheduler.add_job(mako_collector, 'cron', hour=23, minute=10)
# ⑤ アナリスト：日曜 20:10
scheduler.add_job(mako_analyst, 'cron', day_of_week='sun', hour=20, minute=10)
# ⑥ モニター：毎日 07:10 / 13:10 / 22:10
scheduler.add_job(mako_monitor, 'cron', hour=7,  minute=10)
scheduler.add_job(mako_monitor, 'cron', hour=13, minute=10)
scheduler.add_job(mako_monitor, 'cron', hour=22, minute=10)
# MAKO Threads：1日2本（MAKO_THREADS_ACCESS_TOKEN設定後に自動稼働）
# 朝8:00 睡眠共感投稿、夜21:00 アフィスレッド（言い切りNG・共感ベース）
scheduler.add_job(post_mako_threads_morning, 'cron', hour=8, minute=0)
scheduler.add_job(post_mako_threads_aff_auto, 'cron', hour=21, minute=0)
# 毎朝6:30：eBay日本人セラー売れ筋から仕入れ候補をLINEに送信
scheduler.add_job(
    lambda: send_daily_purchase_candidates(os.environ.get('LINE_USER_ID', '')),
    'cron', hour=6, minute=30,
)
def _delayed_scheduler_start():
    time.sleep(120)
    scheduler.start()
    print("[scheduler] 起動完了（デプロイ並走防止のため120秒遅延）")

threading.Thread(target=_delayed_scheduler_start, daemon=True).start()

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
