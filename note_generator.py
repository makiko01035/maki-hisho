import json
from linebot.models import TextSendMessage

from clients import line_bot_api, anthropic_client

NOTE_SESSION_FILE = '/tmp/note_sessions.json'


def load_note_sessions():
    try:
        with open(NOTE_SESSION_FILE, 'r') as f:
            return json.load(f)
    except Exception:
        return {}


def save_note_sessions(data):
    try:
        with open(NOTE_SESSION_FILE, 'w') as f:
            json.dump(data, f, ensure_ascii=False)
    except Exception as e:
        print(f"note_sessions save error: {e}")


def send_long_message(user_id, text, chunk_size=4000):
    """長いテキストを分割してLINEにpush送信"""
    chunks = [text[i:i+chunk_size] for i in range(0, len(text), chunk_size)]
    for i, chunk in enumerate(chunks):
        prefix = f"【{i+1}/{len(chunks)}】\n" if len(chunks) > 1 else ""
        line_bot_api.push_message(user_id, TextSendMessage(text=prefix + chunk))


def generate_note_draft_async(user_id, note_type, target=None, worry=None, experience=None):
    """note下書きをClaude APIで生成してLINEに分割送信"""
    try:
        type_label = "有料" if note_type == "paid" else "無料"
        if note_type == "paid":
            type_instruction = (
                "有料記事として書いてください。\n"
                "- 無料部分：導入・共感・この記事でわかること（全体の1/3程度）\n"
                "- 「ここから有料記事です（300円）」という区切りを入れる\n"
                "- 有料部分：再現性のある具体的な手順・プロンプト・実例を含める\n"
                "- 目標文字数：3,000〜4,000文字"
            )
        else:
            type_instruction = (
                "無料記事として書いてください。\n"
                "- 読みごたえがあり、SNSでシェアされるような内容\n"
                "- 体験談ベースで共感を呼ぶ構成\n"
                "- 目標文字数：1,500〜2,000文字"
            )

        design_info = ""
        if target:
            design_info += f"\n【届けたい読者】{target}"
        if worry:
            design_info += f"\n【読者の悩み】{worry}"
        if experience:
            design_info += f"\n【まきの体験エピソード】{experience}"

        response = anthropic_client.messages.create(
            model='claude-sonnet-4-6',
            max_tokens=4000,
            system=(
                "あなたはAI×日常生活×ワーママ実体験を発信する「まき」として書きます。\n"
                "プロフィール：医療職・3児ワンオペ・夫急逝後にプログラミングゼロからAIで毎日を仕組み化した\n"
                "読者：「AIって何に使うの？」と思っているワーママ・AI初心者・日常を楽にしたい人\n"
                "文体：ですます調・親しみやすい・体験談ベース・専門用語を使わない\n"
                "重要：読者の悩みへの共感から入り、まきの実体験を通じて「私にもできる」と感じてもらう構成にする\n"
                + type_instruction
            ),
            messages=[{
                'role': 'user',
                'content': f'以下の設計情報に基づいてnote{type_label}記事の下書きを書いてください。タイトルも含めて。\n{design_info}'
            }]
        )

        draft = response.content[0].text
        line_bot_api.push_message(user_id, TextSendMessage(
            text=f"📝 note{type_label}記事の下書きができました！\n↓をそのままnoteにコピペしてください👇"
        ))
        send_long_message(user_id, draft)
        if note_type == "paid":
            line_bot_api.push_message(user_id, TextSendMessage(
                text="✅ コピペ後、noteで「有料ライン」を「ここから有料記事です」の行に設定してから公開してください🎉"
            ))

    except Exception as e:
        line_bot_api.push_message(user_id, TextSendMessage(text=f"❌ 下書き生成エラー: {str(e)[:200]}"))
