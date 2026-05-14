import os
import json
import base64
import time
import urllib.parse
import requests
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from linebot.models import TextSendMessage
from clients import line_bot_api, anthropic_client

EBAY_APP_ID = os.environ.get('EBAY_APP_ID', '')
EBAY_CERT_ID = os.environ.get('EBAY_CERT_ID', '')
EBAY_TOKEN_URL = "https://api.ebay.com/identity/v1/oauth2/token"
EBAY_BROWSE_URL = "https://api.ebay.com/buy/browse/v1/item_summary/search"
FINDING_API_URL = "https://svcs.ebay.com/services/search/FindingService/v1"

# 毎日の仕入れ候補リサーチ用キーワード（メルカリで仕入れやすいカテゴリ）
DAILY_RESEARCH_KEYWORDS = [
    "Sanrio Japan figure",
    "Hello Kitty Japan lot",
    "Japan anime figure lot",
    "Japan toy capsule",
    "Murakami Takashi card",
    "Japan kumano makeup brush",
    "Japan tenugui",
    "Japan eraser iwako",
    "Precure card Japan",
    "Japan vintage brooch",
]

# 除外キーワード（重い・大きい・仕入れ困難なもの）
EXCLUDE_TITLE_KEYWORDS = [
    "suit", "jacket", "coat", "dress", "shoes", "guitar", "camera",
    "lens", "furniture", "umbrella", "bag set lot", "ski",
]

# ウォッチセラーリスト（seller_usernameをここに追加するとご毎日自動チェック対象になる）
WATCHED_SELLERS = [
    # 例: "seller_username_here",
]

DEFAULT_KEYWORDS = [
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


def generate_keywords_with_claude(user_query):
    """ユーザーの自然言語クエリからeBay検索キーワードと価格帯を生成"""
    prompt = f"""あなたはeBay物販のリサーチ専門家です。
以下のユーザーの希望条件をもとに、eBayで検索する英語キーワードを10個と適切な価格帯を提案してください。

ユーザーの希望: {user_query}

条件：
- 日本からアメリカへの発送を想定（日本の商品をeBayで売る）
- 軽い・小さいなどの条件はキーワードに「lightweight」「small」「mini」「compact」などを含める
- 「Japan」「Japanese」などの産地を含めると差別化できる
- eBayで実際に需要があるキーワードにする

以下のJSON形式のみで回答してください（他の文章は不要）：
{{
  "keywords": ["keyword1", "keyword2", ...],
  "min_price": 数値,
  "max_price": 数値,
  "research_label": "リサーチ内容の短い説明（日本語）"
}}"""

    try:
        response = anthropic_client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=1024,
            messages=[{"role": "user", "content": prompt}]
        )
        text = response.content[0].text.strip()
        # JSONブロックの抽出
        if "```" in text:
            text = text.split("```")[1]
            if text.startswith("json"):
                text = text[4:]
        return json.loads(text)
    except Exception as e:
        print(f"Claude keyword generation error: {e}")
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


def search_and_score(token, keyword, min_price, max_price):
    """1キーワードを検索してスコアを返す（並列実行用）"""
    items, total = ebay_search(token, keyword, min_price, max_price)
    if not items:
        return None

    prices = []
    watch_counts = []
    for item in items:
        try:
            prices.append(float(item["price"]["value"]))
        except Exception:
            pass
        try:
            watch_counts.append(int(item.get("watchCount", 0)))
        except Exception:
            watch_counts.append(0)

    if not prices:
        return None

    avg = sum(prices) / len(prices)
    avg_watch = sum(watch_counts) / len(watch_counts) if watch_counts else 0
    # ウォッチャー数をスコアに加味（需要の指標）
    score = (avg * len(items) / max(total, 1)) * (1 + avg_watch * 0.1)

    if total <= 30 and avg >= 25:
        judge = "◎超おすすめ"
    elif total <= 80 and avg >= 20:
        judge = "○おすすめ"
    elif total <= 150 and avg >= 15:
        judge = "△要検討"
    else:
        judge = None

    if not judge:
        return None

    return {
        "keyword": keyword,
        "total": total,
        "avg": round(avg, 1),
        "avg_watch": round(avg_watch, 1),
        "score": round(score, 2),
        "judge": judge,
    }


def run_ebay_research(user_id, user_query=None):
    try:
        if user_query:
            line_bot_api.push_message(user_id, TextSendMessage(
                text=f"🤖 「{user_query}」の条件でキーワードを生成中...\n結果まで2〜3分お待ちください！"
            ))
            claude_result = generate_keywords_with_claude(user_query)
            if claude_result:
                keywords = claude_result.get("keywords", DEFAULT_KEYWORDS)
                min_price = claude_result.get("min_price", 10)
                max_price = claude_result.get("max_price", 100)
                label = claude_result.get("research_label", user_query)
            else:
                keywords = DEFAULT_KEYWORDS
                min_price, max_price = 10, 100
                label = "デフォルト（軽量・小物）"
        else:
            line_bot_api.push_message(user_id, TextSendMessage(
                text="🔍 eBayリサーチ中です...2〜3分かかります、そのままお待ちください！"
            ))
            keywords = DEFAULT_KEYWORDS
            min_price, max_price = 10, 100
            label = "軽量・小物カテゴリ"

        token = get_ebay_token()
        if not token:
            line_bot_api.push_message(user_id, TextSendMessage(text="❌ eBay APIの認証に失敗しました"))
            return

        # 並列で全キーワードを同時検索
        results = []
        with ThreadPoolExecutor(max_workers=5) as executor:
            futures = {
                executor.submit(search_and_score, token, kw, min_price, max_price): kw
                for kw in keywords
            }
            for future in as_completed(futures):
                result = future.result()
                if result:
                    results.append(result)

        results.sort(key=lambda x: x["score"], reverse=True)

        if not results:
            line_bot_api.push_message(user_id, TextSendMessage(
                text=f"今回は「{label}」でおすすめ候補が見つかりませんでした😢\n条件を変えて再試行してみてください。"
            ))
            return

        msg = f"📦 eBayリサーチ結果（{label}）\n\n"
        for i, r in enumerate(results[:5], 1):
            msg += f"{i}位 {r['keyword']}\n"
            msg += f"   競合: {r['total']}件 / 平均${r['avg']}\n"
            msg += f"   👀 平均ウォッチ: {r['avg_watch']}件\n"
            msg += f"   {r['judge']}\n\n"

        msg += "💡 次のステップ：\n"
        msg += "① eBayで上記キーワードを検索\n"
        msg += "② Sold Listings（販売済み）でフィルター\n"
        msg += "③ 30日で売れてるか確認してから仕入れ！\n"
        msg += "④ eBayタイトルは「eBayタイトル作って：商品名」で作れます。"
        line_bot_api.push_message(user_id, TextSendMessage(text=msg))

    except Exception as e:
        print(f"eBay research error: {e}")
        line_bot_api.push_message(user_id, TextSendMessage(text=f"❌ リサーチ中にエラーが発生しました: {str(e)[:100]}"))


# ──────────────────────────────────────────
# 毎日の仕入れ候補リサーチ（Finding API）
# ──────────────────────────────────────────

def _calc_purchase_limit(usd_price: float, exchange_rate: float = 150.0) -> int:
    """eBay販売価格から仕入れ上限（円）を逆算。利益率30%以上・eBay手数料15%込み"""
    jpy = usd_price * exchange_rate
    net = jpy * 0.85          # eBay手数料15%引き後
    limit = (net / 1.30) - 2500  # 30%利益確保・送料/梱包2,500円分を引く
    return max(0, int(limit))


def _mercari_url(title: str) -> str:
    """英語タイトルからメルカリ検索URLを生成"""
    q = urllib.parse.quote(title[:40])
    return f"https://jp.mercari.com/search?keyword={q}&status=on_sale"


def _search_jp_sold_one(keyword: str, min_usd: float = 50.0, max_usd: float = 400.0) -> list:
    """1キーワードで日本人セラーが売れた商品を取得（Finding API）"""
    url = (
        f"{FINDING_API_URL}"
        f"?OPERATION-NAME=findCompletedItems"
        f"&SERVICE-VERSION=1.0.0"
        f"&SECURITY-APPNAME={EBAY_APP_ID}"
        f"&RESPONSE-DATA-FORMAT=JSON"
        f"&keywords={urllib.parse.quote(keyword)}"
        f"&itemFilter(0).name=SoldItemsOnly&itemFilter(0).value=true"
        f"&itemFilter(1).name=LocatedIn&itemFilter(1).value=JP"
        f"&itemFilter(2).name=ListingType&itemFilter(2).value=FixedPrice"
        f"&itemFilter(3).name=MinPrice&itemFilter(3).value={min_usd}"
        f"&itemFilter(3).paramName=Currency&itemFilter(3).paramValue=USD"
        f"&itemFilter(4).name=MaxPrice&itemFilter(4).value={max_usd}"
        f"&itemFilter(4).paramName=Currency&itemFilter(4).paramValue=USD"
        f"&sortOrder=EndTimeSoonest"
        f"&paginationInput.entriesPerPage=10"
    )
    try:
        resp = requests.get(url, timeout=15)
        if resp.status_code != 200:
            return []
        data = resp.json()
        return data["findCompletedItemsResponse"][0].get(
            "searchResult", [{}])[0].get("item", [])
    except Exception as e:
        print(f"Finding API error [{keyword}]: {e}")
        return []


def _search_seller_sold_items(seller_username: str, limit: int = 10) -> list:
    """特定セラーが最近売った商品を取得（Finding API）"""
    url = (
        f"{FINDING_API_URL}"
        f"?OPERATION-NAME=findCompletedItems"
        f"&SERVICE-VERSION=1.0.0"
        f"&SECURITY-APPNAME={EBAY_APP_ID}"
        f"&RESPONSE-DATA-FORMAT=JSON"
        f"&itemFilter(0).name=SoldItemsOnly&itemFilter(0).value=true"
        f"&itemFilter(1).name=Seller&itemFilter(1).value={urllib.parse.quote(seller_username)}"
        f"&itemFilter(2).name=ListingType&itemFilter(2).value=FixedPrice"
        f"&sortOrder=EndTimeSoonest"
        f"&paginationInput.entriesPerPage={limit}"
    )
    try:
        resp = requests.get(url, timeout=15)
        if resp.status_code != 200:
            return []
        data = resp.json()
        return data["findCompletedItemsResponse"][0].get(
            "searchResult", [{}])[0].get("item", [])
    except Exception as e:
        print(f"Finding API error [seller:{seller_username}]: {e}")
        return []


def _format_seller_results(items: list, seller_username: str) -> list:
    """セラーの売れた商品をパースして候補リスト形式に変換"""
    results = []
    seen = set()
    for it in items:
        try:
            title = it["title"][0]
            price = float(it["sellingStatus"][0]["convertedCurrentPrice"][0]["__value__"])
            end_time = it["listingInfo"][0]["endTime"][0][:10]
            if title[:30] in seen:
                continue
            seen.add(title[:30])
            title_lower = title.lower()
            if any(ex in title_lower for ex in EXCLUDE_TITLE_KEYWORDS):
                continue
            purchase_limit = _calc_purchase_limit(price)
            results.append({
                "title": title,
                "price_usd": price,
                "sold_date": end_time,
                "purchase_limit": purchase_limit,
                "mercari_url": _mercari_url(title),
                "seller": seller_username,
            })
        except Exception:
            continue
    return results


def check_seller_now(user_id: str, seller_username: str):
    """LINEから即チェック：特定セラーの売れた商品をすぐに返す"""
    try:
        line_bot_api.push_message(user_id, TextSendMessage(
            text=f"セラー「{seller_username}」の売れた商品を調べています..."
        ))
        items = _search_seller_sold_items(seller_username, limit=15)
        results = _format_seller_results(items, seller_username)

        if not results:
            line_bot_api.push_message(user_id, TextSendMessage(
                text=f"「{seller_username}」の売れた商品が見つかりませんでした。\nセラーIDが正しいか確認してください。"
            ))
            return

        results.sort(key=lambda x: x["price_usd"], reverse=True)
        msg = f"[{seller_username}] 最近売れた商品\n\n"
        for i, r in enumerate(results[:7], 1):
            msg += f"{i}. ${r['price_usd']:.0f} | {r['title'][:40]}\n"
            msg += f"   仕入れ上限: ¥{r['purchase_limit']:,}以下 ({r['sold_date']})\n"
            msg += f"   {r['mercari_url']}\n\n"
        msg += f"→ 気に入ったらウォッチリストに追加できます\n「セラー登録：{seller_username}」とClaude Codeに伝えてね"
        line_bot_api.push_message(user_id, TextSendMessage(text=msg))

    except Exception as e:
        print(f"check_seller_now error: {e}")
        line_bot_api.push_message(user_id, TextSendMessage(text=f"エラーが発生しました: {str(e)[:80]}"))


def send_daily_purchase_candidates(user_id: str, debug: bool = False):
    """毎日の仕入れ候補リストをLINEに送信"""
    try:
        all_items = []
        seen_titles = set()
        api_errors = 0
        raw_count = 0

        for kw in DAILY_RESEARCH_KEYWORDS:
            items = _search_jp_sold_one(kw)
            time.sleep(1.2)  # レートリミット対策
            if items is None:
                api_errors += 1
            raw_count += len(items) if items else 0

            for it in (items or []):
                try:
                    title = it["title"][0]
                    price = float(it["sellingStatus"][0]["convertedCurrentPrice"][0]["__value__"])
                    end_time = it["listingInfo"][0]["endTime"][0][:10]

                    # 重複・除外チェック
                    title_lower = title.lower()
                    if any(ex in title_lower for ex in EXCLUDE_TITLE_KEYWORDS):
                        continue
                    if title[:30] in seen_titles:
                        continue
                    seen_titles.add(title[:30])

                    purchase_limit = _calc_purchase_limit(price)
                    if purchase_limit < 10000:  # ¥10,000未満は除外
                        continue

                    all_items.append({
                        "title": title,
                        "price_usd": price,
                        "sold_date": end_time,
                        "purchase_limit": purchase_limit,
                        "mercari_url": _mercari_url(title),
                    })
                except Exception:
                    continue

        # ウォッチセラーの売れた商品も追加
        for seller in WATCHED_SELLERS:
            seller_items = _search_seller_sold_items(seller, limit=10)
            time.sleep(1.2)
            for r in _format_seller_results(seller_items, seller):
                if r["title"][:30] not in seen_titles:
                    seen_titles.add(r["title"][:30])
                    r["seller_watch"] = True
                    all_items.append(r)

        if not all_items:
            msg = f"仕入れ候補リサーチ：条件に合う商品なし\n"
            msg += f"取得件数={raw_count}件 / APIエラー={api_errors}件\n"
            if raw_count == 0:
                msg += "→ Finding APIがレートリミット中の可能性あり。数時間後に再試行してみてください。"
            else:
                msg += f"→ {raw_count}件取得できたが価格条件($50〜$400・仕入れ¥10,000以上)をすべて外れました。"
            line_bot_api.push_message(user_id, TextSendMessage(text=msg))
            return

        # 仕入れ上限が大きい順にソートして上位5件
        all_items.sort(key=lambda x: x["purchase_limit"], reverse=True)
        top = all_items[:5]

        today = datetime.now().strftime("%-m/%-d")
        msg = f"【今日の仕入れ候補】{today}\n\n"
        for i, r in enumerate(top, 1):
            seller_tag = f" [{r.get('seller', '')}]" if r.get("seller_watch") else ""
            msg += f"{i}.{seller_tag} ${r['price_usd']:.0f} | {r['title'][:38]}\n"
            msg += f"   仕入れ上限: ¥{r['purchase_limit']:,}以下\n"
            msg += f"   {r['mercari_url']}\n\n"

        watch_note = f"（ウォッチ: {', '.join(WATCHED_SELLERS)}）\n" if WATCHED_SELLERS else ""
        msg += f"合計{len(all_items)}件から{len(top)}件を抽出 {watch_note}"
        msg += "→ URLをタップしてメルカリで相場確認してね"

        line_bot_api.push_message(user_id, TextSendMessage(text=msg))
        print(f"send_daily_purchase_candidates: {len(top)}件送信完了")

    except Exception as e:
        print(f"send_daily_purchase_candidates error: {e}")
