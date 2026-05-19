import os
import random
import datetime
import requests
from linebot.models import TextSendMessage

from clients import line_bot_api, JST
from blog_yakuzen import search_rakuten_items


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


