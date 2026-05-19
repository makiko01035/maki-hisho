import os
import json
import re
import base64
import urllib.parse
import requests
from flask import Blueprint, request, jsonify, send_from_directory
from google.oauth2.credentials import Credentials as GCreds
from googleapiclient.discovery import build

ebay_bp = Blueprint('ebay', __name__)

EBAY_MGMT_SHEET_ID   = "1pPAVYxeETPq6VVtg7Jd7eapXZf8lgTttndRN6Cd4wqI"
EBAY_MGMT_SHEET_NAME = "売上管理"
EBAY_MGMT_HEADERS    = [
    "item_id", "order_id", "title", "sale_price_usd", "sale_date",
    "purchase_price_jpy", "shipping_method", "shipping_cost_jpy",
    "fx_rate", "ebay_fee_jpy", "profit_jpy", "completed", "notes",
]


def get_ebay_user_token():
    refresh = os.environ.get('EBAY_REFRESH_TOKEN', '')
    app_id  = os.environ.get('EBAY_APP_ID', '')
    cert_id = os.environ.get('EBAY_CERT_ID', '')
    if not (refresh and app_id and cert_id):
        return None
    creds = base64.b64encode(f"{app_id}:{cert_id}".encode()).decode()
    resp  = requests.post(
        "https://api.ebay.com/identity/v1/oauth2/token",
        headers={"Authorization": f"Basic {creds}", "Content-Type": "application/x-www-form-urlencoded"},
        data=f"grant_type=refresh_token&refresh_token={urllib.parse.quote(refresh)}",
        timeout=10,
    )
    return resp.json().get("access_token")


def get_sheets_creds():
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
        result = resp.json()
        token = result.get('access_token')
        if not token:
            print(f"[get_sheets_creds] token取得失敗: {result}")
            return None
        return GCreds(token=token)
    except Exception as e:
        print(f"[get_sheets_creds error] {e}")
        return None


def ensure_ebay_mgmt_sheet(service):
    meta   = service.spreadsheets().get(spreadsheetId=EBAY_MGMT_SHEET_ID).execute()
    titles = [s["properties"]["title"] for s in meta["sheets"]]
    if EBAY_MGMT_SHEET_NAME not in titles:
        service.spreadsheets().batchUpdate(
            spreadsheetId=EBAY_MGMT_SHEET_ID,
            body={"requests": [{"addSheet": {"properties": {"title": EBAY_MGMT_SHEET_NAME}}}]},
        ).execute()
        service.spreadsheets().values().update(
            spreadsheetId=EBAY_MGMT_SHEET_ID,
            range=f"{EBAY_MGMT_SHEET_NAME}!A1",
            valueInputOption="RAW",
            body={"values": [EBAY_MGMT_HEADERS]},
        ).execute()


@ebay_bp.route('/ebay-guide')
def ebay_guide():
    return send_from_directory('.', 'ebay_guide.html')


@ebay_bp.route('/ebay-calc')
def ebay_calculator():
    return send_from_directory('.', 'ebay_calculator.html')


@ebay_bp.route('/ebay-dashboard')
def ebay_dashboard_page():
    return send_from_directory('.', 'ebay_dashboard.html')


@ebay_bp.route('/api/ebay/debug')
def ebay_debug():
    import traceback
    result = {
        'GOOGLE_SHEETS_TOKEN_set': bool(os.environ.get('GOOGLE_SHEETS_TOKEN', '')),
        'EBAY_REFRESH_TOKEN_set':  bool(os.environ.get('EBAY_REFRESH_TOKEN', '')),
        'EBAY_APP_ID_set':         bool(os.environ.get('EBAY_APP_ID', '')),
        'EBAY_CERT_ID_set':        bool(os.environ.get('EBAY_CERT_ID', '')),
    }
    try:
        raw = os.environ.get('GOOGLE_SHEETS_TOKEN', '')
        clean = re.sub(r'[\x00-\x1f\x7f]', '', raw)
        data = json.loads(clean)
        result['token_keys'] = list(data.keys())
        result['refresh_token_prefix'] = data.get('refresh_token', '')[:15]
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
        r = resp.json()
        result['google_token_response'] = {k: v for k, v in r.items() if k != 'access_token'}
        result['sheets_creds_ok'] = 'access_token' in r
    except Exception as e:
        result['sheets_creds_ok'] = False
        result['sheets_error'] = traceback.format_exc()
    try:
        token = get_ebay_user_token()
        result['ebay_token_ok'] = token is not None
    except Exception as e:
        result['ebay_token_ok'] = False
        result['ebay_token_error'] = str(e)
    return jsonify(result)


@ebay_bp.route('/api/ebay/data')
def ebay_data_api():
    try:
        creds = get_sheets_creds()
        if not creds:
            return jsonify({"error": "Google Sheets認証エラー"}), 500
        service = build("sheets", "v4", credentials=creds)
        ensure_ebay_mgmt_sheet(service)
        result = service.spreadsheets().values().get(
            spreadsheetId=EBAY_MGMT_SHEET_ID,
            range=f"{EBAY_MGMT_SHEET_NAME}!A2:M1000",
            valueRenderOption="UNFORMATTED_VALUE",
        ).execute()
        rows   = result.get("values", [])
        orders = []
        for i, row in enumerate(rows):
            obj = {h: (row[j] if j < len(row) else "") for j, h in enumerate(EBAY_MGMT_HEADERS)}
            obj["row_num"] = i + 2
            orders.append(obj)
        return jsonify({"orders": orders})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@ebay_bp.route('/api/ebay/update', methods=['POST'])
def ebay_update_api():
    try:
        data          = request.json
        row_num       = data.get("row_num")
        sale_usd      = float(data.get("sale_price_usd", 0))
        purchase      = float(data.get("purchase_price_jpy", 0))
        ship_method   = data.get("shipping_method", "CPASS")
        ship_cost     = float(data.get("shipping_cost_jpy", 0))
        fx            = float(data.get("fx_rate", 155))
        notes         = data.get("notes", "")
        sales_jpy     = round(sale_usd * fx)
        ebay_fee      = round(sales_jpy * 0.1325)
        profit        = sales_jpy - round(purchase) - round(ship_cost) - ebay_fee

        creds = get_sheets_creds()
        if not creds:
            return jsonify({"error": "Google Sheets認証エラー"}), 500
        service = build("sheets", "v4", credentials=creds)
        service.spreadsheets().values().update(
            spreadsheetId=EBAY_MGMT_SHEET_ID,
            range=f"{EBAY_MGMT_SHEET_NAME}!F{row_num}:M{row_num}",
            valueInputOption="RAW",
            body={"values": [[purchase, ship_method, ship_cost, fx, ebay_fee, profit, "TRUE", notes]]},
        ).execute()
        return jsonify({"success": True, "profit": profit, "ebay_fee": ebay_fee})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@ebay_bp.route('/api/ebay/sync')
def ebay_sync_api():
    try:
        token = get_ebay_user_token()
        if not token:
            return jsonify({"error": "EBAY_REFRESH_TOKENが未設定です。Renderの環境変数を確認してください"}), 500

        resp = requests.get(
            "https://api.ebay.com/sell/fulfillment/v1/order",
            headers={"Authorization": f"Bearer {token}"},
            params={"limit": 200, "filter": "creationdate:[2026-01-01T00:00:00.000Z..]"},
            timeout=20,
        )
        ebay_orders = resp.json().get("orders", [])

        creds = get_sheets_creds()
        if not creds:
            return jsonify({"error": "Google Sheets認証エラー"}), 500
        service = build("sheets", "v4", credentials=creds)
        ensure_ebay_mgmt_sheet(service)

        existing = service.spreadsheets().values().get(
            spreadsheetId=EBAY_MGMT_SHEET_ID,
            range=f"{EBAY_MGMT_SHEET_NAME}!B2:B1000",
        ).execute()
        existing_ids = {row[0] for row in existing.get("values", []) if row}

        new_rows = []
        for order in ebay_orders:
            order_id = order.get("orderId", "")
            if order_id in existing_ids:
                continue
            sale_date   = (order.get("creationDate") or "")[:10]
            total_price = (order.get("pricingSummary") or {}).get("total", {}).get("value", "0")
            for item in order.get("lineItems", []):
                new_rows.append([
                    item.get("legacyItemId", ""),
                    order_id,
                    item.get("title", ""),
                    total_price,
                    sale_date,
                    "", "", "", "155", "", "", "FALSE", "",
                ])

        if new_rows:
            service.spreadsheets().values().append(
                spreadsheetId=EBAY_MGMT_SHEET_ID,
                range=f"{EBAY_MGMT_SHEET_NAME}!A2",
                valueInputOption="RAW",
                insertDataOption="INSERT_ROWS",
                body={"values": new_rows},
            ).execute()

        return jsonify({"added": len(new_rows), "fetched": len(ebay_orders)})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@ebay_bp.route('/ebay-callback')
def ebay_callback():
    code = request.args.get('code')
    if not code:
        return 'codeパラメータが見つかりません。', 400
    return f'''<html><head><meta charset="utf-8"></head><body>
<h2>eBay認証成功！</h2>
<p>以下のcodeをClaude Codeに貼り付けてください：</p>
<textarea rows="4" cols="80" onclick="this.select()">{code}</textarea>
<br><br>
<p style="color:gray">このページを閉じてOKです。</p>
</body></html>'''
