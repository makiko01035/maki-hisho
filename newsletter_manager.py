import os
import json
import datetime
import requests

from clients import JST

NEWSLETTER_SESSION_FILE = '/tmp/newsletter_sessions.json'


def load_newsletter_sessions():
    try:
        with open(NEWSLETTER_SESSION_FILE, 'r') as f:
            return json.load(f)
    except Exception:
        return {}


def save_newsletter_sessions(data):
    try:
        with open(NEWSLETTER_SESSION_FILE, 'w') as f:
            json.dump(data, f, ensure_ascii=False)
    except Exception as e:
        print(f"newsletter_sessions save error: {e}")


def save_newsletter_to_notion(email):
    """メルマガ1件をNotionのメルマガDBに保存"""
    notion_token = os.environ.get('NOTION_TOKEN', '')
    headers = {
        "Authorization": f"Bearer {notion_token}",
        "Notion-Version": "2025-09-03",
        "Content-Type": "application/json"
    }
    today = datetime.datetime.now(JST).strftime('%Y-%m-%d')
    title = f"[{email.get('category', '')}] {email.get('from_name', email.get('from', ''))}"
    content = f"件名：{email.get('subject', '')}\n\n{email.get('summary', '')}"
    body = {
        "after": "323f8d6d-41de-809d-9e98-f9a5da8556a8",
        "children": [{
            "object": "block",
            "type": "to_do",
            "to_do": {
                "rich_text": [{"type": "text", "text": {"content": f"📧 {today} {title}\n{content}"}}],
                "checked": False
            }
        }]
    }
    requests.patch(
        "https://api.notion.com/v1/blocks/323f8d6d41de80dea66efad500806f69/children",
        headers=headers,
        json=body
    )
