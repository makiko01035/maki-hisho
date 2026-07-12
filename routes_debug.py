import os
import re
import json
import random
import threading
import time
import requests
import feedparser
from flask import Blueprint, jsonify, request, send_from_directory, Response
from linebot.models import TextSendMessage

from clients import line_bot_api
from sns_engine_koharu import (
    run_researcher as koharu_researcher,
    run_writer as koharu_writer,
    run_analyst as koharu_analyst,
    run_poster_morning as koharu_engine_poster_morning_fn,
    run_poster_aff as koharu_engine_poster_aff_fn,
)
from sns_engine_mako import (
    run_researcher as mako_researcher,
    run_writer as mako_writer,
    run_poster_info as mako_poster_info,
    run_poster_morning_quote as mako_poster_morning_quote,
    run_quote_generator as mako_quote_generator,
)
from sns_direct_poster import (
    TRAVEL_GENRES,
    _get_kvision_x_client,
    post_kvision_morning_tweet,
    post_kvision_card_tweet,
    post_koharu_threads_morning,
    post_mako_threads_morning,
    post_mako_threads_aff_auto,
)
from threads_room import send_room_suggestion_slot, ROOM_GENRES, post_to_threads, reply_to_threads
from x_poster import _get_x_client, generate_x_post

debug_bp = Blueprint('debug', __name__)


@debug_bp.route('/test-x-post')
def test_x_post():
    import tweepy
    try:
        client = _get_x_client()
        if not client:
            return 'Error: client is None (keys missing)', 500
        post_text = generate_x_post(0)
        resp = client.create_tweet(text=post_text)
        return f'Success: {resp}', 200
    except tweepy.errors.Unauthorized as e:
        return f'401 Unauthorized - response: {e.response.text if hasattr(e, "response") else str(e)}', 401
    except tweepy.errors.Forbidden as e:
        return f'403 Forbidden - response: {e.response.text if hasattr(e, "response") else str(e)}', 403
    except Exception as e:
        return f'Error ({type(e).__name__}): {e}', 500


@debug_bp.route('/test-threads')
def test_threads():
    """Threads接続テスト＆テスト投稿"""
    access_token = os.environ.get('THREADS_ACCESS_TOKEN', '')
    user_id = os.environ.get('THREADS_USER_ID', '')
    if not access_token or not user_id:
        missing = []
        if not access_token:
            missing.append('THREADS_ACCESS_TOKEN')
        if not user_id:
            missing.append('THREADS_USER_ID')
        return f'❌ Render環境変数が未設定です: {", ".join(missing)}', 400
    post_id = post_to_threads('【テスト投稿】まきの秘書ボットからThreads連携テスト中🧵')
    if post_id:
        time.sleep(3)
        reply_to_threads(post_id, '🛒 コチラ！\nhttps://room.rakuten.co.jp/makiko01035\n[楽天PR]')
        return '✅ Threads投稿成功！（本文＋コメントURLの2段構え）Threadsアプリで確認してください。'
    return '❌ 投稿失敗。Renderのログを確認してください。', 500


@debug_bp.route('/debug-x-auth')
def debug_x_auth():
    import requests
    from requests_oauthlib import OAuth1
    api_key = os.environ.get('X_API_KEY', '')
    api_secret = os.environ.get('X_API_SECRET', '')
    access_token = os.environ.get('X_ACCESS_TOKEN', '')
    access_token_secret = os.environ.get('X_ACCESS_TOKEN_SECRET', '')
    try:
        auth = OAuth1(api_key, api_secret, access_token, access_token_secret)
        r = requests.post(
            'https://api.twitter.com/2/tweets',
            json={'text': 'テスト投稿（自動）🤖 #AI副業'},
            auth=auth
        )
        return {'status': r.status_code, 'body': r.json()}, 200
    except Exception as e:
        return {'error': str(e)}, 500


@debug_bp.route('/debug-x-keys')
def debug_x_keys():
    def mask(v):
        return (v[:6] + '...' + v[-4:]) if v and len(v) > 10 else ('(empty)' if not v else v)
    return {
        'X_API_KEY': mask(os.environ.get('X_API_KEY')),
        'X_API_SECRET': mask(os.environ.get('X_API_SECRET')),
        'X_ACCESS_TOKEN': mask(os.environ.get('X_ACCESS_TOKEN')),
        'X_ACCESS_TOKEN_SECRET': mask(os.environ.get('X_ACCESS_TOKEN_SECRET')),
    }

@debug_bp.route('/threads-guide')
def threads_guide():
    return send_from_directory('.', 'threads_guide.html')

@debug_bp.route('/check-kvision')
def check_kvision():
    """KVISION X APIキーの設定状況を確認"""
    keys = {
        'KVISION_X_API_KEY': os.environ.get('KVISION_X_API_KEY', ''),
        'KVISION_X_API_SECRET': os.environ.get('KVISION_X_API_SECRET', ''),
        'KVISION_X_ACCESS_TOKEN': os.environ.get('KVISION_X_ACCESS_TOKEN', ''),
        'KVISION_X_ACCESS_TOKEN_SECRET': os.environ.get('KVISION_X_ACCESS_TOKEN_SECRET', ''),
    }
    result = []
    for k, v in keys.items():
        status = f'✅ 設定済み（{v[:6]}...）' if v.strip() else '❌ 未設定'
        result.append(f'{k}: {status}')
    return '<br>'.join(result)



@debug_bp.route('/post-kvision-now')
def post_kvision_now():
    """今すぐ@kvision_mにアフィスレッドを1本送る（手動テスト用）"""
    import random
    slot = random.randint(0, len(TRAVEL_GENRES) - 1)
    client = _get_kvision_x_client()
    if not client:
        return '❌ KVISION X APIキーが未設定です。Renderの環境変数を確認してください。', 500
    try:
        post_kvision_travel_aff(slot)
        return f'✅ @kvision_m スレッド投稿完了！ジャンル：{TRAVEL_GENRES[slot]["name"]}　Xアプリで確認してください。'
    except Exception as e:
        return f'❌ エラー: {e}', 500


@debug_bp.route('/post-kvision-morning-now')
def post_kvision_morning_now():
    """今すぐ@kvision_mに朝つぶやきを送る（手動テスト用）"""
    try:
        post_kvision_morning_tweet()
        return '✅ @kvision_m 朝つぶやき投稿完了！Xアプリで確認してください。'
    except Exception as e:
        return f'❌ エラー: {e}', 500


@debug_bp.route('/post-kvision-card-now')
def post_kvision_card_now():
    """今すぐ楽天カード誘導ツイートを送る（手動テスト用）"""
    try:
        post_kvision_card_tweet()
        return '✅ @kvision_m 楽天カード誘導ツイート完了！（スレッド形式・URLランダム）'
    except Exception as e:
        return f'❌ エラー: {e}', 500


@debug_bp.route('/post-kvision-listcard-now')
def post_kvision_listcard_now():
    """今すぐリスト型カード画像投稿を送る（手動テスト用）"""
    try:
        from koharumama_card_post import post_kvision_card_image
        post_kvision_card_image()
        return '✅ @kvision_m カード画像投稿完了！Xアプリで確認してください。'
    except Exception as e:
        return f'❌ エラー: {e}', 500


@debug_bp.route('/test-line-send')
def test_line_send():
    """LINEにテストメッセージを送信して動作確認"""
    try:
        user_id = os.environ.get('LINE_USER_ID', '')
        if not user_id:
            return '❌ LINE_USER_ID が未設定', 500
        line_bot_api.push_message(user_id, TextSendMessage(text='🔔 LINEテスト送信成功！こはるままエンジンからのテストメッセージです。'))
        return f'✅ LINE送信成功（宛先: {user_id[:8]}...）'
    except Exception as e:
        return f'❌ LINE送信エラー: {e}', 500


@debug_bp.route('/koharu-stock-status')
def koharu_stock_status():
    """こはるまま承認待ち・承認済みストックの状態確認"""
    import json as _json
    pending_path  = '/tmp/koharu_stock_pending.json'
    approved_path = '/tmp/koharu_stock_approved.json'
    def _read(path):
        try:
            if os.path.exists(path):
                with open(path, encoding='utf-8') as f:
                    return _json.load(f)
        except Exception:
            pass
        return {}
    pending  = _read(pending_path)
    approved = _read(approved_path)
    p_posts  = pending.get('posts', [])
    a_posts  = approved.get('posts', [])
    return jsonify({
        'pending': {
            'count':        len(p_posts),
            'created_at':   pending.get('created_at'),
            'weekly_theme': pending.get('weekly_theme'),
            'preview': [{'type': p.get('type'), 'body': p.get('body','')[:50], 'score': p.get('score')} for p in p_posts[:5]],
        },
        'approved': {
            'count':    len(a_posts),
            'unposted': len([p for p in a_posts if not p.get('posted')]),
        },
    })


@debug_bp.route('/koharu-engine-writer-debug')
def koharu_engine_writer_debug():
    """ライターをフォアグラウンドで実行してエラーを直接確認（デバッグ用）"""
    import traceback, io, sys
    buf = io.StringIO()
    old_stdout = sys.stdout
    sys.stdout = buf
    try:
        koharu_writer()
        output = buf.getvalue()
        sys.stdout = old_stdout
        return f'<pre>✅ 完了\n\n{output}</pre>'
    except Exception as e:
        output = buf.getvalue()
        sys.stdout = old_stdout
        return f'<pre>❌ エラー: {e}\n\n{traceback.format_exc()}\n\nログ:\n{output}</pre>', 500


@debug_bp.route('/koharu-engine-writer-now')
def koharu_engine_writer_now():
    """こはるままエンジン：②ライターを今すぐ実行（手動テスト）"""
    try:
        import threading
        threading.Thread(target=koharu_writer, daemon=True).start()
        return '✅ こはるままライター起動！数分後にLINEに投稿案が届きます。'
    except Exception as e:
        return f'❌ {e}', 500


@debug_bp.route('/koharu-engine-poster-morning-now')
def koharu_engine_poster_morning_now():
    """こはるままエンジン：本番スケジューラと同じ朝投稿関数を今すぐ実行（手動テスト・実際にThreadsに投稿されます）"""
    import traceback, io, sys
    buf = io.StringIO()
    old_stdout = sys.stdout
    sys.stdout = buf
    try:
        koharu_engine_poster_morning_fn()
        output = buf.getvalue()
        sys.stdout = old_stdout
        return f'<pre>✅ 完了\n\n{output}</pre>'
    except Exception as e:
        output = buf.getvalue()
        sys.stdout = old_stdout
        return f'<pre>❌ エラー: {e}\n\n{traceback.format_exc()}\n\nログ:\n{output}</pre>', 500


@debug_bp.route('/koharu-engine-poster-aff-now')
def koharu_engine_poster_aff_now():
    """こはるままエンジン：本番スケジューラと同じアフィ投稿関数を今すぐ実行（手動テスト・実際にThreadsに投稿されます）"""
    import traceback, io, sys
    buf = io.StringIO()
    old_stdout = sys.stdout
    sys.stdout = buf
    try:
        koharu_engine_poster_aff_fn()
        output = buf.getvalue()
        sys.stdout = old_stdout
        return f'<pre>✅ 完了\n\n{output}</pre>'
    except Exception as e:
        output = buf.getvalue()
        sys.stdout = old_stdout
        return f'<pre>❌ エラー: {e}\n\n{traceback.format_exc()}\n\nログ:\n{output}</pre>', 500


@debug_bp.route('/koharu-engine-researcher-now')
def koharu_engine_researcher_now():
    """こはるままエンジン：①リサーチャーを今すぐ実行（手動テスト）"""
    try:
        koharu_researcher()
        return '✅ こはるままリサーチャー完了！'
    except Exception as e:
        return f'❌ {e}', 500


@debug_bp.route('/koharu-engine-analyst-now')
def koharu_engine_analyst_now():
    """こはるままエンジン：⑤アナリストを今すぐ実行（手動テスト）"""
    try:
        import threading
        threading.Thread(target=koharu_analyst, daemon=True).start()
        return '✅ こはるままアナリスト起動！数分後にLINEにレポートが届きます。'
    except Exception as e:
        return f'❌ {e}', 500


@debug_bp.route('/mako-engine-writer-now')
def mako_engine_writer_now():
    """MAKOエンジン：②ライターを今すぐ実行（手動テスト）"""
    try:
        import threading
        threading.Thread(target=mako_writer, daemon=True).start()
        return '✅ MAKOライター起動！数分後にLINEに投稿案が届きます。'
    except Exception as e:
        return f'❌ {e}', 500


@debug_bp.route('/mako-engine-researcher-now')
def mako_engine_researcher_now():
    """MAKOエンジン：①リサーチャーを今すぐ実行（手動テスト）"""
    try:
        mako_researcher()
        return '✅ MAKOリサーチャー完了！'
    except Exception as e:
        return f'❌ {e}', 500


@debug_bp.route('/mako-posted-log')
def mako_posted_log():
    """MAKOの投稿済みログ（直近50件）を表示。/tmpが再起動でリセットされていないか確認用"""
    import json as _json
    try:
        with open('/tmp/mako_posted_log.json', 'r', encoding='utf-8') as f:
            data = _json.load(f)
        return _json.dumps(data, ensure_ascii=False, indent=2)
    except FileNotFoundError:
        return '📭 投稿ログが存在しません（Render再起動でリセットされたか、まだ1件も投稿されていません）', 404
    except Exception as e:
        return f'❌ ログ読み取りエラー: {e}', 500


@debug_bp.route('/mako-threads-token-check')
def mako_threads_token_check():
    """MAKOのThreadsトークン・ユーザーIDが設定されているか確認"""
    token   = os.environ.get('MAKO_THREADS_ACCESS_TOKEN', '').strip()
    user_id = os.environ.get('MAKO_THREADS_USER_ID', '').strip()
    return {
        'MAKO_THREADS_ACCESS_TOKEN_設定済み': bool(token),
        'MAKO_THREADS_USER_ID_設定済み':      bool(user_id),
    }


@debug_bp.route('/mako-stock-status')
def mako_stock_status():
    """MAKO承認待ち・承認済みストックの状態確認"""
    import json as _json
    pending_path  = '/tmp/mako_stock_pending.json'
    approved_path = '/tmp/mako_stock_approved.json'
    def _read(path):
        try:
            with open(path, 'r', encoding='utf-8') as f:
                return _json.load(f)
        except Exception:
            return None
    pending  = _read(pending_path)
    approved = _read(approved_path)
    return _json.dumps({
        'pending':  {'count': len((pending  or {}).get('posts', [])), 'created_at': (pending  or {}).get('created_at')},
        'approved': {'count': len((approved or {}).get('posts', [])), 'created_at': (approved or {}).get('created_at')},
    }, ensure_ascii=False, indent=2)


@debug_bp.route('/koharu-writer-log')
def koharu_writer_log():
    """こはるままライターの直近エラーログを表示"""
    try:
        with open('/tmp/koharu_writer_error.log', 'r', encoding='utf-8') as f:
            return f'<pre>{f.read()}</pre>'
    except FileNotFoundError:
        return '✅ エラーログなし（ライターは正常終了しています）'
    except Exception as e:
        return f'❌ ログ読み取りエラー: {e}', 500


@debug_bp.route('/post-koharu-threads-now')
def post_koharu_threads_now():
    """今すぐこはるままのThreadsにアフィ投稿を送る（手動テスト用）"""
    try:
        post_koharu_threads_aff_auto()
        return '✅ こはるまま Threads アフィ投稿完了！Threadsアプリで確認してください。'
    except Exception as e:
        return f'❌ エラー: {e}', 500


@debug_bp.route('/post-koharu-threads-morning-now')
def post_koharu_threads_morning_now():
    """今すぐこはるままのThreadsに朝つぶやきを送る（手動テスト用）"""
    try:
        post_koharu_threads_morning()
        return '✅ こはるまま Threads 朝つぶやき投稿完了！Threadsアプリで確認してください。'
    except Exception as e:
        return f'❌ エラー: {e}', 500


@debug_bp.route('/post-mako-threads-now')
def post_mako_threads_now():
    """今すぐMAKOのThreadsにアフィ投稿を送る（手動テスト用）"""
    try:
        post_mako_threads_aff_auto()
        return '✅ MAKO Threads アフィ投稿完了！Threadsアプリで確認してください。'
    except Exception as e:
        return f'❌ エラー: {e}', 500


@debug_bp.route('/post-mako-threads-morning-now')
def post_mako_threads_morning_now():
    """今すぐMAKOのThreadsに朝の共感投稿を送る（手動テスト用）"""
    try:
        post_mako_threads_morning()
        return '✅ MAKO Threads 朝投稿完了！Threadsアプリで確認してください。'
    except Exception as e:
        return f'❌ エラー: {e}', 500


@debug_bp.route('/post-mako-x-now')
def post_mako_x_now():
    """今すぐMAKOのXにテスト投稿（情報系ストック or フォールバック）"""
    try:
        mako_poster_info()
        return '✅ MAKO X＋Threads 情報投稿完了！両アプリで確認してください。'
    except Exception as e:
        return f'❌ エラー: {e}', 500


@debug_bp.route('/post-mako-quote-now')
def post_mako_quote_now():
    """今すぐMAKOの朝格言をXに投稿（手動テスト用）"""
    try:
        mako_poster_morning_quote()
        return '✅ MAKO格言投稿完了！Xアプリで確認してください。'
    except Exception as e:
        return f'❌ エラー: {e}', 500


@debug_bp.route('/generate-mako-quotes-now')
def generate_mako_quotes_now():
    """今すぐMAKO格言30本を生成してストックに追加（手動テスト用）"""
    try:
        mako_quote_generator()
        return '✅ MAKO格言生成完了！LINEに結果が届きます。'
    except Exception as e:
        return f'❌ エラー: {e}', 500


@debug_bp.route('/post-threads-now')
def post_threads_now():
    """今すぐThreadsに楽天アフィ投稿を1本送る（手動トリガー）"""
    import random
    slot = random.randint(0, len(ROOM_GENRES) - 1)
    try:
        send_room_suggestion_slot(slot)
        return f'✅ Threads投稿完了！ジャンル：{ROOM_GENRES[slot]["name"]}　Threadsアプリで確認してください。'
    except Exception as e:
        return f'❌ エラー: {e}', 500



@debug_bp.route('/debug-image')
def debug_image():
    """画像URLの取得状態をデバッグするエンドポイント"""
    img_url = request.args.get('url', '')
    if not img_url:
        return 'url param required', 400
    try:
        r = requests.get(img_url, timeout=15, headers={'User-Agent': 'Mozilla/5.0'}, allow_redirects=True)
        ct = r.headers.get('Content-Type', 'unknown')
        first_bytes = r.content[:16].hex()
        return {'status': r.status_code, 'content_type': ct, 'size': len(r.content), 'first_bytes_hex': first_bytes}
    except Exception as e:
        return {'error': str(e)}, 500


@debug_bp.route('/diary-debug')
def diary_debug():
    """日記追記のフルテスト用エンドポイント"""
    import requests as req
    lines = []
    notion_token = os.environ.get('NOTION_TOKEN', '')
    if not notion_token:
        return "NOTION_TOKEN is NOT set in environment variables", 500
    lines.append(f"NOTION_TOKEN: set (length={len(notion_token)})")

    headers = {
        "Authorization": f"Bearer {notion_token}",
        "Notion-Version": "2025-09-03",
        "Content-Type": "application/json"
    }

    # ① 検索テスト
    r = req.post("https://api.notion.com/v1/search",
        headers=headers,
        json={"query": "日記", "filter": {"value": "page", "property": "object"}, "page_size": 5}
    )
    lines.append(f"[1] search status: {r.status_code}")

    db_id = None
    title_prop_name = None
    date_prop_name = None
    today_str = datetime.date.today().strftime('%Y-%m-%d')
    today_page_id = None

    if r.status_code == 200:
        results = r.json().get("results", [])
        lines.append(f"[1] search results count: {len(results)}")
        for i, page in enumerate(results[:5]):
            parent = page.get("parent", {})
            props = page.get("properties", {})
            full_db_id = parent.get('database_id', '')
            if full_db_id and db_id is None:
                db_id = full_db_id
            for pname, pval in props.items():
                if pval.get("type") == "title" and title_prop_name is None:
                    title_prop_name = pname
                if pval.get("type") == "date" and date_prop_name is None:
                    date_prop_name = pname
            date_val = props.get(date_prop_name or "日付", {}).get("date") or {}
            is_today = date_val.get("start") == today_str
            lines.append(f"  page[{i}] id={page['id'][:8]}... date={date_val.get('start','?')} is_today={is_today}")
            if is_today:
                today_page_id = page["id"]
    else:
        lines.append(f"[1] search error: {r.text[:300]}")
        return "\n".join(lines), 200, {"Content-Type": "text/plain; charset=utf-8"}

    lines.append(f"[1] detected: db_id={db_id}, title_prop={title_prop_name}, date_prop={date_prop_name}")
    lines.append(f"[1] today ({today_str}) page exists: {today_page_id is not None}")

    # ② 今日のページがなければ作成テスト（child_data_source_ids にフォールバック）
    if not today_page_id:
        use_title = title_prop_name or "今日やること"
        use_date = date_prop_name or "日付"
        # 試すDBのIDリスト：親DB → child IDs の順で試す
        candidate_db_ids = [
            db_id or "323f8d6d-41de-8082-9c88-e476d05c2a0a",
            "323f8d6d-41de-809c-9d88-000b8eb19cbb",
            "323f8d6d-41de-80ff-80f3-000b5b69f8b7"
        ]
        for attempt_db_id in candidate_db_ids:
            lines.append(f"[2] trying db={attempt_db_id}, title_prop={use_title}, date_prop={use_date}")
            r2 = req.post("https://api.notion.com/v1/pages",
                headers=headers,
                json={
                    "parent": {"database_id": attempt_db_id},
                    "properties": {
                        use_title: {"title": [{"text": {"content": "日記"}}]},
                        use_date: {"date": {"start": today_str}}
                    }
                }
            )
            lines.append(f"[2] create status: {r2.status_code}")
            if r2.status_code == 200:
                today_page_id = r2.json()["id"]
                lines.append(f"[2] created page id={today_page_id[:8]}... with db={attempt_db_id}")
                break
            else:
                err = r2.json()
                lines.append(f"[2] create error: {err.get('message','?')}")
                # child_data_source_ids が返ってきたら追加
                extra_ids = err.get("additional_data", {}).get("child_data_source_ids", [])
                for eid in extra_ids:
                    if eid not in candidate_db_ids:
                        candidate_db_ids.append(eid)
        if not today_page_id:
            lines.append("[2] FAILED: 全候補DBで作成失敗")
            return "\n".join(lines), 200, {"Content-Type": "text/plain; charset=utf-8"}
    else:
        lines.append(f"[2] using existing page id={today_page_id[:8]}...")

    # ③ テストメモ追記
    r3 = req.patch(
        f"https://api.notion.com/v1/blocks/{today_page_id}/children",
        headers=headers,
        json={"children": [{"object": "block", "type": "bulleted_list_item",
            "bulleted_list_item": {"rich_text": [{"type": "text", "text": {"content": "【デバッグテスト】diary-debugエンドポイントからの書き込みテスト"}}]}}]}
    )
    lines.append(f"[3] append status: {r3.status_code}")
    if r3.status_code != 200:
        lines.append(f"[3] append error: {r3.text[:500]}")
    else:
        lines.append("[3] SUCCESS: メモの追記に成功しました！")

    return "\n".join(lines), 200, {"Content-Type": "text/plain; charset=utf-8"}


@debug_bp.route('/debug-amazon-ip')
def debug_amazon_ip():
    """RenderのクラウドIPからAmazon.co.jpスクレイピングが通るか確認するデバッグ用エンドポイント
    （電脳仕入れカレンダーをRenderに乗せられるか判断するための一時テスト・2026-07-12）"""
    import re as _re
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36',
        'Accept-Language': 'ja-JP,ja;q=0.9',
    }
    try:
        r = requests.get('https://www.amazon.co.jp/s?k=4902370536485', headers=headers, timeout=20)
        has_asin = bool(_re.search(r'data-asin="[A-Z0-9]{10}"', r.text))
        is_captcha = 'captcha' in r.text.lower() or 'automated access' in r.text.lower() or 'ロボットではありません' in r.text
        return {
            'status_code': r.status_code,
            'body_length': len(r.text),
            'found_product_asin': has_asin,
            'looks_like_captcha_block': is_captcha,
            'verdict': 'OK: Amazon検索が通っています' if (r.status_code == 200 and has_asin and not is_captcha)
                       else 'NG: ブロックまたは想定外の応答です',
        }
    except Exception as e:
        return {'error': str(e)}, 500


@debug_bp.route('/check-creds')
def check_creds():
    """GOOGLE_CREDENTIALS の形式を確認するデバッグ用エンドポイント"""
    try:
        raw = os.environ.get('GOOGLE_CREDENTIALS', '')
        parsed = json.loads(raw)
        keys = list(parsed.keys())
        scopes = parsed.get('scopes', [])
        has_refresh = bool(parsed.get('refresh_token'))
        return f"OK\nkeys: {keys}\nscopes: {scopes}\nhas_refresh_token: {has_refresh}\nfirst_30_chars: {raw[:30]}"
    except Exception as e:
        raw = os.environ.get('GOOGLE_CREDENTIALS', '')
        return f"JSON parse error: {e}\nfirst_80_chars: {raw[:80]}", 500


@debug_bp.route('/get-koharu-threads-uid')
def get_koharu_threads_uid():
    token = os.environ.get('KOHARU_THREADS_ACCESS_TOKEN', '').strip()
    if not token:
        return 'KOHARU_THREADS_ACCESS_TOKEN が未設定です', 400
    res = requests.get('https://graph.threads.net/v1.0/me', params={'fields': 'id,username', 'access_token': token}, timeout=10)
    return res.json()


@debug_bp.route('/get-mako-threads-uid')
def get_mako_threads_uid():
    token = os.environ.get('MAKO_THREADS_ACCESS_TOKEN', '').strip()
    if not token:
        return 'MAKO_THREADS_ACCESS_TOKEN が未設定です', 400
    res = requests.get('https://graph.threads.net/v1.0/me', params={'fields': 'id,username', 'access_token': token}, timeout=10)
    return res.json()


@debug_bp.route('/check-threads-app')
def check_threads_app():
    """THREADS_APP_IDが正しく設定されているか確認するデバッグ用"""
    app_id = os.environ.get('THREADS_APP_ID', '')
    app_secret = os.environ.get('THREADS_APP_SECRET', '')
    return {
        'THREADS_APP_ID_先頭6桁': app_id[:6] + '...' if len(app_id) > 6 else f'({len(app_id)}文字)',
        'THREADS_APP_ID_桁数': len(app_id),
        'THREADS_APP_ID_数字のみか': app_id.isdigit(),
        'THREADS_APP_SECRET_先頭4文字': app_secret[:4] + '...' if len(app_secret) > 4 else f'({len(app_secret)}文字)',
    }


@debug_bp.route('/auth/threads')
def auth_threads():
    app_id = os.environ.get('THREADS_APP_ID', '').strip()
    if not app_id:
        return 'THREADS_APP_ID が設定されていません。Renderに設定してください。', 400
    redirect_uri = 'https://maki-hisho.onrender.com/auth/threads/callback'
    auth_url = (
        f"https://www.threads.net/oauth/authorize"
        f"?client_id={app_id}"
        f"&redirect_uri={redirect_uri}"
        f"&scope=threads_basic,threads_content_publish"
        f"&response_type=code"
    )
    return f'''<html><body>
<h2>Threads認証</h2>
<p><a href="{auth_url}" style="font-size:20px;padding:10px;background:#000;color:#fff;text-decoration:none;border-radius:6px;">
Threadsで認証する</a></p>
</body></html>'''


@debug_bp.route('/auth/threads/callback')
def auth_threads_callback():
    code = request.args.get('code')
    if not code:
        return f'エラー: codeが取得できませんでした。{request.args}', 400
    app_id = os.environ.get('THREADS_APP_ID')
    app_secret = os.environ.get('THREADS_APP_SECRET')
    redirect_uri = 'https://maki-hisho.onrender.com/auth/threads/callback'
    res = requests.post(
        'https://graph.threads.net/oauth/access_token',
        data={
            'client_id': app_id,
            'client_secret': app_secret,
            'grant_type': 'authorization_code',
            'redirect_uri': redirect_uri,
            'code': code,
        },
        timeout=15
    )
    if res.status_code != 200:
        return f'短期トークン取得エラー: {res.status_code} {res.text}', 400
    short_token = res.json().get('access_token')
    long_res = requests.get(
        'https://graph.threads.net/access_token',
        params={
            'grant_type': 'th_exchange_token',
            'client_secret': app_secret,
            'access_token': short_token,
        },
        timeout=15
    )
    if long_res.status_code != 200:
        return f'長期トークン取得エラー: {long_res.status_code} {long_res.text}', 400
    long_token = long_res.json().get('access_token')
    user_id_val = res.json().get('user_id', '（取得できませんでした）')
    account = request.args.get('account', 'koharu')
    if account == 'mako':
        token_key = 'MAKO_THREADS_ACCESS_TOKEN'
        uid_key = 'MAKO_THREADS_USER_ID'
        label = 'MAKO'
    else:
        token_key = 'KOHARU_THREADS_ACCESS_TOKEN'
        uid_key = 'KOHARU_THREADS_USER_ID'
        label = 'こはるまま'
    return f'''<html><body>
<h2>✅ {label} Threads認証成功！</h2>
<p>以下2つをRenderの環境変数にコピペしてください：</p>
<p><b>{token_key}:</b><br>
<textarea rows="4" cols="80">{long_token}</textarea></p>
<p><b>{uid_key}:</b><br>
<textarea rows="1" cols="80">{user_id_val}</textarea></p>
<p><small>トークンは60日間有効。期限切れになったら /auth/threads?account={account} に再アクセスしてください。</small></p>
</body></html>'''


@debug_bp.route('/rakuten-room-rss')
def rakuten_room_rss():
    try:
        headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36'}
        raw = requests.get('https://room.rakuten.co.jp/makiko01035/items/feed/rss', headers=headers, timeout=10)
        feed = feedparser.parse(raw.text)
        items_xml = ''
        for entry in feed.entries:
            title = entry.get('title', '')
            link = entry.get('link', '')
            summary = entry.get('summary', entry.get('description', ''))
            img_match = re.search(r'<img[^>]+src=["\']([^"\']+)["\']', summary)
            img_url = img_match.group(1) if img_match else ''
            summary_clean = re.sub(r'<[^>]+>', '', summary).strip()
            items_xml += f'''  <item>
    <title><![CDATA[{title}]]></title>
    <link>{link}</link>
    <description><![CDATA[{summary_clean}]]></description>
    <enclosure url="{img_url}" type="image/jpeg" length="0"/>
  </item>\n'''
        rss_xml = f'''<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <title>まきの楽天room</title>
    <link>https://room.rakuten.co.jp/makiko01035/items</link>
    <description>まきの楽天roomコレクション</description>
{items_xml}  </channel>
</rss>'''
        return Response(rss_xml, mimetype='application/rss+xml; charset=utf-8')
    except Exception as e:
        return str(e), 500



@debug_bp.route('/test-kw-debug')
def test_kw_debug():
    """KWフロー診断：Search Console接続・push_message・Anthropic APIを順番に確認"""
    import traceback
    results = {}
    user_id = os.environ.get('LINE_USER_ID', '')

    # 1. push_message テスト
    try:
        line_bot_api.push_message(user_id, TextSendMessage(text='[診断] push_message テスト OK'))
        results['push_message'] = 'OK'
    except Exception as e:
        results['push_message'] = f'ERROR: {e}'

    # 2. get_google_creds テスト
    try:
        from x_analytics import get_google_creds
        creds = get_google_creds()
        results['creds'] = 'OK' if creds else 'None'
        results['creds_bool'] = bool(creds)
    except Exception as e:
        results['creds'] = f'ERROR: {e}'

    # 3. Search Console API テスト（10秒タイムアウト）
    try:
        import concurrent.futures
        from x_analytics import get_google_creds
        creds = get_google_creds()
        def _sc_test():
            from googleapiclient.discovery import build
            svc = build('searchconsole', 'v1', credentials=creds)
            return svc.searchanalytics().query(
                siteUrl='https://foodmakehealth.com/',
                body={'startDate': '2026-05-01', 'endDate': '2026-05-10',
                      'dimensions': ['query'], 'rowLimit': 1}
            ).execute()
        executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)
        future = executor.submit(_sc_test)
        try:
            r = future.result(timeout=10)
            results['search_console'] = f"OK rows={len(r.get('rows',[]))}"
        except concurrent.futures.TimeoutError:
            results['search_console'] = 'TIMEOUT (10s)'
        finally:
            executor.shutdown(wait=False)
    except Exception as e:
        results['search_console'] = f'ERROR: {e}\n{traceback.format_exc()[:300]}'

    # 4. Anthropic API テスト
    try:
        from clients import anthropic_client
        resp = anthropic_client.messages.create(
            model='claude-haiku-4-5-20251001', max_tokens=10,
            messages=[{'role': 'user', 'content': 'hi'}]
        )
        results['anthropic'] = f'OK: {resp.content[0].text[:30]}'
    except Exception as e:
        results['anthropic'] = f'ERROR: {e}'

    return jsonify(results)
