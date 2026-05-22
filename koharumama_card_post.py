"""
koharumama_card_post.py
@kvision_m（こはるまま）リスト型カード画像投稿
日曜 11:00 に自動実行。商品写真4枚+タイトルのカード画像を生成してXに投稿する。
"""

import io
import os
import random
from datetime import datetime

import requests

FONT_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'fonts', 'NotoSansJP-Bold.otf')

SEASON_MAP = {
    1: "お正月・初旅行・防寒",      2: "バレンタイン・冬旅行",
    3: "春旅行・桜お花見",           4: "春のお出かけ・GW準備",
    5: "GW旅行・アウトドア",         6: "梅雨・夏旅行準備",
    7: "夏休み・海・プール",          8: "お盆帰省・子連れ旅行",
    9: "秋旅行・運動会",              10: "紅葉・ハロウィン",
    11: "七五三・冬旅行準備",         12: "クリスマス・年末帰省",
}


# ============================================================
# テーマ生成
# ============================================================

def _generate_theme():
    import anthropic
    import json

    month = datetime.now().month
    season_hint = SEASON_MAP.get(month, "")

    prompt = f"""今は{month}月・{season_hint}のシーズンです。
こはるまま（旅行×楽天アフィ・30-40代子連れワーママ向け）のX投稿用カード画像を作ります。
「〇〇リスト4選」の形式で、楽天で買えるグッズ4点を選んでください。

以下のJSON形式のみで出力（コードブロック不要）：
{{
  "card_title": "タイトル（「〇〇リスト♡」形式・20字以内）",
  "scenes": [
    {{"label": "シーン名（8字以内）", "keyword": "楽天検索キーワード（日本語）"}},
    {{"label": "シーン名（8字以内）", "keyword": "楽天検索キーワード（日本語）"}},
    {{"label": "シーン名（8字以内）", "keyword": "楽天検索キーワード（日本語）"}},
    {{"label": "シーン名（8字以内）", "keyword": "楽天検索キーワード（日本語）"}}
  ],
  "tweet_hook": "投稿の冒頭1行（25字以内・共感型・体言止めOK）"
}}"""

    try:
        client = anthropic.Anthropic(api_key=os.environ.get('ANTHROPIC_API_KEY', ''))
        resp = client.messages.create(
            model='claude-haiku-4-5-20251001',
            max_tokens=500,
            messages=[{'role': 'user', 'content': prompt}]
        )
        text = resp.content[0].text.strip().replace('```json', '').replace('```', '').strip()
        return json.loads(text)
    except Exception as e:
        print(f"[card_post] theme generate error: {e}")
        return {
            "card_title": "夏旅に持っていくもの♡",
            "scenes": [
                {"label": "UV対策",       "keyword": "日傘 折りたたみ UVカット 軽量 レディース"},
                {"label": "子連れグッズ",  "keyword": "子ども 旅行 暇つぶし 新幹線 おもちゃ"},
                {"label": "旅行ポーチ",    "keyword": "旅行ポーチ コスメ 防水 コンパクト"},
                {"label": "ハンディファン","keyword": "ハンディファン 携帯扇風機 USB 旅行 軽量"},
            ],
            "tweet_hook": "夏旅の前に揃えたいもの4選",
        }


# ============================================================
# 楽天API
# ============================================================

def _fetch_rakuten_item(keyword):
    from blog_yakuzen import search_rakuten_items
    try:
        items = search_rakuten_items(keyword, hits=5)
        if not items:
            return None
        with_image = [i for i in items if i.get('image')]
        return random.choice(with_image) if with_image else random.choice(items)
    except Exception as e:
        print(f"[card_post] rakuten fetch error ({keyword}): {e}")
        return None


def _download_image(url, size):
    from PIL import Image as PILImage
    try:
        r = requests.get(url, timeout=10, verify=False)
        r.raise_for_status()
        img = PILImage.open(io.BytesIO(r.content)).convert('RGB')
        return img.resize(size, PILImage.LANCZOS)
    except Exception as e:
        print(f"[card_post] image download error: {e}")
        return None


# ============================================================
# カード画像生成
# ============================================================

def _make_card_image(title, scenes_data):
    from PIL import Image, ImageDraw, ImageFont

    W, H = 1080, 1080

    BG_TOP   = (255, 240, 245)
    BG_BOT   = (255, 218, 228)
    CARD_BG  = (255, 255, 255)
    TITLE_FG = (185, 60,  95)
    LABEL_FG = (255, 255, 255)
    LABEL_BG = (210, 85, 120)
    CTA_BG   = (200, 70, 105)
    CTA_FG   = (255, 255, 255)
    NUM_BG   = (185, 60,  95)
    NUM_FG   = (255, 255, 255)

    img = Image.new('RGB', (W, H))
    draw = ImageDraw.Draw(img)
    for y in range(H):
        r = int(BG_TOP[0] + (BG_BOT[0] - BG_TOP[0]) * y / H)
        g = int(BG_TOP[1] + (BG_BOT[1] - BG_TOP[1]) * y / H)
        b = int(BG_TOP[2] + (BG_BOT[2] - BG_TOP[2]) * y / H)
        draw.line([(0, y), (W, y)], fill=(r, g, b))

    def _font(size):
        try:
            return ImageFont.truetype(FONT_PATH, size)
        except Exception:
            return ImageFont.load_default()

    font_title = _font(54)
    font_label = _font(27)
    font_num   = _font(28)
    font_cta   = _font(36)

    # タイトルエリア
    TITLE_H = 115
    draw.rectangle([0, 0, W, TITLE_H], fill=(255, 248, 251))
    tb = draw.textbbox((0, 0), title, font=font_title)
    tw = tb[2] - tb[0]
    draw.text(((W - tw) // 2, (TITLE_H - (tb[3] - tb[1])) // 2 - 2), title, font=font_title, fill=TITLE_FG)
    draw.rectangle([30, TITLE_H - 5, W - 30, TITLE_H - 1], fill=TITLE_FG)

    # 2x2 グリッド
    PAD    = 18
    CTA_H  = 82
    GRID_W = (W - PAD * 3) // 2
    GRID_H = (H - TITLE_H - PAD * 3 - CTA_H) // 2
    IMG_H  = GRID_H - 58

    positions = [
        (PAD,            TITLE_H + PAD),
        (PAD * 2 + GRID_W, TITLE_H + PAD),
        (PAD,            TITLE_H + PAD * 2 + GRID_H),
        (PAD * 2 + GRID_W, TITLE_H + PAD * 2 + GRID_H),
    ]

    for i, (sd, (cx, cy)) in enumerate(zip(scenes_data, positions)):
        label = sd['label']
        item  = sd.get('item')

        draw.rounded_rectangle([cx, cy, cx + GRID_W, cy + GRID_H], radius=14, fill=CARD_BG)

        # 商品画像
        photo = None
        if item and item.get('image'):
            photo = _download_image(item['image'], size=(GRID_W, IMG_H))
        if photo:
            mask = Image.new('L', (GRID_W, IMG_H), 0)
            from PIL import ImageDraw as _ID2
            md = _ID2.Draw(mask)
            md.rounded_rectangle([0, 0, GRID_W, IMG_H], radius=14, fill=255)
            md.rectangle([0, IMG_H - 16, GRID_W, IMG_H], fill=255)
            img.paste(photo, (cx, cy), mask)
        else:
            draw.rectangle([cx, cy, cx + GRID_W, cy + IMG_H], fill=(245, 220, 230))

        # 番号バッジ
        draw.ellipse([cx + 10, cy + 8, cx + 46, cy + 44], fill=NUM_BG)
        nb = draw.textbbox((0, 0), str(i + 1), font=font_num)
        nw, nh = nb[2] - nb[0], nb[3] - nb[1]
        draw.text((cx + 10 + (36 - nw) // 2, cy + 8 + (36 - nh) // 2 - 2), str(i + 1), font=font_num, fill=NUM_FG)

        # ラベル
        label_y = cy + IMG_H
        draw.rounded_rectangle([cx, label_y, cx + GRID_W, cy + GRID_H], radius=14, fill=LABEL_BG)
        lb = draw.textbbox((0, 0), label, font=font_label)
        lw, lh = lb[2] - lb[0], lb[3] - lb[1]
        draw.text((cx + (GRID_W - lw) // 2, label_y + (58 - lh) // 2 - 2), label, font=font_label, fill=LABEL_FG)

    # CTAバナー
    cta_y = H - CTA_H
    draw.rectangle([0, cta_y, W, H], fill=CTA_BG)
    cta_text = "楽天でチェック♡  タップしてね →"
    ctab = draw.textbbox((0, 0), cta_text, font=font_cta)
    ctaw, ctah = ctab[2] - ctab[0], ctab[3] - ctab[1]
    draw.text(((W - ctaw) // 2, cta_y + (CTA_H - ctah) // 2 - 2), cta_text, font=font_cta, fill=CTA_FG)

    buf = io.BytesIO()
    img.save(buf, format='PNG', optimize=True)
    buf.seek(0)
    return buf


# ============================================================
# X 投稿
# ============================================================

def _get_kvision_x_api():
    import tweepy
    api_key    = (os.environ.get('KVISION_X_API_KEY') or '').strip()
    api_secret = (os.environ.get('KVISION_X_API_SECRET') or '').strip()
    acc_token  = (os.environ.get('KVISION_X_ACCESS_TOKEN') or '').strip()
    acc_secret = (os.environ.get('KVISION_X_ACCESS_TOKEN_SECRET') or '').strip()
    if not all([api_key, api_secret, acc_token, acc_secret]):
        return None
    auth = tweepy.OAuth1UserHandler(api_key, api_secret, acc_token, acc_secret)
    return tweepy.API(auth)


# ============================================================
# メイン処理
# ============================================================

def post_kvision_card_image():
    """日曜 11:00：リスト型カード画像を生成して @kvision_m に投稿"""
    try:
        theme      = _generate_theme()
        card_title = theme['card_title']
        scenes     = theme['scenes']
        tweet_hook = theme.get('tweet_hook', card_title)

        scenes_data = []
        aff_urls    = []
        for sc in scenes[:4]:
            item = _fetch_rakuten_item(sc['keyword'])
            scenes_data.append({'label': sc['label'], 'item': item})
            if item and item.get('url'):
                aff_urls.append(item['url'])

        img_buf = _make_card_image(card_title, scenes_data)

        url_line = ('\n\n' + aff_urls[0]) if aff_urls else ''
        tweet_text = f"{tweet_hook}\n\n{card_title}{url_line}"
        if len(tweet_text) > 280:
            tweet_text = tweet_text[:278] + '…'

        from sns_direct_poster import _get_kvision_x_client
        api    = _get_kvision_x_api()
        client = _get_kvision_x_client()

        if not api or not client:
            print("[card_post] KVISION X API keys not configured, skipping")
            return

        media    = api.media_upload(filename='koharu_card.png', file=img_buf)
        resp     = client.create_tweet(text=tweet_text, media_ids=[media.media_id])
        tweet_id = resp.data['id']
        print(f"[card_post] posted: {tweet_id} / {card_title}")

    except Exception as e:
        print(f"[card_post] error: {e}")
        try:
            from clients import line_bot_api
            from linebot.models import TextSendMessage
            line_bot_api.push_message(
                os.environ.get('LINE_USER_ID', ''),
                TextSendMessage(text=f"❌ こはるままカード投稿エラー\n{str(e)[:200]}")
            )
        except Exception:
            pass
