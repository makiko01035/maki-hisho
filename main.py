import os
import json
import datetime
import threading
import time
import requests
from flask import Flask, request, abort, send_from_directory, jsonify
from linebot.exceptions import InvalidSignatureError
from linebot.models import TextSendMessage
from apscheduler.schedulers.background import BackgroundScheduler

from clients import line_bot_api, handler, JST
from ebay_handler import send_daily_purchase_candidates
from sns_engine_koharu import (
    run_researcher as koharu_researcher,
    run_writer     as koharu_writer,
    run_poster_morning as koharu_poster_morning,
    run_poster_aff     as koharu_poster_aff,
    run_collector  as koharu_collector,
    run_analyst    as koharu_analyst,
    run_monitor    as koharu_monitor,
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
)
from blog_yakuzen import auto_blog_new, auto_blog_rewrite
from ebay_dashboard import ebay_bp
from calendar_manager import check_deadline_reminders
from newsletter_manager import load_newsletter_sessions, save_newsletter_sessions
from scheduler_reminders import (
    send_morning_message, send_preparation_reminder,
    send_ebay_reset_reminder, send_threads_api_reminder,
    send_hsbc_reminder, send_zaitage_reminder,
    send_may28_finance_reminder, send_may25_reminder, send_may30_reminder,
    send_x_engage_reminder, send_famm_reminder, send_famm_deadline_reminder,
    send_sekisui_blog_reminder, send_ebay_check_reminder,
    send_a8_check_reminder, send_monthly_review_reminder,
)
from x_poster import post_to_x_daily, post_to_x_noon, post_to_x_evening
from x_analytics import (
    send_weekly_seo_report, send_note_reminder, send_note_weekly_reminder,
    send_x_weekly_report, send_daily_work_log,
)
from threads_room import send_room_suggestion_slot, send_threads_token_reminder
from sns_direct_poster import (
    post_kvision_room_intro, post_koharu_threads_room_intro,
    post_kvision_morning_tweet, post_kvision_travel_aff_auto, post_kvision_card_tweet,
    post_koharu_threads_card,
    post_mako_threads_morning, post_mako_threads_aff_auto,
)
from routes_debug import debug_bp
from routes_company import company_bp
import line_handler  # @handler.add デコレーターを登録

app = Flask(__name__)
app.register_blueprint(ebay_bp)
app.register_blueprint(debug_bp)
app.register_blueprint(company_bp)




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
