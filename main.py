import os
import json
import base64
import datetime
import pytz
from flask import Flask, request, abort
from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import MessageEvent, TextMessage, TextSendMessage, ImageMessage
from anthropic import Anthropic
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from apscheduler.schedulers.background import BackgroundScheduler

app = Flask(__name__)

line_bot_api = LineBotApi(os.environ['LINE_CHANNEL_ACCESS_TOKEN'])
handler = WebhookHandler(os.environ['LINE_CHANNEL_SECRET'])
anthropic_client = Anthropic(api_key=os.environ['ANTHROPIC_API_KEY'])

JST = pytz.timezone('Asia/Tokyo')

# 画像から抽出したイベント情報を一時保存（確認待ち）
pending_events = {}


def get_calendar_service():
    from google.auth.transport.requests import Request
    creds_info = json.loads(os.environ['GOOGLE_CREDENTIALS'])
    creds = Credentials(
        token=None,
        refresh_token=creds_info.get('refresh_token'),
        token_uri='https://oauth2.googleapis.com/token',
        client_id=creds_info.get('client_id'),
        client_secret=creds_info.get('client_secret'),
        scopes=creds_info.get('scopes'),
    )
    creds.refresh(Request())
    return build('calendar', 'v3', credentials=creds)


def get_or_create_maybe_calendar(service):
    """「気になるイベント」カレンダーを取得または作成"""
    calendars = service.calendarList().list().execute().get('items', [])
    for cal in calendars:
        if cal.get('summary') == '気になるイベント':
            return cal['id']
    new_cal = service.calendars().insert(body={
        'summary': '気になるイベント',
        'timeZone': 'Asia/Tokyo'
    }).execute()
    return new_cal['id']


def get_upcoming_events(days=7):
    service = get_calendar_service()
    now = datetime.datetime.now(JST)
    start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    end = now + datetime.timedelta(days=days)

    calendars = service.calendarList().list().execute().get('items', [])
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


def format_events(events):
    if not events:
        return "予定はありません。"
    lines = []
    for event in events:
        start = event['start'].get('dateTime', event['start'].get('date'))
        if 'T' in start:
            dt = datetime.datetime.fromisoformat(start).astimezone(JST)
            start_str = dt.strftime('%m/%d %H:%M')
        else:
            start_str = start
        cal_name = event.get('_calendar_name', '')
        cal_label = f"[{cal_name}] " if cal_name else ""
        lines.append(f"・{start_str} {cal_label}{event['summary']}")
    return '\n'.join(lines)


@app.route('/callback', methods=['POST'])
def callback():
    signature = request.headers['X-Line-Signature']
    body = request.get_data(as_text=True)
    try:
        handler.handle(body, signature)
    except InvalidSignatureError:
        abort(400)
    return 'OK'


@handler.add(MessageEvent, message=ImageMessage)
def handle_image(event):
    user_id = event.source.user_id
    message_id = event.message.id

    # LINEから画像をダウンロード
    message_content = line_bot_api.get_message_content(message_id)
    image_data = b''.join(chunk for chunk in message_content.iter_content())
    image_base64 = base64.standard_b64encode(image_data).decode('utf-8')

    # Claudeで画像からイベント情報を抽出
    try:
        response = anthropic_client.messages.create(
            model='claude-sonnet-4-6',
            max_tokens=500,
            messages=[{
                'role': 'user',
                'content': [
                    {
                        'type': 'image',
                        'source': {
                            'type': 'base64',
                            'media_type': 'image/jpeg',
                            'data': image_base64
                        }
                    },
                    {
                        'type': 'text',
                        'text': """このチラシやプリントからイベント情報を抽出してください。
以下のJSON形式のみ返してください（情報がない場合はnullにしてください）：
{
  "title": "イベント名",
  "date": "YYYY-MM-DD",
  "start_time": "HH:MM",
  "end_time": "HH:MM",
  "location": "場所",
  "description": "その他メモ"
}"""
                    }
                ]
            }]
        )

        extracted = json.loads(response.content[0].text.strip())
        pending_events[user_id] = extracted

        msg = "📋 読み取れました！「気になるイベント」に登録しますね\n\n"
        msg += f"📌 {extracted.get('title') or '（タイトル不明）'}\n"
        if extracted.get('date'):
            msg += f"📅 {extracted['date']}\n"
        if extracted.get('start_time'):
            time_str = extracted['start_time']
            if extracted.get('end_time'):
                time_str += f"〜{extracted['end_time']}"
            msg += f"🕐 {time_str}\n"
        if extracted.get('location'):
            msg += f"📍 {extracted['location']}\n"
        msg += "\n「登録して」と送ってくれたら保存します！"

        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=msg))

    except Exception as e:
        print(f"Image extract error: {e}")
        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(text="画像からイベント情報を読み取れませんでした😢\n別の角度や明るさで撮り直してみてください。")
        )


@handler.add(MessageEvent, message=TextMessage)
def handle_message(event):
    user_message = event.message.text
    user_id = event.source.user_id

    # ユーザーIDを確認するコマンド
    if user_message == 'myid':
        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(text=f'あなたのユーザーID:\n{user_id}')
        )
        return

    # カレンダー一覧を確認するコマンド
    if user_message == 'カレンダー一覧':
        try:
            service = get_calendar_service()
            calendars = service.calendarList().list().execute().get('items', [])
            cal_list = '\n'.join([f"・{c.get('summary', '')} ({c.get('accessRole', '')})" for c in calendars])
            line_bot_api.reply_message(
                event.reply_token,
                TextSendMessage(text=f'取得できているカレンダー:\n{cal_list}')
            )
        except Exception as e:
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text=f'エラー: {e}'))
        return

    # 画像確認後の「登録して」コマンド
    if user_message == '登録して' and user_id in pending_events:
        extracted = pending_events.pop(user_id)
        try:
            service = get_calendar_service()
            cal_id = get_or_create_maybe_calendar(service)

            if extracted.get('date') and extracted.get('start_time'):
                start_dt = datetime.datetime.fromisoformat(f"{extracted['date']}T{extracted['start_time']}:00")
                start_dt = JST.localize(start_dt)
                if extracted.get('end_time'):
                    end_dt = datetime.datetime.fromisoformat(f"{extracted['date']}T{extracted['end_time']}:00")
                    end_dt = JST.localize(end_dt)
                else:
                    end_dt = start_dt + datetime.timedelta(hours=1)
                event_body = {
                    'summary': extracted.get('title') or 'イベント',
                    'location': extracted.get('location') or '',
                    'description': extracted.get('description') or '',
                    'start': {'dateTime': start_dt.isoformat(), 'timeZone': 'Asia/Tokyo'},
                    'end': {'dateTime': end_dt.isoformat(), 'timeZone': 'Asia/Tokyo'},
                }
            elif extracted.get('date'):
                event_body = {
                    'summary': extracted.get('title') or 'イベント',
                    'location': extracted.get('location') or '',
                    'description': extracted.get('description') or '',
                    'start': {'date': extracted['date']},
                    'end': {'date': extracted['date']},
                }
            else:
                line_bot_api.reply_message(
                    event.reply_token,
                    TextSendMessage(text="日付が読み取れなかったので登録できませんでした😢\n日付を教えてもらえますか？")
                )
                return

            service.events().insert(calendarId=cal_id, body=event_body).execute()
            line_bot_api.reply_message(
                event.reply_token,
                TextSendMessage(text=f"✅ 「気になるイベント」カレンダーに登録しました！\n\n📌 {extracted.get('title') or 'イベント'}")
            )
        except Exception as e:
            print(f"Calendar insert error: {e}")
            line_bot_api.reply_message(
                event.reply_token,
                TextSendMessage(text=f"登録中にエラーが発生しました😢\n{str(e)[:100]}")
            )
        return

    try:
        events = get_upcoming_events(days=14)
        events_text = format_events(events)
    except Exception as e:
        print(f"Calendar error: {e}")
        import traceback
        traceback.print_exc()
        events_text = f"（カレンダー取得エラー: {str(e)[:100]}）"

    now_str = datetime.datetime.now(JST).strftime('%Y年%m月%d日 %H:%M')

    response = anthropic_client.messages.create(
        model='claude-sonnet-4-6',
        max_tokens=1000,
        system=f"""あなたはまきさんの個人秘書「まきの秘書」です。
現在時刻: {now_str}

【今後2週間の予定】
{events_text}

役割:
- スケジュール確認・整理
- やるべきことのリマインド
- 事前準備が必要なことの提案
- 親切で簡潔に日本語で返答する

予定の追加・変更はGoogleカレンダーを直接操作するよう案内してください。""",
        messages=[{'role': 'user', 'content': user_message}]
    )

    reply_text = response.content[0].text
    line_bot_api.reply_message(
        event.reply_token,
        TextSendMessage(text=reply_text)
    )


def send_morning_message():
    try:
        events = get_upcoming_events(days=1)
        today_str = datetime.datetime.now(JST).strftime('%m月%d日')

        if events:
            msg = f"おはようございます！{today_str}の予定です😊\n\n"
            msg += format_events(events)
        else:
            msg = f"おはようございます！{today_str}は予定がありません。\nゆっくり過ごせそうですね！"

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


scheduler = BackgroundScheduler(timezone='Asia/Tokyo')
scheduler.add_job(send_morning_message, 'cron', hour=7, minute=0)
scheduler.add_job(send_preparation_reminder, 'cron', hour=20, minute=0, day_of_week='sun')
scheduler.start()

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
