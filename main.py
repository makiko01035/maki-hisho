import os
import json
import base64
import datetime
import threading
import pytz
import requests
import markdown as md_lib
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

PENDING_FILE = '/tmp/pending_events.json'
SEKISUI_SESSION_FILE = '/tmp/sekisui_sessions.json'


def load_pending_events():
    try:
        with open(PENDING_FILE, 'r') as f:
            return json.load(f)
    except Exception:
        return {}


def save_pending_events(data):
    try:
        with open(PENDING_FILE, 'w') as f:
            json.dump(data, f)
    except Exception as e:
        print(f"pending_events save error: {e}")


def load_sekisui_sessions():
    try:
        with open(SEKISUI_SESSION_FILE, 'r') as f:
            return json.load(f)
    except Exception:
        return {}


def save_sekisui_sessions(data):
    try:
        with open(SEKISUI_SESSION_FILE, 'w') as f:
            json.dump(data, f, ensure_ascii=False)
    except Exception as e:
        print(f"sekisui_sessions save error: {e}")


def suggest_sekisui_themes():
    response = anthropic_client.messages.create(
        model='claude-sonnet-4-6',
        max_tokens=400,
        messages=[{
            'role': 'user',
            'content': """セキスイハイムで家を建てた施主が書くブログ向けに、
注文住宅を検討中の読者に役立つ記事テーマを3つ提案してください。
施主の実体験を盛り込める具体的なテーマにしてください。
以下の形式だけ返してください：
1. テーマ名
2. テーマ名
3. テーマ名"""
        }]
    )
    return response.content[0].text.strip()


def generate_sekisui_article(user_input):
    response = anthropic_client.messages.create(
        model='claude-sonnet-4-6',
        max_tokens=3000,
        messages=[{
            'role': 'user',
            'content': f"""セキスイハイムで家を建てた施主のブログ記事を書いてください。

施主からの情報：{user_input}

条件：
- 1500〜2000文字程度
- 注文住宅を検討中の方向け
- 実体験を自然に盛り込む（「私の場合は〜」「実際に〜でした」など）
- 見出し（##）を使って3〜4セクションに分ける
- Markdown形式で出力
- 最初の行は「# タイトル」形式
- 記事末尾に「<!-- sekisui-affiliate-cta -->」を1行追加"""
        }]
    )
    return response.content[0].text.strip()


def fetch_pexels_image_for_wp(keyword):
    pexels_key = os.environ.get('PEXELS_API_KEY')
    if not pexels_key:
        return None
    try:
        res = requests.get(
            'https://api.pexels.com/v1/search',
            headers={'Authorization': pexels_key},
            params={'query': keyword, 'per_page': 1, 'orientation': 'landscape'},
            timeout=10
        )
        photos = res.json().get('photos', [])
        if not photos:
            return None
        img_res = requests.get(photos[0]['src']['large2x'], timeout=10)
        if img_res.status_code != 200:
            return None
        return img_res.content, f"pexels_{photos[0]['id']}.jpg"
    except Exception as e:
        print(f"Pexels error: {e}")
        return None


def upload_image_to_wp(wp_url, wp_user, wp_pass, img_data, filename):
    try:
        res = requests.post(
            f'{wp_url}/wp-json/wp/v2/media',
            auth=(wp_user, wp_pass),
            headers={
                'Content-Disposition': f'attachment; filename="{filename}"',
                'Content-Type': 'image/jpeg',
            },
            data=img_data,
            timeout=30
        )
        if res.status_code == 201:
            return res.json()['id']
    except Exception as e:
        print(f"Image upload error: {e}")
    return None


def post_to_sekisui_wp(title, content_md):
    wp_url = os.environ.get('SEKISUI_WP_URL', 'https://order-sekisui.com')
    wp_user = os.environ.get('SEKISUI_WP_USER', 'makiko01035')
    wp_pass = os.environ['SEKISUI_WP_APP_PASSWORD']

    html = md_lib.markdown(content_md, extensions=['tables', 'nl2br'])
    data = {'title': title, 'content': html, 'status': 'draft'}

    img_result = fetch_pexels_image_for_wp(title)
    if img_result:
        img_data, filename = img_result
        media_id = upload_image_to_wp(wp_url, wp_user, wp_pass, img_data, filename)
        if media_id:
            data['featured_media'] = media_id

    res = requests.post(
        f'{wp_url}/wp-json/wp/v2/posts',
        auth=(wp_user, wp_pass),
        json=data,
        timeout=30
    )
    if res.status_code == 201:
        post = res.json()
        return post['id'], post['link']
    raise Exception(f"WP投稿エラー: {res.status_code}")


def process_sekisui_article(user_id, user_input):
    try:
        article_md = generate_sekisui_article(user_input)
        lines = article_md.split('\n')
        title = lines[0].lstrip('# ').strip()
        content = '\n'.join(lines[1:]).lstrip('\n')
        post_id, _ = post_to_sekisui_wp(title, content)
        edit_url = f"https://order-sekisui.com/wp-admin/post.php?post={post_id}&action=edit"
        msg = f"✅ セキスイ記事を下書き保存しました！\n\n📝 {title}\n\n確認・公開はこちら：\n{edit_url}"
        line_bot_api.push_message(user_id, TextSendMessage(text=msg))
    except Exception as e:
        print(f"Sekisui article error: {e}")
        line_bot_api.push_message(user_id, TextSendMessage(text=f"😢 記事作成中にエラーが発生しました。\n{str(e)[:100]}"))


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


def check_deadline_reminders():
    """申込期限の1週間前・3日前・前日・当日にLINE通知を送る"""
    try:
        service = get_calendar_service()
        cal_id = get_or_create_maybe_calendar(service)
        now = datetime.datetime.now(JST)
        today = now.date()
        user_id = os.environ['LINE_USER_ID']

        notify_days = {
            0: ("⚠️", "今日", "まだ申し込んでいなければ急いでください！"),
            1: ("📢", "明日", "忘れずに申し込んでください！"),
            3: ("📌", "3日後", "早めに準備を始めてください！"),
            7: ("📅", "1週間後", "早めに確認しておきましょう！"),
        }

        for days_ahead, (icon, label, action) in notify_days.items():
            target_date = today + datetime.timedelta(days=days_ahead)
            start = datetime.datetime.combine(target_date, datetime.time.min).astimezone(JST)
            end = datetime.datetime.combine(target_date, datetime.time.max).astimezone(JST)

            result = service.events().list(
                calendarId=cal_id,
                timeMin=start.isoformat(),
                timeMax=end.isoformat(),
                maxResults=20,
                singleEvents=True,
                orderBy='startTime'
            ).execute()

            for e in result.get('items', []):
                if '【申込期限】' not in e.get('summary', ''):
                    continue
                title = e['summary'].replace('【申込期限】', '').strip()
                msg = f"{icon} 【申込期限 {label}！】\n「{title}」の申し込み期限は{label}です！\n{action}"
                line_bot_api.push_message(user_id, TextSendMessage(text=msg))
    except Exception as e:
        print(f"Deadline reminder error: {e}")


@app.route('/ping')
def ping():
    return 'OK'


@app.route('/trigger/morning', methods=['GET', 'POST'])
def trigger_morning():
    threading.Thread(target=send_morning_message).start()
    return 'OK'


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

    try:
        # LINEから画像をダウンロード
        message_content = line_bot_api.get_message_content(message_id)
        image_data = b''.join(chunk for chunk in message_content.iter_content())
        image_base64 = base64.standard_b64encode(image_data).decode('utf-8')
        if image_data[:4] == b'\x89PNG':
            media_type = 'image/png'
        elif image_data[:4] == b'RIFF':
            media_type = 'image/webp'
        elif image_data[:6] in (b'GIF87a', b'GIF89a'):
            media_type = 'image/gif'
        else:
            media_type = 'image/jpeg'
    except Exception as e:
        print(f"Image download error: {e}")
        import traceback; traceback.print_exc()
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=f"画像の取得に失敗しました😢\nエラー: {str(e)[:100]}"))
        return

    # Claudeで画像からイベント情報を抽出
    try:
        response = anthropic_client.messages.create(
            model='claude-sonnet-4-6',
            max_tokens=2000,
            messages=[{
                'role': 'user',
                'content': [
                    {
                        'type': 'image',
                        'source': {
                            'type': 'base64',
                            'media_type': media_type,
                            'data': image_base64
                        }
                    },
                    {
                        'type': 'text',
                        'text': """このチラシやプリントから全てのイベント・日程情報を抽出してください。
複数の日程がある場合は全て抽出してください。
以下のJSON配列形式のみ返してください（情報がない場合はnullにしてください）：
[
  {
    "title": "イベント名",
    "date": "YYYY-MM-DD",
    "start_time": "HH:MM",
    "end_time": "HH:MM",
    "location": "場所",
    "description": "その他メモ",
    "application_deadline": "YYYY-MM-DD"
  }
]
application_deadlineは申込締切・申込期限・締切日などの日付です。ない場合はnullにしてください。
必ずJSON配列（[...]）で返してください。"""
                    }
                ]
            }]
        )

        raw_text = response.content[0].text.strip()
        start = raw_text.find('[')
        decoder = json.JSONDecoder()
        extracted_list, _ = decoder.raw_decode(raw_text, start)
        if isinstance(extracted_list, dict):
            extracted_list = [extracted_list]
        pending_events = load_pending_events()
        pending_events[user_id] = extracted_list
        save_pending_events(pending_events)

        msg = f"📋 {len(extracted_list)}件読み取れました！\n\n"
        for i, ev in enumerate(extracted_list, 1):
            msg += f"【{i}】📌 {ev.get('title') or '（タイトル不明）'}\n"
            if ev.get('date'):
                msg += f"　📅 {ev['date']}\n"
            if ev.get('start_time'):
                time_str = ev['start_time']
                if ev.get('end_time'):
                    time_str += f"〜{ev['end_time']}"
                msg += f"　🕐 {time_str}\n"
            if ev.get('location'):
                msg += f"　📍 {ev['location']}\n"
            if ev.get('application_deadline'):
                msg += f"　⚠️ 申込期限: {ev['application_deadline']}\n"
            msg += "\n"
        msg += "「登録して」と送ってくれたら全て保存します！"

        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=msg))

    except Exception as e:
        print(f"Image extract error: {e}")
        import traceback; traceback.print_exc()
        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(text=f"画像の読み取りに失敗しました😢\nエラー: {str(e)[:150]}")
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

    # 手動期限登録（「〇〇の期限 4月10日」など）
    if any(kw in user_message for kw in ['の期限', 'の締切', 'の締め切り', 'の申込期限', 'の手続き期限']):
        try:
            parse_response = anthropic_client.messages.create(
                model='claude-sonnet-4-6',
                max_tokens=300,
                messages=[{
                    'role': 'user',
                    'content': f"""以下のメッセージから手続き名と期限日を抽出してください。
今日の日付: {datetime.datetime.now(JST).strftime('%Y-%m-%d')}
メッセージ: {user_message}
以下のJSON形式のみ返してください（他の文字は不要）:
{{"title": "手続き名", "deadline": "YYYY-MM-DD"}}
日付が不明な場合はdeadlineをnullにしてください。"""
                }]
            )
            parsed = json.loads(parse_response.content[0].text.strip())
            title = parsed.get('title')
            deadline = parsed.get('deadline')

            if title and deadline:
                service = get_calendar_service()
                cal_id = get_or_create_maybe_calendar(service)
                deadline_event = {
                    'summary': f"【申込期限】{title}",
                    'description': '申込期限です。忘れずに！',
                    'start': {'date': deadline},
                    'end': {'date': deadline},
                }
                service.events().insert(calendarId=cal_id, body=deadline_event).execute()
                line_bot_api.reply_message(
                    event.reply_token,
                    TextSendMessage(text=f"✅ 登録しました！\n📌 {title}\n⚠️ 期限: {deadline}\n\n1週間前・3日前・前日・当日にお知らせします！")
                )
            else:
                line_bot_api.reply_message(
                    event.reply_token,
                    TextSendMessage(text="手続き名か期限日が読み取れませんでした😢\n例：「〇〇の期限 4月10日」のように送ってください！")
                )
        except Exception as e:
            print(f"Manual deadline error: {e}")
            line_bot_api.reply_message(
                event.reply_token,
                TextSendMessage(text=f"登録中にエラーが発生しました😢\n{str(e)[:100]}")
            )
        return

    # 画像確認後の「登録して」コマンド
    pending_events = load_pending_events()
    if user_message == '登録して' and user_id in pending_events:
        extracted_list = pending_events.pop(user_id)
        save_pending_events(pending_events)
        try:
            service = get_calendar_service()
            cal_id = get_or_create_maybe_calendar(service)
            registered = []
            deadline_count = 0

            for extracted in extracted_list:
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
                    continue

                service.events().insert(calendarId=cal_id, body=event_body).execute()
                registered.append(extracted.get('title') or 'イベント')

                deadline = extracted.get('application_deadline')
                if deadline:
                    deadline_event = {
                        'summary': f"【申込期限】{extracted.get('title') or 'イベント'}",
                        'description': '申込期限です。忘れずに！',
                        'start': {'date': deadline},
                        'end': {'date': deadline},
                    }
                    service.events().insert(calendarId=cal_id, body=deadline_event).execute()
                    deadline_count += 1

            reply = f"✅ {len(registered)}件を「気になるイベント」に登録しました！\n\n"
            for title in registered:
                reply += f"📌 {title}\n"
            if deadline_count:
                reply += f"\n⚠️ 申込期限{deadline_count}件も登録しました！\n1週間前・3日前・前日・当日にお知らせします！"
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply))

        except Exception as e:
            print(f"Calendar insert error: {e}")
            line_bot_api.reply_message(
                event.reply_token,
                TextSendMessage(text=f"登録中にエラーが発生しました😢\n{str(e)[:100]}")
            )
        return

    # セキスイブログ：作成中セッションチェック
    sekisui_sessions = load_sekisui_sessions()
    if user_id in sekisui_sessions and sekisui_sessions[user_id] == 'waiting_for_content':
        del sekisui_sessions[user_id]
        save_sekisui_sessions(sekisui_sessions)
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text="✍️ 記事を作成中です...少しお待ちください！（1〜2分かかります）"))
        threading.Thread(target=process_sekisui_article, args=(user_id, user_message)).start()
        return

    # セキスイブログ：キーワード検出 → テーマ提案
    sekisui_keywords = ['セキスイ記事', 'セキスイブログ', 'セキスイ 記事', 'order-sekisui']
    if any(kw in user_message for kw in sekisui_keywords):
        themes = suggest_sekisui_themes()
        sekisui_sessions[user_id] = 'waiting_for_content'
        save_sekisui_sessions(sekisui_sessions)
        msg = f"🏠 セキスイブログ記事を作りましょう！\n\nテーマ候補：\n{themes}\n\n番号と実体験・エピソードを一緒に教えてください！\n例：「2番で。先月の電気代が想像より安くて驚いた」"
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=msg))
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
        now = datetime.datetime.now(JST)
        weekdays = ['月', '火', '水', '木', '金', '土', '日']
        today_str = f"{now.strftime('%m月%d日')}({weekdays[now.weekday()]})"

        msg = f"🌅 おはようございます！\n📅 {today_str}の予定\n"

        if events:
            msg += "\n"
            for event in events:
                start = event['start'].get('dateTime', event['start'].get('date'))
                if 'T' in start:
                    dt = datetime.datetime.fromisoformat(start).astimezone(JST)
                    time_str = dt.strftime('%H:%M')
                else:
                    time_str = "終日"
                title = event.get('summary', '（タイトルなし）')
                cal_name = event.get('_calendar_name', '')
                msg += f"⏰ {time_str}  {title}\n"
                if cal_name:
                    msg += f"   📂 {cal_name}\n"
                msg += "\n"
            msg += "今日も素敵な1日を！✨"
        else:
            msg += "\n予定はありません 🌸\nゆっくり過ごせそうですね！"

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


def send_yakuzen_blog_reminder():
    try:
        user_id = os.environ['LINE_USER_ID']
        line_bot_api.push_message(user_id, TextSendMessage(
            text="✍️ 【薬膳ブログ更新日・火曜日】\n今日は薬膳ブログ＋Pinterest投稿の日です！\n\nClaudeに👇と声かけしてね\n「Pinterest今週分お願い」"
        ))
    except Exception as e:
        print(f"Yakuzen blog reminder error: {e}")


def send_sekisui_blog_reminder():
    try:
        user_id = os.environ['LINE_USER_ID']
        line_bot_api.push_message(user_id, TextSendMessage(
            text="🏠 【セキスイブログ更新日・木曜日】\n今日はセキスイブログの投稿日です！\n\n① 音声入力でネタを話す\n② テキストをClaudeに貼って👇\n「セキスイの記事投稿して」"
        ))
    except Exception as e:
        print(f"Sekisui blog reminder error: {e}")


def send_ebay_check_reminder():
    try:
        user_id = os.environ['LINE_USER_ID']
        line_bot_api.push_message(user_id, TextSendMessage(
            text="📦 【eBayチェック日・土曜日】\n今日はeBayの確認日です！\n\nClaudeに👇と声かけしてね\n「eBay状況確認して、次やること教えて」"
        ))
    except Exception as e:
        print(f"eBay check reminder error: {e}")


def send_a8_check_reminder():
    try:
        user_id = os.environ['LINE_USER_ID']
        line_bot_api.push_message(user_id, TextSendMessage(
            text="📧 【A8審査確認】\nA8の審査結果メールが届いていませんか？\n\n審査が通っていたらClaudeに👇\n「○○のA8審査通った。リンク追加して」\n\n確認待ち：\n・ユーキャン\n・がくぶん\n・ヒューマンアカデミー"
        ))
    except Exception as e:
        print(f"A8 check reminder error: {e}")


def send_monthly_review_reminder():
    try:
        user_id = os.environ['LINE_USER_ID']
        line_bot_api.push_message(user_id, TextSendMessage(
            text="📊 【月初振り返り】\n今日は先月の振り返り日です！\n\nClaudeに👇と声かけしてね\n「先月の副業収支まとめて」\n「薬膳・セキスイの今月進捗教えて」\n「eBay今月の売上と反省点まとめて」"
        ))
    except Exception as e:
        print(f"Monthly review reminder error: {e}")


scheduler = BackgroundScheduler(timezone='Asia/Tokyo')
scheduler.add_job(send_preparation_reminder, 'cron', hour=20, minute=0, day_of_week='sun')
scheduler.add_job(check_deadline_reminders, 'cron', hour=8, minute=0)
# 毎月1日朝9時：Famm更新リマインダー
scheduler.add_job(send_famm_reminder, 'cron', day=1, hour=9, minute=0)
# 毎月6日朝9時：Famm期限3日前リマインダー
scheduler.add_job(send_famm_deadline_reminder, 'cron', day=6, hour=9, minute=0)
# 毎週火曜朝9時：薬膳ブログ更新リマインダー
scheduler.add_job(send_yakuzen_blog_reminder, 'cron', day_of_week='tue', hour=9, minute=0)
# 毎週木曜朝9時：セキスイブログ更新リマインダー
scheduler.add_job(send_sekisui_blog_reminder, 'cron', day_of_week='thu', hour=9, minute=0)
# 毎週土曜朝9時：eBayチェックリマインダー
scheduler.add_job(send_ebay_check_reminder, 'cron', day_of_week='sat', hour=9, minute=0)
# 毎月1日朝9時30分：月初振り返りリマインダー（Fammリマインダーの30分後）
scheduler.add_job(send_monthly_review_reminder, 'cron', day=1, hour=9, minute=30)
# 毎週月曜朝9時：A8審査確認リマインダー（全審査通過後に削除してOK）
scheduler.add_job(send_a8_check_reminder, 'cron', day_of_week='mon', hour=9, minute=0)
scheduler.start()

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
