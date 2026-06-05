# -*- coding: utf-8 -*-
"""
実店舗仕入れレシートOCR → Googleスプレッドシート追記モジュール
LINEでレシート画像を送ると Amazon or メルカリ仕入れリストに自動追加する
"""
import os
import re
import json
import time
import datetime
import requests
import httplib2
import google_auth_httplib2
from googleapiclient.discovery import build
from google.oauth2.credentials import Credentials as GCreds

SPREADSHEET_ID = "1pPAVYxeETPq6VVtg7Jd7eapXZf8lgTttndRN6Cd4wqI"
AMAZON_SHEET   = "Amazon仕入れ管理"
MERCARI_SHEET  = "メルカリ仕入れ管理"
KEEPA_API_KEY  = os.environ.get('KEEPA_API_KEY', 'qm7suqd5ehemt109m37s85sp0bbq3g2lc6c4d089dbvvnpbnm3qtn0kvf2mfsp8p')

MERCARI_HEADER = [
    "No.", "商品名", "仕入れ先", "仕入れ価格(円)",
    "メルカリ売値(円)", "利益率(%)【手動】", "仕入れ日", "ステータス", "メモ"
]


def _get_sheets_creds():
    from google.auth.transport.requests import Request
    raw = os.environ.get('GOOGLE_SHEETS_TOKEN', '')
    try:
        clean = re.sub(r'[\x00-\x1f\x7f]', '', raw) if raw else ''
        data = json.loads(clean) if clean else json.load(open('token_sheets.json', encoding='utf-8'))
        creds = GCreds(
            token=None,
            refresh_token=data['refresh_token'],
            token_uri='https://oauth2.googleapis.com/token',
            client_id=data['client_id'],
            client_secret=data['client_secret'],
            scopes=['https://www.googleapis.com/auth/spreadsheets'],
        )
        creds.refresh(Request())
        return creds
    except Exception as e:
        print(f"[purchase_receipt] sheets creds error: {e}")
        return None


def _build_memo(item: dict) -> str:
    """個数・JANコードからメモ文字列を生成する（Amazon・メルカリ共通）"""
    unit_price = item.get('unit_price', item.get('price', ''))
    qty = int(item.get('quantity', 1) or 1)
    jan = (item.get('jan') or '').strip()
    parts = []
    if qty > 1:
        parts.append(f'{qty}個購入・単価{int(unit_price):,}円')
    if jan:
        parts.append(f'JAN:{jan}')
    return '・'.join(parts)


def _item_price_str(item: dict) -> str:
    """単価×個数の表示文字列を生成する（LINE確認メッセージ用）"""
    unit = item.get('unit_price', item.get('price', 0))
    qty = int(item.get('quantity', 1) or 1)
    if qty > 1:
        return f'{int(unit):,}円 × {qty}個 = {int(unit) * qty:,}円'
    return f'{int(unit):,}円'


def enrich_items_with_asin(items: list[dict]) -> list[dict]:
    """JANコードがある商品にKeepaでASINを一括付与する。OCR直後に呼ぶ。"""
    targets = [(i, (item.get('jan') or '').strip()) for i, item in enumerate(items)]
    targets = [(i, jan) for i, jan in targets if jan]
    if not targets:
        return items

    jan_codes = ','.join(jan for _, jan in targets)
    try:
        resp = requests.get(
            'https://api.keepa.com/product',
            params={'key': KEEPA_API_KEY, 'domain': 5, 'code': jan_codes, 'history': 0},
            timeout=20,
            verify=False,
        )
        products = resp.json().get('products', []) if resp.status_code == 200 else []
        print(f"[Keepa] JAN一括検索: {len(targets)}件 → {len(products)}件ヒット")
    except Exception as e:
        print(f"[Keepa] 一括検索例外: {e}")
        products = []

    # レスポンスはリクエスト順に対応する（見つからない場合はnull）
    for (idx, jan), product in zip(targets, products or [None] * len(targets)):
        asin = product.get('asin') if product else None
        items[idx]['asin'] = asin if asin else f'JAN:{jan}'

    return items


def parse_receipt_with_vision(anthropic_client, image_base64: str, media_type: str) -> list[dict]:
    """Claude Visionでレシートから商品情報を抽出する。画像・PDF両対応。503過負荷時は最大3回リトライ。"""
    today = datetime.date.today().strftime('%Y/%m/%d')
    file_block = {
        'type': 'document' if media_type == 'application/pdf' else 'image',
        'source': {'type': 'base64', 'media_type': media_type, 'data': image_base64}
    }
    prompt_text = f"""これは実店舗での仕入れレシートです。
商品情報をすべて抽出してJSON配列で返してください。
今日の日付（レシートに日付がない場合のデフォルト）: {today}

以下のJSON配列形式のみ返してください（説明文は不要）:
[
  {{
    "name": "商品名（できるだけ正確に）",
    "unit_price": 単価（数字のみ・円記号なし）,
    "quantity": 個数（数字のみ・1個なら1）,
    "store": "店舗名（レシートから読み取る）",
    "date": "YYYY/MM/DD形式の仕入れ日",
    "jan": "JANコード（バーコード番号・13桁の数字・なければnull）"
  }}
]
・複数商品があれば全て含める
・「2コ×単699」「×2 @500」などの表記は unit_price=699, quantity=2 のように分解する
・単価が不明な場合は合計額を unit_price に入れ quantity=1 とする
・小計・合計・消費税・レジ袋・ポイント等の明細行は除外する
・JANコードは商品コード・バーコード番号として印字されている13桁の数字
・商品名が読み取れない場合は「商品名不明」とする"""
    last_err = None
    for attempt in range(3):
        try:
            response = anthropic_client.messages.create(
                model='claude-sonnet-4-6',
                max_tokens=1500,
                messages=[{
                    'role': 'user',
                    'content': [
                        file_block,
                        {'type': 'text', 'text': prompt_text}
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
        except Exception as e:
            last_err = e
            err_str = str(e)
            if any(x in err_str for x in ['overloaded', '529', '503']) and attempt < 2:
                time.sleep(15 * (attempt + 1))
                continue
            raise
    raise last_err


def format_confirm_message(items: list[dict], target: str) -> str:
    """LINE用の確認メッセージを生成する"""
    label = 'Amazon仕入れ' if target == 'amazon' else 'メルカリ仕入れ'
    lines = [f'📦 読み取り結果（{label}リスト）\n{"━" * 20}']
    for i, item in enumerate(items, 1):
        line = (
            f'【{i}】{item["name"]}\n'
            f'    店舗: {item["store"]}\n'
            f'    価格: {_item_price_str(item)}\n'
            f'    日付: {item["date"]}'
        )
        if item.get('asin'):
            line += f'\n    ASIN: {item["asin"]}'
        elif item.get('jan'):
            line += f'\n    JAN: {item["jan"]}'
        lines.append(line)
    lines.append('━' * 20)
    lines.append('「OK」で追加 ／ 「キャンセル」でやり直し')
    return '\n'.join(lines)


def append_to_amazon_sheet(items: list[dict]) -> int:
    """Amazon仕入れ管理シートの末尾に商品を追記する。追加した件数を返す。"""
    creds = _get_sheets_creds()
    if not creds:
        raise RuntimeError('Googleスプレッドシート認証に失敗しました')

    authorized_http = google_auth_httplib2.AuthorizedHttp(
        creds, http=httplib2.Http(disable_ssl_certificate_validation=True)
    )
    service = build('sheets', 'v4', http=authorized_http)
    sheet = service.spreadsheets()

    existing = sheet.values().get(
        spreadsheetId=SPREADSHEET_ID, range=f'{AMAZON_SHEET}!A:A',
    ).execute().get('values', [])
    next_no = len(existing)

    rows = []
    for i, item in enumerate(items):
        no = next_no + i
        row_num = no + 1
        unit_price = item.get('unit_price', item.get('price', ''))
        asin = item.get('asin', '')
        row = [
            str(no), '', item.get('store', ''), item.get('name', ''), asin,
            str(unit_price), '', '8',
            f'=IF(OR(G{row_num}="",H{row_num}=""),"",ROUND(G{row_num}*H{row_num}/100,0))',
            '',
            f'=IF(OR(G{row_num}="",J{row_num}=""),"",G{row_num}-F{row_num}-I{row_num}-J{row_num})',
            f'=IF(OR(K{row_num}="",G{row_num}="",G{row_num}=0),"",ROUND(K{row_num}/G{row_num}*100,1))',
            'スポット', '1回限り', item.get('date', ''), '書類待ち', _build_memo(item),
        ]
        rows.append(row)

    sheet.values().append(
        spreadsheetId=SPREADSHEET_ID, range=f'{AMAZON_SHEET}!A1',
        valueInputOption='USER_ENTERED', insertDataOption='INSERT_ROWS',
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
            spreadsheetId=SPREADSHEET_ID, range=f'{MERCARI_SHEET}!A1',
            valueInputOption='RAW', body={'values': [MERCARI_HEADER]},
        ).execute()

    existing = sheet.values().get(
        spreadsheetId=SPREADSHEET_ID, range=f'{MERCARI_SHEET}!A:A',
    ).execute().get('values', [])
    next_no = len(existing)

    rows = []
    for i, item in enumerate(items):
        no = next_no + i
        unit_price = item.get('unit_price', item.get('price', ''))
        row = [
            str(no), item.get('name', ''), item.get('store', ''), str(unit_price),
            '', '', item.get('date', ''), '仕入れ済み', _build_memo(item),
        ]
        rows.append(row)

    sheet.values().append(
        spreadsheetId=SPREADSHEET_ID, range=f'{MERCARI_SHEET}!A1',
        valueInputOption='RAW', insertDataOption='INSERT_ROWS',
        body={'values': rows},
    ).execute()
    return len(rows)
