import os
import threading
import time
from flask import Flask, request, abort
from linebot.exceptions import InvalidSignatureError
from apscheduler.schedulers.background import BackgroundScheduler

from clients import line_bot_api, handler
from ebay_handler import send_daily_purchase_candidates
from sns_engine_koharu import (
    run_researcher as koharu_researcher,
    run_writer     as koharu_writer,
    run_poster_morning as koharu_poster_morning,
    run_poster_aff     as koharu_poster_aff,
    run_collector  as koharu_collector,
    run_analyst    as koharu_analyst,
    run_monitor    as koharu_monitor,
)
from sns_engine_mako import (
    run_researcher as mako_researcher,
    run_writer     as mako_writer,
    run_poster_info as mako_poster_info,
    run_poster_aff  as mako_poster_aff,
    run_collector   as mako_collector,
    run_analyst     as mako_analyst,
    run_monitor     as mako_monitor,
    run_poster_morning_quote as mako_poster_morning_quote,
    run_quote_generator      as mako_quote_generator,
)
from blog_yakuzen import auto_blog_new, auto_blog_rewrite
from ebay_dashboard import ebay_bp
from calendar_manager import check_deadline_reminders
from scheduler_reminders import (
    send_morning_message, send_preparation_reminder,
    send_hsbc_reminder, send_zaitage_reminder,
    send_x_engage_reminder, send_famm_deadline_reminder,
    send_ebay_check_reminder, send_monthly_review_reminder,
)
from x_poster import post_to_x_daily, post_to_x_noon, post_to_x_evening
from x_analytics import (
    send_weekly_seo_report, send_note_reminder, send_note_weekly_reminder,
    send_x_weekly_report, send_daily_work_log,
)
from threads_room import send_room_suggestion_slot, send_threads_token_reminder
from sns_direct_poster import (
    post_kvision_room_intro, post_koharu_threads_room_intro,
    post_kvision_morning_tweet, post_kvision_travel_aff_auto, post_kvision_card_tweet,
    post_koharu_threads_card,
    post_mako_threads_morning, post_mako_threads_aff_auto,
    post_mako_x_morning_tweet,
)
from koharumama_card_post import post_kvision_card_image
from routes_debug import debug_bp
from routes_company import company_bp
from routes_wp import wp_bp
import line_handler  # @handler.add デコレーターを登録

app = Flask(__name__)
app.register_blueprint(ebay_bp)
app.register_blueprint(debug_bp)
app.register_blueprint(company_bp)
app.register_blueprint(wp_bp)




@app.route('/ping')
def ping():
    return 'OK'


@app.route('/run-blog-now', methods=['POST'])
def run_blog_now():
    data = request.get_json(silent=True) or {}
    if data.get('secret') != os.environ.get('NOTIFY_SECRET', 'maki2025'):
        abort(403)
    mode = data.get('mode', 'new')
    if mode == 'rewrite':
        threading.Thread(target=auto_blog_rewrite, daemon=True).start()
        return {'status': 'started', 'mode': 'rewrite'}
    else:
        threading.Thread(target=auto_blog_new, daemon=True).start()
        return {'status': 'started', 'mode': 'new'}


@app.route('/debug-blog', methods=['GET', 'POST'])
def debug_blog():
    """環境変数確認のみ（診断用）"""
    import sys
    from pathlib import Path
    kw_file = Path(__file__).parent / 'keywords_new.txt'
    lines = [l.strip() for l in kw_file.read_text(encoding='utf-8').splitlines() if l.strip()]
    return {
        'keyword': lines[0] if lines else '(空)',
        'ANTHROPIC_API_KEY': 'OK' if os.environ.get('ANTHROPIC_API_KEY') else 'NG',
        'YAKUZEN_WP_APP_PASSWORD': 'OK' if os.environ.get('YAKUZEN_WP_APP_PASSWORD') else 'NG',
        'LINE_CHANNEL_ACCESS_TOKEN': 'OK' if os.environ.get('LINE_CHANNEL_ACCESS_TOKEN') else 'NG',
        'python': sys.version,
    }


@app.route('/debug-phase4', methods=['GET', 'POST'])
def debug_phase4():
    """Anthropic API実呼び出しテスト（短い返答）"""
    import traceback, time
    try:
        import anthropic
        client = anthropic.Anthropic(api_key=os.environ.get('ANTHROPIC_API_KEY'))
        t0 = time.time()
        resp = client.messages.create(
            model='claude-haiku-4-5-20251001',
            max_tokens=50,
            messages=[{'role': 'user', 'content': '「はい」とだけ答えてください。'}]
        )
        elapsed = round(time.time() - t0, 2)
        return {'status': 'ok', 'reply': resp.content[0].text, 'elapsed_sec': elapsed}
    except Exception as e:
        return {'error': str(e), 'traceback': traceback.format_exc()[-800:]}




@app.route('/callback', methods=['POST'])
def callback():
    signature = request.headers['X-Line-Signature']
    body = request.get_data(as_text=True)
    try:
        handler.handle(body, signature)
    except InvalidSignatureError:
        abort(400)
    return 'OK'







scheduler = BackgroundScheduler(timezone='Asia/Tokyo')
scheduler.add_job(send_morning_message, 'cron', hour=7, minute=0)
scheduler.add_job(send_preparation_reminder, 'cron', hour=20, minute=0, day_of_week='sun')
scheduler.add_job(check_deadline_reminders, 'cron', hour=8, minute=0)
# 3・6・9・12月の1日朝8時30分：HSBC換金リマインダー（3か月に1回）
scheduler.add_job(send_hsbc_reminder, 'cron', month='3,6,9,12', day=1, hour=8, minute=30)
# 毎月6日朝9時：Famm期限3日前リマインダー
scheduler.add_job(send_famm_deadline_reminder, 'cron', day=6, hour=9, minute=0)
# 月・木 8:00：7stepパイプラインで新規記事を自動作成

scheduler.add_job(auto_blog_new,     'cron', day_of_week='mon,thu', hour=8, minute=0)
scheduler.add_job(auto_blog_rewrite, 'cron', day_of_week='wed,sat', hour=8, minute=0)
# 毎週土曜朝9時：eBayチェックリマインダー
scheduler.add_job(send_ebay_check_reminder, 'cron', day_of_week='sat', hour=9, minute=0)
# 毎月1日朝9時30分：月初振り返りリマインダー（Fammリマインダーの30分後）
scheduler.add_job(send_monthly_review_reminder, 'cron', day=1, hour=9, minute=30)
# 毎週月曜朝9時10分：在宅専門医 取得プロジェクト週次リマインダー
scheduler.add_job(send_zaitage_reminder, 'cron', day_of_week='mon', hour=9, minute=10)
# 毎日朝8:30・昼12:30（奇数日のみ）・夜19:30：X（Twitter）自動投稿（2〜3本/日）
scheduler.add_job(post_to_x_daily, 'cron', hour=8, minute=30)
scheduler.add_job(post_to_x_noon, 'cron', hour=12, minute=30)
scheduler.add_job(post_to_x_evening, 'cron', hour=19, minute=30)
# 毎週月・水・金 朝9時20分：X エンゲージメントリマインダー
scheduler.add_job(send_x_engage_reminder, 'cron', day_of_week='mon', hour=9, minute=20)  # 月水金→月曜のみ（通数削減）
# 毎週月曜朝9時30分：週次SEOレポート（薬膳・セキスイ・X）
scheduler.add_job(send_weekly_seo_report, 'cron', day_of_week='mon', hour=9, minute=30)
# 毎月末日朝9時：noteリマインド
scheduler.add_job(send_note_reminder, 'cron', day='last', hour=9, minute=0)
scheduler.add_job(send_note_weekly_reminder, 'cron', day_of_week='thu', hour=9, minute=5)
# 毎週月曜9時40分：週次Xパフォーマンスレポート（PDCA用）
scheduler.add_job(send_x_weekly_report, 'cron', day_of_week='mon', hour=9, minute=40)
# 毎週月曜18時：業務ログ（毎日→月曜のみに変更・通数削減）
scheduler.add_job(send_daily_work_log, 'cron', day_of_week='mon', hour=18, minute=0)
# 毎日5本：楽天アフィ→Threads自動投稿（朝1・昼1・夕1・夜2、1時間以上間隔）
# ジャンルは7種ローテーション（slot % 7）
scheduler.add_job(send_room_suggestion_slot, 'cron', hour=7, minute=30, args=[0])   # UV・日焼け止め
scheduler.add_job(send_room_suggestion_slot, 'cron', hour=12, minute=30, args=[1])  # 冷感寝具
scheduler.add_job(send_room_suggestion_slot, 'cron', hour=17, minute=30, args=[2])  # 父の日ギフト
scheduler.add_job(send_room_suggestion_slot, 'cron', hour=20, minute=0, args=[4])   # 美容サプリ（ゴールデンタイム）
scheduler.add_job(send_room_suggestion_slot, 'cron', hour=22, minute=0, args=[6])   # スキンケア
# 7月7日朝9時：Threadsトークン更新リマインド（60日期限）
scheduler.add_job(send_threads_token_reminder, 'date', run_date='2026-07-07 09:00:00', timezone='Asia/Tokyo')
# @kvision_m（こはるまま）旅行×楽天アフィ X：1日2本 + 楽天カード週2
# 朝9:00 旅あるあるつぶやき、夜20:30 固定＋月替わりジャンルをローテーションしてアフィスレッド
# 水・土曜12:30 楽天カード誘導ツイート（RAKUTEN_CARD_AFF_URL設定済みならURL付き）
scheduler.add_job(post_kvision_morning_tweet, 'cron', hour=9, minute=0)
scheduler.add_job(post_kvision_travel_aff_auto, 'cron', hour=20, minute=30)
scheduler.add_job(post_kvision_card_tweet, 'cron', day_of_week='wed,sat', hour=12, minute=0, jitter=3600)
# 木曜夜19時：楽天ROOM誘導投稿（前後30分ランダム）
scheduler.add_job(post_kvision_room_intro, 'cron', day_of_week='thu', hour=19, minute=0, jitter=1800)
# 日曜11時：リスト型カード画像投稿（商品写真4枚+タイトル）
scheduler.add_job(post_kvision_card_image, 'cron', day_of_week='sun', hour=11, minute=0)
# ========== こはるまま SNSエンジン 6ロール ==========
# ① リサーチャー：月曜 05:00（今週のテーマ生成）
scheduler.add_job(koharu_researcher, 'cron', day_of_week='mon', hour=5, minute=0)
# ② ライター：月曜 06:00（投稿案生成→AI採点→LINEで確認依頼）
scheduler.add_job(koharu_writer, 'cron', day_of_week='mon', hour=6, minute=0)
# ③ ポスター：承認済みストックから投稿（ストック切れ時はフォールバック自動生成）
scheduler.add_job(koharu_poster_morning, 'cron', hour=6, minute=30, jitter=7200)   # 6:30〜8:30のランダム
scheduler.add_job(koharu_poster_aff,     'cron', hour=19, minute=0,  jitter=7200)   # 19:00〜21:00のランダム
# カード・ROOM誘導は既存関数を継続
scheduler.add_job(post_koharu_threads_card,      'cron', day_of_week='wed,sat', hour=12, minute=0, jitter=3600)
scheduler.add_job(post_koharu_threads_room_intro,'cron', day_of_week='thu', hour=19, minute=0, jitter=1800)
# ④ コレクター：毎日 23:00（Threads APIでパフォーマンスデータ取得）
scheduler.add_job(koharu_collector, 'cron', hour=23, minute=0)
# ⑤ アナリスト：日曜 20:00（週次分析→LINEレポート）
scheduler.add_job(koharu_analyst, 'cron', day_of_week='sun', hour=20, minute=0)
# ⑥ モニター：毎日 07:00 / 13:00 / 22:00（正常稼働・凍結チェック）
scheduler.add_job(koharu_monitor, 'cron', hour=7,  minute=0)
scheduler.add_job(koharu_monitor, 'cron', hour=13, minute=0)
scheduler.add_job(koharu_monitor, 'cron', hour=22, minute=0)
# ========== MAKO SNSエンジン 6ロール（x:10 オフセット・こはるままと衝突回避）==========
# ① リサーチャー：月曜 05:10
scheduler.add_job(mako_researcher, 'cron', day_of_week='mon', hour=5, minute=10)
# ② ライター：月曜 06:10
scheduler.add_job(mako_writer, 'cron', day_of_week='mon', hour=6, minute=10)
# ③ ポスター：承認済みストックから投稿（ストック切れ時はリアルタイム生成）
scheduler.add_job(mako_poster_info, 'cron', hour=6, minute=40, jitter=7200)  # 6:40〜8:40のランダム
scheduler.add_job(mako_poster_aff,  'cron', hour=19, minute=10, jitter=7200)  # 19:10〜21:10のランダム
# ④ コレクター：毎日 23:10
scheduler.add_job(mako_collector, 'cron', hour=23, minute=10)
# ⑤ アナリスト：日曜 20:10
scheduler.add_job(mako_analyst, 'cron', day_of_week='sun', hour=20, minute=10)
# ⑥ モニター：毎日 07:10 / 13:10 / 22:10
scheduler.add_job(mako_monitor, 'cron', hour=7,  minute=10)
scheduler.add_job(mako_monitor, 'cron', hour=13, minute=10)
scheduler.add_job(mako_monitor, 'cron', hour=22, minute=10)
# ⑦ 格言ポスター：毎朝 05:00〜05:15（X のみ・Threadsはまきさんが手動で貼る）
scheduler.add_job(mako_poster_morning_quote, 'cron', hour=22, minute=0)
# ⑧ 格言ジェネレーター：毎月1日 04:00（30本生成→LINEに通知）
scheduler.add_job(mako_quote_generator, 'cron', day=1, hour=4, minute=0)
# MAKO Threads：1日2本（MAKO_THREADS_ACCESS_TOKEN設定後に自動稼働）
# 朝8:00 睡眠共感投稿、夜21:00 アフィスレッド（言い切りNG・共感ベース）
scheduler.add_job(post_mako_threads_morning, 'cron', hour=8, minute=0)
scheduler.add_job(post_mako_threads_aff_auto, 'cron', hour=21, minute=0)
# MAKO X：朝7:30 えまたまスタイルのツリー投稿（医学×睡眠・日付ベースローテーション）
scheduler.add_job(post_mako_x_morning_tweet, 'cron', hour=7, minute=30)
# 毎朝5:30：eBay日本人セラー売れ筋から仕入れ候補をLINEに送信
scheduler.add_job(
    lambda: send_daily_purchase_candidates(os.environ.get('LINE_USER_ID', '')),
    'cron', hour=5, minute=30,
)
def _delayed_scheduler_start():
    time.sleep(120)
    scheduler.start()
    print("[scheduler] 起動完了（デプロイ並走防止のため120秒遅延）")

threading.Thread(target=_delayed_scheduler_start, daemon=True).start()

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
