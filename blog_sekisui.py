import os
import requests
import markdown as md_lib
from linebot.models import TextSendMessage
from clients import line_bot_api, anthropic_client

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


SEKISUI_PEXELS_KEYWORDS = [
    "japanese house interior", "modern house interior", "new home living room",
    "house exterior japan", "bright living room", "modern kitchen interior",
    "japanese home design", "cozy home interior", "house construction",
    "family home interior", "new house bright room", "home design modern",
]

def _title_to_pexels_keyword(title: str) -> str:
    """日本語タイトルをPexels検索用英語キーワードに変換"""
    try:
        resp = anthropic_client.messages.create(
            model='claude-haiku-4-5-20251001',
            max_tokens=30,
            messages=[{'role': 'user', 'content':
                f'次の日本語の住宅ブログ記事タイトルに合う、Pexels画像検索用の英語キーワードを1〜3語で返してください。キーワードのみ回答。\n{title}'}]
        )
        kw = resp.content[0].text.strip().lower()
        return kw if kw else 'japanese house interior'
    except Exception:
        import hashlib
        idx = int(hashlib.md5(title.encode()).hexdigest(), 16) % len(SEKISUI_PEXELS_KEYWORDS)
        return SEKISUI_PEXELS_KEYWORDS[idx]


def fetch_pexels_image_for_wp(keyword):
    pexels_key = os.environ.get('PEXELS_API_KEY')
    if not pexels_key:
        return None
    try:
        res = requests.get(
            'https://api.pexels.com/v1/search',
            headers={'Authorization': pexels_key},
            params={'query': keyword, 'per_page': 5, 'orientation': 'landscape'},
            timeout=10
        )
        photos = res.json().get('photos', [])
        if not photos:
            return None
        import random
        photo = random.choice(photos)
        img_res = requests.get(photo['src']['large2x'], timeout=10)
        if img_res.status_code != 200:
            return None
        return img_res.content, f"pexels_{photo['id']}.jpg"
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


def post_to_sekisui_wp(title, content_md):
    wp_url = os.environ.get('SEKISUI_WP_URL', 'https://order-sekisui.com')
    wp_user = os.environ.get('SEKISUI_WP_USER', 'makiko01035')
    wp_pass = os.environ['SEKISUI_WP_APP_PASSWORD']

    html = md_lib.markdown(content_md, extensions=['tables', 'nl2br'])
    html = html.replace('<!-- sekisui-affiliate-cta -->', SEKISUI_CTA_BOX)
    # 英語スラッグを生成（URL文字化け防止）
    import re, datetime
    date_str = datetime.date.today().strftime('%Y%m%d')
    ascii_part = re.sub(r'[^a-z0-9-]', '', re.sub(r'[^\x00-\x7F]', '', title).lower().replace(' ', '-')).strip('-')[:20]
    slug = f"sekisui-{ascii_part}-{date_str}" if ascii_part else f"sekisui-{date_str}"
    data = {'title': title, 'content': html, 'status': 'publish', 'slug': slug}

    pexels_kw = _title_to_pexels_keyword(title)
    print(f"Pexels keyword: {pexels_kw}")
    img_result = fetch_pexels_image_for_wp(pexels_kw)
    if img_result:
        img_data, filename = img_result
        # タイトルオーバーレイを合成してからアップロード
        try:
            from PIL import Image, ImageDraw, ImageFont
            from io import BytesIO
            import os as _os
            img = Image.open(BytesIO(img_data)).convert('RGBA')
            img = img.resize((1080, 1080), Image.LANCZOS)
            ov = Image.new('RGBA', (1080, 1080), (0, 0, 0, 0))
            d = ImageDraw.Draw(ov)
            for y in range(1080):
                d.line([(0, y), (1080, y)], fill=(0, 0, 0, int(180 * y / 1080)))
            img = Image.alpha_composite(img, ov)
            draw = ImageDraw.Draw(img)
            font_path = _os.path.join(_os.path.dirname(__file__), 'fonts', 'NotoSansJP-Bold.otf')
            with open(font_path, 'rb') as _ff:
                font = ImageFont.truetype(BytesIO(_ff.read()), 60)
            lines_t, t = [], title
            while len(t) > 14:
                lines_t.append(t[:14]); t = t[14:]
            lines_t.append(t)
            y_s = 1080 - 72 * len(lines_t) - 80
            for ln in lines_t:
                bx = draw.textbbox((0, 0), ln, font=font)
                x = (1080 - (bx[2] - bx[0])) // 2
                draw.text((x+2, y_s+2), ln, font=font, fill=(0, 0, 0, 200))
                draw.text((x, y_s), ln, font=font, fill=(255, 255, 255, 255))
                y_s += 72
            buf = BytesIO()
            img.convert('RGB').save(buf, format='JPEG', quality=90)
            img_data = buf.getvalue()
            filename = 'ig_' + filename
        except Exception as oe:
            import traceback
            print(f"Overlay error: {oe}\n{traceback.format_exc()}")
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
