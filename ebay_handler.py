import os
import base64
import time
import requests
from linebot.models import TextSendMessage
from clients import line_bot_api

EBAY_APP_ID = os.environ.get('EBAY_APP_ID', '')
EBAY_CERT_ID = os.environ.get('EBAY_CERT_ID', '')
EBAY_TOKEN_URL = "https://api.ebay.com/identity/v1/oauth2/token"
EBAY_BROWSE_URL = "https://api.ebay.com/buy/browse/v1/item_summary/search"

EBAY_KEYWORDS = [
    "Japan vintage kanzashi hair pin",
    "Japan vintage brooch",
    "Japan vintage kimono accessory",
    "Japan tenugui vintage",
    "Japan furoshiki vintage",
    "Japan vintage handkerchief",
    "Japan vintage coin purse",
    "Japan vintage fan sensu",
    "Japan washi tape",
    "Japan vintage eraser iwako",
    "Japan vintage badge pin",
    "Japan vintage patch embroidered",
    "Japan vintage incense holder",
    "Japan vintage chopsticks lacquer",
]


def get_ebay_token():
    credentials = base64.b64encode(f"{EBAY_APP_ID}:{EBAY_CERT_ID}".encode()).decode()
    headers = {
        "Authorization": f"Basic {credentials}",
        "Content-Type": "application/x-www-form-urlencoded",
    }
    data = "grant_type=client_credentials&scope=https://api.ebay.com/oauth/api_scope"
    res = requests.post(EBAY_TOKEN_URL, headers=headers, data=data, timeout=10)
    if res.status_code == 200:
        return res.json().get("access_token")
    return None


def ebay_search(token, keyword, min_price=10, max_price=100):
    headers = {
        "Authorization": f"Bearer {token}",
        "X-EBAY-C-MARKETPLACE-ID": "EBAY_US",
    }
    params = {
        "q": keyword,
        "filter": f"price:[{min_price}..{max_price}],priceCurrency:USD,buyingOptions:{{FIXED_PRICE}}",
        "limit": 50,
    }
    try:
        res = requests.get(EBAY_BROWSE_URL, headers=headers, params=params, timeout=10)
        if res.status_code == 200:
            data = res.json()
            return data.get("itemSummaries", []), data.get("total", 0)
    except Exception:
        pass
    return [], 0


def run_ebay_research(user_id):
    try:
        line_bot_api.push_message(user_id, TextSendMessage(text="🔍 eBayリサーチ中です...2〜3分かかります、そのままお待ちください！"))

        token = get_ebay_token()
        if not token:
            line_bot_api.push_message(user_id, TextSendMessage(text="❌ eBay APIの認証に失敗しました"))
            return

        results = []
        for keyword in EBAY_KEYWORDS:
            items, total = ebay_search(token, keyword)
            if not items:
                time.sleep(0.5)
                continue
            prices = []
            for item in items:
                try:
                    prices.append(float(item["price"]["value"]))
                except Exception:
                    pass
            if not prices:
                time.sleep(0.5)
                continue
            avg = sum(prices) / len(prices)
            score = (avg * len(items)) / max(total, 1)

            if total <= 30 and avg >= 25:
                judge = "◎超おすすめ"
            elif total <= 80 and avg >= 20:
                judge = "○おすすめ"
            elif total <= 150 and avg >= 15:
                judge = "△要検討"
            else:
                judge = None

            if judge:
                results.append({
                    "keyword": keyword,
                    "total": total,
                    "avg": round(avg, 1),
                    "score": round(score, 2),
                    "judge": judge,
                })
            time.sleep(0.5)

        results.sort(key=lambda x: x["score"], reverse=True)

        if not results:
            line_bot_api.push_message(user_id, TextSendMessage(text="今回はおすすめ候補が見つかりませんでした😢\nキーワードを変えて再試行します。"))
            return

        msg = "📦 eBayリサーチ結果（軽量・小物カテゴリ）\n\n"
        for i, r in enumerate(results[:5], 1):
            msg += f"{i}位 {r['keyword']}\n"
            msg += f"   競合: {r['total']}件 / 平均${r['avg']}\n"
            msg += f"   {r['judge']}\n\n"

        msg += "💡 メルカリで仕入れてみましょう！\neBayタイトルは「eBayタイトル作って：商品名」で作れます。"
        line_bot_api.push_message(user_id, TextSendMessage(text=msg))

    except Exception as e:
        print(f"eBay research error: {e}")
        line_bot_api.push_message(user_id, TextSendMessage(text=f"❌ リサーチ中にエラーが発生しました: {str(e)[:100]}"))
