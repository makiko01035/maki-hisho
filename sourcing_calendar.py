# -*- coding: utf-8 -*-
"""
電脳仕入れカレンダー: 5・10のつく日(5,10,15,20,25,30)に楽天市場・Yahoo!ショッピングの
店舗を3〜4件ずつローテーションでスキャンし、Amazon転売の利益候補をスプレッドシートに保存する。
Renderの無人cronで動く（PCの電源に依存しない）。LINE通知なし（スプシで確認する運用）。
"""
import os
import re
import json
import time
import random
import traceback
from datetime import datetime

from ec_profit_scan import (
    fetch_rakuten_items, fetch_yahoo_items, search_amazon_by_jan,
    simple_profit, keepa_precise_profit,
    SCREEN_PROFIT_THRESHOLD, FINAL_PROFIT_THRESHOLD, FINAL_PROFIT_RATE_THRESHOLD,
)

SPREADSHEET_ID = "1pPAVYxeETPq6VVtg7Jd7eapXZf8lgTttndRN6Cd4wqI"
SHEET_NAME = "電脳仕入れ候補"

MAX_PAGES_PER_STORE = 8     # ec_profit_scan.pyのfetch_*既定20より短く抑える（無人実行のため暴走防止）
MAX_ITEMS_PER_STORE = 120   # 大型店舗（ハピネット/ケーズデンキ/LOHACOなど）の実行時間を上限で頭打ちにする

# 21店舗を6グループ(4,3,4,3,4,3)に分割。各グループに Yahoo1店 + 楽天2〜3店 を配置し、
# 5・10のつく日6回で全21店舗をちょうど1巡する。(name, site, url)
STORE_GROUPS = {
    5: [
        ("ぽちべる", "rakuten", "https://www.rakuten.co.jp/pochibell/"),
        ("JBL・AKG公式ストア", "rakuten", "https://www.rakuten.co.jp/jblstore/"),
        ("公式サンリオオンラインショップ", "rakuten", "https://www.rakuten.co.jp/sanrio/"),
        ("コジマYahoo!店", "yahoo", "https://store.shopping.yahoo.co.jp/y-kojima/"),
    ],
    10: [
        ("sokuhai-ソクハイ-", "rakuten", "https://www.rakuten.co.jp/soku-hai/"),
        ("プラスマート", "rakuten", "https://www.rakuten.co.jp/plusmart/"),
        ("らいぶshop", "yahoo", "https://store.shopping.yahoo.co.jp/light-hikari/"),
    ],
    15: [
        ("THINK RICH STORE", "rakuten", "https://www.rakuten.co.jp/thinkrich/"),
        ("ニッチ・リッチ・キャッチ", "rakuten", "https://www.rakuten.co.jp/mitsuyoshi/"),
        ("SuperSportsXEBIO楽天市場支店", "rakuten", "https://www.rakuten.co.jp/supersportsxebio/"),
        ("ブラウニーストア", "yahoo", "https://store.shopping.yahoo.co.jp/brownie-store/"),
    ],
    20: [
        ("ハピネットオンライン", "rakuten", "https://www.rakuten.co.jp/es-toys/"),
        ("MARUSOU", "rakuten", "https://www.rakuten.co.jp/marusou/"),
        ("エレコムダイレクトショップ", "yahoo", "https://store.shopping.yahoo.co.jp/elecom/"),
    ],
    25: [
        ("ケーズデンキ楽天市場店", "rakuten", "https://www.rakuten.co.jp/ksdenki/"),
        ("松風オンライン楽天市場店", "rakuten", "https://www.rakuten.co.jp/matukaze/"),
        ("Vドラッグ楽天市場店", "rakuten", "https://www.rakuten.co.jp/v-drug/"),
        ("LOHACO", "yahoo", "https://store.shopping.yahoo.co.jp/y-lohaco/"),
    ],
    30: [
        ("ベースストア", "rakuten", "https://www.rakuten.co.jp/bexcs/"),
        ("LULUSTOCK", "rakuten", "https://www.rakuten.co.jp/lulustock/"),
        ("一休さん2号館", "yahoo", "https://store.shopping.yahoo.co.jp/1932/"),
    ],
}


def run_sourcing_scan(day_override=None, only_store=None):
    """cron本体。day_override/only_storeはデバッグルートからの手動テスト用。"""
    try:
        target_day = day_override if day_override in STORE_GROUPS else datetime.now().day
        group = STORE_GROUPS.get(target_day, STORE_GROUPS[5])
        if only_store:
            group = [g for g in group if g[0] == only_store] or group

        print(f"[電脳仕入れ] {datetime.now():%Y-%m-%d %H:%M} day={target_day} 対象店舗: {[g[0] for g in group]}")

        all_candidates = []
        for name, site, url in group:
            try:
                found = _scan_store(name, site, url)
                print(f"  [{name}] 最終候補 {len(found)}件")
                all_candidates.extend(found)
            except Exception as e:
                # 1店舗の失敗（サイト構造変更・ブロック等）で全体を止めない
                print(f"[電脳仕入れ] {name} でエラー: {e}\n{traceback.format_exc()}")
                continue

        print(f"[電脳仕入れ] 合計最終候補 {len(all_candidates)}件")
        if all_candidates:
            _save_to_sheet(all_candidates)
        else:
            print("[電脳仕入れ] 候補なし。スプレッドシート保存はスキップ。")
    except Exception as e:
        print(f"[電脳仕入れ] run_sourcing_scan 致命的エラー: {e}\n{traceback.format_exc()}")


def _scan_store(name, site, url):
    fetch_fn = fetch_rakuten_items if site == "rakuten" else fetch_yahoo_items
    items = fetch_fn(url, max_pages=MAX_PAGES_PER_STORE)[:MAX_ITEMS_PER_STORE]
    print(f"  [{name}] 取得 {len(items)}件（上限{MAX_ITEMS_PER_STORE}件でカット）")

    screened = []
    for item in items:
        result = search_amazon_by_jan(item["jan"])
        if result:
            asin, atitle, aprice = result
            sp = simple_profit(item["price"], aprice)
            if sp >= SCREEN_PROFIT_THRESHOLD:
                item["_asin"] = asin
                screened.append(item)
        time.sleep(random.uniform(1.2, 2.0))

    final = []
    for item in screened:
        precise = keepa_precise_profit(item["jan"], item["price"])
        time.sleep(2)
        if not precise:
            continue
        if precise["profit"] >= FINAL_PROFIT_THRESHOLD and precise["profit_rate"] >= FINAL_PROFIT_RATE_THRESHOLD:
            final.append({
                "store_name": name,
                "source_name": item["name"], "source_price": item["price"], "source_url": item["url"],
                "jan": item["jan"], "asin": precise["asin"], "amazon_title": precise["title"],
                "amazon_price": precise["amazon_price"], "referral_fee": precise["referral_fee"],
                "fba_fee": precise["fba_fee"], "profit": precise["profit"], "profit_rate": precise["profit_rate"],
            })
    return final


def _get_sheets_service():
    """calendar_manager.get_calendar_service()と同じRender安全パターン（GOOGLE_CREDENTIALS必須）。
    ローカルtoken_sheets.jsonやローカルrefresh()は絶対に使わない（feedback_google_token参照）。"""
    from google.oauth2.credentials import Credentials
    from google.auth.transport.requests import Request
    from googleapiclient.discovery import build

    raw = os.environ['GOOGLE_CREDENTIALS']
    clean = re.sub(r'[\x00-\x1f\x7f]', '', raw)
    creds_info = json.loads(clean)
    creds = Credentials(
        token=None,
        refresh_token=creds_info.get('refresh_token'),
        token_uri='https://oauth2.googleapis.com/token',
        client_id=creds_info.get('client_id'),
        client_secret=creds_info.get('client_secret'),
        scopes=['https://www.googleapis.com/auth/spreadsheets'],
    )
    creds.refresh(Request())
    return build('sheets', 'v4', credentials=creds)  # Render(Linux)なのでSSL回避不要


def _save_to_sheet(candidates):
    service = _get_sheets_service()
    today = datetime.now().strftime("%Y/%m/%d")
    header = ["確認日", "仕入れ元店舗", "ASIN", "JAN", "Amazon商品名", "仕入れ元商品名",
              "仕入れ元価格", "Amazon価格", "紹介料", "FBA手数料", "推定利益", "利益率(%)",
              "仕入れ元URL", "Amazonリンク"]

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

    rows = [[
        today, c["store_name"], c["asin"], c["jan"], c["amazon_title"][:60], c["source_name"][:60],
        c["source_price"], c["amazon_price"], c["referral_fee"], c["fba_fee"],
        c["profit"], round(c["profit_rate"] * 100, 1),
        c["source_url"], f"https://www.amazon.co.jp/dp/{c['asin']}",
    ] for c in candidates]

    service.spreadsheets().values().append(
        spreadsheetId=SPREADSHEET_ID, range=f"{SHEET_NAME}!A1",
        valueInputOption="USER_ENTERED", insertDataOption="INSERT_ROWS",
        body={"values": [[str(v) for v in row] for row in rows]}
    ).execute()
    print(f"[電脳仕入れ] {len(rows)}件をスプレッドシート「{SHEET_NAME}」に保存")
