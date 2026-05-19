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
    run_poster_morning_quote as mako_poster_morning_quote,
    run_quote_generator      as mako_quote_generator,
    handle_mako_approval,
)
from blog_yakuzen import auto_rewrite_yakuzen, process_yakuzen_new_article, process_yakuzen_rewrite, rewrite_yakuzen_by_slug, rewrite_yakuzen_by_keyword, get_pinterest_access_token, check_old_yakuzen_post, delete_yakuzen_post, kw_auto_rewrite, kw_auto_new_article, auto_blog_new, auto_blog_rewrite
from blog_sekisui import suggest_sekisui_themes, process_sekisui_article
from ebay_dashboard import ebay_bp
from calendar_manager import (
    get_calendar_service,
    get_or_create_maybe_calendar,
    get_upcoming_events,
    format_events,
    check_deadline_reminders,
)
from room_tagger import load_room_tag_sessions, save_room_tag_sessions, generate_room_tags
from newsletter_manager import load_newsletter_sessions, save_newsletter_sessions, save_newsletter_to_notion
from note_generator import load_note_sessions, save_note_sessions, send_long_message, generate_note_draft_async
from print_manager import load_prints, save_prints, load_print_sessions, save_print_sessions
from scheduler_reminders import (
    send_morning_message, send_preparation_reminder,
    send_ebay_reset_reminder, send_threads_api_reminder,
    send_hsbc_reminder, send_zaitage_reminder,
    send_may28_finance_reminder, send_may25_reminder, send_may30_reminder,
    send_x_engage_reminder, send_famm_reminder, send_famm_deadline_reminder,
    send_sekisui_blog_reminder, send_ebay_check_reminder,
    send_a8_check_reminder, send_monthly_review_reminder,
)
from x_poster import (
    TWEET_STOCK, QUOTE_TWEET_TEMPLATES,
    get_tweet_for_slot, generate_x_post,
    _get_x_client,
    _post_tweet, post_to_x_daily, post_to_x_noon, post_to_x_evening,
)
from x_analytics import (
    get_google_creds,
    fetch_search_console,
    fetch_x_weekly_metrics,
    send_weekly_seo_report,
    send_note_reminder,
    send_note_weekly_reminder,
    send_x_weekly_report,
    send_daily_work_log,
    find_or_create_diary_page,
    fetch_diary_memos_from_notion,
    auto_tweet_from_diary_memos,
    add_diary_memo,
    auto_improve_tweet_stock,
)
from threads_room import (
    ROOM_GENRES, HOOKS, LINK_PHRASES,
    post_to_threads, reply_to_threads,
    _fetch_room_suggestion, send_room_suggestion_slot, send_threads_token_reminder,
)
from sns_direct_poster import (
    post_kvision_room_intro, post_koharu_threads_room_intro,
    _get_kvision_x_client,
    post_kvision_morning_tweet, post_kvision_travel_aff_auto, post_kvision_card_tweet,
    post_koharu_threads_card,
    post_mako_threads_morning, post_mako_threads_aff_auto,
)

from routes_debug import debug_bp
from routes_company import company_bp

app = Flask(__name__)
app.register_blueprint(ebay_bp)
app.register_blueprint(debug_bp)
app.register_blueprint(company_bp)

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



@app.route('/ping')
def ping():
    return 'OK'



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
    allowed = {os.environ.get('LINE_USER_ID', ''), os.environ.get('NOTIFY_SECRET', '')}
    if not secret or secret not in allowed:
        return {'error': 'unauthorized'}, 401
    data = request.json or {}
    title = data.get('title', '')
    url = data.get('url', '') or data.get('post_url', '')
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
                returned_post_id = post['id']
                # アイキャッチ画像をバックグラウンドで設定
                def _set_eyecatch_bg(_pid, _title, _wp_url, _wp_user, _wp_pass):
                    try:
                        from blog_yakuzen import generate_pexels_keyword, fetch_pexels_image_url, upload_image_to_yakuzen_wp
                        kw = generate_pexels_keyword(_title)
                        img_url = fetch_pexels_image_url(kw)
                        if img_url:
                            media_id = upload_image_to_yakuzen_wp(img_url, _title)
                            if media_id:
                                requests.post(f'{_wp_url}/wp-json/wp/v2/posts/{_pid}',
                                              auth=(_wp_user, _wp_pass),
                                              json={'featured_media': media_id}, timeout=30)
                                print(f'アイキャッチ設定完了 post_id={_pid} media_id={media_id}')
                    except Exception as e:
                        print(f'アイキャッチ設定エラー: {e}')
                import threading
                threading.Thread(target=_set_eyecatch_bg,
                                 args=(returned_post_id, title, wp_url, wp_user, wp_pass),
                                 daemon=True).start()
                _notify_line_ig(title, post_url, content_md)
                return {'status': 'ok', 'post_id': returned_post_id, 'url': post_url}, res.status_code
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


@app.route('/set-eyecatch', methods=['POST'])
def set_eyecatch():
    """既存の薬膳記事にアイキャッチ画像を後付け設定する"""
    secret = request.headers.get('X-Secret', '')
    if secret != os.environ.get('LINE_USER_ID', ''):
        return {'error': 'unauthorized'}, 401
    data = request.json or {}
    post_id = data.get('post_id')
    title = data.get('title', '')
    if not post_id:
        return {'error': 'post_id required'}, 400
    try:
        from blog_yakuzen import generate_pexels_keyword, fetch_pexels_image_url, upload_image_to_yakuzen_wp
        wp_url = os.environ.get('YAKUZEN_WP_URL', 'https://foodmakehealth.com')
        wp_user = os.environ.get('YAKUZEN_WP_USER', 'makiko01035')
        wp_pass = os.environ['YAKUZEN_WP_APP_PASSWORD']
        keyword = generate_pexels_keyword(title) if title else 'sleep nature calm'
        img_url = fetch_pexels_image_url(keyword)
        if not img_url:
            return {'error': 'Pexels画像が見つかりませんでした', 'keyword': keyword}, 500
        # WPメディアに直接アップロード（詳細エラー取得のためインライン実装）
        img_data = requests.get(img_url, timeout=15).content
        filename = f"eyecatch-{post_id}.jpg"
        upload_res = requests.post(
            f'{wp_url}/wp-json/wp/v2/media',
            auth=(wp_user, wp_pass),
            headers={
                'Content-Disposition': f'attachment; filename="{filename}"',
                'Content-Type': 'image/jpeg',
            },
            data=img_data,
            timeout=30
        )
        if upload_res.status_code != 201:
            return {'error': 'WPメディアアップロード失敗', 'status': upload_res.status_code,
                    'detail': upload_res.text[:300]}, 500
        media_id = upload_res.json()['id']
        res = requests.post(
            f'{wp_url}/wp-json/wp/v2/posts/{post_id}',
            auth=(wp_user, wp_pass),
            json={'featured_media': media_id},
            timeout=30
        )
        if res.status_code == 200:
            return {'status': 'ok', 'post_id': post_id, 'media_id': media_id, 'keyword': keyword}, 200
        return {'error': res.text[:200]}, 500
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
                "Notion-Version": "2025-09-03",
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
    # 「こはるまま」「こはる」「コハルママ」「コハル」すべて受け付ける
    if (user_message.startswith('こはるまま') or user_message.startswith('こはる') or
            user_message.startswith('コハルママ') or user_message.startswith('コハル')):
        if koharu_handle_approval(user_message):
            return
        # 未対応の場合はそのまま通常処理へ

    # MAKO投稿承認コマンド
    # 「MAKO」「mako」「ＭＡＫＯ」「ｍａｋｏ」「まこ」すべて受け付ける
    if (user_message.upper().startswith('MAKO') or user_message.startswith('ＭＡＫＯ') or
            user_message.startswith('ｍａｋｏ') or user_message.startswith('まこ')):
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
                    if ok:
                        msg = "✅ 日記に追記しました！"
                    else:
                        msg = "❌ 追記に失敗しました\nRenderログで [diary] を検索して原因を確認してください"
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

    # 睡眠ブログ：セッションチェック
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


    # 睡眠記事（睡眠ブログ）：キーワード検出
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

scheduler.add_job(auto_blog_new,     'cron', day_of_week='mon,thu', hour=8, minute=0)
scheduler.add_job(auto_blog_rewrite, 'cron', day_of_week='wed,sat', hour=8, minute=0)
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
# 2026-05-28 朝9時：家計整理月末確認リマインド（一回限り）
scheduler.add_job(send_may28_finance_reminder, 'date', run_date='2026-05-28 09:00:00')
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
# ⑦ 格言ポスター：毎朝 05:00〜05:15（X のみ・Threadsはまきさんが手動で貼る）
scheduler.add_job(mako_poster_morning_quote, 'cron', hour=5, minute=0, jitter=900)
# ⑧ 格言ジェネレーター：毎月1日 04:00（30本生成→LINEに通知）
scheduler.add_job(mako_quote_generator, 'cron', day=1, hour=4, minute=0)
# MAKO Threads：1日2本（MAKO_THREADS_ACCESS_TOKEN設定後に自動稼働）
# 朝8:00 睡眠共感投稿、夜21:00 アフィスレッド（言い切りNG・共感ベース）
scheduler.add_job(post_mako_threads_morning, 'cron', hour=8, minute=0)
scheduler.add_job(post_mako_threads_aff_auto, 'cron', hour=21, minute=0)
# 毎朝5:30：eBay日本人セラー売れ筋から仕入れ候補をLINEに送信
scheduler.add_job(
    lambda: send_daily_purchase_candidates(os.environ.get('LINE_USER_ID', '')),
    'cron', hour=5, minute=30,
)
def _delayed_scheduler_start():
    time.sleep(120)
    scheduler.start()
    print("[scheduler] 起動完了（デプロイ並走防止のため120秒遅延）")

threading.Thread(target=_delayed_scheduler_start, daemon=True).start()

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
