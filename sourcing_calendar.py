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
    fetch_rakuten_items, fetch_yahoo_items, fetch_shopify_items, search_amazon_by_jan,
    simple_profit, keepa_precise_profit,
    SCREEN_PROFIT_THRESHOLD, FINAL_PROFIT_THRESHOLD, FINAL_PROFIT_RATE_THRESHOLD,
)

SPREADSHEET_ID = "1pPAVYxeETPq6VVtg7Jd7eapXZf8lgTttndRN6Cd4wqI"
SHEET_NAME = "電脳仕入れ候補"

MAX_PAGES_PER_STORE = 8     # ec_profit_scan.pyのfetch_*既定20より短く抑える（無人実行のため暴走防止）
MAX_ITEMS_PER_STORE = 120   # 大型店舗（ハピネット/ケーズデンキ/LOHACOなど）の実行時間を上限で頭打ちにする

# 29店舗を6グループ(5,5,5,4,5,5)に分割。各グループに Yahoo1店 + 楽天2〜3店 + Shopify製メーカー直販1店 を配置し、
# 5・10のつく日6回で全29店舗をちょうど1巡する。(name, site, url)
STORE_GROUPS = {
    5: [
        ("ぽちべる", "rakuten", "https://www.rakuten.co.jp/pochibell/"),
        ("JBL・AKG公式ストア", "rakuten", "https://www.rakuten.co.jp/jblstore/"),
        ("公式サンリオオンラインショップ", "rakuten", "https://www.rakuten.co.jp/sanrio/"),
        ("コジマYahoo!店", "yahoo", "https://store.shopping.yahoo.co.jp/y-kojima/"),
        ("山新アウトレット", "shopify", "https://outlet.yamashin-grp.co.jp/"),
    ],
    10: [
        ("sokuhai-ソクハイ-", "rakuten", "https://www.rakuten.co.jp/soku-hai/"),
        ("プラスマート", "rakuten", "https://www.rakuten.co.jp/plusmart/"),
        ("アイリスオーヤマ公式 楽天市場店", "rakuten", "https://www.rakuten.co.jp/irisplaza-r/"),
        ("らいぶshop", "yahoo", "https://store.shopping.yahoo.co.jp/light-hikari/"),
        ("トクポチ", "shopify", "https://tokupochi.com/"),
    ],
    15: [
        ("THINK RICH STORE", "rakuten", "https://www.rakuten.co.jp/thinkrich/"),
        ("ニッチ・リッチ・キャッチ", "rakuten", "https://www.rakuten.co.jp/mitsuyoshi/"),
        ("SuperSportsXEBIO楽天市場支店", "rakuten", "https://www.rakuten.co.jp/supersportsxebio/"),
        ("ブラウニーストア", "yahoo", "https://store.shopping.yahoo.co.jp/brownie-store/"),
        ("日清食品グループオンラインストア", "shopify", "https://store.nissin.com/"),
    ],
    20: [
        ("ハピネットオンライン", "rakuten", "https://www.rakuten.co.jp/es-toys/"),
        ("MARUSOU", "rakuten", "https://www.rakuten.co.jp/marusou/"),
        ("エレコムダイレクトショップ", "yahoo", "https://store.shopping.yahoo.co.jp/elecom/"),
        ("成城石井.com", "shopify", "https://seijoishii.com/"),
    ],
    25: [
        ("ケーズデンキ楽天市場店", "rakuten", "https://www.rakuten.co.jp/ksdenki/"),
        ("松風オンライン楽天市場店", "rakuten", "https://www.rakuten.co.jp/matukaze/"),
        ("Vドラッグ楽天市場店", "rakuten", "https://www.rakuten.co.jp/v-drug/"),
        ("LOHACO", "yahoo", "https://store.shopping.yahoo.co.jp/y-lohaco/"),
        ("kuradashi", "shopify", "https://kuradashi.jp/"),
    ],
    30: [
        ("ベースストア", "rakuten", "https://www.rakuten.co.jp/bexcs/"),
        ("LULUSTOCK", "rakuten", "https://www.rakuten.co.jp/lulustock/"),
        ("ミスターマックス楽天市場店", "rakuten", "https://www.rakuten.co.jp/mrmax-r/"),
        ("一休さん2号館", "yahoo", "https://store.shopping.yahoo.co.jp/1932/"),
        ("おざとや アウトレット", "shopify", "https://ozatoya.co.jp/"),
    ],
}


STATUS_LOG_FILE = "/tmp/sourcing_scan_status.json"


def _write_status_log(status):
    """Renderのログ画面が見えない環境からでも実行結果を確認できるよう/tmpに残す（/sourcing-scan-statusで読める）"""
    try:
        status["updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with open(STATUS_LOG_FILE, "w", encoding="utf-8") as f:
            json.dump(status, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


def run_sourcing_scan(day_override=None, only_store=None):
    """cron本体。day_override/only_storeはデバッグルートからの手動テスト用。"""
    status = {"type": "sourcing_scan", "started_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")}
    try:
        target_day = day_override if day_override in STORE_GROUPS else datetime.now().day
        group = STORE_GROUPS.get(target_day, STORE_GROUPS[5])
        if only_store:
            group = [g for g in group if g[0] == only_store] or group

        print(f"[電脳仕入れ] {datetime.now():%Y-%m-%d %H:%M} day={target_day} 対象店舗: {[g[0] for g in group]}")
        status["day"] = target_day
        status["stores"] = [g[0] for g in group]
        status["per_store"] = {}

        all_candidates = []
        for name, site, url in group:
            try:
                found, counts = _scan_store(name, site, url)
                print(f"  [{name}] 最終候補 {len(found)}件 (取得{counts['fetched']}/スクリーニング通過{counts['screened']})")
                status["per_store"][name] = {"candidates": len(found), **counts}
                all_candidates.extend(found)
            except Exception as e:
                # 1店舗の失敗（サイト構造変更・ブロック等）で全体を止めない
                print(f"[電脳仕入れ] {name} でエラー: {e}\n{traceback.format_exc()}")
                status["per_store"][name] = {"error": str(e)}
                continue

        print(f"[電脳仕入れ] 合計最終候補 {len(all_candidates)}件")
        status["total_candidates"] = len(all_candidates)
        if all_candidates:
            _save_to_sheet(all_candidates)
            status["saved"] = True
        else:
            print("[電脳仕入れ] 候補なし。スプレッドシート保存はスキップ。")
            status["saved"] = False
        status["result"] = "completed"
    except Exception as e:
        print(f"[電脳仕入れ] run_sourcing_scan 致命的エラー: {e}\n{traceback.format_exc()}")
        status["result"] = "fatal_error"
        status["error"] = str(e)
    finally:
        _write_status_log(status)


def _scan_store(name, site, url):
    fetch_fn = {"rakuten": fetch_rakuten_items, "yahoo": fetch_yahoo_items, "shopify": fetch_shopify_items}[site]
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
    counts = {"fetched": len(items), "screened": len(screened)}
    return final, counts


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


# ヘッダー列（0-indexed）。ウォッチ機能はこの並びに依存するので変更時は両方直す
HEADER = ["確認日", "仕入れ元店舗", "ASIN", "JAN", "Amazon商品名", "仕入れ元商品名",
          "仕入れ元価格", "Amazon価格", "紹介料", "FBA手数料", "推定利益", "利益率(%)",
          "仕入れ元URL", "Amazonリンク", "ウォッチ", "最終確認日(ウォッチ)", "現在の利益(ウォッチ)", "現在の利益率%(ウォッチ)"]
COL_JAN, COL_SOURCE_PRICE, COL_WATCH, COL_LAST_CHECK, COL_CUR_PROFIT, COL_CUR_RATE = 3, 6, 14, 15, 16, 17


def _ensure_sheet(service):
    """タブが無ければ作成し、ヘッダーが無ければ書き込む＋ウォッチ列にチェックボックスを設定する"""
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
            valueInputOption="USER_ENTERED", body={"values": [HEADER]}
        ).execute()
        meta = service.spreadsheets().get(spreadsheetId=SPREADSHEET_ID).execute()
        sheet_id = next(s["properties"]["sheetId"] for s in meta["sheets"] if s["properties"]["title"] == SHEET_NAME)
        service.spreadsheets().batchUpdate(
            spreadsheetId=SPREADSHEET_ID,
            body={"requests": [{"setDataValidation": {
                "range": {"sheetId": sheet_id, "startRowIndex": 1, "endRowIndex": 2000,
                          "startColumnIndex": COL_WATCH, "endColumnIndex": COL_WATCH + 1},
                "rule": {"condition": {"type": "BOOLEAN"}, "strict": True, "showCustomUi": True},
            }}]}
        ).execute()


def _save_to_sheet(candidates):
    service = _get_sheets_service()
    today = datetime.now().strftime("%Y/%m/%d")
    _ensure_sheet(service)

    rows = [[
        today, c["store_name"], c["asin"], c["jan"], c["amazon_title"][:60], c["source_name"][:60],
        c["source_price"], c["amazon_price"], c["referral_fee"], c["fba_fee"],
        c["profit"], round(c["profit_rate"] * 100, 1),
        c["source_url"], f"https://www.amazon.co.jp/dp/{c['asin']}",
        False, "", "", "",
    ] for c in candidates]

    service.spreadsheets().values().append(
        spreadsheetId=SPREADSHEET_ID, range=f"{SHEET_NAME}!A1",
        valueInputOption="USER_ENTERED", insertDataOption="INSERT_ROWS",
        body={"values": [[v if isinstance(v, bool) else str(v) for v in row] for row in rows]}
    ).execute()
    print(f"[電脳仕入れ] {len(rows)}件をスプレッドシート「{SHEET_NAME}」に保存")


def run_watchlist_check():
    """「電脳仕入れ候補」タブでウォッチ列がチェック済みの行だけ、Amazon価格を再チェックして更新する。
    店舗巡回より軽い処理なので、店舗スキャンと同じ5・10のつく日に一緒に実行できる。"""
    try:
        service = _get_sheets_service()
        result = service.spreadsheets().values().get(
            spreadsheetId=SPREADSHEET_ID, range=f"{SHEET_NAME}!A2:R2000"
        ).execute()
        rows = result.get("values", [])
        if not rows:
            print("[定番ウォッチ] 対象なし（シート未作成 or データなし）")
            return

        today = datetime.now().strftime("%Y/%m/%d")
        updates = []
        checked = 0
        for i, row in enumerate(rows):
            row_num = i + 2  # ヘッダーが1行目なのでデータは2行目から
            is_watched = len(row) > COL_WATCH and str(row[COL_WATCH]).upper() == "TRUE"
            if not is_watched:
                continue
            jan = row[COL_JAN] if len(row) > COL_JAN else None
            source_price_raw = row[COL_SOURCE_PRICE] if len(row) > COL_SOURCE_PRICE else None
            if not jan or not source_price_raw:
                continue
            try:
                source_price = int(float(source_price_raw))
            except (ValueError, TypeError):
                continue

            checked += 1
            precise = keepa_precise_profit(jan, source_price)
            time.sleep(2)
            if not precise:
                updates.append({"range": f"{SHEET_NAME}!P{row_num}", "values": [[today]]})
                continue
            updates.append({
                "range": f"{SHEET_NAME}!P{row_num}:R{row_num}",
                "values": [[today, precise["profit"], round(precise["profit_rate"] * 100, 1)]],
            })
            print(f"  [定番ウォッチ] JAN={jan} 現在利益{precise['profit']:+.0f}円 (利益率{precise['profit_rate']*100:.1f}%)")

        if updates:
            service.spreadsheets().values().batchUpdate(
                spreadsheetId=SPREADSHEET_ID,
                body={"valueInputOption": "USER_ENTERED", "data": updates}
            ).execute()
        print(f"[定番ウォッチ] {checked}件チェック・{len(updates)}件更新")
    except Exception as e:
        print(f"[定番ウォッチ] run_watchlist_check エラー: {e}\n{traceback.format_exc()}")
