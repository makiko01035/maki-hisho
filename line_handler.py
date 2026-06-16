import os
import json
import base64
import re
import datetime
import threading
import requests
from linebot.models import (
    TextSendMessage, ImageMessage, AudioMessage, FileMessage,
    MessageEvent, TextMessage,
)

from clients import line_bot_api, handler, anthropic_client, JST
from calendar_manager import (
    get_calendar_service, get_or_create_maybe_calendar,
    get_upcoming_events, format_events,
)
from newsletter_manager import (
    load_newsletter_sessions, save_newsletter_sessions, save_newsletter_to_notion,
)
from note_generator import load_note_sessions, save_note_sessions, generate_note_draft_async
from print_manager import load_prints, save_prints, load_print_sessions, save_print_sessions
from room_tagger import load_room_tag_sessions, save_room_tag_sessions, generate_room_tags
from blog_sekisui import suggest_sekisui_themes, process_sekisui_article
from blog_yakuzen import (
    auto_rewrite_yakuzen, process_yakuzen_new_article,
    rewrite_yakuzen_by_slug, rewrite_yakuzen_by_keyword,
    kw_auto_rewrite, kw_auto_new_article,
    delete_yakuzen_post, check_old_yakuzen_post,
)
from x_analytics import (
    send_weekly_seo_report, send_x_weekly_report, send_daily_work_log,
    add_diary_memo, get_google_creds, add_study_memo,
)
from ebay_handler import run_ebay_research, send_daily_purchase_candidates, check_seller_now
from sns_engine_koharu import handle_approval as koharu_handle_approval
from sns_engine_mako import handle_mako_approval
from purchase_receipt import (
    parse_receipt_with_vision, enrich_items_with_asin,
    format_confirm_message,
    append_to_amazon_sheet, append_to_mercari_sheet,
)

PENDING_FILE = '/tmp/pending_events.json'
SEKISUI_SESSION_FILE = '/tmp/sekisui_sessions.json'
YAKUZEN_SESSION_FILE = '/tmp/yakuzen_sessions.json'
PURCHASE_SESSION_FILE = '/tmp/purchase_sessions.json'

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


def load_yakuzen_sessions():
    try:
        with open(YAKUZEN_SESSION_FILE, 'r') as f:
            return json.load(f)
    except Exception:
        return {}


def save_yakuzen_sessions(data):
    try:
        with open(YAKUZEN_SESSION_FILE, 'w') as f:
            json.dump(data, f, ensure_ascii=False)
    except Exception as e:
        print(f"yakuzen_sessions save error: {e}")


def load_purchase_sessions():
    try:
        with open(PURCHASE_SESSION_FILE, 'r') as f:
            return json.load(f)
    except Exception:
        return {}


def save_purchase_sessions(data):
    try:
        with open(PURCHASE_SESSION_FILE, 'w') as f:
            json.dump(data, f, ensure_ascii=False)
    except Exception as e:
        print(f"purchase_sessions save error: {e}")


def _start_old_check(user_id, skip_ids):
    """古い記事チェックを開始してセッションにpost_idを保存"""
    yakuzen_sessions = load_yakuzen_sessions()
    post_id = check_old_yakuzen_post(user_id, skip_ids)
    if post_id:
        yakuzen_sessions[user_id] = {
            'state': 'waiting_for_old_rewrite_confirm',
            'post_id': post_id,
            'skip_ids': skip_ids
        }
        save_yakuzen_sessions(yakuzen_sessions)


def rewrite_yakuzen_by_post_id(user_id, post_id):
    """post_idを指定して記事をリライト"""
    import html as html_lib
    from blog_yakuzen import (get_yakuzen_wp_creds, generate_yakuzen_rewrite,
                               generate_pexels_keyword, fetch_pexels_image_url,
                               upload_image_to_yakuzen_wp, detect_category_id,
                               post_to_yakuzen_wp, try_post_to_pinterest, send_sns_messages)
    try:
        wp_url, wp_user, wp_pass = get_yakuzen_wp_creds()
        res = requests.get(f'{wp_url}/wp-json/wp/v2/posts/{post_id}',
                           auth=(wp_user, wp_pass),
                           params={'_fields': 'id,title,content'}, timeout=15)
        post = res.json()
        post_title = html_lib.unescape(post['title']['rendered'])
        post_content = post['content']['rendered']
        article_md = generate_yakuzen_rewrite(post_title, post_content)
        lines = article_md.split('\n')
        new_title = lines[0].lstrip('# ').strip()
        new_content = '\n'.join(lines[1:]).lstrip('\n')
        keyword = generate_pexels_keyword(new_title)
        image_url = fetch_pexels_image_url(keyword)
        media_id = upload_image_to_yakuzen_wp(image_url, new_title) if image_url else None
        new_cat_id = detect_category_id(new_title, new_content)
        _, link = post_to_yakuzen_wp(new_title, new_content, post_id=post_id, status='publish',
                                     featured_media_id=media_id, categories=[new_cat_id])
        try_post_to_pinterest(new_title, link, new_content, image_url=image_url)
        line_bot_api.push_message(user_id, TextSendMessage(text=f"✅ リライト完了！\n\n📝 {new_title}\n🔗 {link}"))
        send_sns_messages(user_id, new_title, link, image_url, new_content)
    except Exception as e:
        print(f"rewrite_yakuzen_by_post_id error: {e}")
        line_bot_api.push_message(user_id, TextSendMessage(text=f"😢 エラーが発生しました。\n{str(e)[:150]}"))


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

    # 楽天Roomタグセッションチェック
    room_tag_sessions = load_room_tag_sessions()
    if user_id in room_tag_sessions and room_tag_sessions[user_id] == 'waiting':
        del room_tag_sessions[user_id]
        save_room_tag_sessions(room_tag_sessions)
        try:
            tags = generate_room_tags(image_base64=image_base64, media_type=media_type)
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text=f"🏷️ 楽天Roomタグ\n\n{tags}"))
        except Exception as e:
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text=f"タグ生成エラー: {str(e)[:100]}"))
        return

    # プリントセッションチェック
    print_sessions = load_print_sessions()
    if user_id in print_sessions and print_sessions[user_id] == 'waiting_for_print_image':
        del print_sessions[user_id]
        save_print_sessions(print_sessions)
        try:
            response = anthropic_client.messages.create(
                model='claude-sonnet-4-6',
                max_tokens=1000,
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
                            'text': """これは学校や習い事から届いたプリント・お知らせです。
以下のJSON形式で情報を抽出してください（情報がない場合はnullにしてください）：
{
  "title": "プリント名・タイトル",
  "category": "カテゴリ（行事/提出物/集金/持ち物/アンケート/連絡/その他）",
  "deadline": "締切・提出期限（YYYY-MM-DD形式、ない場合はnull）",
  "amount": "集金額（例：500円、ない場合はnull）",
  "items": "持ち物・提出物の内容（ない場合はnull）",
  "notes": "その他重要なメモ（ない場合はnull）"
}
JSON形式のみ返してください。"""
                        }
                    ]
                }]
            )
            raw_text = response.content[0].text.strip()
            import re
            if '```' in raw_text:
                match = re.search(r'```(?:json)?\s*([\s\S]*?)```', raw_text)
                if match:
                    raw_text = match.group(1).strip()
            start = raw_text.find('{')
            end = raw_text.rfind('}')
            print_data = json.loads(raw_text[start:end+1])

            prints = load_prints()
            user_prints = prints.get(user_id, [])
            new_id = max([p['id'] for p in user_prints], default=0) + 1
            print_data['id'] = new_id
            print_data['created_at'] = datetime.date.today().isoformat()
            print_data['done'] = False
            user_prints.append(print_data)
            prints[user_id] = user_prints
            save_prints(prints)

            msg = f"📄 プリントを保存しました！（No.{new_id}）\n\n"
            msg += f"📌 {print_data.get('title') or '（タイトル不明）'}\n"
            msg += f"🏷️ {print_data.get('category') or '不明'}\n"
            if print_data.get('deadline'):
                msg += f"⚠️ 締切: {print_data['deadline']}\n"
            if print_data.get('amount'):
                msg += f"💴 集金: {print_data['amount']}\n"
            if print_data.get('items'):
                msg += f"🎒 持ち物: {print_data['items']}\n"
            if print_data.get('notes'):
                msg += f"📝 メモ: {print_data['notes']}\n"

            if print_data.get('deadline'):
                msg += f"\n「プリント登録 {new_id}」で締切をカレンダーに登録できます！"

            line_bot_api.reply_message(event.reply_token, TextSendMessage(text=msg))
        except Exception as e:
            print(f"Print extract error: {e}")
            import traceback; traceback.print_exc()
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text=f"プリントの読み取りに失敗しました😢\nエラー: {str(e)[:100]}"))
        return

    # 仕入れレシートセッションチェック
    purchase_sessions = load_purchase_sessions()
    if user_id in purchase_sessions and purchase_sessions[user_id].get('state') == 'waiting_for_receipt':
        target = purchase_sessions[user_id].get('target', 'amazon')
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text="📷 レシートを読み取り中です...少々お待ちください"))
        def _process_receipt(uid, img_b64, mt, tgt):
            try:
                items = parse_receipt_with_vision(anthropic_client, img_b64, mt)
                items = enrich_items_with_asin(items)
                if not items:
                    line_bot_api.push_message(uid, TextSendMessage(text="商品情報が読み取れませんでした😢\nもう一度鮮明な画像を送ってください"))
                    return
                ps = load_purchase_sessions()
                ps[uid] = {'state': 'waiting_for_confirm', 'target': tgt, 'items': items}
                save_purchase_sessions(ps)
                msg = format_confirm_message(items, tgt)
                line_bot_api.push_message(uid, TextSendMessage(text=msg))
            except Exception as e:
                print(f"Receipt parse error: {e}")
                import traceback; traceback.print_exc()
                line_bot_api.push_message(uid, TextSendMessage(text=f"読み取りに失敗しました😢\nエラー: {str(e)[:300]}"))
        threading.Thread(target=_process_receipt, args=(user_id, image_base64, media_type, target)).start()
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
                        'text': f"""このチラシやプリントから全てのイベント・日程情報を抽出してください。
複数の日程がある場合は全て抽出してください。
今日の日付: {datetime.datetime.now(JST).strftime('%Y-%m-%d')}
年が書かれていない日付は今日の年（{datetime.datetime.now(JST).year}年）を使ってください。ただし、今日の日付より前になる場合は翌年にしてください。
以下のJSON配列形式のみ返してください（情報がない場合はnullにしてください）：
[
  {{
    "title": "イベント名",
    "date": "YYYY-MM-DD",
    "start_time": "HH:MM",
    "end_time": "HH:MM",
    "location": "場所",
    "description": "その他メモ",
    "application_start": "YYYY-MM-DD",
    "application_deadline": "YYYY-MM-DD"
  }}
]
application_startは申込開始日・受付開始日・予約開始日などの日付です。ない場合はnullにしてください。
application_deadlineは申込締切・申込期限・締切日などの日付です。ない場合はnullにしてください。
必ずJSON配列（[...]）で返してください。"""
                    }
                ]
            }]
        )

        raw_text = response.content[0].text.strip()
        # Markdownコードブロックを除去
        if '```' in raw_text:
            import re
            match = re.search(r'```(?:json)?\s*([\s\S]*?)```', raw_text)
            if match:
                raw_text = match.group(1).strip()
        # JSON配列を抽出してパース
        start = raw_text.find('[')
        end = raw_text.rfind(']')
        if start == -1 or end == -1:
            # 配列がない場合はオブジェクトを探す
            start = raw_text.find('{')
            end = raw_text.rfind('}')
            json_str = raw_text[start:end+1]
            extracted_list = [json.loads(json_str)]
        else:
            json_str = raw_text[start:end+1]
            extracted_list = json.loads(json_str)
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
            if ev.get('application_start'):
                msg += f"　🟢 申込開始: {ev['application_start']}\n"
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


def run_transcription(user_id, audio_data, filename='audio.m4a'):
    try:
        groq_api_key = os.environ.get('GROQ_API_KEY', '')
        ext = filename.rsplit('.', 1)[-1].lower() if '.' in filename else 'm4a'
        mime = {'mp3': 'audio/mpeg', 'mp4': 'audio/mp4', 'wav': 'audio/wav', 'webm': 'audio/webm'}.get(ext, 'audio/m4a')
        files = {'file': (filename, audio_data, mime)}
        data = {'model': 'whisper-large-v3', 'language': 'ja', 'response_format': 'text'}
        resp = requests.post(
            'https://api.groq.com/openai/v1/audio/transcriptions',
            headers={'Authorization': f'Bearer {groq_api_key}'},
            files=files,
            data=data,
            timeout=60
        )
        if resp.status_code != 200:
            raise Exception(f"Groq error {resp.status_code}: {resp.text[:200]}")
        transcript = resp.text.strip()

        summary = anthropic_client.messages.create(
            model='claude-sonnet-4-6',
            max_tokens=1500,
            messages=[{
                'role': 'user',
                'content': f"""以下の音声文字起こしから議事録を作成してください。

【文字起こし】
{transcript}

【議事録フォーマット】
📋 議事録
日時：{datetime.datetime.now(JST).strftime('%Y年%m月%d日 %H:%M')}

■ 話題・内容
（要点を箇条書きで）

■ 決定事項
（決まったことを箇条書き、なければ「なし」）

■ ToDoリスト
（誰が何をするか、なければ「なし」）

---
📝 文字起こし原文
{transcript}"""
            }]
        )
        result = summary.content[0].text
        line_bot_api.push_message(user_id, TextSendMessage(text=result))
    except Exception as e:
        print(f"Transcription error: {e}")
        import traceback; traceback.print_exc()
        line_bot_api.push_message(user_id, TextSendMessage(text=f"文字起こしに失敗しました😢\n{str(e)[:150]}"))


@handler.add(MessageEvent, message=AudioMessage)
def handle_audio(event):
    message_id = event.message.id
    try:
        message_content = line_bot_api.get_message_content(message_id)
        audio_data = b''.join(chunk for chunk in message_content.iter_content())
    except Exception as e:
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=f"音声の取得に失敗しました😢\n{str(e)[:100]}"))
        return
    line_bot_api.reply_message(event.reply_token, TextSendMessage(text="🎤 音声を受け取りました！文字起こし中...少々お待ちください"))
    threading.Thread(target=run_transcription, args=(event.source.user_id, audio_data)).start()


@handler.add(MessageEvent, message=FileMessage)
def handle_file(event):
    filename = event.message.file_name
    ext = filename.rsplit('.', 1)[-1].lower() if '.' in filename else ''
    if ext not in ('mp3', 'mp4', 'm4a', 'wav', 'webm', 'ogg', 'flac'):
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=f"音声ファイル（mp3/m4a/wav等）を送ってください。\n受け取ったファイル: {filename}"))
        return
    message_id = event.message.id
    try:
        message_content = line_bot_api.get_message_content(message_id)
        audio_data = b''.join(chunk for chunk in message_content.iter_content())
    except Exception as e:
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=f"ファイルの取得に失敗しました😢\n{str(e)[:100]}"))
        return
    line_bot_api.reply_message(event.reply_token, TextSendMessage(text=f"🎤 {filename} を受け取りました！文字起こし中...少々お待ちください"))
    threading.Thread(target=run_transcription, args=(event.source.user_id, audio_data, filename)).start()


def _sanitize_text(text: str) -> str:
    # 孤立サロゲート文字を除去（Anthropic APIのJSON serialization失敗を防ぐ）
    return text.encode('utf-16', 'surrogatepass').decode('utf-16', errors='replace')


@handler.add(MessageEvent, message=TextMessage)
def handle_message(event):
    user_message = _sanitize_text(event.message.text)
    user_id = event.source.user_id

    # こはるまま投稿承認コマンド
    # 「こはるまま」「こはる」「コハルママ」「コハル」すべて受け付ける
    if (user_message.startswith('こはるまま') or user_message.startswith('こはる') or
            user_message.startswith('コハルママ') or user_message.startswith('コハル')):
        if koharu_handle_approval(user_message):
            return
        # 未対応の場合はそのまま通常処理へ

    # MAKO投稿承認コマンド
    # 「MAKO」「mako」「ＭＡＫＯ」「ｍａｋｏ」「まこ」すべて受け付ける
    if (user_message.upper().startswith('MAKO') or user_message.startswith('ＭＡＫＯ') or
            user_message.startswith('ｍａｋｏ') or user_message.startswith('まこ')):
        if handle_mako_approval(user_message):
            return
        # 未対応の場合はそのまま通常処理へ

    # メルマガ保存コマンド（「①③保存して」など）
    if '保存' in user_message and any(c in user_message for c in '①②③④⑤⑥⑦⑧⑨⑩0123456789'):
        sessions = load_newsletter_sessions()
        session = sessions.get(user_id)
        if not session:
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text='保存できるメルマガが見つかりませんでした。\nまずメルマガまとめを受け取ってから返信してください。'))
            return
        emails = session.get('emails', [])
        circle_map = {'①': 1, '②': 2, '③': 3, '④': 4, '⑤': 5, '⑥': 6, '⑦': 7, '⑧': 8, '⑨': 9, '⑩': 10}
        numbers = set()
        for char, num in circle_map.items():
            if char in user_message:
                numbers.add(num)
        import re as _re
        for m in _re.findall(r'\d+', user_message):
            n = int(m)
            if 1 <= n <= len(emails):
                numbers.add(n)
        if not numbers:
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text='番号が見つかりませんでした。\n例：「①③保存して」'))
            return
        saved = []
        circle_chars = '①②③④⑤⑥⑦⑧⑨⑩'
        for n in sorted(numbers):
            if n <= len(emails):
                save_newsletter_to_notion(emails[n - 1])
                saved.append(circle_chars[n - 1])
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=f'{"・".join(saved)}をNotionの今週やることに保存しました✅'))
        return

    # ユーザーIDを確認するコマンド
    if user_message == 'myid':
        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(text=f'あなたのユーザーID:\n{user_id}')
        )
        return

    # 社内ダッシュボード
    if user_message in ['会社', 'ダッシュボード']:
        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(text='📊 まきの会社 ダッシュボード\nhttps://maki-hisho.onrender.com/company')
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
    if user_message == '登録して' and user_id not in pending_events:
        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(text="📋 登録するチラシ画像が見つかりません。\n\nもう一度チラシの画像を送ってから「登録して」と送ってください！")
        )
        return
    if user_message == '登録して' and user_id in pending_events:
        extracted_list = pending_events.pop(user_id)
        save_pending_events(pending_events)
        try:
            service = get_calendar_service()
            cal_id = get_or_create_maybe_calendar(service)
            registered = []
            deadline_count = 0
            start_count = 0

            for extracted in extracted_list:
                if extracted.get('date') and extracted.get('start_time'):
                    start_dt = datetime.datetime.fromisoformat(f"{extracted['date']}T{extracted['start_time']}:00")
                    start_dt = JST.localize(start_dt)
                    if extracted.get('end_time'):
                        end_dt = datetime.datetime.fromisoformat(f"{extracted['date']}T{extracted['end_time']}:00")
                        end_dt = JST.localize(end_dt)
                        if end_dt <= start_dt:
                            end_dt += datetime.timedelta(days=1)
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

                app_start = extracted.get('application_start')
                if app_start:
                    start_event = {
                        'summary': f"【申込開始】{extracted.get('title') or 'イベント'}",
                        'description': '申込開始日です。忘れずに申し込みを！',
                        'start': {'date': app_start},
                        'end': {'date': app_start},
                    }
                    service.events().insert(calendarId=cal_id, body=start_event).execute()
                    start_count += 1

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
            if start_count:
                reply += f"\n🟢 申込開始日{start_count}件も登録しました！\n当日にお知らせします！"
            if deadline_count:
                reply += f"\n⚠️ 申込期限{deadline_count}件も登録しました！\n1週間前・3日前・前日・当日にお知らせします！"
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply))

        except Exception as e:
            import traceback
            tb = traceback.format_exc()
            print(f"Calendar insert error: {e}\n{tb}")
            # どのステップで失敗したか判別
            err_str = str(e)
            if 'GOOGLE_CREDENTIALS' in tb or ('json' in err_str.lower() and 'double quotes' in err_str.lower()):
                detail = "Google認証情報が壊れています😢\n\nRenderの GOOGLE_CREDENTIALS を credentials_for_render.txt の内容で更新してください。"
            elif 'HttpError' in err_str or 'googleapis' in err_str:
                detail = f"GoogleカレンダーAPIエラー:\n{err_str[:120]}"
            else:
                detail = err_str[:120]
            line_bot_api.reply_message(
                event.reply_token,
                TextSendMessage(text=f"登録中にエラーが発生しました😢\n{detail}")
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

    # ========== プリント管理 ==========

    # プリント一覧
    if user_message in ['プリント一覧', 'プリント確認', 'プリントリスト']:
        prints = load_prints()
        user_prints = [p for p in prints.get(user_id, []) if not p.get('done')]
        if not user_prints:
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text="📭 保存中のプリントはありません！\n「プリント」と送って写真を撮ると保存できます📄"))
        else:
            msg = f"📄 プリント一覧（{len(user_prints)}件）\n\n"
            for p in user_prints:
                msg += f"【No.{p['id']}】{p.get('title') or '（タイトル不明）'}\n"
                msg += f"  🏷️ {p.get('category') or '不明'}"
                if p.get('deadline'):
                    msg += f"  ⚠️ 締切:{p['deadline']}"
                if p.get('amount'):
                    msg += f"  💴{p['amount']}"
                msg += "\n"
            msg += "\n「プリント完了 番号」で完了済みにできます"
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text=msg))
        return

    # プリント完了
    import re as _re
    m = _re.match(r'^プリント完了\s*(\d+)$', user_message.strip())
    if m:
        target_id = int(m.group(1))
        prints = load_prints()
        user_prints = prints.get(user_id, [])
        found = False
        for p in user_prints:
            if p['id'] == target_id:
                p['done'] = True
                found = True
                break
        if found:
            save_prints(prints)
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text=f"✅ No.{target_id} を完了にしました！お疲れさまです🎉"))
        else:
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text=f"No.{target_id} が見つかりませんでした。「プリント一覧」で番号を確認してください。"))
        return

    # プリント締切をカレンダー登録
    m2 = _re.match(r'^プリント登録\s*(\d+)$', user_message.strip())
    if m2:
        target_id = int(m2.group(1))
        prints = load_prints()
        user_prints = prints.get(user_id, [])
        target_print = next((p for p in user_prints if p['id'] == target_id), None)
        if not target_print:
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text=f"No.{target_id} が見つかりませんでした。"))
            return
        if not target_print.get('deadline'):
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text=f"No.{target_id}「{target_print.get('title')}」には締切日がありません。"))
            return
        try:
            service = get_calendar_service()
            cal_id = get_or_create_maybe_calendar(service)
            deadline_event = {
                'summary': f"【プリント締切】{target_print.get('title') or 'プリント'}",
                'description': f"カテゴリ: {target_print.get('category') or ''}\n集金: {target_print.get('amount') or ''}\n持ち物: {target_print.get('items') or ''}",
                'start': {'date': target_print['deadline']},
                'end': {'date': target_print['deadline']},
            }
            service.events().insert(calendarId=cal_id, body=deadline_event).execute()
            line_bot_api.reply_message(event.reply_token, TextSendMessage(
                text=f"✅ カレンダーに登録しました！\n📌 {target_print.get('title')}\n⚠️ 締切: {target_print['deadline']}\n\n1週間前・3日前・前日にお知らせします！"
            ))
        except Exception as e:
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text=f"登録エラー: {str(e)[:100]}"))
        return

    # プリントモード開始（写真を待つ）
    print_trigger_keywords = ['プリント', 'プリントきた', 'プリント撮る', '学校のプリント', 'おたより', 'お知らせ来た']
    if any(kw in user_message for kw in print_trigger_keywords):
        print_sessions = load_print_sessions()
        print_sessions[user_id] = 'waiting_for_print_image'
        save_print_sessions(print_sessions)
        line_bot_api.reply_message(event.reply_token, TextSendMessage(
            text="📄 プリントの写真を送ってください！\n\n撮影のコツ：\n・平らに置いて撮る\n・文字がはっきり見えるように\n・プリント全体が入るように"
        ))
        return

    # 仕入れ記録トリガー
    purchase_trigger_keywords = ['仕入れ', '仕入れ記録', '仕入れ登録', '実店舗仕入れ', 'レシート入力']
    if any(kw in user_message for kw in purchase_trigger_keywords):
        purchase_sessions = load_purchase_sessions()
        purchase_sessions[user_id] = {'state': 'waiting_for_target'}
        save_purchase_sessions(purchase_sessions)
        line_bot_api.reply_message(event.reply_token, TextSendMessage(
            text="🛒 仕入れ記録\nどちらのリストに追加しますか？\n\n1. Amazon仕入れ管理\n2. メルカリ仕入れ管理"
        ))
        return

    # 仕入れ先選択（waiting_for_target状態のとき）
    purchase_sessions = load_purchase_sessions()
    if user_id in purchase_sessions and purchase_sessions[user_id].get('state') == 'waiting_for_target':
        if user_message in ['1', 'Amazon', 'amazon', 'Amazon仕入れ', 'amazon仕入れ']:
            purchase_sessions[user_id] = {'state': 'waiting_for_receipt', 'target': 'amazon'}
            save_purchase_sessions(purchase_sessions)
            line_bot_api.reply_message(event.reply_token, TextSendMessage(
                text="📦 Amazon仕入れリストに追加します。\nレシートの写真を送ってください📷"
            ))
            return
        elif user_message in ['2', 'メルカリ', 'mercari', 'メルカリ仕入れ', 'mercari仕入れ']:
            purchase_sessions[user_id] = {'state': 'waiting_for_receipt', 'target': 'mercari'}
            save_purchase_sessions(purchase_sessions)
            line_bot_api.reply_message(event.reply_token, TextSendMessage(
                text="📦 メルカリ仕入れリストに追加します。\nレシートの写真を送ってください📷"
            ))
            return

    # 仕入れ確認（OK / キャンセル）
    if user_id in purchase_sessions and purchase_sessions[user_id].get('state') == 'waiting_for_confirm':
        if user_message.upper() in ['OK', 'ＯＫ', 'ok', 'オーケー', '追加して']:
            target = purchase_sessions[user_id].get('target', 'amazon')
            items = purchase_sessions[user_id].get('items', [])
            del purchase_sessions[user_id]
            save_purchase_sessions(purchase_sessions)
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text="✅ スプレッドシートに追加中..."))
            def _append_items(uid, tgt, its):
                try:
                    if tgt == 'amazon':
                        count = append_to_amazon_sheet(its)
                        sheet_name = 'Amazon仕入れ管理'
                    else:
                        count = append_to_mercari_sheet(its)
                        sheet_name = 'メルカリ仕入れ管理'
                    line_bot_api.push_message(uid, TextSendMessage(
                        text=f"✅ {count}件を「{sheet_name}」に追加しました！\n\nhttps://docs.google.com/spreadsheets/d/1pPAVYxeETPq6VVtg7Jd7eapXZf8lgTttndRN6Cd4wqI/edit"
                    ))
                except Exception as e:
                    print(f"Append purchase error: {e}")
                    import traceback; traceback.print_exc()
                    line_bot_api.push_message(uid, TextSendMessage(text=f"追加に失敗しました😢\nエラー: {str(e)[:100]}"))
            threading.Thread(target=_append_items, args=(user_id, target, items)).start()
            return
        elif user_message in ['キャンセル', 'cancel', 'Cancel', 'やめる', 'やり直し']:
            del purchase_sessions[user_id]
            save_purchase_sessions(purchase_sessions)
            line_bot_api.reply_message(event.reply_token, TextSendMessage(
                text="キャンセルしました。\n「仕入れ」でもう一度始められます。"
            ))
            return

    # SEOレポート即時取得
    seo_keywords = ['SEOレポート', 'seoレポート', '流入確認', '流入みせて', 'ブログ流入', '検索流入']
    if any(kw in user_message for kw in seo_keywords):
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text="📊 Search Consoleを確認中です...少しお待ちください！"))
        threading.Thread(target=send_weekly_seo_report).start()
        return

    # Xレポート即時取得
    x_report_keywords = ['Xレポート', 'xレポート', 'X分析', 'x分析', 'ツイート分析', '投稿分析']
    if any(kw in user_message for kw in x_report_keywords):
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text="📊 X投稿のパフォーマンスを集計中です...少しお待ちください！"))
        threading.Thread(target=send_x_weekly_report).start()
        return

    # 業務ログ即時取得
    work_log_keywords = ['業務ログ', '今日のログ', '今日の作業', '作業ログ']
    if any(kw in user_message for kw in work_log_keywords):
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text="📋 今日の業務ログを確認中です..."))
        threading.Thread(target=send_daily_work_log).start()
        return

    # 日記メモ（「メモ」または「日記」改行形式 → Notionの今日の日記ページに追記）
    if user_message.startswith('メモ\n') or user_message.startswith('日記\n'):
        memo_text = user_message.split('\n', 1)[1].strip()
        if memo_text:
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text="📓 日記に追記中..."))
            def _add_diary():
                ok = add_diary_memo(memo_text)
                uid = os.environ.get('LINE_USER_ID', '')
                if uid:
                    if ok:
                        msg = "✅ 日記に追記しました！"
                    else:
                        msg = "❌ 追記に失敗しました\nRenderログで [diary] を検索して原因を確認してください"
                    line_bot_api.push_message(uid, TextSendMessage(text=msg))
            threading.Thread(target=_add_diary).start()
        else:
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text="📝 2行目に内容を書いてください\n例：\nメモ\n今日の外来、更年期が多かった\n\nまたは\n日記\n今日あったこと"))
        return

    # 勉強ノートへのメモ追加
    if user_message.startswith('メモ：') or user_message.startswith('メモ:'):
        memo_text = user_message.split('：', 1)[-1].split(':', 1)[-1].strip()
        if memo_text:
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text="✏️ 勉強ノートに追加中...2〜3分で反映されます"))
            def _add_memo():
                ok = add_study_memo(memo_text)
                uid = os.environ.get('LINE_USER_ID', '')
                if uid:
                    msg = "✅ 勉強ノートにメモを追加しました！" if ok else "❌ メモ追加に失敗しました"
                    line_bot_api.push_message(uid, TextSendMessage(text=msg))
            threading.Thread(target=_add_memo).start()
        return

    # Threadsネタ生成
    if any(kw in user_message for kw in ['スレッズネタ', 'threadsネタ', 'Threadsネタ', 'スレッドネタ']):
        try:
            import random
            genre = random.choice(['育児・子育て', '美容・スキンケア', '収納・暮らし', '睡眠・健康', '節約・お買い物'])
            prompt = (
                f"あなたは3人の子どもを育てるワーママ（医療職・副業中）です。\n"
                f"今日のThreadsネタジャンル：{genre}\n\n"
                "以下の3パターンのThreads投稿文を作ってください。\n"
                "それぞれ120字以内・ですます調NG・体言止めや口語OK・ハッシュタグなし・リアルな体験談ベースで。\n\n"
                "①【共感型】育児・生活のあるあるや気づき（共感を呼ぶ）\n"
                "②【レビュー型】買って使ってみた正直な感想（購買意欲を高める）\n"
                "③【日常型】今日あったこと・思ったこと（親しみやすさ・フォロワー獲得）\n\n"
                "出力形式：\n"
                "①（共感型）\n投稿文\n\n②（レビュー型）\n投稿文\n\n③（日常型）\n投稿文\n\n"
                "余計な説明不要。投稿文だけ出力。"
            )
            resp = anthropic_client.messages.create(
                model='claude-haiku-4-5-20251001',
                max_tokens=600,
                messages=[{'role': 'user', 'content': prompt}]
            )
            ideas = resp.content[0].text.strip()
            reply = f"🧵 今日のThreadsネタ（{genre}）\n\n{ideas}\n\n👆コピペして投稿してみて！\nいいね・コメントきたら教えてね📊"
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply))
        except Exception as e:
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text=f"ネタ生成エラー: {str(e)[:100]}"))
        return

    # 楽天Roomタグ生成
    room_tag_sessions = load_room_tag_sessions()
    if any(kw in user_message for kw in ['roomタグ', 'Roomタグ', 'ルームタグ', 'roomハッシュ']):
        keyword = None
        for sep in ['roomタグ', 'Roomタグ', 'ルームタグ', 'roomハッシュ']:
            if sep in user_message:
                rest = user_message.split(sep, 1)[1].strip()
                if rest:
                    keyword = rest
                break
        if keyword:
            try:
                tags = generate_room_tags(text=keyword)
                line_bot_api.reply_message(event.reply_token, TextSendMessage(text=f"🏷️ 楽天Roomタグ\n\n{tags}"))
            except Exception as e:
                line_bot_api.reply_message(event.reply_token, TextSendMessage(text=f"タグ生成エラー: {str(e)[:100]}"))
        else:
            room_tag_sessions[user_id] = 'waiting'
            save_room_tag_sessions(room_tag_sessions)
            line_bot_api.reply_message(event.reply_token, TextSendMessage(
                text="🏷️ 商品名を送るか、商品の写真を送ってください！\nハッシュタグを作ります📦"
            ))
        return

    if user_id in room_tag_sessions and room_tag_sessions[user_id] == 'waiting':
        del room_tag_sessions[user_id]
        save_room_tag_sessions(room_tag_sessions)
        try:
            tags = generate_room_tags(text=user_message)
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text=f"🏷️ 楽天Roomタグ\n\n{tags}"))
        except Exception as e:
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text=f"タグ生成エラー: {str(e)[:100]}"))
        return

    # eBayヘルプ
    if user_message in ['eBayヘルプ', 'ebayヘルプ', 'eBay ヘルプ', '物販ヘルプ']:
        msg = (
            "【eBayコマンド一覧】\n\n"
            "・ebayリサーチ\n　→ 今日の仕入れ候補5件\n\n"
            "・仕入れ候補\n　→ 同上\n\n"
            "・ebay サンリオ（ジャンル指定）\n　→ そのジャンルの売れ筋を検索\n\n"
            "・セラーチェック：username\n　→ セラーの売れた商品を確認\n\n"
            "・eBayタイトル作って：商品名\n　→ 英語タイトルを生成"
        )
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=msg))
        return

    # 仕入れ候補 即時実行（テスト・デバッグ兼用）
    if user_message in ['仕入れ候補', '仕入れ候補テスト', '仕入れリサーチ']:
        threading.Thread(
            target=send_daily_purchase_candidates, args=(user_id,), daemon=True
        ).start()
        return

    # セラーチェック（即時）
    seller_check_prefixes = ['セラーチェック：', 'セラーチェック:', 'セラー確認：', 'セラー確認:']
    for prefix in seller_check_prefixes:
        if user_message.startswith(prefix):
            seller_name = user_message[len(prefix):].strip()
            if seller_name:
                threading.Thread(target=check_seller_now, args=(user_id, seller_name), daemon=True).start()
            return

    # eBayリサーチ
    ebay_research_keywords = ['eBayリサーチ', 'ebayリサーチ', 'eBay リサーチ', 'eBayリサーチして', '物販リサーチ', 'リサーチして']
    msg_lower = user_message.lower()
    is_ebay_research = any(kw in user_message for kw in ebay_research_keywords)
    # 「ebay:〇〇」「eBay：〇〇」「ebay 〇〇」形式も検出
    if not is_ebay_research and msg_lower.startswith('ebay'):
        rest = user_message[4:].lstrip('： :　 ')
        if rest:
            is_ebay_research = True
    if is_ebay_research:
        # 「eBayリサーチ：〇〇」「ebay:〇〇」「ebay 〇〇」形式で条件指定があれば抽出
        user_query = None
        for sep in ['：', ':']:
            if sep in user_message:
                parts = user_message.split(sep, 1)
                if len(parts) == 2 and parts[1].strip():
                    user_query = parts[1].strip()
                    break
        # 「ebay 〇〇」形式（コロンなし・スペース区切り）
        if not user_query and msg_lower.startswith('ebay'):
            rest = user_message[4:].lstrip('　 ')
            if rest and not any(kw in rest for kw in ['リサーチ', 'research']):
                user_query = rest.strip()
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text="📦 eBayリサーチを開始します！\n結果が届くまで2〜3分お待ちください🔍"))
        threading.Thread(target=run_ebay_research, args=(user_id, user_query)).start()
        return

    # セキスイブログ：キーワード検出 → テーマ提案
    # note下書き：セッションチェック
    note_sessions = load_note_sessions()
    if user_id in note_sessions:
        session = note_sessions[user_id]
        state = session.get('state')

        if state == 'waiting_for_note_type':
            import unicodedata
            normalized = unicodedata.normalize('NFKC', user_message)
            if any(kw in normalized for kw in ['有料', '1', '①']):
                note_sessions[user_id] = {'state': 'waiting_for_note_target', 'type': 'paid'}
                save_note_sessions(note_sessions)
                line_bot_api.reply_message(event.reply_token, TextSendMessage(
                    text="💰 有料記事ですね！\n\n【Step1】誰に届けたい記事ですか？\n\n例：「AIって何に使うかわからないワーママ」「忙しくて新しいことを始める余裕がない人」\n（「おまかせ」でもOK）"
                ))
            elif any(kw in normalized for kw in ['無料', '2', '②']):
                note_sessions[user_id] = {'state': 'waiting_for_note_target', 'type': 'free'}
                save_note_sessions(note_sessions)
                line_bot_api.reply_message(event.reply_token, TextSendMessage(
                    text="📖 無料記事ですね！\n\n【Step1】誰に届けたい記事ですか？\n\n例：「AIって何に使うかわからないワーママ」「忙しくて新しいことを始める余裕がない人」\n（「おまかせ」でもOK）"
                ))
            else:
                del note_sessions[user_id]
                save_note_sessions(note_sessions)
                line_bot_api.reply_message(event.reply_token, TextSendMessage(text="キャンセルしました。"))
            return

        if state == 'waiting_for_note_target':
            if user_message in ['おまかせ', 'おまかせで']:
                target = 'AIって何に使うの？と思っているワーママ・AI初心者'
            else:
                target = user_message
            note_sessions[user_id] = {
                'state': 'waiting_for_note_worry',
                'type': session.get('type', 'paid'),
                'target': target
            }
            save_note_sessions(note_sessions)
            line_bot_api.reply_message(event.reply_token, TextSendMessage(
                text=f"【Step2】その人の一番の悩みは何ですか？\n\n例：「毎日パンクしてるけどAIは難しそう」「何から始めればいいかわからない」\n（「おまかせ」でもOK）"
            ))
            return

        if state == 'waiting_for_note_worry':
            if user_message in ['おまかせ', 'おまかせで']:
                worry = 'AIは難しそう・何に使えばいいかわからない・でも何か変えたい'
            else:
                worry = user_message
            note_sessions[user_id] = {
                'state': 'waiting_for_note_experience',
                'type': session.get('type', 'paid'),
                'target': session.get('target', ''),
                'worry': worry
            }
            save_note_sessions(note_sessions)
            line_bot_api.reply_message(event.reply_token, TextSendMessage(
                text=f"【Step3】まきさんのどんな体験エピソードとつながりますか？\n\n例：「夫急逝後にYohanaを試してAIに行き着いた話」「先生に教えてもらって1か月課金してみた話」\n（「おまかせ」でもOK）"
            ))
            return

        if state == 'waiting_for_note_experience':
            if user_message in ['おまかせ', 'おまかせで']:
                experience = (
                    "以下のエピソードから記事テーマに合うものを選んでください：\n"
                    "・毎朝7時にLINEで今日の予定が届く。Googleカレンダーを確認しに行く手間がゼロ\n"
                    "・学校のプリントをLINEに写真で送るだけで日時・場所・申込期限が自動でカレンダーに登録される\n"
                    "・LINEで「〇〇の期限 5月10日」と打つだけで1週間前〜当日まで自動リマインド\n"
                    "・プログラミングゼロでも3日後に動くものができた（エラーはコピペして「直して」と言うだけ）\n"
                    "・APIとかRenderとかGitHubとか全部意味不明のまま進めたが、言われた通りにやったら動いた\n"
                    "・Googleカレンダーの通知は別のことをしていると流れてしまう→LINEは届くから忘れない\n"
                    "・家にも秘書がいてくれたらと思っていたが、AIで月ほぼ0円で実現した\n"
                    "・Yohanaを試したが続かなかった→AIは言い直しやすくて相性が良かった\n"
                    "・不動産投資の講師の先生にClaude Codeを教えてもらって「困っていることをそのまま話すだけ」という言葉で試してみた\n"
                    "重要：AI副業・収益化・コード技術の話は控えめに。「日常が楽になった」視点を中心にしてください。"
                )
            else:
                experience = user_message
            note_type = session.get('type', 'paid')
            target = session.get('target', '')
            worry = session.get('worry', '')
            del note_sessions[user_id]
            save_note_sessions(note_sessions)
            type_label = "有料" if note_type == "paid" else "無料"
            line_bot_api.reply_message(event.reply_token, TextSendMessage(
                text=f"✍️ note{type_label}記事の下書きを作成中です...\n少しお待ちください（1〜2分かかります）"
            ))
            threading.Thread(target=generate_note_draft_async, args=(user_id, note_type, target, worry, experience)).start()
            return

    # note公開済み報告 → NOTE_PUBLISHED_TITLESを次のラインナップ記事で自動更新
    if user_message in ['note公開した', 'note公開', '公開した']:
        published_count = len(NOTE_PUBLISHED_TITLES)
        if published_count < len(NOTE_LINEUP):
            next_item = NOTE_LINEUP[published_count]
            type_label = "無料" if next_item["type"] == "free" else "有料"
            line_bot_api.reply_message(event.reply_token, TextSendMessage(
                text=f"🎉 お疲れ様でした！\n\n次の記事はこれです👇\n▶ [{type_label}] {next_item['title']}\n\n「note書きたい」で下書きを作れます✨\n（NOTE_PUBLISHED_TITLESへの記録はClaude Codeで更新します）"
            ))
        else:
            line_bot_api.reply_message(event.reply_token, TextSendMessage(
                text="🎉 全ラインナップ公開完了！すごい！\n次のシリーズ設計はClaude Codeに相談してください✨"
            ))
        return

    # noteキーワード
    note_keywords = ['note書きたい', 'note下書き', 'note記事', 'note作りたい', 'noteかきたい']
    if any(kw in user_message for kw in note_keywords):
        note_sessions[user_id] = {'state': 'waiting_for_note_type'}
        save_note_sessions(note_sessions)
        line_bot_api.reply_message(event.reply_token, TextSendMessage(
            text="📝 note記事を作りましょう！\n\nどちらにしますか？\n\n1️⃣ 有料記事（300〜500円・テクニック系）\n2️⃣ 無料記事（体験談・共感系）\n\n番号か言葉で教えてください！"
        ))
        return

    sekisui_keywords = ['セキスイ記事', 'セキスイブログ', 'セキスイ 記事', 'order-sekisui']
    if any(kw in user_message for kw in sekisui_keywords):
        themes = suggest_sekisui_themes()
        sekisui_sessions[user_id] = 'waiting_for_content'
        save_sekisui_sessions(sekisui_sessions)
        msg = f"🏠 セキスイブログ記事を作りましょう！\n\nテーマ候補：\n{themes}\n\n番号と実体験・エピソードを一緒に教えてください！\n例：「2番で。先月の電気代が想像より安くて驚いた」"
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=msg))
        return

    # 睡眠ブログ：セッションチェック
    yakuzen_sessions = load_yakuzen_sessions()
    if user_id in yakuzen_sessions:
        session = yakuzen_sessions[user_id]
        state = session.get('state')

        if state == 'waiting_for_mode':
            import unicodedata
            normalized = unicodedata.normalize('NFKC', user_message)
            if any(kw in normalized for kw in ['新規', '作成', '新しい', '1', '①']):
                del yakuzen_sessions[user_id]
                save_yakuzen_sessions(yakuzen_sessions)
                line_bot_api.reply_message(event.reply_token, TextSendMessage(
                    text="✍️ 今の季節・人気ワードからテーマを自動選定して記事を作成します！\n少しお待ちください（1〜2分かかります）"
                ))
                threading.Thread(target=process_yakuzen_new_article, args=(user_id,)).start()
            elif any(kw in normalized for kw in ['リライト', '更新', '既存', '2', '②']):
                yakuzen_sessions[user_id] = {'state': 'waiting_for_rewrite_target'}
                save_yakuzen_sessions(yakuzen_sessions)
                line_bot_api.reply_message(event.reply_token, TextSendMessage(
                    text="🌿 リライトします！\n\n・URLを貼り付ける\n・キーワードを入力（例：アーユルヴェーダ、花粉症）\n・「自動」で季節に合った記事を自動選択"
                ))
            elif any(kw in normalized for kw in ['テーマ', '指定', '自分', '3', '③']):
                yakuzen_sessions[user_id] = {'state': 'waiting_for_new_topic'}
                save_yakuzen_sessions(yakuzen_sessions)
                line_bot_api.reply_message(event.reply_token, TextSendMessage(
                    text="✍️ テーマを入力してください！\n例：「更年期の不眠」「寝つきが悪い30代女性」「なつめで睡眠改善」"
                ))
            elif any(kw in normalized for kw in ['古い', '4', '④', '古い記事']):
                del yakuzen_sessions[user_id]
                save_yakuzen_sessions(yakuzen_sessions)
                line_bot_api.reply_message(event.reply_token, TextSendMessage(
                    text="🔍 一番古い記事を確認しています...少しお待ちください！"
                ))
                threading.Thread(target=_start_old_check, args=(user_id, [])).start()
            elif any(kw in normalized for kw in ['KW選定', 'KWリライト', 'kw選定', '5', '⑤']):
                del yakuzen_sessions[user_id]
                save_yakuzen_sessions(yakuzen_sessions)
                line_bot_api.reply_message(event.reply_token, TextSendMessage(
                    text="🔍 Search ConsoleでKW分析→リライト全自動で開始します！\n少しお待ちください！"
                ))
                creds = get_google_creds()
                threading.Thread(target=kw_auto_rewrite, args=(user_id, creds)).start()
            elif any(kw in normalized for kw in ['KW新規', 'kw新規', '6', '⑥']):
                del yakuzen_sessions[user_id]
                save_yakuzen_sessions(yakuzen_sessions)
                line_bot_api.reply_message(event.reply_token, TextSendMessage(
                    text="🔍 Search ConsoleでKW分析→新規記事全自動で開始します！\n少しお待ちください！"
                ))
                creds = get_google_creds()
                threading.Thread(target=kw_auto_new_article, args=(user_id, creds)).start()
            else:
                line_bot_api.reply_message(event.reply_token, TextSendMessage(
                    text="「1」か「新規作成」、または「2」か「リライト」と送ってください！\n（テーマ指定は「3」、古い記事チェックは「4」、KW選定リライトは「5」、KW選定新規は「6」）"
                ))
            return

        elif state == 'waiting_for_rewrite_target':
            del yakuzen_sessions[user_id]
            save_yakuzen_sessions(yakuzen_sessions)
            import unicodedata
            normalized = unicodedata.normalize('NFKC', user_message)
            if '自動' in normalized or normalized.strip() in ['auto', '']:
                line_bot_api.reply_message(event.reply_token, TextSendMessage(
                    text="🌿 季節に合った記事を自動選択してリライトします！\n数分かかります..."
                ))
                threading.Thread(target=auto_rewrite_yakuzen, args=(user_id,)).start()
            elif 'foodmakehealth.com' in user_message:
                slug = user_message.strip().rstrip('/').split('/')[-1]
                line_bot_api.reply_message(event.reply_token, TextSendMessage(
                    text=f"✍️ 「{slug}」の記事をリライト中です...少しお待ちください！"
                ))
                threading.Thread(target=rewrite_yakuzen_by_slug, args=(user_id, slug)).start()
            else:
                keyword = user_message.strip()
                line_bot_api.reply_message(event.reply_token, TextSendMessage(
                    text=f"🔍 「{keyword}」で記事を検索してリライトします...少しお待ちください！"
                ))
                threading.Thread(target=rewrite_yakuzen_by_keyword, args=(user_id, keyword)).start()
            return

        elif state == 'waiting_for_new_topic':
            del yakuzen_sessions[user_id]
            save_yakuzen_sessions(yakuzen_sessions)
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text="✍️ 睡眠記事を作成中です...少しお待ちください！（1〜2分かかります）"))
            threading.Thread(target=process_yakuzen_new_article, args=(user_id, user_message)).start()
            return

        elif state == 'waiting_for_old_rewrite_confirm':
            import unicodedata
            normalized = unicodedata.normalize('NFKC', user_message)
            post_id = session.get('post_id')
            skip_ids = session.get('skip_ids', [])

            if any(kw in normalized for kw in ['リライト', '1', '①']):
                del yakuzen_sessions[user_id]
                save_yakuzen_sessions(yakuzen_sessions)
                line_bot_api.reply_message(event.reply_token, TextSendMessage(
                    text="✍️ リライト中です...少しお待ちください！"
                ))
                threading.Thread(target=rewrite_yakuzen_by_post_id, args=(user_id, post_id)).start()

            elif any(kw in normalized for kw in ['スキップ', '次', '2', '②']):
                new_skip = skip_ids + [post_id]
                del yakuzen_sessions[user_id]
                save_yakuzen_sessions(yakuzen_sessions)
                line_bot_api.reply_message(event.reply_token, TextSendMessage(
                    text="⏭ スキップして次の記事を確認します..."
                ))
                threading.Thread(target=_start_old_check, args=(user_id, new_skip)).start()

            elif any(kw in normalized for kw in ['削除', '3', '③']):
                from blog_yakuzen import delete_yakuzen_post
                delete_yakuzen_post(post_id)
                new_skip = skip_ids
                del yakuzen_sessions[user_id]
                save_yakuzen_sessions(yakuzen_sessions)
                line_bot_api.reply_message(event.reply_token, TextSendMessage(
                    text="🗑 削除しました。次の記事を確認します..."
                ))
                threading.Thread(target=_start_old_check, args=(user_id, new_skip)).start()

            elif any(kw in normalized for kw in ['やめる', '終わり', '4', '④', 'やめ', '終了']):
                del yakuzen_sessions[user_id]
                save_yakuzen_sessions(yakuzen_sessions)
                line_bot_api.reply_message(event.reply_token, TextSendMessage(
                    text="✅ 古い記事チェックを終了しました！お疲れ様でした。"
                ))
            else:
                line_bot_api.reply_message(event.reply_token, TextSendMessage(
                    text="1️⃣ リライト / 2️⃣ スキップ / 3️⃣ 削除 / 4️⃣ やめる\nで返答してください！"
                ))
            return


    # 睡眠記事（睡眠ブログ）：キーワード検出
    yakuzen_keywords = ['睡眠記事', '薬膳記事', '薬膳ブログ', '薬膳 記事', '薬膳リライト', 'foodmakehealth', '薬膳の記事']
    if any(kw in user_message for kw in yakuzen_keywords):
        yakuzen_sessions[user_id] = {'state': 'waiting_for_mode'}
        save_yakuzen_sessions(yakuzen_sessions)
        msg = "🌙 睡眠記事、何をしますか？\n\n1️⃣ 新規作成（季節・人気ワードからテーマ自動決定）\n2️⃣ リライト（既存記事を更新）\n3️⃣ テーマ指定で新規作成\n4️⃣ 古い記事チェック＆リライト\n5️⃣ KW選定→リライト（Search Console分析→全自動）\n6️⃣ KW選定→新規記事（Search Console分析→全自動）\n\n番号か言葉で教えてください！"
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


# noteの投稿計画（セット単位：無料1〜2本＋有料1本 = 1セット、月2セット）
NOTE_LINEUP = [
    # セット1
    {"set": 1, "type": "free", "title": "毎朝7時に予定が届くようになって、朝が変わった話"},
    {"set": 1, "type": "free", "title": "学校のプリントに追われなくなった話"},
    {"set": 1, "type": "paid", "title": "毎朝7時のLINE通知を作るまでの全手順（300円）"},
    # セット2
    {"set": 2, "type": "free", "title": "忘れるのは意志の問題じゃなかった話"},
    {"set": 2, "type": "free", "title": "エラーが怖くなくなった日のこと"},
    {"set": 2, "type": "paid", "title": "チラシ写真→カレンダー自動登録の作り方（300円）"},
    # セット3
    {"set": 3, "type": "free", "title": "AIに役割を与えたら使い方が変わった話"},
    {"set": 3, "type": "paid", "title": "締切リマインダーを月0円で作る方法（300円）"},
]

# 公開済みnote記事（公開したら末尾に追加する）
NOTE_PUBLISHED_TITLES = [
    "秘書が欲しかった私が、プログラミングゼロでAIに話しかけたら毎日が変わった話（300円）",  # 2026-05-16
]

