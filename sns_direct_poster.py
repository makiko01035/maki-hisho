import os
import json
import datetime
import time
import random
import requests
import threading
from linebot.models import TextSendMessage

from clients import line_bot_api, anthropic_client, JST
from blog_yakuzen import search_rakuten_items
from threads_room import HOOKS, LINK_PHRASES, post_to_threads


# --- こはるまま：@kvision_m 旅行×楽天アフィ X自動投稿（1日2本）---

KVISION_GENRE_LOG_PATH = '/tmp/kvision_genre_log.json'

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

MAKO_GENRE_LOG_PATH = '/tmp/mako_genre_log.json'

MAKO_THREADS_AFF_GENRES = [
    {'name': '睡眠サプリ（GABA）',   'keyword': 'GABA 睡眠 サプリ'},
    {'name': 'ホットアイマスク',      'keyword': 'ホットアイマスク 蒸気 睡眠'},
    {'name': 'なつめ（薬膳）',        'keyword': 'なつめ 薬膳 乾燥'},
    {'name': 'カモミールハーブティー', 'keyword': 'カモミール ハーブティー 安眠'},
    {'name': '加味逍遙散',            'keyword': '加味逍遙散 漢方 更年期'},
    {'name': 'クコの実（薬膳）',      'keyword': 'クコの実 薬膳 乾燥'},
    {'name': '睡眠枕',                'keyword': '枕 睡眠 低反発'},
    {'name': '加味帰脾湯',            'keyword': '加味帰脾湯 漢方 不眠'},
    {'name': 'マグネシウムサプリ',    'keyword': 'マグネシウム サプリ 睡眠'},
    {'name': '竜眼肉（薬膳）',        'keyword': '竜眼肉 りゅうがんにく 薬膳'},
    {'name': 'ラベンダーアロマ',      'keyword': 'ラベンダー アロマオイル 安眠'},
    {'name': '酸棗仁湯',              'keyword': '酸棗仁湯 漢方 不眠'},
    {'name': '抑肝散',                'keyword': '抑肝散 漢方 神経 不眠'},
    {'name': '睡眠ハーブティー',      'keyword': '睡眠茶 ハーブ パッションフラワー'},
    {'name': '黒ごま・黒豆（薬膳）', 'keyword': '黒ごま 黒豆 薬膳 腎'},
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
        try:
            line_bot_api.push_message(os.environ.get('LINE_USER_ID', ''), TextSendMessage(text=f"❌ @kvision_m 朝つぶやき投稿エラー\n{str(e)[:200]}"))
        except Exception:
            pass


def post_kvision_travel_aff(slot_index):
    """@kvision_mに旅行×楽天アフィをXにスレッド形式で投稿（本文→リプライにURL）"""
    import time as _time
    all_genres = _get_all_kvision_genres()
    genre = all_genres[slot_index % len(all_genres)]
    try:
        body, url = _fetch_travel_suggestion(genre)
        if not body or not url:
            return
        score = _check_kvision_post_quality(body)
        if score < 60:
            print(f"post_kvision_travel_aff: quality low ({score}pts), skipping")
            try:
                line_bot_api.push_message(os.environ.get('LINE_USER_ID', ''), TextSendMessage(
                    text=f"⚠️ @kvision_m アフィスレッド品質低下（{score}点）でスキップ\nジャンル：{genre['name']}\n本文：{body[:100]}"
                ))
            except Exception:
                pass
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
        _record_genre_used(genre['name'])
        print(f"kvision X thread post ({genre['name']}) successful [score:{score}]")
    except Exception as e:
        print(f"post_kvision_travel_aff({slot_index}) error: {e}")
        try:
            line_bot_api.push_message(os.environ.get('LINE_USER_ID', ''), TextSendMessage(text=f"❌ @kvision_m アフィスレッド投稿エラー（{genre['name']}）\n{str(e)[:200]}"))
        except Exception:
            pass


# ========== 月替わりジャンル・ジャンル管理 ==========

def _get_monthly_kvision_genres():
    """今月の特集ジャンルをkvision_monthly_genres.jsonから取得"""
    import json
    month_key = datetime.datetime.now().strftime('%m')
    try:
        with open('kvision_monthly_genres.json', 'r', encoding='utf-8') as f:
            data = json.load(f)
        return data.get(month_key, [])
    except Exception as e:
        print(f"monthly kvision genres read error: {e}")
        return []


def _get_all_kvision_genres():
    """固定ジャンル + 今月の特集ジャンルを合わせたリスト"""
    return TRAVEL_GENRES + _get_monthly_kvision_genres()


def _load_genre_log():
    try:
        with open(KVISION_GENRE_LOG_PATH, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return {}


def _save_genre_log(log):
    try:
        with open(KVISION_GENRE_LOG_PATH, 'w', encoding='utf-8') as f:
            json.dump(log, f, ensure_ascii=False)
    except Exception as e:
        print(f"genre log save error: {e}")


def _select_least_used_genre():
    """最後に使った日が最も古いジャンルを選ぶ（均等ローテーション）"""
    all_genres = _get_all_kvision_genres()
    log = _load_genre_log()
    return min(all_genres, key=lambda g: log.get(g['name'], ''))


def _record_genre_used(genre_name):
    """ジャンルの使用日時をログに記録"""
    log = _load_genre_log()
    log[genre_name] = datetime.datetime.now().isoformat()
    _save_genre_log(log)


def _check_kvision_post_quality(body):
    """投稿文を100点満点でAI採点。60点未満はNG"""
    prompt = (
        f"以下のX投稿文を100点満点で採点してください。\n\n"
        f"投稿文：\n{body}\n\n"
        f"採点基準：\n"
        f"・口語・自然体で宣伝臭くない：30点\n"
        f"・30〜40代子連れワーママに刺さる言葉：30点\n"
        f"・3行以内・簡潔：20点\n"
        f"・冒頭が自然につながっている：20点\n\n"
        f"数字だけ出力（例：75）"
    )
    try:
        resp = anthropic_client.messages.create(
            model='claude-haiku-4-5-20251001',
            max_tokens=10,
            messages=[{'role': 'user', 'content': prompt}]
        )
        score_str = resp.content[0].text.strip()
        return int(''.join(filter(str.isdigit, score_str)) or '100')
    except Exception:
        return 100  # エラー時は通過させる


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
    """最長未使用ジャンルを優先選択。3日に1回は固定アフィストックから投稿"""
    import time as _time
    day = datetime.datetime.now().day
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
            try:
                line_bot_api.push_message(os.environ.get('LINE_USER_ID', ''), TextSendMessage(text=f"❌ @kvision_m 固定アフィ投稿エラー\n{str(e)[:200]}"))
            except Exception:
                pass
    else:
        genre = _select_least_used_genre()
        all_genres = _get_all_kvision_genres()
        slot = next((i for i, g in enumerate(all_genres) if g['name'] == genre['name']), 0)
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
    day = datetime.datetime.now().day
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


def _load_mako_genre_log():
    try:
        with open(MAKO_GENRE_LOG_PATH, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return {}


def _save_mako_genre_log(log):
    try:
        with open(MAKO_GENRE_LOG_PATH, 'w', encoding='utf-8') as f:
            json.dump(log, f, ensure_ascii=False)
    except Exception as e:
        print(f"mako genre log save error: {e}")


def _select_mako_least_used_genre():
    """最後に使った日が最も古いMAKOジャンルを選ぶ（均等ローテーション）"""
    log = _load_mako_genre_log()
    return min(MAKO_THREADS_AFF_GENRES, key=lambda g: log.get(g['name'], ''))


def _record_mako_genre_used(genre_name):
    log = _load_mako_genre_log()
    log[genre_name] = datetime.datetime.now().isoformat()
    _save_mako_genre_log(log)


def _check_mako_post_quality(body):
    """MAKO投稿文を100点満点でAI採点。60点未満はNG"""
    prompt = (
        f"以下のThreads投稿文を100点満点で採点してください。\n\n"
        f"投稿文：\n{body}\n\n"
        f"採点基準：\n"
        f"・「〜です」「〜効果があります」など言い切り表現がない：30点\n"
        f"・宣伝・売る感じがなく共感・知識ベース：30点\n"
        f"・悩みへの共感から自然に始まっている：20点\n"
        f"・150文字以内・簡潔：20点\n\n"
        f"数字だけ出力（例：75）"
    )
    try:
        resp = anthropic_client.messages.create(
            model='claude-haiku-4-5-20251001',
            max_tokens=10,
            messages=[{'role': 'user', 'content': prompt}]
        )
        score_str = resp.content[0].text.strip()
        return int(''.join(filter(str.isdigit, score_str)) or '100')
    except Exception:
        return 100


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
    """MAKO Threads夜21:00：睡眠グッズ・サプリアフィ投稿（均等ローテーション＋AIチェック）"""
    import time as _time
    genre = _select_mako_least_used_genre()
    try:
        body, url = _fetch_mako_sleep_suggestion(genre)
        if not body or not url:
            return
        score = _check_mako_post_quality(body)
        if score < 60:
            print(f"post_mako_threads_aff_auto: quality low ({score}pts), skipping")
            try:
                line_bot_api.push_message(os.environ.get('LINE_USER_ID', ''), TextSendMessage(
                    text=f"⚠️ MAKO アフィ投稿品質低下（{score}点）でスキップ\nジャンル：{genre['name']}\n本文：{body[:100]}"
                ))
            except Exception:
                pass
            return
        post_id = _post_to_mako_threads(body)
        if post_id and url:
            _time.sleep(5)
            _post_to_mako_threads(
                f"気になる方はこちら\n{url}\n[楽天PR]",
                reply_to_id=post_id
            )
        _record_mako_genre_used(genre['name'])
        print(f"mako threads aff post ({genre['name']}) successful [score:{score}]")
    except Exception as e:
        print(f"post_mako_threads_aff_auto error: {e}")
        try:
            line_bot_api.push_message(os.environ.get('LINE_USER_ID', ''), TextSendMessage(text=f"❌ MAKO アフィ投稿エラー（{genre['name']}）\n{str(e)[:200]}"))
        except Exception:
            pass


