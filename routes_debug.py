import os
import json
import random
import threading
from flask import Blueprint, jsonify, request
from linebot.models import TextSendMessage

from clients import line_bot_api
from sns_engine_koharu import (
    run_researcher as koharu_researcher,
    run_writer as koharu_writer,
    run_analyst as koharu_analyst,
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
from threads_room import send_room_suggestion_slot, ROOM_GENRES

debug_bp = Blueprint('debug', __name__)

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

