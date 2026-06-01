import os
import datetime
import threading
import requests
from linebot.models import TextSendMessage

from clients import line_bot_api, JST
from calendar_manager import get_calendar_service, get_upcoming_events, format_events

MORNING_SENT_FILE = '/tmp/morning_sent_date.txt'

_morning_sent_date = None
_morning_sent_lock = threading.Lock()


def send_morning_message():
    global _morning_sent_date
    today = datetime.datetime.now(JST).date()
    today_str = today.isoformat()

    with _morning_sent_lock:
        if _morning_sent_date == today:
            print(f"Morning: already sent today (memory), skipping.")
            return
        try:
            if os.path.exists(MORNING_SENT_FILE):
                with open(MORNING_SENT_FILE, 'r') as f:
                    if f.read().strip() == today_str:
                        _morning_sent_date = today
                        print(f"Morning: already sent today (file), skipping.")
                        return
        except Exception:
            pass
        _morning_sent_date = today
        try:
            with open(MORNING_SENT_FILE, 'w') as f:
                f.write(today_str)
        except Exception:
            pass

    print(f"Morning: sending for {today_str}")
    try:
        service = get_calendar_service()
        now = datetime.datetime.now(JST)
        weekdays = ['月', '火', '水', '木', '金', '土', '日']

        today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        today_end = now.replace(hour=23, minute=59, second=59, microsecond=0)
        tomorrow_start = today_start + datetime.timedelta(days=1)
        tomorrow_end = today_end + datetime.timedelta(days=1)

        calendars = service.calendarList().list().execute().get('items', [])

        def fetch_day_events(start, end):
            all_events = []
            for cal in calendars:
                try:
                    result = service.events().list(
                        calendarId=cal['id'],
                        timeMin=start.isoformat(),
                        timeMax=end.isoformat(),
                        maxResults=20,
                        singleEvents=True,
                        orderBy='startTime'
                    ).execute()
                    for event in result.get('items', []):
                        event['_calendar_name'] = cal.get('summary', '')
                    all_events.extend(result.get('items', []))
                except Exception:
                    pass
            all_events.sort(key=lambda e: e['start'].get('dateTime', e['start'].get('date', '')))
            return all_events

        def format_day(events):
            if not events:
                return "予定なし 🌸\n"
            lines = ""
            for event in events:
                start = event['start'].get('dateTime', event['start'].get('date'))
                if 'T' in start:
                    dt = datetime.datetime.fromisoformat(start).astimezone(JST)
                    time_str = dt.strftime('%H:%M')
                else:
                    time_str = "終日"
                title = event.get('summary', '（タイトルなし）')
                lines += f"⏰ {time_str}  {title}\n"
            return lines

        today_events = fetch_day_events(today_start, today_end)
        tomorrow_events = fetch_day_events(tomorrow_start, tomorrow_end)

        today_str = f"{now.strftime('%m/%d')}({weekdays[now.weekday()]})"
        tomorrow_str = f"{(now + datetime.timedelta(days=1)).strftime('%m/%d')}({weekdays[(now.weekday() + 1) % 7]})"

        msg = f"🌅 おはようございます！\n\n"
        msg += f"📅 今日 {today_str}\n"
        msg += format_day(today_events)
        msg += f"\n📅 明日 {tomorrow_str}\n"
        msg += format_day(tomorrow_events)
        msg += "\n今日も素敵な1日を！✨"

        user_id = os.environ['LINE_USER_ID']
        line_bot_api.push_message(user_id, TextSendMessage(text=msg))
    except Exception as e:
        print(f"Morning message error: {e}")


def send_preparation_reminder():
    try:
        events = get_upcoming_events(days=3)
        if not events:
            return
        user_id = os.environ['LINE_USER_ID']
        msg = "【3日以内の予定】事前準備は大丈夫ですか？\n\n"
        msg += format_events(events)
        msg += "\n\n準備が必要なことがあれば教えてください！"
        line_bot_api.push_message(user_id, TextSendMessage(text=msg))
    except Exception as e:
        print(f"Preparation reminder error: {e}")



def send_hsbc_reminder():
    try:
        user_id = os.environ['LINE_USER_ID']
        now = datetime.datetime.now(JST)
        month = now.month
        rate_text = ""
        try:
            res = requests.get("https://api.exchangerate-api.com/v4/latest/USD", timeout=5)
            data = res.json()
            usd_jpy = data["rates"]["JPY"]
            hkd_usd = data["rates"]["HKD"]
            hkd_jpy = usd_jpy / hkd_usd
            rate_text = f"\n📊 今日のレート\nUSD/JPY: {usd_jpy:.1f}円\nHKD/JPY: {hkd_jpy:.2f}円"
        except Exception:
            rate_text = "\n（レート取得失敗）"
        msg = (
            f"🏦 【HSBC {month}月の換金リマインダー】\n"
            f"今月のHKD↔USD換金を忘れずに！\n"
            f"少額でOK。口座維持のための換金です。{rate_text}\n\n"
            f"HSBCアプリ → 外貨両替 → HKD→USD（または逆）"
        )
        line_bot_api.push_message(user_id, TextSendMessage(text=msg))
    except Exception as e:
        print(f"HSBC reminder error: {e}")


def send_zaitage_reminder():
    try:
        user_id = os.environ['LINE_USER_ID']
        msg = (
            "🏠【在宅専門医 週次リマインダー】\n\n"
            "今週も少しだけ進めよう！\n\n"
            "📋 今すぐできること\n"
            "・他施設研修 2か所目の問い合わせ\n"
            "・ポートフォリオのテーマを1つ決める\n"
            "・症例を1〜2例メモする\n\n"
            "⚠️ 2026年10〜11月の受験登録を忘れずに！\n"
            "実践者コースは2027年が実質ラストチャンス。\n\n"
            "Notionで進捗確認👇\n"
            "https://www.notion.so/344f8d6d41de818db44fc33af7cf39e5"
        )
        line_bot_api.push_message(user_id, TextSendMessage(text=msg))
    except Exception as e:
        print(f"Zaitage reminder error: {e}")



def send_x_engage_reminder():
    try:
        user_id = os.environ['LINE_USER_ID']
        msg = (
            "𝕏【X エンゲージメントタイム】\n\n"
            "今日の10分アクション👇\n\n"
            "① 検索：「AI副業」「ワーママ 副業」「ClaudeCode」\n"
            "② フォロワー300〜5000人の投稿に共感リプを3件\n"
            "③ 自分にリプが来てたら返信する\n\n"
            "リポストは不要。リプライだけでOK✨"
        )
        line_bot_api.push_message(user_id, TextSendMessage(text=msg))
    except Exception as e:
        print(f"X engage reminder error: {e}")


def send_famm_reminder():
    try:
        user_id = os.environ['LINE_USER_ID']
        line_bot_api.push_message(user_id, TextSendMessage(
            text="📸 【Famm更新】今月のFammの更新をお忘れなく！\n期限は今月9日です。"
        ))
    except Exception as e:
        print(f"Famm reminder error: {e}")


def send_famm_deadline_reminder():
    try:
        user_id = os.environ['LINE_USER_ID']
        line_bot_api.push_message(user_id, TextSendMessage(
            text="⚠️ 【Famm期限まで3日！】\nFammの更新期限は9日です。まだの方はお早めに！"
        ))
    except Exception as e:
        print(f"Famm deadline reminder error: {e}")


def send_ebay_check_reminder():
    try:
        user_id = os.environ['LINE_USER_ID']
        line_bot_api.push_message(user_id, TextSendMessage(
            text="📦 【eBayチェック日・土曜日】\n今日はeBayの確認日です！\n\nClaudeに👇と声かけしてね\n「eBay状況確認して、次やること教えて」"
        ))
    except Exception as e:
        print(f"eBay check reminder error: {e}")


def send_monthly_review_reminder():
    try:
        user_id = os.environ['LINE_USER_ID']
        line_bot_api.push_message(user_id, TextSendMessage(
            text="📊 【月初振り返り】\n今日は先月の振り返り日です！\n\nClaudeに👇と声かけしてね\n「先月の副業収支まとめて」\n「薬膳・セキスイの今月進捗教えて」\n「eBay今月の売上と反省点まとめて」"
        ))
    except Exception as e:
        print(f"Monthly review reminder error: {e}")
