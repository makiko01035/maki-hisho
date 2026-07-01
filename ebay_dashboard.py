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
    "buyer_username", "msg_sent",
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


def send_buyer_message(order_id, item_id, tracking_number="", buyer_username=""):
    """発送後バイヤーへ感謝＋フィードバック依頼メッセージを送信（Trading API使用）"""
    token = get_ebay_user_token()
    if not token:
        return False, "eBayトークン取得失敗"

    if not buyer_username:
        r = requests.get(
            f"https://api.ebay.com/sell/fulfillment/v1/order/{urllib.parse.quote(order_id, safe='')}",
            headers={"Authorization": f"Bearer {token}"},
            timeout=10,
        )
        buyer_username = r.json().get("buyer", {}).get("username", "")
    if not buyer_username:
        return False, "バイヤー名取得失敗"

    tracking_line = f"\nTracking number: {tracking_number}" if tracking_number else ""
    body = (
        f"Hi {buyer_username},\n\n"
        f"Thank you so much for your purchase!\n"
        f"Your item has been shipped.{tracking_line}\n\n"
        f"I hope you enjoy it! If you have any questions, please feel free to contact me anytime.\n\n"
        f"It would mean a lot to me if you could leave feedback when you receive the item.\n\n"
        f"Thank you again and have a wonderful day!\n"
        f"Best regards,\nMaki"
    )
    safe_body = body.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')

    xml_body = f"""<?xml version="1.0" encoding="utf-8"?>
<AddMemberMessageAAQtoPartnerRequest xmlns="urn:ebay:apis:eBLBaseComponents">
  <ItemID>{item_id}</ItemID>
  <MemberMessage>
    <Body>{safe_body}</Body>
    <RecipientID>{buyer_username}</RecipientID>
    <Subject>Thank you for your purchase! / Item shipped</Subject>
  </MemberMessage>
</AddMemberMessageAAQtoPartnerRequest>"""

    resp = requests.post(
        "https://api.ebay.com/ws/api.dll",
        headers={
            "X-EBAY-API-CALL-NAME": "AddMemberMessageAAQtoPartner",
            "X-EBAY-API-SITEID": "0",
            "X-EBAY-API-COMPATIBILITY-LEVEL": "967",
            "X-EBAY-API-IAF-TOKEN": token,
            "Content-Type": "text/xml",
        },
        data=xml_body.encode("utf-8"),
        timeout=10,
    )

    if "Ack>Success" in resp.text or "Ack>Warning" in resp.text:
        return True, "送信成功"
    else:
        return False, f"送信失敗: {resp.text[:300]}"


def get_sheets_creds():
    raw = os.environ.get('GOOGLE_CREDENTIALS') or os.environ.get('GOOGLE_SHEETS_TOKEN', '')
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
        'GOOGLE_CREDENTIALS_set':  bool(os.environ.get('GOOGLE_CREDENTIALS', '')),
        'GOOGLE_SHEETS_TOKEN_set': bool(os.environ.get('GOOGLE_SHEETS_TOKEN', '')),
        'EBAY_REFRESH_TOKEN_set':  bool(os.environ.get('EBAY_REFRESH_TOKEN', '')),
        'EBAY_APP_ID_set':         bool(os.environ.get('EBAY_APP_ID', '')),
        'EBAY_CERT_ID_set':        bool(os.environ.get('EBAY_CERT_ID', '')),
    }
    try:
        raw = os.environ.get('GOOGLE_CREDENTIALS') or os.environ.get('GOOGLE_SHEETS_TOKEN', '')
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

        # 既存行を全列読み込み（msg_sent判定のため）
        existing_result = service.spreadsheets().values().get(
            spreadsheetId=EBAY_MGMT_SHEET_ID,
            range=f"{EBAY_MGMT_SHEET_NAME}!A2:O1000",
            valueRenderOption="UNFORMATTED_VALUE",
        ).execute()
        existing_rows = existing_result.get("values", [])
        existing_map = {}
        for i, row in enumerate(existing_rows):
            if len(row) > 1:
                existing_map[row[1]] = {
                    "row_num":       i + 2,
                    "item_id":       row[0] if row else "",
                    "buyer_username": row[13] if len(row) > 13 else "",
                    "msg_sent":      row[14] if len(row) > 14 else "",
                }

        new_rows = []
        auto_sent = []
        sent_this_sync = set()

        for order in ebay_orders:
            order_id           = order.get("orderId", "")
            fulfillment_status = order.get("orderFulfillmentStatus", "")
            buyer_username     = order.get("buyer", {}).get("username", "")
            sale_date          = (order.get("creationDate") or "")[:10]
            total_price        = (order.get("pricingSummary") or {}).get("total", {}).get("value", "0")

            for item in order.get("lineItems", []):
                item_id = item.get("legacyItemId", "")
                if order_id not in existing_map:
                    new_rows.append([
                        item_id, order_id, item.get("title", ""),
                        total_price, sale_date,
                        "", "", "", "155", "", "", "FALSE", "",
                        buyer_username, "FALSE",
                    ])
                elif (fulfillment_status == "FULFILLED"
                      and existing_map[order_id]["msg_sent"] != "TRUE"
                      and order_id not in sent_this_sync):
                    info   = existing_map[order_id]
                    uname  = info["buyer_username"] or buyer_username
                    ok, _  = send_buyer_message(order_id, info["item_id"] or item_id, buyer_username=uname)
                    if ok:
                        sent_this_sync.add(order_id)
                        auto_sent.append(order_id)
                        service.spreadsheets().values().update(
                            spreadsheetId=EBAY_MGMT_SHEET_ID,
                            range=f"{EBAY_MGMT_SHEET_NAME}!N{info['row_num']}:O{info['row_num']}",
                            valueInputOption="RAW",
                            body={"values": [[uname, "TRUE"]]},
                        ).execute()

        if new_rows:
            service.spreadsheets().values().append(
                spreadsheetId=EBAY_MGMT_SHEET_ID,
                range=f"{EBAY_MGMT_SHEET_NAME}!A2",
                valueInputOption="RAW",
                insertDataOption="INSERT_ROWS",
                body={"values": new_rows},
            ).execute()

        return jsonify({"added": len(new_rows), "fetched": len(ebay_orders), "auto_sent": len(auto_sent)})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@ebay_bp.route('/api/ebay/test-message')
def ebay_test_message():
    """メッセージ送信テスト（1件だけ試す）"""
    order_id      = "21-14803-88338"
    item_id       = "336643762779"
    buyer_username = "roro10101"
    token = get_ebay_user_token()
    if not token:
        return jsonify({"error": "トークン取得失敗"})
    safe_body = "Hi roro10101, this is a test message. Please ignore.".replace('&','&amp;')
    xml_body = f"""<?xml version="1.0" encoding="utf-8"?>
<AddMemberMessageAAQtoPartnerRequest xmlns="urn:ebay:apis:eBLBaseComponents">
  <ItemID>{item_id}</ItemID>
  <MemberMessage>
    <Body>{safe_body}</Body>
    <RecipientID>{buyer_username}</RecipientID>
    <Subject>Test</Subject>
  </MemberMessage>
</AddMemberMessageAAQtoPartnerRequest>"""
    resp = requests.post(
        "https://api.ebay.com/ws/api.dll",
        headers={
            "X-EBAY-API-CALL-NAME": "AddMemberMessageAAQtoPartner",
            "X-EBAY-API-SITEID": "0",
            "X-EBAY-API-COMPATIBILITY-LEVEL": "967",
            "X-EBAY-API-IAF-TOKEN": token,
            "Content-Type": "text/xml",
        },
        data=xml_body.encode("utf-8"),
        timeout=10,
    )
    return jsonify({"status": resp.status_code, "response": resp.text[:1000]})


@ebay_bp.route('/api/ebay/debug-sync')
def ebay_debug_sync():
    """同期時に何が起きているか確認用"""
    try:
        token = get_ebay_user_token()
        if not token:
            return jsonify({"error": "eBayトークン取得失敗"}), 500
        resp = requests.get(
            "https://api.ebay.com/sell/fulfillment/v1/order",
            headers={"Authorization": f"Bearer {token}"},
            params={"limit": 50, "filter": "creationdate:[2026-01-01T00:00:00.000Z..]"},
            timeout=20,
        )
        orders = resp.json().get("orders", [])
        result = []
        for o in orders:
            result.append({
                "order_id": o.get("orderId", ""),
                "status":   o.get("orderFulfillmentStatus", ""),
                "buyer":    o.get("buyer", {}).get("username", ""),
                "item_id":  o.get("lineItems", [{}])[0].get("legacyItemId", "") if o.get("lineItems") else "",
            })
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@ebay_bp.route('/api/ebay/send-message', methods=['POST'])
def ebay_send_message_api():
    try:
        data     = request.json
        order_id = data.get("order_id", "")
        item_id  = data.get("item_id", "")
        tracking = data.get("tracking_number", "")
        if not order_id:
            return jsonify({"error": "order_idが必要です"}), 400
        success, msg = send_buyer_message(order_id, item_id, tracking)
        if success:
            return jsonify({"success": True, "message": msg})
        else:
            return jsonify({"error": msg}), 500
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
