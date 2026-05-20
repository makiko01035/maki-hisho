import os
import re
import datetime
import threading
import requests
from flask import Blueprint, request, jsonify
from linebot.models import TextSendMessage
from clients import line_bot_api, JST
from newsletter_manager import load_newsletter_sessions, save_newsletter_sessions

wp_bp = Blueprint('wp', __name__)


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


@wp_bp.route('/overlay-image')
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


@wp_bp.route('/wp-post-published', methods=['POST'])
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

    threading.Thread(target=_do_overlay_and_update, args=(post_id, wp_url, wp_user, wp_pass), daemon=True).start()
    return {'status': 'accepted'}, 202


@wp_bp.route('/rewrite-yakuzen-direct', methods=['POST'])
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
            user_id = os.environ.get('LINE_USER_ID', '')
            line_bot_api.push_message(user_id, TextSendMessage(text=f'❌ リライトエラー：{str(e)[:200]}'))

    threading.Thread(target=_do_rewrite, args=(post_id, instruction)).start()
    return {'status': 'accepted', 'message': 'リライト開始。完了はLINEに通知します'}, 202


@wp_bp.route('/set-yakuzen-image', methods=['POST'])
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


@wp_bp.route('/post-sekisui-direct', methods=['POST'])
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


def _fetch_wp_post_info(post_url):
    """記事URLからWP REST APIで本文・アイキャッチURLを取得。(content_md, featured_url)を返す"""
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
            content_md = re.sub(r'<[^>]+>', '', content_html)[:3000]
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


@wp_bp.route('/notify-ig', methods=['POST'])
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


@wp_bp.route('/post-yakuzen-direct', methods=['POST'])
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
            wp_url = os.environ.get('YAKUZEN_WP_URL', 'https://foodmakehealth.com')
            wp_user = os.environ.get('YAKUZEN_WP_USER', 'makiko01035')
            wp_pass = os.environ['YAKUZEN_WP_APP_PASSWORD']
            post_data = {'title': title, 'content': content_html, 'status': 'publish'}
            if slug:
                post_data['slug'] = slug
            categories = data.get('categories', [])
            if categories:
                post_data['categories'] = categories
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
            requests.post(f'{wp_url}/wp-json/wp/v2/posts/{post_id}',
                          auth=(wp_user, wp_pass), json={'slug': slug}, timeout=15)
        _notify_line_ig(title, post_url, content_md)
        return {'status': 'ok', 'post_id': post_id, 'url': post_url}, 201
    except Exception as e:
        return {'error': str(e)}, 500


@wp_bp.route('/set-eyecatch', methods=['POST'])
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


@wp_bp.route('/update-yakuzen-meta', methods=['POST'])
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


@wp_bp.route('/newsletter-summary', methods=['POST'])
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


@wp_bp.route('/add-task', methods=['POST'])
def add_task():
    """Power Automateからメール検知時に呼ばれる：LINE通知 + Notionにタスク追加"""
    data = request.json or {}
    secret = data.get('secret')
    if secret != os.environ.get('NOTIFY_SECRET', ''):
        return jsonify({'error': 'Unauthorized'}), 401

    line_message = data.get('message', '')
    task_title = data.get('task', '')
    results = {}

    if line_message:
        try:
            line_bot_api.push_message(
                os.environ.get('LINE_USER_ID'),
                TextSendMessage(text=line_message)
            )
            results['line'] = 'sent'
        except Exception as e:
            results['line_error'] = str(e)

    if task_title:
        try:
            notion_token = os.environ.get('NOTION_TOKEN', '')
            notion_headers = {
                "Authorization": f"Bearer {notion_token}",
                "Notion-Version": "2025-09-03",
                "Content-Type": "application/json"
            }
            body = {
                "after": "323f8d6d-41de-809d-9e98-f9a5da8556a8",
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
