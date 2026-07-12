# -*- coding: utf-8 -*-
"""
仕入れ先(楽天市場・Yahoo!ショッピング・Nike公式サイトの店舗/カテゴリページ、または商品キーワード)から
Amazon転売の利益商品を探すツール

使い方:
  店舗URLでスクリーニング:
    python ec_profit_scan.py url "https://item.rakuten.co.jp/marusou/c/0000000127/"
    python ec_profit_scan.py url "https://store.shopping.yahoo.co.jp/storekatayama/toy.html"
    python ec_profit_scan.py url "https://www.nike.com/jp/w?q=..."

  キーワード単体でAmazon市場調査のみ（仕入れ値なし・保存なし）:
    python ec_profit_scan.py keyword "パーラービーズ なかよしどうぶつセット"

設計のポイント（2026-06のマルソウ店リサーチで判明した知見）:
  - 楽天: 商品ページURL末尾の数字列はJANコード(EAN)である（「商品番号」表示だが実体はJAN）
  - Yahoo!ショッピング: 一覧ページのdata-beacon属性に jan: フィールドが明示されている
  - JANコードでAmazon検索(無料スクレイピング)すると高精度で一致する
    （商品名のあいまい検索は誤マッチが多発するため使わない）
  - 候補が絞れたら最後にKeepaのcode検索（JAN→ASIN）で正確な価格・カテゴリ・重量を取得し再計算する
    （Keepaは1検索1トークン程度を消費するため全件には使わず、スクリーニング後の候補だけに使う）
  - Nike公式サイト（2026-07追加）: JAN/EANは非公開のため代わりにスタイルコード（例: BQ4153-100）を
    一意識別子として使う。一覧ページの__NEXT_DATA__(Wall.productGroupings)に埋め込まれている。
    Keepaのcode検索はバーコード専用でスタイルコードでは引けないため、Amazon検索で特定済みのASINを
    直接keepa_get_by_asinに渡して精密計算する（keepa_precise_profit_by_asin）。

Keepa計算の注意（2026-06-09の修正経験）:
  - domain=5(日本)のcsv値は1ユニット=1円（100で割らない）
  - FBA手数料はカテゴリ別販売手数料率 + 重量別固定手数料 で計算する
"""

import sys
import os
import re
import json
import time
import random
import urllib.parse
import html as htmllib
import ssl

if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')

ssl._create_default_https_context = ssl._create_unverified_context

import requests
import urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

from datetime import datetime

# ========================
# 設定
# ========================
KEEPA_API_KEY = "qm7suqd5ehemt109m37s85sp0bbq3g2lc6c4d089dbvvnpbnm3qtn0kvf2mfsp8p"
SPREADSHEET_ID = "1pPAVYxeETPq6VVtg7Jd7eapXZf8lgTttndRN6Cd4wqI"
TOKEN_FILE = "token_sheets.json"
SHEET_NAME = "Amazon仕入れ候補"
DOMAIN = 5  # Amazon.co.jp

UA_HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36',
    'Accept-Language': 'ja-JP,ja;q=0.9',
}

SCREEN_PROFIT_THRESHOLD = 150    # 簡易計算でこれ未満は候補から外す（精密計算とのズレを見込んで最終ラインより低めに設定）
FINAL_PROFIT_THRESHOLD = 300     # Keepa精密計算後の最終採用ライン（利益額）
FINAL_PROFIT_RATE_THRESHOLD = 0.10  # 最終採用ライン（利益率＝利益/Amazon売価）。電脳仕入れカレンダーと共通の閾値（2026-07-12合意）

FEE_RATE_BY_CATEGORY_KEYWORD = {
    "おもちゃ": 0.08, "ホビー": 0.08, "ゲーム": 0.08,
    "ホーム": 0.08, "キッチン": 0.08,
    "ビューティー": 0.08, "コスメ": 0.08,
    "文房具": 0.08, "オフィス": 0.08,
}
DEFAULT_FEE_RATE = 0.10

FBA_FEE_TABLE = [(250, 496), (500, 541), (1000, 596), (2000, 746)]
FBA_FEE_LARGE = 1500


# ========================
# サイト判定
# ========================

def detect_site(url):
    if "rakuten.co.jp" in url:
        return "rakuten"
    if "yahoo.co.jp" in url:
        return "yahoo"
    if "nike.com" in url:
        return "nike"
    return None


# ========================
# 楽天: 商品リスト取得（JAN付き）
# ========================

def _discover_rakuten_categories(shop_url, limit=6):
    """ショップTOPページ(www.rakuten.co.jp/<shop>/)には商品一覧が無く、
    カテゴリページ(item.rakuten.co.jp/<shop>/c/<catid>/)に遷移しないと商品が取れない。
    TOPページのHTMLからカテゴリURLを自動発見する（2026-07-12発覚・対応）。"""
    shop_match = re.search(r'rakuten\.co\.jp/([a-zA-Z0-9_-]+)/?', shop_url)
    shop_id = shop_match.group(1) if shop_match else None
    if not shop_id:
        return []
    try:
        r = requests.get(shop_url, headers=UA_HEADERS, timeout=20, verify=False)
    except Exception as e:
        print(f"  [楽天] カテゴリ発見用の接続エラー: {e}")
        return []
    if r.status_code != 200:
        return []
    html_text = r.content.decode('euc-jp', errors='ignore')
    cat_ids = []
    seen = set()
    for m in re.finditer(rf'item\.rakuten\.co\.jp/{re.escape(shop_id)}/c/(\d+)', html_text):
        cid = m.group(1)
        if cid not in seen:
            seen.add(cid)
            cat_ids.append(cid)
    return [f'https://item.rakuten.co.jp/{shop_id}/c/{cid}/' for cid in cat_ids[:limit]]


def _fetch_rakuten_category(shop_url, max_pages=20):
    items = []
    for page in range(1, max_pages + 1):
        if page == 1:
            url = shop_url
        else:
            sep = '&' if '?' in shop_url else '?'
            url = f"{shop_url.rstrip('/')}/{sep}p={page}&s=1" if '?' not in shop_url else f"{shop_url}&p={page}&s=1"
        try:
            r = requests.get(url, headers=UA_HEADERS, timeout=20, verify=False)
        except Exception as e:
            print(f"  [楽天] 接続エラー: {e}")
            break
        if r.status_code != 200:
            break
        html_text = r.content.decode('euc-jp', errors='ignore')
        rows = re.findall(
            r'href="(https://item\.rakuten\.co\.jp/[^/"]+/(\d{8,13})/)" class="category_itemnamelink">([^<]+)</a>.*?category_itemprice">([0-9,]+)円',
            html_text, re.S
        )
        if not rows:
            break
        for url_, jan, name, price in rows:
            items.append({
                "name": htmllib.unescape(name),
                "price": int(price.replace(',', '')),
                "jan": jan,
                "url": url_,
            })
        print(f"  [楽天] page {page}: {len(rows)}件")
        time.sleep(1)
    return items


def fetch_rakuten_items(shop_url, max_pages=20):
    # ショップTOPページ（カテゴリページでない）ならカテゴリURLを自動発見して横断スキャンする
    if not re.search(r'item\.rakuten\.co\.jp/[^/]+/c/\d+', shop_url):
        category_urls = _discover_rakuten_categories(shop_url)
        if category_urls:
            print(f"  [楽天] TOPページのためカテゴリ{len(category_urls)}件を自動発見: {category_urls}")
            pages_each = max(1, max_pages // len(category_urls))
            items = []
            for cat_url in category_urls:
                items.extend(_fetch_rakuten_category(cat_url, max_pages=pages_each))
            return items
        print("  [楽天] カテゴリを発見できず、TOPページを直接スキャンします（通常0件になります）")
    return _fetch_rakuten_category(shop_url, max_pages=max_pages)


# ========================
# Yahoo!ショッピング: 商品リスト取得（JAN付き）
# ========================

def fetch_yahoo_items(shop_url, max_pages=20):
    # ショップTOPページには商品一覧が無いため、search.html（全商品一覧ページ）に切り替える
    # （2026-07-12発覚・対応。すでに具体的なカテゴリ/検索ページが渡された場合はそのまま使う）
    m = re.match(r'(https://store\.shopping\.yahoo\.co\.jp/[a-zA-Z0-9_-]+)/?(?:[?#].*)?$', shop_url)
    if m:
        shop_url = m.group(1) + '/search.html'
        print(f"  [Yahoo] TOPページのためsearch.htmlに切り替え: {shop_url}")

    items = []
    seen_jan = set()
    for page in range(1, max_pages + 1):
        sep = '&' if '?' in shop_url else '?'
        url = shop_url if page == 1 else f"{shop_url}{sep}page={page}"
        try:
            r = requests.get(url, headers=UA_HEADERS, timeout=20, verify=False)
        except Exception as e:
            print(f"  [Yahoo] 接続エラー: {e}")
            break
        if r.status_code != 200:
            break
        text = r.text
        beacons = re.findall(r'data-beacon="([^"]*jan:\d+[^"]*)"', text)
        page_count = 0
        for b in beacons:
            jan_m = re.search(r'jan:(\d+);', b)
            price_m = re.search(r';prc:(\d+);', b)
            name_m = re.search(r'tname:([^;]+);', b)
            if not (jan_m and price_m and name_m):
                continue
            jan = jan_m.group(1)
            if jan in seen_jan or jan == "0":
                continue
            seen_jan.add(jan)
            items.append({
                "name": htmllib.unescape(name_m.group(1)),
                "price": int(price_m.group(1)),
                "jan": jan,
                "url": f"{shop_url.split('/')[0]}//{shop_url.split('/')[2]}/{shop_url.split('/')[3]}/{jan}.html",
            })
            page_count += 1
        print(f"  [Yahoo] page {page}: {page_count}件")
        if page_count == 0:
            break
        time.sleep(1)
    return items


# ========================
# Nike公式サイト: 商品リスト取得（スタイルコード付き）
# ========================
# NikeはJAN/EANを公開していないため、JANの代わりに一意性のあるスタイルコード
# （例: BQ4153-100）をAmazon検索キーに使う（2026-07-06 まきさんとの合意事項）。
# 商品はSSRされたHTML内の__NEXT_DATA__にWall.productGroupingsとして埋め込まれている。
#
# 【既知の制約】 ページ送り（&anchor=&count=）はページ本体のURLでは無視され、
# 何度リクエストしても常に同じ1ページ目（既定の並び順で24グループ≒40〜50件）しか返らない
# （2026-07-06に確認済み: anchor=0/24/48すべて同一内容が返ってきた）。
# 本当のページ送りはNike内部API（/discover/product_wall/v1/...）で行われているが、
# `nike-api-caller-id` という非公開ヘッダーが必須で単純な取得はできない。
# そのため現状は「1ページ目に表示される商品」のみが対象になる。

def fetch_nike_items(shop_url):
    items = []
    seen_codes = set()
    try:
        r = requests.get(shop_url, headers=UA_HEADERS, timeout=20, verify=False)
    except Exception as e:
        print(f"  [Nike] 接続エラー: {e}")
        return items
    if r.status_code != 200:
        print(f"  [Nike] status {r.status_code}")
        return items
    m = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', r.text, re.S)
    if not m:
        print("  [Nike] 商品データが見つかりませんでした")
        return items
    try:
        data = json.loads(m.group(1))
        wall = data['props']['pageProps']['initialState']['Wall']
        groupings = wall.get('productGroupings') or []
        total_resources = (wall.get('pageData') or {}).get('totalResources')
    except Exception:
        print("  [Nike] 商品データの解析に失敗しました")
        return items

    for g in groupings:
        for p in (g.get('products') or []):
            code = p.get('productCode')
            price = (p.get('prices') or {}).get('currentPrice')
            if not code or not price or code in seen_codes:
                continue
            seen_codes.add(code)
            copy = p.get('copy') or {}
            name = f"{copy.get('title') or ''} {copy.get('subTitle') or ''}".strip()
            pdp_url = (p.get('pdpUrl') or {}).get('url') or ''
            items.append({
                "name": name,
                "price": int(price),
                "jan": code,
                "url": pdp_url,
            })
    print(f"  [Nike] 1ページ目: {len(items)}件"
          + (f"（サイト全体では{total_resources}件ヒットしているが、ページ送りは未対応のため1ページ目のみ）" if total_resources else ""))
    return items


# ========================
# JAN -> Amazon検索（無料スクレイピング、スクリーニング用）
# ========================

def search_amazon_by_jan(jan, retry=2):
    """JANコードでAmazon検索。一致すれば(asin, title, price)を返す"""
    url = 'https://www.amazon.co.jp/s?k=' + jan
    for attempt in range(retry + 1):
        try:
            r = requests.get(url, headers=UA_HEADERS, timeout=20, verify=False)
        except Exception:
            time.sleep(3)
            continue
        if r.status_code != 200:
            time.sleep(3)
            continue
        text = r.text
        parts = re.split(r'(?=data-asin="[A-Z0-9]{10})', text)
        for block in parts:
            m = re.match(r'data-asin="([A-Z0-9]{10})"', block)
            if not m:
                continue
            title_m = re.search(r'<h2[^>]*aria-label="([^"]+)"', block)
            price_m = re.search(r'a-price-whole">([0-9,]+)', block)
            if title_m and price_m:
                return (m.group(1), htmllib.unescape(title_m.group(1)), int(price_m.group(1).replace(',', '')))
        return None  # ページは取れたが該当商品なし
    return None


def search_amazon_by_keyword(keyword, retry=2):
    """商品名キーワードでAmazon検索結果を複数件返す（キーワード単体モード用）"""
    url = 'https://www.amazon.co.jp/s?k=' + urllib.parse.quote(keyword)
    for attempt in range(retry + 1):
        try:
            r = requests.get(url, headers=UA_HEADERS, timeout=20, verify=False)
        except Exception:
            time.sleep(3)
            continue
        if r.status_code != 200:
            time.sleep(3)
            continue
        text = r.text
        results = []
        seen = set()
        parts = re.split(r'(?=data-asin="[A-Z0-9]{10})', text)
        for block in parts:
            m = re.match(r'data-asin="([A-Z0-9]{10})"', block)
            if not m:
                continue
            asin = m.group(1)
            if asin in seen:
                continue
            title_m = re.search(r'<h2[^>]*aria-label="([^"]+)"', block)
            price_m = re.search(r'a-price-whole">([0-9,]+)', block)
            if title_m and price_m:
                results.append((asin, htmllib.unescape(title_m.group(1)), int(price_m.group(1).replace(',', ''))))
                seen.add(asin)
        return results
    return []


def simple_profit(source_price, amazon_price):
    return amazon_price * 0.85 - 300 - source_price


# ========================
# Keepa: JAN(code) -> ASIN + 精密利益計算
# ========================

def keepa_get_by_code(code, retry=2):
    params = {"key": KEEPA_API_KEY, "domain": DOMAIN, "code": code, "history": 1, "stats": 1}
    for attempt in range(retry + 1):
        try:
            resp = requests.get("https://api.keepa.com/product", params=params, timeout=30, verify=False)
        except Exception as e:
            print(f"  [Keepa] 接続エラー: {e}")
            time.sleep(5)
            continue
        if resp.status_code == 429:
            data = resp.json()
            wait_sec = max(data.get("refillIn", 60000) // 1000, 5)
            print(f"  [Keepa] トークン不足。{wait_sec}秒待機...")
            time.sleep(wait_sec)
            continue
        if resp.status_code != 200:
            return None
        data = resp.json()
        products = data.get("products", [])
        return products[0] if products else None
    return None


def _fee_rate_for_category(category_tree):
    names = " ".join([c.get("name", "") for c in (category_tree or [])])
    for kw, rate in FEE_RATE_BY_CATEGORY_KEYWORD.items():
        if kw in names:
            return rate
    return DEFAULT_FEE_RATE


def _fba_fee_for_weight(weight_g):
    if weight_g is None or weight_g <= 0:
        return FBA_FEE_TABLE[0][1]
    for limit, fee in FBA_FEE_TABLE:
        if weight_g <= limit:
            return fee
    return FBA_FEE_LARGE


def _profit_from_keepa_product(product, source_price):
    csv_data = product.get("csv") or []
    amazon_price = None
    for idx in (1, 0):
        if len(csv_data) > idx and csv_data[idx]:
            for v in reversed(csv_data[idx]):
                if isinstance(v, (int, float)) and v > 0:
                    amazon_price = v
                    break
        if amazon_price:
            break
    if not amazon_price:
        return None

    fee_rate = _fee_rate_for_category(product.get("categoryTree"))
    weight_decigrams = product.get("packageWeight")
    weight_g = (weight_decigrams / 10) if weight_decigrams else None
    fba_fee = _fba_fee_for_weight(weight_g)
    referral_fee = round(amazon_price * fee_rate)
    profit = amazon_price - referral_fee - fba_fee - source_price
    profit_rate = (profit / amazon_price) if amazon_price else 0

    return {
        "asin": product.get("asin"),
        "title": product.get("title", ""),
        "amazon_price": amazon_price,
        "fee_rate": fee_rate,
        "referral_fee": referral_fee,
        "fba_fee": fba_fee,
        "profit": profit,
        "profit_rate": profit_rate,
    }


def keepa_precise_profit(jan, source_price):
    product = keepa_get_by_code(jan)
    if not product:
        return None
    return _profit_from_keepa_product(product, source_price)


def keepa_precise_profit_by_asin(asin, source_price):
    """NikeなどJAN非公開のサイト用。スクレイピングで既に特定済みのASINからKeepaデータを引く"""
    product = keepa_get_by_asin(asin, history=1)  # history=1必須(csv価格データ取得のため)
    if not product:
        return None
    return _profit_from_keepa_product(product, source_price)


# ========================
# スプレッドシート保存
# ========================

def save_candidates_to_sheet(candidates):
    if not candidates:
        print("候補なし。スプレッドシート保存はスキップします。")
        return

    import httplib2
    from google.oauth2.credentials import Credentials
    from googleapiclient.discovery import build
    import google_auth_httplib2

    SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]
    if not os.path.exists(TOKEN_FILE):
        print(f"認証エラー: {TOKEN_FILE} が見つかりません")
        return
    creds = Credentials.from_authorized_user_file(TOKEN_FILE, SCOPES)
    if not creds.valid:
        # ローカルでrefresh_tokenを使ってGoogleに問い合わせるのは厳禁
        # （Render側GOOGLE_CREDENTIALSの無効化を招くため。feedback_google_token参照）。
        # アクセストークンが切れている場合はここで停止し、まきさんに再取得手順を案内する。
        print(f"認証エラー: {TOKEN_FILE} のアクセストークンが期限切れです。")
        print("ローカルで自動リフレッシュはしません（Render側のトークンを壊す既知のリスクがあるため）。")
        print("まきさんが python refresh_sheets_token.py を手動実行して再取得してください。")
        return

    http = httplib2.Http(disable_ssl_certificate_validation=True)
    authed_http = google_auth_httplib2.AuthorizedHttp(creds, http)
    service = build("sheets", "v4", http=authed_http)

    today = datetime.now().strftime("%Y/%m/%d")
    header = ["確認日", "ASIN", "JAN", "Amazon商品名", "仕入れ元商品名", "仕入れ価格",
              "Amazon価格", "紹介料(概算)", "FBA手数料(概算)", "推定利益", "仕入れ元URL", "Amazonリンク"]
    rows = []
    for c in candidates:
        rows.append([
            today, c["asin"], c["jan"], c["amazon_title"][:60], c["source_name"][:60],
            c["source_price"], c["amazon_price"], c["referral_fee"], c["fba_fee"],
            c["profit"], c["source_url"], f"https://www.amazon.co.jp/dp/{c['asin']}",
        ])

    meta = service.spreadsheets().get(spreadsheetId=SPREADSHEET_ID).execute()
    existing_sheets = [s["properties"]["title"] for s in meta["sheets"]]
    if SHEET_NAME not in existing_sheets:
        service.spreadsheets().batchUpdate(
            spreadsheetId=SPREADSHEET_ID,
            body={"requests": [{"addSheet": {"properties": {"title": SHEET_NAME}}}]}
        ).execute()

    result = service.spreadsheets().values().get(
        spreadsheetId=SPREADSHEET_ID, range=f"{SHEET_NAME}!A1"
    ).execute()
    if not result.get("values"):
        service.spreadsheets().values().update(
            spreadsheetId=SPREADSHEET_ID, range=f"{SHEET_NAME}!A1",
            valueInputOption="USER_ENTERED", body={"values": [header]}
        ).execute()

    service.spreadsheets().values().append(
        spreadsheetId=SPREADSHEET_ID, range=f"{SHEET_NAME}!A1",
        valueInputOption="USER_ENTERED", insertDataOption="INSERT_ROWS",
        body={"values": [[str(v) for v in row] for row in rows]}
    ).execute()
    print(f"{len(rows)}件をスプレッドシートに保存しました")
    print(f"URL: https://docs.google.com/spreadsheets/d/{SPREADSHEET_ID}/edit")


# ========================
# モード1: URLスクリーニング
# ========================

def run_url_mode(shop_url):
    site = detect_site(shop_url)
    if site is None:
        print("対応していないURLです（楽天市場・Yahoo!ショッピング・Nike公式のみ対応）")
        return

    print(f"\n[Step1] {site} 商品リスト取得中...")
    if site == "rakuten":
        items = fetch_rakuten_items(shop_url)
    elif site == "yahoo":
        items = fetch_yahoo_items(shop_url)
    else:
        items = fetch_nike_items(shop_url)
    # 注: Nikeは1ページ目（トップ表示分）のみが対象。それ以外のサイトはmax_pages既定20ページまで巡回する。
    print(f"取得件数: {len(items)}件")
    if not items:
        print("商品が取得できませんでした。")
        return

    print("\n[Step2] JANコード/型番でAmazon検索 + 簡易スクリーニング中...")
    screened = []
    for idx, item in enumerate(items):
        result = search_amazon_by_jan(item["jan"])
        if result:
            asin, atitle, aprice = result
            item["_asin"] = asin
            sp = simple_profit(item["price"], aprice)
            mark = "OK" if sp >= SCREEN_PROFIT_THRESHOLD else "--"
            print(f"  [{idx+1}/{len(items)}] {mark} 簡易利益{sp:+.0f}円 仕入{item['price']} vs Amazon{aprice} | {item['name'][:25]}")
            if sp >= SCREEN_PROFIT_THRESHOLD:
                screened.append(item)
        else:
            print(f"  [{idx+1}/{len(items)}] -- Amazon該当なし | {item['name'][:25]}")
        time.sleep(random.uniform(1.2, 2.0))

    print(f"\n簡易スクリーニング通過: {len(screened)}件")
    if not screened:
        print("候補なし。終了します。")
        return

    print("\n[Step3] Keepaで精密判定中...")
    final_candidates = []
    for item in screened:
        if site == "nike":
            # Nikeのスタイルコードはバーコード(EAN/UPC)ではないため、
            # Keepaのcode検索は使えない。既にAmazon検索で特定済みのASINを直接引く。
            precise = keepa_precise_profit_by_asin(item["_asin"], item["price"])
        else:
            precise = keepa_precise_profit(item["jan"], item["price"])
        time.sleep(2)
        if not precise:
            print(f"  Keepa取得失敗: {item['name'][:25]}")
            continue
        print(f"  {item['name'][:25]} -> 精密利益{precise['profit']:+.0f}円 (利益率{precise['profit_rate']*100:.1f}%) "
              f"(Amazon{precise['amazon_price']}円 - 紹介料{precise['referral_fee']}円 - FBA{precise['fba_fee']}円 - 仕入{item['price']}円)")
        if precise["profit"] >= FINAL_PROFIT_THRESHOLD and precise["profit_rate"] >= FINAL_PROFIT_RATE_THRESHOLD:
            final_candidates.append({
                "source_name": item["name"], "source_price": item["price"], "source_url": item["url"],
                "jan": item["jan"], "asin": precise["asin"], "amazon_title": precise["title"],
                "amazon_price": precise["amazon_price"], "referral_fee": precise["referral_fee"],
                "fba_fee": precise["fba_fee"], "profit": precise["profit"],
            })

    print(f"\n最終候補: {len(final_candidates)}件")
    for c in sorted(final_candidates, key=lambda x: x["profit"], reverse=True):
        print(f"  +{c['profit']:.0f}円 | {c['source_name'][:30]} (ASIN:{c['asin']})")

    save_candidates_to_sheet(final_candidates)


# ========================
# モード2: キーワード単体（市場調査のみ・保存なし）
# ========================

def run_keyword_mode(keyword):
    print(f"\nキーワード「{keyword}」でAmazon調査中...")
    results = search_amazon_by_keyword(keyword)
    if not results:
        print("該当商品が見つかりませんでした。")
        return

    print(f"\nAmazon検索結果（上位5件・価格安い順）:")
    for asin, title, price in sorted(results, key=lambda x: x[2])[:5]:
        print(f"  {price:>6}円 | {title[:45]} (ASIN:{asin})")

    top_asin = sorted(results, key=lambda x: x[2])[0][0]
    print(f"\n最安値ASIN({top_asin})をKeepaで需要調査中...")

    params_result = keepa_get_by_asin(top_asin)
    if not params_result:
        print("Keepaデータ取得失敗")
        return

    bsr = None
    ranks = params_result.get("salesRanks") or {}
    for cat_id, arr in ranks.items():
        if arr and len(arr) >= 2 and isinstance(arr[-1], (int, float)) and arr[-1] > 0:
            if bsr is None or arr[-1] < bsr:
                bsr = arr[-1]
    category_tree = params_result.get("categoryTree") or []
    category_name = category_tree[-1]["name"] if category_tree else "不明"

    print(f"  カテゴリ: {category_name}")
    print(f"  BSR(売れ行きランキング): {bsr if bsr else '不明（データなし）'}")
    print(f"  ※BSRが小さいほど売れている。カテゴリ内順位なので絶対値より相対比較で見る")


def keepa_get_by_asin(asin, history=0, retry=2):
    params = {"key": KEEPA_API_KEY, "domain": DOMAIN, "asin": asin, "history": history, "stats": 1}
    for attempt in range(retry + 1):
        try:
            resp = requests.get("https://api.keepa.com/product", params=params, timeout=30, verify=False)
        except Exception:
            time.sleep(5)
            continue
        if resp.status_code != 200:
            return None
        data = resp.json()
        products = data.get("products", [])
        return products[0] if products else None
    return None


# ========================
# メイン
# ========================

def main():
    if len(sys.argv) < 3 or sys.argv[1] not in ("url", "keyword"):
        print("使い方:")
        print('  python ec_profit_scan.py url "<楽天・Yahoo!ショッピング・Nike公式のURL>"')
        print('  python ec_profit_scan.py keyword "<商品名キーワード>"')
        return

    mode = sys.argv[1]
    arg = sys.argv[2]

    print("=" * 50)
    print("EC -> Amazon 利益商品スキャン開始")
    print("=" * 50)

    if mode == "url":
        run_url_mode(arg)
    else:
        run_keyword_mode(arg)


if __name__ == "__main__":
    main()
