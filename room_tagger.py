import json

from clients import anthropic_client

ROOM_TAG_SESSION_FILE = '/tmp/room_tag_sessions.json'


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
