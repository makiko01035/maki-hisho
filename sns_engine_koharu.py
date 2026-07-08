"""
sns_engine_koharu.py
こはるまま SNS自動化エンジン（6ロール）

① リサーチャー   月曜 05:00  今週のテーマ・ジャンル候補を生成
② ライター      月曜 06:00  投稿案生成 → AI採点 → 80点以上は自動承認・70〜79点はLINEで確認依頼
③ ポスター      スケジューラ  承認済みストックから連投防止で投稿
④ コレクター    毎日 23:00  Threads APIでパフォーマンスデータ取得
⑤ アナリスト    日曜 20:00  週次分析 → LINEレポート → ライターへフィードバック
⑥ モニター     毎日 07:00 / 13:00 / 22:00  正常稼働・凍結チェック

LINEコマンド（main.pyから handle_approval を呼ぶ）:
  「こはるままOK」        → 全投稿案を承認
  「こはるまま2,4はNG」   → 指定番号を除いて承認
  「こはるまま確認」       → 承認待ち件数を通知
"""

import json
import os
import random
import re
import time
from datetime import datetime, timedelta
import pytz
_JST = pytz.timezone('Asia/Tokyo')

import requests

# ============================================================
# パス定義（Renderの /tmp は揮発性だが投稿ストックとして使用）
# ============================================================
STOCK_PENDING_PATH  = '/tmp/koharu_stock_pending.json'
STOCK_APPROVED_PATH = '/tmp/koharu_stock_approved.json'
ANALYTICS_PATH      = '/tmp/koharu_analytics.json'
RESEARCH_PATH       = '/tmp/koharu_research.json'
POSTED_LOG_PATH     = '/tmp/koharu_posted_log.json'
FEEDBACK_PATH       = '/tmp/koharu_feedback.json'

# フォールバック用（ストック切れ時）
_FALLBACK_MORNING = [
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

_FALLBACK_HOOKS = [
    "子連れ旅でこれ持ってくよかった",
    "旅行前日に気づいた神アイテム",
    "旅行バッグに必ず入れてるの、これ",
    "去年の旅行で後悔して今年買ったのが",
    "子連れ旅行、荷物減らすために買ったのが",
    "おでかけバッグに毎回入れてるのが",
    "旅行の準備で「これ買ってよかった」ってなったのが",
]

# threads_guide.html の冒頭フック集（カテゴリ別）。使い回し感を減らすため全カテゴリからランダム選択
_HOOK_CATEGORIES = {
    '驚き・発見系': [
        "正直ノーマークだった。", "完全ノーマークだった、", "事件です。", "天才やん...",
        "信じられないんですが、", "大変なことが起こりました", "センスいい人しかきづいてないけど、",
    ],
    '共感・呼びかけ系': _FALLBACK_HOOKS + [
        "出産前に知りたかった...", "3年悩んだアレ、1日で解決した。", "今すぐやめて！！",
        "勘違いしている人が多いですが、", "100回以上言ってるけど、",
    ],
    '愛用・推し系': [
        "私が愛してやまない、", "私の推しの、", "これ内緒にして欲しいのですが、",
        "ずっと我慢してたけど買った。",
    ],
    '価格・お得系': [
        "値段バグなんよ...", "これで1,000円台？", "価格見て二度見した", "マラソンで価格おかしいことになってる",
    ],
    '口コミ・実績系': [
        "楽天1位、納得🙂‍↕️", "SNSでバズってた理由が分かる...", "口コミ見たら泣いた",
        "★4.8の理由、見ればわかる", "口コミに『もっと早く買えば』多すぎ", "低評価レビューが参考になる...",
    ],
    '緊急性系': [
        "前回は3日で売り切れてた😭", "再販待ってた人、今出てる", "在庫残りわずかって出てる", "クーポン今日まで！！",
    ],
}
_ALL_HOOKS = [h for hooks in _HOOK_CATEGORIES.values() for h in hooks]

# コメント欄の誘導フレーズ（「こちら」系はリーチが弱いのでNG＝threads_guide.html）
_LINK_PHRASES = [
    "楽天1位、納得🙂‍↕️", "SNSでバズってた理由が分かる...", "口コミ見たら泣いた", "★4.8の理由、見ればわかる",
    "値段バグなんよ...", "価格見て二度見した", "マラソンで価格おかしいことになってる",
    "前回は3日で売り切れてた😭", "再販待ってた人、今出てる", "在庫残りわずかって出てる",
]


def _link_reply_text(url):
    return f"{random.choice(_LINK_PHRASES)}\n{url}\n[楽天PR]"

_FALLBACK_AFF_GENRES = [
    {'name': 'シアーパーカー・UVカット', 'keyword': 'シアーパーカー UVカット 羽織り レディース'},
    {'name': '折りたたみ日傘',           'keyword': '日傘 折りたたみ 完全遮光 軽量 UVカット'},
    {'name': 'アウトドアワゴン',         'keyword': 'キャリーワゴン アウトドア 折りたたみ 子連れ'},
    {'name': 'ハンディファン',           'keyword': 'ハンディファン 携帯扇風機 軽量 USB 旅行'},
    {'name': 'スーツケース',             'keyword': 'スーツケース 軽量 旅行 子連れ レディース'},
    {'name': '子ども旅行グッズ',         'keyword': '子ども 旅行 暇つぶし 新幹線 おもちゃ'},
    {'name': '旅行ポーチ',               'keyword': '旅行ポーチ トラベル コスメ 防水 コンパクト'},
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
    token = os.environ.get('LINE_CHANNEL_ACCESS_TOKEN', '')
    user_id = os.environ.get('LINE_USER_ID', '')
    if not token or not user_id:
        print("[koharu] _send_line: credentials not set")
        return
    try:
        LineBotApi(token).push_message(user_id, TextSendMessage(text=message))
    except Exception as e:
        print(f"[koharu] _send_line error: {e}")


def _load(path, default=None):
    if default is None:
        default = {}
    try:
        if os.path.exists(path):
            with open(path, 'r', encoding='utf-8') as f:
                return json.load(f)
    except Exception as e:
        print(f"[koharu] _load error ({path}): {e}")
    return default


def _save(path, data):
    try:
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"[koharu] _save error ({path}): {e}")


def _post(text, reply_to_id=None):
    """こはるままのThreadsに投稿。成功時は post_id、失敗時は None"""
    access_token = os.environ.get('KOHARU_THREADS_ACCESS_TOKEN', '').strip()
    user_id = os.environ.get('KOHARU_THREADS_USER_ID', '').strip()
    if not access_token or not user_id:
        print("[koharu] Threads tokens not set")
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
            print(f"[koharu] container error: {r.status_code} {r.text[:200]}")
            return None
        creation_id = r.json().get('id')
        time.sleep(5)
        pub = requests.post(
            f'https://graph.threads.net/v1.0/{user_id}/threads_publish',
            params={'creation_id': creation_id, 'access_token': access_token},
            timeout=15
        )
        if pub.status_code != 200:
            print(f"[koharu] publish error: {pub.status_code} {pub.text[:200]}")
            return None
        post_id = pub.json().get('id')
        print(f"[koharu] posted: {post_id}")
        return post_id
    except Exception as e:
        print(f"[koharu] _post error: {e}")
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
# ① リサーチャー  月曜 05:00
# ============================================================

def run_researcher():
    """今週のテーマ・ジャンル候補を生成して research.json に保存"""
    try:
        month = datetime.now().month
        day   = datetime.now().day
        is_marathon = 15 <= day <= 25

        season_map = {
            1: "お正月明け・防寒・初旅行",    2: "バレンタイン・冬旅行",
            3: "春旅行準備・卒業旅行",         4: "春のお出かけ・GW準備",
            5: "GW旅行・アウトドア開始",       6: "梅雨・夏旅行準備・父の日",
            7: "夏休み・プール・海・熱中症対策", 8: "お盆帰省・子連れ旅行最盛期",
            9: "秋旅行・運動会",               10: "紅葉・秋旅行・ハロウィン",
            11: "七五三・冬旅行準備",          12: "クリスマス旅行・年末帰省",
        }
        season_hint = season_map.get(month, '')

        feedback = _load(FEEDBACK_PATH, {})
        advice   = feedback.get('advice', '')

        prompt = (
            f"今は{month}月・{season_hint}のシーズンです。\n"
            f"楽天マラソン期間中：{'はい（投稿を強化）' if is_marathon else 'いいえ'}\n"
            + (f"前週のアナリストからの提言：{advice}\n" if advice else "")
            + "\nこはるまま（旅行×楽天アフィ・30-40代子連れワーママ向け）の"
            "今週のSNS投稿テーマをプランニングしてください。\n\n"
            "以下のJSON形式のみで出力（コードブロック不要）：\n"
            '{\n'
            '  "weekly_theme": "今週の大テーマ（20字以内）",\n'
            '  "morning_themes": ["朝つぶやきテーマ①", "テーマ②", "テーマ③"],\n'
            '  "aff_keywords": [\n'
            '    {"name": "ジャンル名", "keyword": "楽天検索キーワード"},\n'
            '    ... （7件）\n'
            '  ],\n'
            f'  "marathon_boost": {"true" if is_marathon else "false"},\n'
            '  "hook_hint": "今週意識すべきフックのヒント（30字以内）"\n'
            '}'
        )

        resp = _client().messages.create(
            model='claude-haiku-4-5-20251001',
            max_tokens=900,
            messages=[{'role': 'user', 'content': prompt}]
        )
        raw = resp.content[0].text.strip()
        m = re.search(r'\{.*\}', raw, re.DOTALL)
        research = json.loads(m.group()) if m else {
            'weekly_theme': season_hint,
            'morning_themes': [],
            'aff_keywords': _FALLBACK_AFF_GENRES,
            'marathon_boost': is_marathon,
            'hook_hint': '共感・お得感・季節感',
        }
        research['generated_at'] = datetime.now().isoformat()
        _save(RESEARCH_PATH, research)
        print(f"[koharu] researcher done: {research.get('weekly_theme')}")

    except Exception as e:
        print(f"[koharu] run_researcher error: {e}")


# ============================================================
# ② ライター  月曜 06:00
# ============================================================

def run_writer():
    """投稿案生成 → AI採点 → 承認待ちストック保存 → LINEで確認依頼"""
    try:
        research     = _load(RESEARCH_PATH, {})
        feedback       = _load(FEEDBACK_PATH, {})
        weekly_theme   = research.get('weekly_theme', '旅行×楽天アフィ')
        hook_hint      = research.get('hook_hint', '共感・お得感')
        aff_keywords   = research.get('aff_keywords') or _FALLBACK_AFF_GENRES
        morning_themes = research.get('morning_themes', [])
        good_hooks     = feedback.get('good_hooks', [])
        is_marathon    = research.get('marathon_boost', False)

        # 1日5投稿（朝1・アフィ4）＋休日朝1本分。強化期（楽天マラソン）は生成数を2倍
        morning_count = 18 if is_marathon else 9
        aff_count     = 56 if is_marathon else 28

        ai = _client()
        posts = []

        # ---- 朝つぶやき（通常9本・強化期18本）----
        m_prompt = (
            f"今週テーマ：{weekly_theme}　フックヒント：{hook_hint}\n"
            f"参考テーマ：{', '.join(morning_themes)}\n"
            + (f"先週バズった1行目（参考）：{', '.join(good_hooks[:3])}\n" if good_hooks else "")
            + "こはるまま（旅行×楽天アフィ・30-40代子連れワーママ向け）の"
            f"朝つぶやきを{morning_count}本生成。\n\n"
            "ルール：1投稿50字以内・テキストのみ・旅あるある共感系（売り込みなし）\n"
            "ですます調NG・口語・体言止めOK・URLなし・ハッシュタグなし\n"
            "1行目が命。\n\n"
            'JSON形式のみで出力：[{"type":"morning","body":"投稿文","theme":"テーマ"}, ...]'
        )
        r = ai.messages.create(
            model='claude-haiku-4-5-20251001', max_tokens=4000 if is_marathon else 2000,
            messages=[{'role': 'user', 'content': m_prompt}]
        )
        mt = re.search(r'\[.*\]', r.content[0].text, re.DOTALL)
        if mt:
            posts.extend(json.loads(mt.group()))

        # ---- アフィ投稿（通常28本・強化期56本）楽天API → Claude生成 ----
        # ジャンル数より必要数が多い場合はキーワードを周回して埋める
        try:
            from blog_yakuzen import search_rakuten_items
        except ImportError:
            search_rakuten_items = None

        if aff_keywords:
            repeat = (aff_count // len(aff_keywords)) + 1
            aff_kw_list = (aff_keywords * repeat)[:aff_count]
        else:
            aff_kw_list = []
        for i, kw in enumerate(aff_kw_list):
            try:
                item = None
                if search_rakuten_items:
                    items = search_rakuten_items(kw.get('keyword', '旅行グッズ'), hits=5)
                    if items:
                        item = random.choice(items)

                hook = random.choice(_ALL_HOOKS)

                if item:
                    a_prompt = (
                        f"商品名：{item['name'][:40]}\n価格：{item['price']}円\n"
                        f"ジャンル：{kw.get('name')}\n\n"
                        f"こはるままのThreads投稿文を作成。冒頭は必ず「{hook}」で始める。\n"
                        "ルール：3行以内・URLなし・ハッシュタグなし\n"
                        "ですます調NG・口語・体言止めOK\n"
                        "機能ではなく『使った後の変化・未来』を伝える\n"
                        "30〜40代子連れ旅行好きワーママに刺さる言葉\n\n"
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
                    # 楽天API失敗時は共感ネタで代替
                    posts.append({
                        'type': 'morning',
                        'body': f"{hook}、旅行前に調べておいてよかった一品。（{kw.get('name')}）",
                        'genre': kw.get('name'), 'theme': kw.get('name'),
                    })
            except Exception as e:
                print(f"[koharu] aff gen error ({kw.get('name')}): {e}")

        # ---- 採点フィルタ ----
        # 80点以上は自動承認（確認不要）、70〜79点はLINE確認待ち、70点未満は再生成
        auto_approved = []
        approved = []
        rejected = 0
        for post in posts:
            score, fb = _score_post(post['body'], post['type'], ai)
            post['score'] = score
            if score >= 80:
                auto_approved.append(post)
            elif score >= 70:
                approved.append(post)
            else:
                regen = _regenerate(post, fb, ai)
                if regen:
                    new_score, _ = _score_post(regen['body'], regen['type'], ai)
                    regen['score'] = new_score
                    if new_score >= 80:
                        auto_approved.append(regen)
                    elif new_score >= 70:
                        approved.append(regen)
                    else:
                        rejected += 1
                else:
                    rejected += 1

        # 80点以上は即・承認済みストックへ
        if auto_approved:
            approved_stock = _load(STOCK_APPROVED_PATH, {'posts': []})
            approved_stock.setdefault('posts', []).extend(auto_approved)
            _save(STOCK_APPROVED_PATH, approved_stock)

        _save(STOCK_PENDING_PATH, {
            'created_at': datetime.now().isoformat(),
            'weekly_theme': weekly_theme,
            'posts': approved,
            'rejected_count': rejected,
        })

        # LINE確認依頼（70〜79点の分だけ）
        auto_morning = [p for p in auto_approved if p['type'] == 'morning']
        auto_aff     = [p for p in auto_approved if p['type'] == 'aff']
        morning_posts = [p for p in approved if p['type'] == 'morning']
        aff_posts     = [p for p in approved if p['type'] == 'aff']

        marathon_label = "🔥 楽天マラソン強化期" if is_marathon else "通常期"

        if not approved:
            # 確認待ちがゼロ＝全部自動承認 or 落選のみ。確認依頼は送らない
            msg = (
                f"📱 今週のこはるまま投稿 自動承認完了！【{marathon_label}】\n\n"
                f"テーマ：{weekly_theme}\n"
                f"自動承認（80点以上）：{len(auto_approved)}本"
                f"（朝{len(auto_morning)}・アフィ{len(auto_aff)}）\n"
                f"採点落ち：{rejected}本\n\n"
                "操作不要でそのまま投稿されます。"
            )
        else:
            morning_prev = '\n'.join([
                f"  {i+1}. {p['body'][:30]}… ({p['score']}点)"
                for i, p in enumerate(morning_posts[:3])
            ])
            aff_prev = '\n'.join([
                f"  {len(morning_posts)+i+1}. {p['body'][:30]}… ({p['score']}点)"
                for i, p in enumerate(aff_posts[:3])
            ])
            msg = (
                f"📱 今週のこはるまま投稿案【{marathon_label}】\n\n"
                f"テーマ：{weekly_theme}\n"
                f"自動承認（80点以上・確認不要）：{len(auto_approved)}本\n"
                f"確認待ち（70〜79点）：{len(approved)}本"
                f"（朝{len(morning_posts)}・アフィ{len(aff_posts)}）\n"
                f"採点落ち：{rejected}本\n\n"
                f"【朝つぶやき（先頭3件）】\n{morning_prev}\n\n"
                f"【アフィ投稿（先頭3件）】\n{aff_prev}\n\n"
                f"✅ 全部OKなら「こはるままOK」\n"
                f"番号指定で除外する場合「こはるまま2,4はNG」\n"
                f"件数確認は「こはるまま確認」"
            )
        _send_line(msg)
        print(f"[koharu] writer done: auto={len(auto_approved)} pending={len(approved)} rejected={rejected}")

    except Exception as e:
        import traceback
        err_msg = f"[koharu] run_writer error: {e}\n{traceback.format_exc()}"
        print(err_msg)
        try:
            with open('/tmp/koharu_writer_error.log', 'w', encoding='utf-8') as f:
                f.write(err_msg)
        except Exception:
            pass
        _send_line(f"⚠️ こはるままライターでエラーが発生しました\n{type(e).__name__}: {e}\n\n詳細は /koharu-writer-log で確認できます")


def _score_post(body, post_type, ai=None):
    """投稿案を採点（0-100点）と改善フィードバックを返す"""
    try:
        if ai is None:
            ai = _client()
        criteria = (
            "採点基準（各25点）：\n"
            "1. フック強度：1行目で「続き読みたい」と思えるか\n"
            "2. 共感度：30-40代子連れワーママに刺さるか\n"
        )
        if post_type == 'aff':
            criteria += (
                "3. アフィ導線の自然さ：売り込み感なく興味を引けるか\n"
                "4. トーン：ですます調NG・口語・体言止めOK・URLなし\n"
            )
        else:
            criteria += (
                "3. 旅あるある度：「わかる！」となるか\n"
                "4. トーン：ですます調NG・口語・体言止めOK・売り込みなし\n"
            )
        prompt = (
            f"投稿文：{body}\n\n{criteria}\n"
            'JSON形式のみで出力：{"score": 整数0-100, "feedback": "改善点30字以内"}'
        )
        r = ai.messages.create(
            model='claude-haiku-4-5-20251001', max_tokens=150,
            messages=[{'role': 'user', 'content': prompt}]
        )
        m = re.search(r'\{.*\}', r.content[0].text, re.DOTALL)
        if m:
            result = json.loads(m.group())
            return result.get('score', 50), result.get('feedback', '')
        return 50, ''
    except Exception as e:
        print(f"[koharu] _score_post error: {e}")
        return 50, ''


def _regenerate(post, feedback_text, ai):
    """低スコア投稿を1回だけ再生成"""
    try:
        prompt = (
            f"以下の投稿を改善してください。\n\n"
            f"元：{post['body']}\n改善点：{feedback_text}\n\n"
            "こはるまま（旅行×楽天アフィ・30-40代子連れワーママ向け）のThreads投稿。\n"
            "ですます調NG・口語・体言止めOK・URLなし・1行目のフックを強くする。\n"
            "投稿文だけ出力。"
        )
        r = ai.messages.create(
            model='claude-haiku-4-5-20251001', max_tokens=300,
            messages=[{'role': 'user', 'content': prompt}]
        )
        new_post = post.copy()
        new_post['body'] = r.content[0].text.strip()
        new_post['regenerated'] = True
        return new_post
    except Exception as e:
        print(f"[koharu] _regenerate error: {e}")
        return None


# ============================================================
# LINEからの承認処理（main.py の message_handler から呼ぶ）
# ============================================================

def _norm(s):
    """全角英数→半角・大文字→小文字に正規化（OK/ｏｋ/ＯＫ等を統一）"""
    import unicodedata
    return unicodedata.normalize('NFKC', s).lower()


# こはるまま系接頭辞（長い順：先にこはるままをマッチさせる）
_KOHARU_PREFIXES = ['こはるまま', 'コハルママ', 'こはる', 'コハル']
# _norm後のOK/NG認識集合
# おｋ→NFKC→おk、んｇ→NFKC→んg
_KOHARU_OK_SET = {'ok', 'おk'}
_KOHARU_NG_SET = {'ng', 'んg'}


def _strip_koharu_prefix(msg):
    """こはるまま系接頭辞を除去してnorm済みsuffixを返す。なければNone"""
    for p in _KOHARU_PREFIXES:
        if msg.startswith(p):
            return _norm(msg[len(p):])
    return None


def handle_approval(message):
    """
    戻り値: True = 処理済み / False = 未対応メッセージ
    """
    msg    = message.strip()
    suffix = _strip_koharu_prefix(msg)

    if suffix is None:
        return False

    suffix = suffix.strip()

    # 確認コマンド：「こはるまま確認」「こはる確認」「コハル確認」等
    if suffix == '確認':
        pending  = _load(STOCK_PENDING_PATH, {})
        approved = _load(STOCK_APPROVED_PATH, {'posts': []})
        p_posts  = pending.get('posts', [])
        a_posts  = [p for p in approved.get('posts', []) if not p.get('posted')]
        p_morning = sum(1 for p in p_posts if p.get('type') == 'morning')
        p_aff     = sum(1 for p in p_posts if p.get('type') == 'aff')
        a_morning = sum(1 for p in a_posts if p.get('type') == 'morning')
        a_aff     = sum(1 for p in a_posts if p.get('type') == 'aff')
        _send_line(
            f"📱 こはるまま投稿ストック状況\n\n"
            f"承認待ち：{len(p_posts)}本（朝{p_morning}・アフィ{p_aff}）\n"
            f"承認済み（未投稿）：{len(a_posts)}本（朝{a_morning}・アフィ{a_aff}）"
        )
        return True

    # OK承認：「こはるままOK」「こはるままok」「こはるままＯＫ」「こはるままおｋ」
    #          「こはるOK」「コハルok」等すべて対応
    if suffix in _KOHARU_OK_SET:
        pending = _load(STOCK_PENDING_PATH, {})
        posts = pending.get('posts', [])
        if not posts:
            _send_line("📭 承認待ちの投稿案がありません。月曜に自動生成されます。")
            return True
        approved = _load(STOCK_APPROVED_PATH, {'posts': []})
        approved['posts'].extend(posts)
        _save(STOCK_APPROVED_PATH, approved)
        _save(STOCK_PENDING_PATH, {})
        m_cnt = sum(1 for p in posts if p.get('type') == 'morning')
        a_cnt = sum(1 for p in posts if p.get('type') == 'aff')
        _send_line(
            f"✅ {len(posts)}本を承認しました！\n"
            f"（朝つぶやき{m_cnt}本・アフィ{a_cnt}本）\n\n"
            "今週から朝7:30と夜20:00に順次投稿されます。"
        )
        return True

    # NG番号除外：「こはるまま2,4はNG」「こはる2はng」「こはる2はんｇ」等
    # suffix はnorm済み（小文字・半角）なのでNG/ng/んｇはすべてng/んgになっている
    for ng_var in _KOHARU_NG_SET:
        m = re.match(rf'^([0-9,、\s]+)は{re.escape(ng_var)}$', suffix)
        if m:
            ng_str = re.sub(r'[、\s]', ',', m.group(1))
            ng_idx = {int(n) - 1 for n in ng_str.split(',') if n.strip().isdigit()}
            pending = _load(STOCK_PENDING_PATH, {})
            posts   = pending.get('posts', [])
            kept = [p for i, p in enumerate(posts) if i not in ng_idx]
            approved = _load(STOCK_APPROVED_PATH, {'posts': []})
            approved['posts'].extend(kept)
            _save(STOCK_APPROVED_PATH, approved)
            _save(STOCK_PENDING_PATH, {})
            _send_line(f"✅ {len(kept)}本を承認（{len(ng_idx)}本を除外）しました。")
            return True

    return False


# ============================================================
# ③ ポスター  スケジューラから呼ばれる
# ============================================================

def run_poster_morning():
    """朝つぶやき：承認済みストック → なければフォールバック"""
    try:
        approved = _load(STOCK_APPROVED_PATH, {'posts': []})
        candidates = [
            p for p in approved.get('posts', [])
            if p.get('type') == 'morning' and not p.get('posted')
        ]
        if candidates:
            log = _load(POSTED_LOG_PATH, {'recent': []})
            recent_themes = [p.get('theme') for p in log['recent'][-3:]]
            no_dup = [p for p in candidates if p.get('theme') not in recent_themes]
            post = random.choice(no_dup if no_dup else candidates)

            post_id = _post(post['body'])
            if post_id:
                post.update({'posted': True, 'posted_at': datetime.now().isoformat(), 'post_id': post_id})
                _save(STOCK_APPROVED_PATH, approved)
                _log_post(post)
                print(f"[koharu] poster morning (stock): {post['body'][:30]}")
                return

        # フォールバック
        text = random.choice(_FALLBACK_MORNING)
        post_id = _post(text)
        if post_id:
            _log_post({'type': 'morning', 'body': text, 'theme': 'fallback',
                       'posted_at': datetime.now(_JST).isoformat(), 'post_id': post_id})
        print(f"[koharu] poster morning (fallback): {text[:30]}")

    except Exception as e:
        print(f"[koharu] run_poster_morning error: {e}")
        try:
            _post(random.choice(_FALLBACK_MORNING))
        except Exception:
            pass


def run_poster_aff():
    """アフィ投稿：承認済みストック → なければフォールバック"""
    try:
        approved = _load(STOCK_APPROVED_PATH, {'posts': []})
        candidates = [
            p for p in approved.get('posts', [])
            if p.get('type') == 'aff' and not p.get('posted')
        ]
        if candidates:
            log = _load(POSTED_LOG_PATH, {'recent': []})
            recent_genres = [p.get('genre') for p in log['recent'][-3:]]
            no_dup = [p for p in candidates if p.get('genre') not in recent_genres]
            post = random.choice(no_dup if no_dup else candidates)

            post_id = _post(post['body'])
            if post_id:
                time.sleep(5)
                if post.get('url'):
                    _post(_link_reply_text(post['url']), reply_to_id=post_id)
                post.update({'posted': True, 'posted_at': datetime.now().isoformat(), 'post_id': post_id})
                _save(STOCK_APPROVED_PATH, approved)
                _log_post(post)
                print(f"[koharu] poster aff (stock): {post['body'][:30]}")
                return

        # フォールバック（楽天APIでその場生成）
        _fallback_aff()

    except Exception as e:
        print(f"[koharu] run_poster_aff error: {e}")
        _fallback_aff()


def run_poster_aff_boost():
    """5と0のつく日・楽天マラソン期のブースト投稿。対象日以外は何もしない"""
    now = datetime.now(_JST)
    is_marathon = 15 <= now.day <= 25
    boost_days = (5, 10, 15, 20, 25, 30)
    tomorrow_day = (now + timedelta(days=1)).day
    is_5_0_boost = now.day in boost_days or tomorrow_day in boost_days
    if not (is_marathon or is_5_0_boost):
        return
    print(f"[koharu] boost day (marathon={is_marathon}, 5_0={is_5_0_boost}): posting extra")
    run_poster_aff()


def _fallback_aff():
    """ストック切れ時：楽天APIでその場生成して投稿"""
    try:
        from blog_yakuzen import search_rakuten_items
        genre = random.choice(_FALLBACK_AFF_GENRES)
        items = search_rakuten_items(genre['keyword'], hits=5)
        if not items:
            return
        item = random.choice(items)
        hook = random.choice(_ALL_HOOKS)
        prompt = (
            f"商品名：{item['name'][:40]}\n価格：{item['price']}円\n\n"
            f"こはるままのThreads投稿文。冒頭は「{hook}」で始める。\n"
            "3行以内・URLなし・ですます調NG・体言止めOK。投稿文だけ出力。"
        )
        resp = _client().messages.create(
            model='claude-haiku-4-5-20251001', max_tokens=200,
            messages=[{'role': 'user', 'content': prompt}]
        )
        body = resp.content[0].text.strip()
        post_id = _post(body)
        if post_id and item.get('url'):
            time.sleep(5)
            _post(_link_reply_text(item['url']), reply_to_id=post_id)
        print(f"[koharu] fallback aff done: {body[:30]}")
    except Exception as e:
        print(f"[koharu] _fallback_aff error: {e}")


# ============================================================
# ④ コレクター  毎日 23:00
# ============================================================

def run_collector():
    """Threads APIで投稿パフォーマンスデータを取得・蓄積"""
    try:
        token   = os.environ.get('KOHARU_THREADS_ACCESS_TOKEN', '').strip()
        user_id = os.environ.get('KOHARU_THREADS_USER_ID', '').strip()
        if not token or not user_id:
            print("[koharu] collector: tokens not set")
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
            print(f"[koharu] collector API error: {r.status_code} {r.text[:200]}")
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
                # 既存エントリを最新値で更新
                for i, p in enumerate(analytics['posts']):
                    if p['id'] == tid:
                        analytics['posts'][i] = entry
                        break
            else:
                analytics['posts'].append(entry)
                new_count += 1

        analytics['posts']       = analytics['posts'][-200:]
        analytics['last_collected'] = datetime.now().isoformat()
        _save(ANALYTICS_PATH, analytics)
        print(f"[koharu] collector done: {new_count} new, total {len(analytics['posts'])}")

    except Exception as e:
        print(f"[koharu] run_collector error: {e}")


# ============================================================
# ⑤ アナリスト  日曜 20:00
# ============================================================

def run_analyst():
    """1週間の投稿を分析 → LINEレポート → フィードバック保存"""
    try:
        analytics = _load(ANALYTICS_PATH, {'posts': []})
        posts = analytics.get('posts', [])

        if not posts:
            _send_line("📊 こはるまま：分析データがまだありません。\nコレクターが蓄積し始めたら来週から分析できます！")
            return

        # 1週間分を抽出
        cutoff = datetime.now() - timedelta(days=7)
        recent = []
        for p in posts:
            ts = p.get('timestamp', '')
            if not ts:
                continue
            try:
                # "2026-05-16T12:00:00+0000" のような形式に対応
                dt = datetime.fromisoformat(ts.replace('Z', '+00:00').replace('+0000', '+00:00'))
                if dt.replace(tzinfo=None) > cutoff:
                    recent.append(p)
            except Exception:
                pass

        if not recent:
            _send_line("📊 こはるまま：今週の投稿データがありません。")
            return

        # エンゲージメントスコア（いいね×3 + RT×5 + 閲覧×0.01）
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

        # Claude分析
        top_texts = '\n'.join([p.get('text', '')[:60] for p in top3])
        a_prompt = (
            f"今週バズった投稿3件の1行目フックのパターンを分析。\n\n{top_texts}\n\n"
            "「なぜ効いたか」を30字以内で分析し、来週のライターへのアドバイスを一言で。\n"
            'JSON：{"pattern":"パターン説明","advice":"来週へのアドバイス",'
            '"good_hooks":["1行目①","1行目②","1行目③"]}'
        )
        r = _client().messages.create(
            model='claude-haiku-4-5-20251001', max_tokens=400,
            messages=[{'role': 'user', 'content': a_prompt}]
        )
        m = re.search(r'\{.*\}', r.content[0].text, re.DOTALL)
        analysis = json.loads(m.group()) if m else {}

        _save(FEEDBACK_PATH, {
            'updated_at':  datetime.now().isoformat(),
            'good_hooks':  analysis.get('good_hooks', []),
            'hook_pattern': analysis.get('pattern', ''),
            'advice':      analysis.get('advice', ''),
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
            f"📊 こはるまま 週次レポート\n\n"
            f"📅 今週の投稿数：{len(recent)}本\n"
            f"❤️ 平均いいね：{avg_like:.1f}\n"
            f"👀 平均閲覧：{avg_view:.0f}\n\n"
            f"🔥 伸びた投稿TOP3：\n{top_lines}\n\n"
            f"📉 伸びなかった投稿：\n{bot_lines}\n\n"
            f"💡 バズりパターン：{analysis.get('pattern','データ収集中')}\n"
            f"✏️ 来週への提言：{analysis.get('advice','')}"
        )
        print("[koharu] analyst done")

    except Exception as e:
        print(f"[koharu] run_analyst error: {e}")


# ============================================================
# ⑥ モニター  毎日 07:00 / 13:00 / 22:00
# ============================================================

def run_monitor():
    """正常稼働確認・凍結チェック・承認放置アラート"""
    try:
        token   = os.environ.get('KOHARU_THREADS_ACCESS_TOKEN', '').strip()
        user_id = os.environ.get('KOHARU_THREADS_USER_ID', '').strip()
        issues  = []

        # Threadsアカウント状態確認
        if token and user_id:
            try:
                r = requests.get(
                    f'https://graph.threads.net/v1.0/{user_id}',
                    params={'fields': 'id,username', 'access_token': token},
                    timeout=10
                )
                if r.status_code == 401:
                    issues.append("⚠️ Threadsトークン期限切れの可能性（401）")
                elif r.status_code == 403:
                    issues.append("🚨 Threadsアカウントに制限がかかっている可能性（403）")
                elif r.status_code not in (200, 404):
                    issues.append(f"⚠️ Threads API異常（{r.status_code}）")
            except Exception as e:
                issues.append(f"⚠️ Threads接続エラー: {str(e)[:40]}")
        # トークン未設定は想定内（未開始）のためアラートなし

        # 22時台のみ：当日投稿確認（Threadsトークン設定済みの場合のみ）
        now_jst = datetime.now(_JST)
        if now_jst.hour >= 22 and token and user_id:
            log = _load(POSTED_LOG_PATH, {'recent': []})
            today = now_jst.strftime('%Y-%m-%d')
            today_posts = [p for p in log['recent'] if (p.get('posted_at') or '').startswith(today)]
            if not today_posts:
                issues.append("⚠️ 今日の投稿記録がありません（スケジューラを確認してください）")

        # 承認待ち放置チェック
        pending = _load(STOCK_PENDING_PATH, {})
        if pending.get('created_at'):
            try:
                days_old = (datetime.now() - datetime.fromisoformat(pending['created_at'])).days
                if days_old >= 3:
                    issues.append(
                        f"📭 承認待ち投稿案が{days_old}日放置されています。\n"
                        "「こはるままOK」で承認してください。"
                    )
            except Exception:
                pass

        if issues:
            _send_line("🔔 こはるままエンジン モニターアラート\n\n" + "\n".join(issues))
            print(f"[koharu] monitor: {len(issues)} issues")
        else:
            print("[koharu] monitor: all OK")

    except Exception as e:
        print(f"[koharu] run_monitor error: {e}")
