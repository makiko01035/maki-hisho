"""
sns_engine_mako.py
MAKO SNS自動化エンジン（6ロール）
アカウント：@MAKOhealthcare（Threads / X）
テーマ：睡眠×薬膳×医学情報 → foodmakehealth.com誘導 → 楽天アフィ

① リサーチャー   月曜 05:10  今週のテーマ・ジャンル候補を生成
② ライター      月曜 06:10  投稿案生成 → AI採点（言い切りNGチェック含む）→ LINEで確認依頼
③ ポスター      スケジューラ  承認済みストックから連投防止で投稿
④ コレクター    毎日 23:10  Threads APIでパフォーマンスデータ取得
⑤ アナリスト    日曜 20:10  週次分析 → LINEレポート → ライターへフィードバック
⑥ モニター     毎日 07:10 / 13:10 / 22:10  正常稼働・凍結チェック

LINEコマンド（main.pyから handle_mako_approval を呼ぶ）:
  「MAKOok」「MAKOＯＫ」等 → 全投稿案を承認
  「MAKO2,4はNG」          → 指定番号を除いて承認
  「MAKO確認」              → 承認待ち件数を通知

こはるままとの主な違い：
  - トーン：言い切りNG（～かもしれません・～という方もいます）
  - アフィ：控えめ（「気になる方はこちら」程度）・楽天マラソン強化なし
  - 目的：ブログ誘導優先→信頼構築→楽天アフィ
  - 採点：言い切り違反が1箇所でも70点未満に自動減点
"""

import json
import os
import random
import re
import time
from datetime import datetime, timedelta

import requests

# ============================================================
# パス定義
# ============================================================
STOCK_PENDING_PATH  = '/tmp/mako_stock_pending.json'
STOCK_APPROVED_PATH = '/tmp/mako_stock_approved.json'
ANALYTICS_PATH      = '/tmp/mako_analytics.json'
RESEARCH_PATH       = '/tmp/mako_research.json'
POSTED_LOG_PATH     = '/tmp/mako_posted_log.json'
FEEDBACK_PATH       = '/tmp/mako_feedback.json'
QUOTE_STOCK_PATH    = '/tmp/mako_quote_stock.json'

# フォールバック用定数（こはるままの MAKO_THREADS_MORNING と同内容）
_FALLBACK_MORNING = [
    "夜中に何度も目が覚める…\n\nそういう方、思った以上に多いんです。\n\n原因の一つに、就寝後の体温調節がうまくいっていないことがあるかもしれません。\n\n入浴で体を温めてから自然に冷ます流れが、深い眠りに入りやすくなる方もいます。",
    "疲れているのに眠れない…\n\nこれって結構つらいですよね。\n\n「疲労」と「眠気」は別物で、体は疲れていても脳が興奮していると眠れないことがあります。\n\n就寝1時間前にブルーライトを避けると、改善した方もいます。\n\n小さなことですが、試してみる価値はあるかもしれません。",
    "更年期に入ってから眠りが浅くなった、という声を聞くことがあります。\n\nエストロゲンの変動が自律神経に影響し、体温調節が乱れやすくなることが関係しているかもしれません。\n\n薬膳的には、血を補う食材（なつめ・クコの実・黒ごまなど）が助けになるという方もいます。",
    "睡眠は「量」より「質」という話があります。\n\n6時間でも深く眠れる方もいれば、8時間眠っても疲れが取れない方もいます。\n\n睡眠の質に関わる要素は体温・光・音・寝具・ストレスなど様々。\n\n一つずつ試してみるのが遠回りに見えて近道かもしれません。",
    "マグネシウムが睡眠に関係している、という話があります。\n\n神経の興奮を抑え、体をリラックスさせる働きがあるとされています。\n\n海藻・ナッツ・ほうれん草などに含まれています。\n\n食事から意識するのも一つかもしれません。",
]

_FALLBACK_AFF_GENRES = [
    {'name': '睡眠サプリ（GABA）',  'keyword': 'GABA 睡眠 サプリ'},
    {'name': 'マグネシウムサプリ',  'keyword': 'マグネシウム サプリ 睡眠'},
    {'name': 'なつめ薬膳食材',      'keyword': 'なつめ 薬膳 乾燥'},
    {'name': '睡眠アイマスク',      'keyword': 'アイマスク 睡眠 遮光'},
    {'name': 'クコの実',            'keyword': 'クコの実 薬膳 乾燥'},
    {'name': '漢方（睡眠）',        'keyword': '漢方 睡眠 改善'},
    {'name': '睡眠枕',              'keyword': '枕 睡眠 低反発'},
]

# ============================================================
# ユーティリティ
# ============================================================

def _client():
    import anthropic
    return anthropic.Anthropic(api_key=os.environ.get('ANTHROPIC_API_KEY', ''))


def _send_line(message):
    from linebot import LineBotApi
    from linebot.models import TextSendMessage
    token   = os.environ.get('LINE_CHANNEL_ACCESS_TOKEN', '')
    user_id = os.environ.get('LINE_USER_ID', '')
    if not token or not user_id:
        print("[mako] _send_line: credentials not set")
        return
    try:
        LineBotApi(token).push_message(user_id, TextSendMessage(text=message))
    except Exception as e:
        print(f"[mako] _send_line error: {e}")


def _load(path, default=None):
    if default is None:
        default = {}
    try:
        if os.path.exists(path):
            with open(path, 'r', encoding='utf-8') as f:
                return json.load(f)
    except Exception as e:
        print(f"[mako] _load error ({path}): {e}")
    return default


def _save(path, data):
    try:
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"[mako] _save error ({path}): {e}")


def _get_mako_x_client():
    """MAKOのX（tweepy）クライアントを返す。キー未設定時はNone"""
    try:
        import tweepy
        api_key    = (os.environ.get('MAKO_X_API_KEY') or '').strip()
        api_secret = (os.environ.get('MAKO_X_API_SECRET') or '').strip()
        access_tok = (os.environ.get('MAKO_X_ACCESS_TOKEN') or '').strip()
        access_sec = (os.environ.get('MAKO_X_ACCESS_TOKEN_SECRET') or '').strip()
        if not all([api_key, api_secret, access_tok, access_sec]):
            return None
        return tweepy.Client(
            consumer_key=api_key, consumer_secret=api_secret,
            access_token=access_tok, access_token_secret=access_sec
        )
    except Exception as e:
        print(f"[mako] _get_mako_x_client error: {e}")
        return None


def _post_x(text, reply_to_id=None):
    """MAKOのXに投稿。280字超は末尾省略。成功時はtweet_id、失敗時はNone"""
    client = _get_mako_x_client()
    if not client:
        print("[mako] MAKO X keys not set, skipping X post")
        return None
    try:
        if len(text) > 280:
            text = text[:278] + '…'
        kwargs = {'text': text}
        if reply_to_id:
            kwargs['in_reply_to_tweet_id'] = reply_to_id
        resp = client.create_tweet(**kwargs)
        tweet_id = resp.data['id']
        print(f"[mako] X posted: {tweet_id}")
        return tweet_id
    except Exception as e:
        print(f"[mako] _post_x error: {e}")
        return None


def _post_threads(text, reply_to_id=None):
    """MAKOのThreadsに投稿。成功時は post_id、失敗時は None"""
    access_token = os.environ.get('MAKO_THREADS_ACCESS_TOKEN', '').strip()
    user_id      = os.environ.get('MAKO_THREADS_USER_ID', '').strip()
    if not access_token or not user_id:
        print("[mako] MAKO_THREADS tokens not set")
        return None
    try:
        params = {'media_type': 'TEXT', 'text': text, 'access_token': access_token}
        if reply_to_id:
            params['reply_to_id'] = reply_to_id
        r = requests.post(
            f'https://graph.threads.net/v1.0/{user_id}/threads',
            params=params, timeout=15
        )
        if r.status_code != 200:
            print(f"[mako] container error: {r.status_code} {r.text[:200]}")
            return None
        creation_id = r.json().get('id')
        time.sleep(5)
        pub = requests.post(
            f'https://graph.threads.net/v1.0/{user_id}/threads_publish',
            params={'creation_id': creation_id, 'access_token': access_token},
            timeout=15
        )
        if pub.status_code != 200:
            print(f"[mako] publish error: {pub.status_code} {pub.text[:200]}")
            return None
        post_id = pub.json().get('id')
        print(f"[mako] posted: {post_id}")
        return post_id
    except Exception as e:
        print(f"[mako] _post_threads error: {e}")
        return None


def _log_post(post):
    log = _load(POSTED_LOG_PATH, {'recent': []})
    log['recent'].append({
        'type':      post.get('type'),
        'body':      (post.get('body') or '')[:50],
        'genre':     post.get('genre', ''),
        'theme':     post.get('theme', ''),
        'posted_at': post.get('posted_at'),
        'post_id':   post.get('post_id'),
    })
    log['recent'] = log['recent'][-50:]
    _save(POSTED_LOG_PATH, log)


# ============================================================
# ① リサーチャー  月曜 05:10
# ============================================================

def run_researcher():
    """今週の睡眠×薬膳テーマ候補を生成して research.json に保存"""
    try:
        month    = datetime.now().month
        feedback = _load(FEEDBACK_PATH, {})
        advice   = feedback.get('advice', '')

        season_map = {
            1: "冬の冷え・乾燥による睡眠障害",      2: "春前の自律神経乱れ・花粉ストレス",
            3: "春の気・のぼせ・イライラ不眠",       4: "新生活ストレス・五月病予防",
            5: "GW明けの疲労・睡眠負債",             6: "梅雨の湿気・重だるさ・不眠",
            7: "夏の熱・心の高ぶり・夜間覚醒",       8: "お盆疲れ・残暑・睡眠の質低下",
            9: "秋の乾燥・悲しみ・睡眠リズム乱れ", 10: "秋深まる・気虚・更年期不眠",
            11: "冬支度・腎の弱り・眠れない夜",     12: "年末疲労・冷え性・深眠り不足",
        }
        season_hint = season_map.get(month, '睡眠改善')

        prompt = (
            f"今は{month}月・{season_hint}の時期です。\n"
            + (f"前週アナリストからの提言：{advice}\n" if advice else "")
            + "\nMAKO（内科医ママ×睡眠改善×薬膳・30-50代更年期〜子育て世代女性向け）の"
            "今週のThreads投稿テーマをプランニングしてください。\n\n"
            "以下のJSON形式のみで出力（コードブロック不要）：\n"
            '{\n'
            '  "weekly_theme": "今週の大テーマ（20字以内）",\n'
            '  "info_themes": ["情報提供テーマ①", "テーマ②", "テーマ③"],\n'
            '  "aff_keywords": [\n'
            '    {"name": "ジャンル名", "keyword": "楽天検索キーワード"},\n'
            '    ... （7件）\n'
            '  ],\n'
            '  "blog_hook": "ブログ誘導に使えるフレーズ（20字以内）",\n'
            '  "hook_hint": "今週意識すべきフックのヒント（30字以内）"\n'
            '}'
        )

        resp = _client().messages.create(
            model='claude-haiku-4-5-20251001',
            max_tokens=900,
            messages=[{'role': 'user', 'content': prompt}]
        )
        raw = resp.content[0].text.strip()
        m   = re.search(r'\{.*\}', raw, re.DOTALL)
        research = json.loads(m.group()) if m else {
            'weekly_theme': season_hint,
            'info_themes':  [],
            'aff_keywords': _FALLBACK_AFF_GENRES,
            'blog_hook':    'ブログでも詳しく書いています',
            'hook_hint':    '悩みへの共感・医学的根拠・薬膳の知恵',
        }
        research['generated_at'] = datetime.now().isoformat()
        _save(RESEARCH_PATH, research)
        print(f"[mako] researcher done: {research.get('weekly_theme')}")

    except Exception as e:
        print(f"[mako] run_researcher error: {e}")


# ============================================================
# ② ライター  月曜 06:10
# ============================================================

def run_writer():
    """投稿案生成 → AI採点（言い切りNGチェック含む）→ 承認待ち保存 → LINEで確認依頼"""
    try:
        research     = _load(RESEARCH_PATH, {})
        feedback     = _load(FEEDBACK_PATH, {})
        weekly_theme = research.get('weekly_theme', '睡眠改善×薬膳')
        hook_hint    = research.get('hook_hint', '悩みへの共感')
        blog_hook    = research.get('blog_hook', 'ブログでも詳しく書いています')
        aff_keywords = research.get('aff_keywords') or _FALLBACK_AFF_GENRES
        info_themes  = research.get('info_themes', [])
        good_hooks   = feedback.get('good_hooks', [])

        # MAKOは楽天マラソン強化なし・通年一定（7本+7本）
        info_count = 7
        aff_count  = 7

        ai    = _client()
        posts = []

        # ---- 情報提供投稿 7本（睡眠の悩み共感・医学知識・薬膳）----
        i_prompt = (
            f"今週テーマ：{weekly_theme}　フックヒント：{hook_hint}\n"
            f"参考テーマ：{', '.join(info_themes)}\n"
            + (f"先週バズった1行目（参考）：{', '.join(good_hooks[:3])}\n" if good_hooks else "")
            + "MAKO（内科医ママ×睡眠改善×薬膳）のThreads情報提供投稿を7本生成。\n\n"
            "【絶対ルール（採点で減点されます）】\n"
            "・言い切りNG：「〜です」「〜効果があります」→「〜かもしれません」「〜という方もいます」「試してみる価値はあるかもしれません」\n"
            "・売り込みNG：商品名・価格・リンクは一切含めない\n"
            "・ハッシュタグなし\n\n"
            "【推奨】\n"
            "・1投稿150字以内\n"
            "・悩みへの共感から始める\n"
            "・医学的根拠または薬膳の知恵を1つ添える\n"
            f"・最後に自然にブログ誘導（「{blog_hook}」など）を1本に1回だけ入れてOK\n\n"
            'JSON形式のみで出力：[{"type":"info","body":"投稿文","theme":"テーマ"}, ...]'
        )
        r = ai.messages.create(
            model='claude-haiku-4-5-20251001', max_tokens=2000,
            messages=[{'role': 'user', 'content': i_prompt}]
        )
        mt = re.search(r'\[.*\]', r.content[0].text, re.DOTALL)
        if mt:
            posts.extend(json.loads(mt.group()))

        # ---- アフィ投稿 7本（楽天API → 控えめな紹介文）----
        try:
            from blog_yakuzen import search_rakuten_items
        except ImportError:
            search_rakuten_items = None

        for i, kw in enumerate(aff_keywords[:aff_count]):
            try:
                item = None
                if search_rakuten_items:
                    items = search_rakuten_items(kw.get('keyword', '睡眠 サプリ'), hits=5)
                    if items:
                        item = random.choice(items)

                if item:
                    a_prompt = (
                        f"商品名：{item['name'][:40]}\n価格：{item['price']}円\n"
                        f"ジャンル：{kw.get('name')}\n\n"
                        "MAKO（内科医ママ×睡眠改善）のThreads投稿文を作成。\n\n"
                        "【絶対ルール】\n"
                        "・言い切りNG：「効果があります」「改善します」→「〜かもしれません」「〜という方もいます」\n"
                        "・売り込み感NG：「今すぐ買って」「おすすめ！」→「気になる方はこちら」「試してみる価値はあるかもしれません」\n"
                        "・商品名・価格・URLは含めない（アフィリンクはコメントに別投稿するため）\n"
                        "・ハッシュタグなし\n\n"
                        "【推奨】\n"
                        "・悩みへの共感から始める\n"
                        "・このジャンルが睡眠にどう関係するかを医学・薬膳の視点で1行説明\n"
                        "・最後は「気になる方はコメント欄へ」で締める\n"
                        "・全体100字以内\n\n"
                        "投稿文だけ出力。"
                    )
                    r2 = ai.messages.create(
                        model='claude-haiku-4-5-20251001', max_tokens=300,
                        messages=[{'role': 'user', 'content': a_prompt}]
                    )
                    body = r2.content[0].text.strip()
                    posts.append({
                        'type': 'aff', 'body': body,
                        'url': item['url'], 'genre': kw.get('name'), 'theme': kw.get('name'),
                    })
                else:
                    # 楽天API失敗時は情報投稿で代替
                    posts.append({
                        'type': 'info',
                        'body': f"{kw.get('name')}が睡眠に関係しているという話があります。\n詳しくはブログに書いています。",
                        'genre': kw.get('name'), 'theme': kw.get('name'),
                    })
            except Exception as e:
                print(f"[mako] aff gen error ({kw.get('name')}): {e}")

        # ---- 採点フィルタ ----
        approved = []
        rejected = 0
        for post in posts:
            score, fb = _score_post(post['body'], post['type'], ai)
            post['score'] = score
            if score >= 70:
                approved.append(post)
            else:
                regen = _regenerate(post, fb, ai)
                if regen:
                    new_score, _ = _score_post(regen['body'], regen['type'], ai)
                    regen['score'] = new_score
                    if new_score >= 70:
                        approved.append(regen)
                    else:
                        rejected += 1
                else:
                    rejected += 1

        _save(STOCK_PENDING_PATH, {
            'created_at':   datetime.now().isoformat(),
            'weekly_theme': weekly_theme,
            'posts':        approved,
            'rejected_count': rejected,
        })

        # LINE確認依頼
        info_posts = [p for p in approved if p['type'] == 'info']
        aff_posts  = [p for p in approved if p['type'] == 'aff']

        info_prev = '\n'.join([
            f"  {i+1}. {p['body'][:30]}… ({p['score']}点)"
            for i, p in enumerate(info_posts[:3])
        ])
        aff_prev = '\n'.join([
            f"  {len(info_posts)+i+1}. {p['body'][:30]}… ({p['score']}点)"
            for i, p in enumerate(aff_posts[:3])
        ])

        _send_line(
            f"🌙 今週のMAKO投稿案 完成！\n\n"
            f"テーマ：{weekly_theme}\n"
            f"生成：{len(approved)}本（採点落ち：{rejected}本）\n"
            f"  └ 情報提供{len(info_posts)}本 ＋ アフィ{len(aff_posts)}本\n\n"
            f"【情報提供（先頭3件）】\n{info_prev}\n\n"
            f"【アフィ投稿（先頭3件）】\n{aff_prev}\n\n"
            f"✅ 全部OKなら「MAKOok」\n"
            f"番号指定で除外する場合「MAKO2,4はNG」\n"
            f"件数確認は「MAKO確認」"
        )
        print(f"[mako] writer done: {len(approved)} posts, {rejected} rejected")

    except Exception as e:
        print(f"[mako] run_writer error: {e}")


def _score_post(body, post_type, ai=None):
    """投稿案を採点（0-100点）。言い切り違反は自動で大幅減点"""
    # 言い切り表現の簡易チェック（採点AI呼び出し前に減点）
    KIRIKIRI_NG = ['効果があります', '改善します', '解消します', 'です。\n', 'できます。', 'なります。']
    penalty = sum(10 for ng in KIRIKIRI_NG if ng in body)

    try:
        if ai is None:
            ai = _client()
        prompt = (
            f"投稿文：{body}\n\n"
            "採点基準（各25点）：\n"
            "1. フック強度：1行目で「続き読みたい」と思えるか\n"
            "2. 共感度：30-50代睡眠に悩む女性に刺さるか\n"
            "3. 医師らしさ：信頼できる情報提供になっているか・言い切りを避けているか\n"
        )
        if post_type == 'aff':
            prompt += "4. アフィ自然さ：売り込み感なく、「気になる方はこちら」程度に収まっているか\n"
        else:
            prompt += "4. 情報価値：医学×薬膳の知見が自然に盛り込まれているか\n"
        prompt += '\nJSON形式のみで出力：{"score": 整数0-100, "feedback": "改善点30字以内"}'

        r = ai.messages.create(
            model='claude-haiku-4-5-20251001', max_tokens=150,
            messages=[{'role': 'user', 'content': prompt}]
        )
        m = re.search(r'\{.*\}', r.content[0].text, re.DOTALL)
        if m:
            result = json.loads(m.group())
            score  = max(0, result.get('score', 50) - penalty)
            return score, result.get('feedback', '')
        return max(0, 50 - penalty), ''
    except Exception as e:
        print(f"[mako] _score_post error: {e}")
        return max(0, 50 - penalty), ''


def _regenerate(post, feedback_text, ai):
    """低スコア投稿を1回だけ再生成（言い切りNGを特に修正）"""
    try:
        prompt = (
            f"以下の投稿を改善してください。\n\n"
            f"元：{post['body']}\n改善点：{feedback_text}\n\n"
            "MAKO（内科医ママ×睡眠改善×薬膳）のThreads投稿。\n"
            "【必須】言い切りNG：「〜かもしれません」「〜という方もいます」に言い換える。\n"
            "売り込み感NG・ハッシュタグなし・1行目のフックを強くする。\n"
            "投稿文だけ出力。"
        )
        r = ai.messages.create(
            model='claude-haiku-4-5-20251001', max_tokens=300,
            messages=[{'role': 'user', 'content': prompt}]
        )
        new_post = post.copy()
        new_post['body']        = r.content[0].text.strip()
        new_post['regenerated'] = True
        return new_post
    except Exception as e:
        print(f"[mako] _regenerate error: {e}")
        return None


# ============================================================
# LINEからの承認処理（main.py の message_handler から呼ぶ）
# ============================================================

def _norm(s):
    import unicodedata
    return unicodedata.normalize('NFKC', s).lower()


def handle_mako_approval(message):
    """
    戻り値: True = 処理済み / False = 未対応メッセージ
    """
    msg      = message.strip()
    msg_norm = _norm(msg)

    if msg == 'MAKO確認':
        pending  = _load(STOCK_PENDING_PATH, {})
        approved = _load(STOCK_APPROVED_PATH, {'posts': []})
        p_posts  = pending.get('posts', [])
        a_posts  = [p for p in approved.get('posts', []) if not p.get('posted')]
        _send_line(
            f"🌙 MAKO投稿ストック状況\n\n"
            f"承認待ち：{len(p_posts)}本（情報{sum(1 for p in p_posts if p.get('type')=='info')}・アフィ{sum(1 for p in p_posts if p.get('type')=='aff')}）\n"
            f"承認済み（未投稿）：{len(a_posts)}本"
        )
        return True

    # OK承認（MAKO ok / MAKOok / MAKOＯＫ 等）
    suffix = _norm(msg.replace('MAKO', '', 1)).strip()
    if msg.upper().startswith('MAKO') and suffix == 'ok':
        pending = _load(STOCK_PENDING_PATH, {})
        posts   = pending.get('posts', [])
        if not posts:
            _send_line("📭 承認待ちのMAKO投稿案がありません。月曜に自動生成されます。")
            return True
        approved = _load(STOCK_APPROVED_PATH, {'posts': []})
        approved['posts'].extend(posts)
        _save(STOCK_APPROVED_PATH, approved)
        _save(STOCK_PENDING_PATH, {})
        i_cnt = sum(1 for p in posts if p.get('type') == 'info')
        a_cnt = sum(1 for p in posts if p.get('type') == 'aff')
        _send_line(
            f"✅ MAKO {len(posts)}本を承認しました！\n"
            f"（情報提供{i_cnt}本・アフィ{a_cnt}本）\n\n"
            "今週から順次投稿されます。"
        )
        return True

    m = re.match(r'MAKO([0-9,、\s]+)はNG$', msg, re.IGNORECASE)
    if m:
        ng_str = re.sub(r'[、\s]', ',', m.group(1))
        ng_idx = {int(n) - 1 for n in ng_str.split(',') if n.strip().isdigit()}
        pending = _load(STOCK_PENDING_PATH, {})
        posts   = pending.get('posts', [])
        kept    = [p for i, p in enumerate(posts) if i not in ng_idx]
        approved = _load(STOCK_APPROVED_PATH, {'posts': []})
        approved['posts'].extend(kept)
        _save(STOCK_APPROVED_PATH, approved)
        _save(STOCK_PENDING_PATH, {})
        _send_line(f"✅ MAKO {len(kept)}本を承認（{len(ng_idx)}本を除外）しました。")
        return True

    return False


# ============================================================
# ③ ポスター  スケジューラから呼ばれる
# ============================================================

def run_poster_info():
    """情報提供投稿：承認済みストック → なければフォールバック"""
    try:
        approved   = _load(STOCK_APPROVED_PATH, {'posts': []})
        candidates = [
            p for p in approved.get('posts', [])
            if p.get('type') == 'info' and not p.get('posted')
        ]
        if candidates:
            log           = _load(POSTED_LOG_PATH, {'recent': []})
            recent_themes = [p.get('theme') for p in log['recent'][-3:]]
            no_dup        = [p for p in candidates if p.get('theme') not in recent_themes]
            post          = random.choice(no_dup if no_dup else candidates)

            post_id = _post_threads(post['body'])
            if post_id:
                post.update({'posted': True, 'posted_at': datetime.now().isoformat(), 'post_id': post_id})
                _save(STOCK_APPROVED_PATH, approved)
                _log_post(post)
                print(f"[mako] poster info (stock): {post['body'][:30]}")
                time.sleep(10)
                _post_x(post['body'])
                return

        # フォールバック
        text = random.choice(_FALLBACK_MORNING)
        _post_threads(text)
        print(f"[mako] poster info (fallback): {text[:30]}")
        time.sleep(10)
        _post_x(text)

    except Exception as e:
        print(f"[mako] run_poster_info error: {e}")
        try:
            _post_threads(random.choice(_FALLBACK_MORNING))
        except Exception:
            pass


def run_poster_aff():
    """アフィ投稿：承認済みストック → なければ楽天APIでその場生成"""
    try:
        approved   = _load(STOCK_APPROVED_PATH, {'posts': []})
        candidates = [
            p for p in approved.get('posts', [])
            if p.get('type') == 'aff' and not p.get('posted')
        ]
        if candidates:
            log           = _load(POSTED_LOG_PATH, {'recent': []})
            recent_genres = [p.get('genre') for p in log['recent'][-3:]]
            no_dup        = [p for p in candidates if p.get('genre') not in recent_genres]
            post          = random.choice(no_dup if no_dup else candidates)

            post_id = _post_threads(post['body'])
            if post_id:
                time.sleep(5)
                if post.get('url'):
                    _post_threads(f"気になる方はこちら\n{post['url']}\n[楽天PR]", reply_to_id=post_id)
                post.update({'posted': True, 'posted_at': datetime.now().isoformat(), 'post_id': post_id})
                _save(STOCK_APPROVED_PATH, approved)
                _log_post(post)
                print(f"[mako] poster aff (stock): {post['body'][:30]}")
                time.sleep(10)
                tweet_id = _post_x(post['body'])
                if tweet_id and post.get('url'):
                    time.sleep(5)
                    _post_x(f"気になる方はこちら\n{post['url']}\n[楽天PR]", reply_to_id=tweet_id)
                return

        _fallback_aff()

    except Exception as e:
        print(f"[mako] run_poster_aff error: {e}")
        _fallback_aff()


def _fallback_aff():
    """ストック切れ時：楽天APIでその場生成して投稿"""
    try:
        from blog_yakuzen import search_rakuten_items
        genre = random.choice(_FALLBACK_AFF_GENRES)
        items = search_rakuten_items(genre['keyword'], hits=5)
        if not items:
            return
        item   = random.choice(items)
        prompt = (
            f"ジャンル：{genre['name']}\n商品名：{item['name'][:40]}\n\n"
            "MAKO（内科医ママ×睡眠改善）のThreads投稿文。\n"
            "言い切りNG・売り込み感NG・「気になる方はコメント欄へ」で締める。\n"
            "100字以内。投稿文だけ出力。"
        )
        resp = _client().messages.create(
            model='claude-haiku-4-5-20251001', max_tokens=200,
            messages=[{'role': 'user', 'content': prompt}]
        )
        body    = resp.content[0].text.strip()
        post_id = _post_threads(body)
        if post_id and item.get('url'):
            time.sleep(5)
            _post_threads(f"気になる方はこちら\n{item['url']}\n[楽天PR]", reply_to_id=post_id)
        print(f"[mako] fallback aff done: {body[:30]}")
        time.sleep(10)
        tweet_id = _post_x(body)
        if tweet_id and item.get('url'):
            time.sleep(5)
            _post_x(f"気になる方はこちら\n{item['url']}\n[楽天PR]", reply_to_id=tweet_id)
    except Exception as e:
        print(f"[mako] _fallback_aff error: {e}")


# ============================================================
# ④ コレクター  毎日 23:10
# ============================================================

def run_collector():
    """Threads APIで投稿パフォーマンスデータを取得・蓄積"""
    try:
        token   = os.environ.get('MAKO_THREADS_ACCESS_TOKEN', '').strip()
        user_id = os.environ.get('MAKO_THREADS_USER_ID', '').strip()
        if not token or not user_id:
            print("[mako] collector: tokens not set")
            return

        r = requests.get(
            f'https://graph.threads.net/v1.0/{user_id}/threads',
            params={
                'fields': 'id,text,timestamp,like_count,reply_count,repost_count,views',
                'access_token': token,
                'limit': 20,
            },
            timeout=15
        )
        if r.status_code != 200:
            print(f"[mako] collector API error: {r.status_code} {r.text[:200]}")
            return

        analytics = _load(ANALYTICS_PATH, {'posts': []})
        existing  = {p['id'] for p in analytics['posts']}
        new_count = 0

        for t in r.json().get('data', []):
            tid = t.get('id')
            if not tid:
                continue
            entry = {
                'id':           tid,
                'text':         (t.get('text') or '')[:100],
                'timestamp':    t.get('timestamp'),
                'like_count':   t.get('like_count', 0),
                'reply_count':  t.get('reply_count', 0),
                'repost_count': t.get('repost_count', 0),
                'views':        t.get('views', 0),
                'collected_at': datetime.now().isoformat(),
            }
            if tid in existing:
                for i, p in enumerate(analytics['posts']):
                    if p['id'] == tid:
                        analytics['posts'][i] = entry
                        break
            else:
                analytics['posts'].append(entry)
                new_count += 1

        analytics['posts']          = analytics['posts'][-200:]
        analytics['last_collected'] = datetime.now().isoformat()
        _save(ANALYTICS_PATH, analytics)
        print(f"[mako] collector done: {new_count} new, total {len(analytics['posts'])}")

    except Exception as e:
        print(f"[mako] run_collector error: {e}")


# ============================================================
# ⑤ アナリスト  日曜 20:10
# ============================================================

def run_analyst():
    """1週間の投稿を分析 → LINEレポート → フィードバック保存"""
    try:
        analytics = _load(ANALYTICS_PATH, {'posts': []})
        posts     = analytics.get('posts', [])

        if not posts:
            _send_line("📊 MAKO：分析データがまだありません。\nコレクターが蓄積し始めたら来週から分析できます！")
            return

        cutoff = datetime.now() - timedelta(days=7)
        recent = []
        for p in posts:
            ts = p.get('timestamp', '')
            if not ts:
                continue
            try:
                dt = datetime.fromisoformat(ts.replace('Z', '+00:00').replace('+0000', '+00:00'))
                if dt.replace(tzinfo=None) > cutoff:
                    recent.append(p)
            except Exception:
                pass

        if not recent:
            _send_line("📊 MAKO：今週の投稿データがありません。")
            return

        for p in recent:
            p['eng'] = (
                int(p.get('like_count', 0)) * 3
                + int(p.get('repost_count', 0)) * 5
                + int(p.get('views', 0) or 0) * 0.01
            )

        ranked   = sorted(recent, key=lambda x: x['eng'], reverse=True)
        top3     = ranked[:3]
        bottom3  = ranked[-3:]
        avg_like = sum(int(p.get('like_count', 0)) for p in recent) / len(recent)
        avg_view = sum(int(p.get('views', 0) or 0) for p in recent) / len(recent)

        top_texts = '\n'.join([p.get('text', '')[:60] for p in top3])
        a_prompt  = (
            f"今週バズったMAKO投稿3件の1行目フックのパターンを分析。\n\n{top_texts}\n\n"
            "「なぜ効いたか」を30字以内で分析し、来週のライターへのアドバイスを一言で。\n"
            "医師×睡眠の投稿として言い切りを避けつつ伸びた理由に注目。\n"
            'JSON：{"pattern":"パターン説明","advice":"来週へのアドバイス","good_hooks":["1行目①","1行目②","1行目③"]}'
        )
        r = _client().messages.create(
            model='claude-haiku-4-5-20251001', max_tokens=400,
            messages=[{'role': 'user', 'content': a_prompt}]
        )
        m        = re.search(r'\{.*\}', r.content[0].text, re.DOTALL)
        analysis = json.loads(m.group()) if m else {}

        _save(FEEDBACK_PATH, {
            'updated_at':   datetime.now().isoformat(),
            'good_hooks':   analysis.get('good_hooks', []),
            'hook_pattern': analysis.get('pattern', ''),
            'advice':       analysis.get('advice', ''),
        })

        top_lines = '\n'.join([
            f"  ❤️{p.get('like_count',0)} 👀{int(p.get('views',0) or 0)} 「{p.get('text','')[:22]}…」"
            for p in top3
        ])
        bot_lines = '\n'.join([
            f"  ❤️{p.get('like_count',0)} 👀{int(p.get('views',0) or 0)} 「{p.get('text','')[:22]}…」"
            for p in bottom3
        ])

        _send_line(
            f"📊 MAKO 週次レポート\n\n"
            f"📅 今週の投稿数：{len(recent)}本\n"
            f"❤️ 平均いいね：{avg_like:.1f}\n"
            f"👀 平均閲覧：{avg_view:.0f}\n\n"
            f"🔥 伸びた投稿TOP3：\n{top_lines}\n\n"
            f"📉 伸びなかった投稿：\n{bot_lines}\n\n"
            f"💡 バズりパターン：{analysis.get('pattern','データ収集中')}\n"
            f"✏️ 来週への提言：{analysis.get('advice','')}"
        )
        print("[mako] analyst done")

    except Exception as e:
        print(f"[mako] run_analyst error: {e}")


# ============================================================
# ⑥ モニター  毎日 07:10 / 13:10 / 22:10
# ============================================================

def run_monitor():
    """正常稼働確認・凍結チェック・承認放置アラート"""
    try:
        token   = os.environ.get('MAKO_THREADS_ACCESS_TOKEN', '').strip()
        user_id = os.environ.get('MAKO_THREADS_USER_ID', '').strip()
        issues  = []

        if token and user_id:
            try:
                r = requests.get(
                    f'https://graph.threads.net/v1.0/{user_id}',
                    params={'fields': 'id,username', 'access_token': token},
                    timeout=10
                )
                if r.status_code == 401:
                    issues.append("⚠️ MAKOのThreadsトークン期限切れの可能性（401）")
                elif r.status_code == 403:
                    issues.append("🚨 MAKOのThreadsアカウントに制限がかかっている可能性（403）")
                elif r.status_code not in (200, 404):
                    issues.append(f"⚠️ MAKO Threads API異常（{r.status_code}）")
            except Exception as e:
                issues.append(f"⚠️ MAKO Threads接続エラー: {str(e)[:40]}")
        # トークン未設定は想定内（未開始）のためアラートなし

        if not _get_mako_x_client():
            issues.append("⚠️ MAKO X APIキー未設定（MAKO_X_API_KEY等）")

        if datetime.now().hour >= 22:
            log        = _load(POSTED_LOG_PATH, {'recent': []})
            today      = datetime.now().strftime('%Y-%m-%d')
            today_posts = [p for p in log['recent'] if (p.get('posted_at') or '').startswith(today)]
            if not today_posts:
                issues.append("⚠️ MAKOの今日の投稿記録がありません")

        pending = _load(STOCK_PENDING_PATH, {})
        if pending.get('created_at'):
            try:
                days_old = (datetime.now() - datetime.fromisoformat(pending['created_at'])).days
                if days_old >= 3:
                    issues.append(
                        f"📭 MAKO承認待ち投稿案が{days_old}日放置されています。\n"
                        "「MAKOok」で承認してください。"
                    )
            except Exception:
                pass

        if issues:
            _send_line("🔔 MAKOエンジン モニターアラート\n\n" + "\n".join(issues))
            print(f"[mako] monitor: {len(issues)} issues")
        else:
            print("[mako] monitor: all OK")

    except Exception as e:
        print(f"[mako] run_monitor error: {e}")


# ============================================================
# ⑦ 格言ポスター  毎朝 05:00〜05:15（X のみ・Threadsは手動）
# ============================================================

def run_poster_morning_quote():
    """格言ストックから1本選んでXに投稿。ストック切れ時はフォールバック文を投稿"""
    try:
        stock = _load(QUOTE_STOCK_PATH, {'quotes': []})
        remaining = [q for q in stock.get('quotes', []) if not q.get('posted')]

        if remaining:
            quote = random.choice(remaining)
            tweet_id = _post_x(quote['body'])
            if tweet_id:
                quote.update({'posted': True, 'posted_at': datetime.now().isoformat()})
                _save(QUOTE_STOCK_PATH, stock)
                print(f"[mako] morning quote posted: {quote['body'][:30]}")
        else:
            # ストック切れ：フォールバック
            fallback = random.choice(_FALLBACK_QUOTES)
            _post_x(fallback)
            print(f"[mako] morning quote (fallback): {fallback[:30]}")

    except Exception as e:
        print(f"[mako] run_poster_morning_quote error: {e}")


# フォールバック格言（ストック切れ時用）
_FALLBACK_QUOTES = [
    "眠れないのは意志が弱いからじゃない\n脳がまだ仕事してるだけかもしれない",
    "回復中に「もっと頑張らなきゃ」は\n逆効果かもしれない",
    "明日の自分は\n今夜ちゃんと眠った自分が作る",
    "「疲れたな」と思ったとき\nそれは心より先に体が白旗を上げてる",
    "ワーママの夜って\nやること終わってから\nやっと自分の時間が来る\n眠れないのはそのせいかもしれない",
    "なにがあろうとオレはオレなんだ\n——水木しげる\n\nがんばりすぎる人ほど\n忘れてる感覚だと思う",
    "どうして、自分を責めるんですか\n重要な時には他人がちゃんと\n責めてくれるんだから\n——アインシュタイン",
    "\"向いてない\"んじゃなくて\n疲れてるだけのこともある",
    "「ちゃんとしなきゃ」の前に\nまず今日を生き延びたことを\n認めていいかもしれない",
    "10分でも、自分の体に戻る時間を作る\nそれが睡眠の質を変えることがある",
]


# ============================================================
# ⑧ 格言ジェネレーター  毎月1日 04:00（スケジューラ経由）
# ============================================================

def run_quote_generator():
    """毎月1日：過去の反応を踏まえて格言30本を生成しストックに追加"""
    try:
        # 過去のアナリストフィードバックを参照
        feedback = _load(FEEDBACK_PATH, {})
        pattern  = feedback.get('pattern', '')
        advice   = feedback.get('advice', '')

        # 残りストック数を確認
        stock     = _load(QUOTE_STOCK_PATH, {'quotes': []})
        remaining = len([q for q in stock.get('quotes', []) if not q.get('posted')])
        print(f"[mako] quote generator: {remaining} remaining in stock")

        feedback_section = ''
        if pattern or advice:
            feedback_section = (
                f"\n\n【先月の反応を踏まえて】\n"
                f"伸びたパターン：{pattern}\n"
                f"来月への提言：{advice}\n"
                f"これらの傾向を参考に、より刺さる言葉を生成してください。"
            )

        prompt = (
            "MAKOというアカウント向けに、朝5時に投稿する格言・共感メモを30本作ってください。\n\n"
            "【MAKOのキャラクター】\n"
            "内科医ママ × 睡眠改善。医学×薬膳の両面から睡眠の悩みに寄り添う。\n"
            "疲れたワーママ・更年期・自分責めしがちな人に向けて発信。\n\n"
            "【絶対ルール】\n"
            "・言い切りNG（「〜です」「〜効果あります」→「かもしれない」「という方もいます」「試す価値はある」）\n"
            "・売り込み感NG\n"
            "・医師として断言するのではなく、寄り添う視点で\n"
            "・1投稿は4行以内・100字以内\n\n"
            "【4タイプを均等に：各7〜8本】\n"
            "①気づき：「〜じゃなくて、〜かもしれない」系\n"
            "②許可：「〜しなくていい」「〜でいい」系\n"
            "③言語化：「〜って、〜感覚がある」共感系\n"
            "④未来：「〜すると、明日が変わる」希望系\n\n"
            "【水木しげる・アインシュタインの実在引用を各1本混ぜる（計2本）】\n"
            "引用形式：「言葉」\n——名前\n\nMAKOの一言（2行以内）\n\n"
            "【出力形式】\n"
            "各投稿を---で区切って、番号なし・説明なし・投稿文のみ出力。"
            + feedback_section
        )

        resp = _client().messages.create(
            model='claude-haiku-4-5-20251001',
            max_tokens=4000,
            messages=[{'role': 'user', 'content': prompt}]
        )
        raw = resp.content[0].text.strip()

        # ---区切りでパース
        new_quotes = []
        for chunk in raw.split('---'):
            body = chunk.strip()
            if body and len(body) >= 10:
                new_quotes.append({
                    'body':       body,
                    'posted':     False,
                    'created_at': datetime.now().isoformat(),
                })

        if not new_quotes:
            print("[mako] quote generator: parse failed, no quotes extracted")
            return

        stock['quotes'] = stock.get('quotes', []) + new_quotes
        # 投稿済みが多くなりすぎたら古い投稿済みを削除（上限200件）
        stock['quotes'] = stock['quotes'][-200:]
        _save(QUOTE_STOCK_PATH, stock)

        total_remaining = len([q for q in stock['quotes'] if not q.get('posted')])
        print(f"[mako] quote generator: added {len(new_quotes)} quotes, total remaining {total_remaining}")
        _send_line(
            f"📖 MAKO格言ストック更新\n\n"
            f"今月分：{len(new_quotes)}本追加\n"
            f"残りストック：{total_remaining}本\n\n"
            f"毎朝5時に1本ずつXに投稿されます"
        )

    except Exception as e:
        print(f"[mako] run_quote_generator error: {e}")
