# -*- coding: utf-8 -*-
"""
実店舗仕入れレシートOCR → Googleスプレッドシート追記モジュール
LINEでレシート画像を送ると Amazon or メルカリ仕入れリストに自動追加する
"""
import os
import re
import json
import datetime
import requests
from googleapiclient.discovery import build
from google.oauth2.credentials import Credentials as GCreds

SPREADSHEET_ID = "1pPAVYxeETPq6VVtg7Jd7eapXZf8lgTttndRN6Cd4wqI"
AMAZON_SHEET   = "Amazon仕入れ管理"
MERCARI_SHEET  = "メルカリ仕入れ管理"

MERCARI_HEADER = [
    "No.", "商品名", "仕入れ先", "仕入れ価格(円)",
    "メルカリ売値(円)", "利益率(%)【手動】", "仕入れ日", "ステータス", "メモ"
]


def _get_sheets_creds():
    raw = os.environ.get('GOOGLE_SHEETS_TOKEN', '')
    try:
        clean = re.sub(r'[\x00-\x1f\x7f]', '', raw) if raw else ''
        data = json.loads(clean) if clean else json.load(open('token_sheets.json', encoding='utf-8'))
        resp = requests.post(
            'https://oauth2.googleapis.com/token',
            data={
                'client_id':     data['client_id'],
                'client_secret': data['client_secret'],
                'refresh_token': data['refresh_token'],
                'grant_type':    'refresh_token',
            },
            timeout=10,
        )
        token = resp.json().get('access_token')
        return GCreds(token=token) if token else None
    except Exception as e:
        print(f"[purchase_receipt] sheets creds error: {e}")
        return None


def parse_receipt_with_vision(anthropic_client, image_base64: str, media_type: str) -> list[dict]:
    """Claude Visionでレシートから商品情報を抽出する。複数商品に対応。"""
    today = datetime.date.today().strftime('%Y/%m/%d')
    response = anthropic_client.messages.create(
        model='claude-sonnet-4-6',
        max_tokens=1500,
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
                    'text': f"""これは実店舗での仕入れレシートです。
商品情報をすべて抽出してJSON配列で返してください。
今日の日付（レシートに日付がない場合のデフォルト）: {today}

以下のJSON配列形式のみ返してください（説明文は不要）:
[
  {{
    "name": "商品名（できるだけ正確に）",
    "price": 金額（数字のみ・円記号なし）,
    "store": "店舗名（レシートから読み取る）",
    "date": "YYYY/MM/DD形式の仕入れ日"
  }}
]
・複数商品があれば全て含める
・金額は税込み価格（小計行や合計行は除外）
・商品名が読み取れない場合は「商品名不明」とする"""
                }
            ]
        }]
    )
    raw = response.content[0].text.strip()
    if '```' in raw:
        m = re.search(r'```(?:json)?\s*([\s\S]*?)```', raw)
        if m:
            raw = m.group(1).strip()
    start = raw.find('[')
    end = raw.rfind(']')
    return json.loads(raw[start:end + 1])


def format_confirm_message(items: list[dict], target: str) -> str:
    label = 'Amazon仕入れ' if target == 'amazon' else 'メルカリ仕入れ'
    lines = [f'📦 読み取り結果（{label}リスト）\n{"━" * 20}']
    for i, item in enumerate(items, 1):
        lines.append(
            f'【{i}】{item["name"]}\n'
            f'    店舗: {item["store"]}\n'
            f'    価格: {item["price"]:,}円\n'
            f'    日付: {item["date"]}'
        )
    lines.append('━' * 20)
    lines.append('「OK」で追加 ／ 「キャンセル」でやり直し')
    return '\n'.join(lines)


def append_to_amazon_sheet(items: list[dict]) -> int:
    """Amazon仕入れ管理シートの末尾に商品を追記する。追加した件数を返す。"""
    creds = _get_sheets_creds()
    if not creds:
        raise RuntimeError('Googleスプレッドシート認証に失敗しました')

    service = build('sheets', 'v4', credentials=creds)
    sheet = service.spreadsheets()

    result = sheet.values().get(
        spreadsheetId=SPREADSHEET_ID,
        range=f'{AMAZON_SHEET}!A:A',
    ).execute()
    existing = result.get('values', [])
    next_no = len(existing)  # ヘッダー行含む行数 = 次のNo.

    rows = []
    for i, item in enumerate(items):
        no = next_no + i
        row_num = no + 1  # スプレッドシートの行番号（1始まり）
        row = [
            str(no),                   # A: No.
            '',                        # B: メーカー（空欄）
            item.get('store', ''),     # C: 仕入れ先
            item.get('name', ''),      # D: 商品名
            '',                        # E: ASIN（空欄）
            str(item.get('price', '')),# F: 仕入れ価格
            '',                        # G: Amazon売値（空欄）
            '8',                       # H: 紹介料率(%)
            f'=IF(OR(G{row_num}="",H{row_num}=""),"",ROUND(G{row_num}*H{row_num}/100,0))',  # I: 紹介料
            '',                        # J: FBA手数料（空欄）
            f'=IF(OR(G{row_num}="",J{row_num}=""),"",G{row_num}-F{row_num}-I{row_num}-J{row_num})',  # K: 粗利
            f'=IF(OR(K{row_num}="",G{row_num}="",G{row_num}=0),"",ROUND(K{row_num}/G{row_num}*100,1))',  # L: 利益率
            'スポット',                # M: ルーチン区分
            '1回限り',                 # N: 仕入れ頻度
            item.get('date', ''),      # O: 最終仕入れ日
            '書類待ち',                # P: ステータス
            '',                        # Q: メモ
        ]
        rows.append(row)

    sheet.values().append(
        spreadsheetId=SPREADSHEET_ID,
        range=f'{AMAZON_SHEET}!A1',
        valueInputOption='USER_ENTERED',
        insertDataOption='INSERT_ROWS',
        body={'values': rows},
    ).execute()
    return len(rows)


def append_to_mercari_sheet(items: list[dict]) -> int:
    """メルカリ仕入れ管理シートの末尾に商品を追記する。シートがなければ作成。"""
    creds = _get_sheets_creds()
    if not creds:
        raise RuntimeError('Googleスプレッドシート認証に失敗しました')

    service = build('sheets', 'v4', credentials=creds)
    sheet = service.spreadsheets()

    meta = sheet.get(spreadsheetId=SPREADSHEET_ID).execute()
    titles = [s['properties']['title'] for s in meta['sheets']]
    if MERCARI_SHEET not in titles:
        service.spreadsheets().batchUpdate(
            spreadsheetId=SPREADSHEET_ID,
            body={'requests': [{'addSheet': {'properties': {'title': MERCARI_SHEET}}}]},
        ).execute()
        sheet.values().update(
            spreadsheetId=SPREADSHEET_ID,
            range=f'{MERCARI_SHEET}!A1',
            valueInputOption='RAW',
            body={'values': [MERCARI_HEADER]},
        ).execute()

    result = sheet.values().get(
        spreadsheetId=SPREADSHEET_ID,
        range=f'{MERCARI_SHEET}!A:A',
    ).execute()
    existing = result.get('values', [])
    next_no = len(existing)

    rows = []
    for i, item in enumerate(items):
        no = next_no + i
        row = [
            str(no),
            item.get('name', ''),
            item.get('store', ''),
            str(item.get('price', '')),
            '',   # メルカリ売値（後で手入力）
            '',   # 利益率（後で手入力）
            item.get('date', ''),
            '仕入れ済み',
            '',
        ]
        rows.append(row)

    sheet.values().append(
        spreadsheetId=SPREADSHEET_ID,
        range=f'{MERCARI_SHEET}!A1',
        valueInputOption='RAW',
        insertDataOption='INSERT_ROWS',
        body={'values': rows},
    ).execute()
    return len(rows)
