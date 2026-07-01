"""毎月1日：前月の副業収支を自動でまとめる仕組み。

- eBay純利益・売上$・販売個数はGoogle Sheetsから自動集計
- Amazon・note・アフィ・AdSense・経費はLINEで1行入力してもらう
- 入力を受け取ったら収支表を計算してLINE返信＋履歴シートに記録

新機能はmain.pyに書かないルールに従い、このファイルに集約する。
"""
import os
import re
import json
import datetime

from linebot.models import TextSendMessage
from googleapiclient.discovery import build

from clients import line_bot_api, JST
from ebay_dashboard import (
    get_sheets_creds, EBAY_MGMT_SHEET_ID, EBAY_MGMT_SHEET_NAME, EBAY_MGMT_HEADERS,
)

# 前月のeBay自動集計結果を一時保存（入力受付時に参照）
MONTHLY_PL_SESSION_FILE = '/tmp/monthly_pl_session.json'
# 収支履歴を残すシートタブ
PL_HISTORY_SHEET_NAME = '月次収支'
PL_HISTORY_HEADERS = [
    '対象月', 'eBay純利益', 'Amazon', 'note', '楽天アフィ', 'AdSense',
    '収入合計', '経費', '純収支', 'eBay売上USD', 'eBay販売個数', '記録日時',
]


def _prev_year_month(today=None):
    """前月の(year, month)を返す。"""
    today = today or datetime.datetime.now(JST).date()
    first_of_this_month = today.replace(day=1)
    last_of_prev = first_of_this_month - datetime.timedelta(days=1)
    return last_of_prev.year, last_of_prev.month


def calc_ebay_for_month(year, month):
    """指定月のeBay純利益・売上USD・販売個数を集計する。"""
    creds = get_sheets_creds()
    if not creds:
        raise RuntimeError('Google Sheets認証エラー')
    service = build('sheets', 'v4', credentials=creds)
    result = service.spreadsheets().values().get(
        spreadsheetId=EBAY_MGMT_SHEET_ID,
        range=f'{EBAY_MGMT_SHEET_NAME}!A2:M1000',
        valueRenderOption='UNFORMATTED_VALUE',
    ).execute()
    rows = result.get('values', [])
    prefix = f'{year:04d}-{month:02d}'
    profit_sum = 0
    usd_sum = 0.0
    count = 0
    for row in rows:
        obj = {h: (row[j] if j < len(row) else '') for j, h in enumerate(EBAY_MGMT_HEADERS)}
        if str(obj.get('sale_date', '')).startswith(prefix):
            count += 1
            try:
                usd_sum += float(obj.get('sale_price_usd') or 0)
            except (ValueError, TypeError):
                pass
            try:
                profit_sum += int(float(obj.get('profit_jpy') or 0))
            except (ValueError, TypeError):
                pass
    return {'profit': profit_sum, 'usd': round(usd_sum, 2), 'count': count}


def _load_session():
    try:
        with open(MONTHLY_PL_SESSION_FILE, encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return {}


def _save_session(data):
    try:
        with open(MONTHLY_PL_SESSION_FILE, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False)
    except Exception as e:
        print(f'[monthly_pl] session保存失敗: {e}')


def send_monthly_pl_prompt():
    """毎月1日に前月のeBayを自動集計し、残りの入力を依頼するLINEを送る。"""
    try:
        user_id = os.environ['LINE_USER_ID']
        year, month = _prev_year_month()
        try:
            ebay = calc_ebay_for_month(year, month)
        except Exception as e:
            print(f'[monthly_pl] eBay集計失敗: {e}')
            ebay = {'profit': 0, 'usd': 0, 'count': 0}

        # 入力受付のためにセッション保存
        _save_session({'year': year, 'month': month, 'ebay': ebay})

        msg = (
            f'📊【{year}年{month}月 副業収支まとめ】\n\n'
            f'eBayは自動集計しました✅\n'
            f'・純利益: {ebay["profit"]:,}円\n'
            f'・売上: ${ebay["usd"]:,}（{ebay["count"]}件）\n\n'
            f'残りの数字を1行で送ってください（無い項目は省略OK）👇\n\n'
            f'収支 amazon 4602 note 300 楽天 178 ad 5 経費 7800\n\n'
            f'※経費はツール代・API課金などの合計\n'
            f'※Anthropic課金は console.anthropic.com→請求 で確認'
        )
        line_bot_api.push_message(user_id, TextSendMessage(text=msg))
    except Exception as e:
        print(f'[monthly_pl] prompt送信エラー: {e}')


def _extract_amount(text, keywords):
    """キーワードの直後に続く数字を取り出す。見つからなければ0。"""
    for kw in keywords:
        m = re.search(rf'{kw}\s*[:：]?\s*(-?[\d,]+)', text, re.IGNORECASE)
        if m:
            try:
                return int(m.group(1).replace(',', ''))
            except ValueError:
                continue
    return 0


def _append_history(record):
    """月次収支をGoogle Sheetsの履歴タブに追記する。"""
    try:
        creds = get_sheets_creds()
        if not creds:
            return
        service = build('sheets', 'v4', credentials=creds)
        meta = service.spreadsheets().get(spreadsheetId=EBAY_MGMT_SHEET_ID).execute()
        titles = [s['properties']['title'] for s in meta['sheets']]
        if PL_HISTORY_SHEET_NAME not in titles:
            service.spreadsheets().batchUpdate(
                spreadsheetId=EBAY_MGMT_SHEET_ID,
                body={'requests': [{'addSheet': {'properties': {'title': PL_HISTORY_SHEET_NAME}}}]},
            ).execute()
            service.spreadsheets().values().update(
                spreadsheetId=EBAY_MGMT_SHEET_ID,
                range=f'{PL_HISTORY_SHEET_NAME}!A1',
                valueInputOption='RAW',
                body={'values': [PL_HISTORY_HEADERS]},
            ).execute()
        service.spreadsheets().values().append(
            spreadsheetId=EBAY_MGMT_SHEET_ID,
            range=f'{PL_HISTORY_SHEET_NAME}!A2',
            valueInputOption='RAW',
            insertDataOption='INSERT_ROWS',
            body={'values': [record]},
        ).execute()
    except Exception as e:
        print(f'[monthly_pl] 履歴追記失敗: {e}')


def handle_monthly_pl_input(user_message):
    """「収支 amazon ... note ...」を受け取り収支表を計算して返す文字列を返す。

    対象外のメッセージなら None を返す（呼び出し側で通常処理へ）。
    """
    if not user_message.strip().startswith('収支'):
        return None

    session = _load_session()
    if session and 'ebay' in session:
        year = session['year']
        month = session['month']
        ebay = session['ebay']
    else:
        # セッションが無ければ前月をその場で集計
        year, month = _prev_year_month()
        try:
            ebay = calc_ebay_for_month(year, month)
        except Exception:
            ebay = {'profit': 0, 'usd': 0, 'count': 0}

    amazon = _extract_amount(user_message, ['amazon', 'アマゾン'])
    note = _extract_amount(user_message, ['note', 'ノート'])
    rakuten = _extract_amount(user_message, ['楽天', 'rakuten'])
    adsense = _extract_amount(user_message, ['ad', 'adsense', 'アド', 'グーグル'])
    cost = _extract_amount(user_message, ['経費', 'コスト', 'cost'])

    ebay_profit = int(ebay.get('profit', 0))
    income = ebay_profit + amazon + note + rakuten + adsense
    net = income - cost

    sign = '＋' if net >= 0 else '−'
    msg = (
        f'💰【{year}年{month}月 副業収支まとめ】\n\n'
        f'■ 収入\n'
        f'・eBay純利益: {ebay_profit:,}円（{ebay.get("count", 0)}件）\n'
        f'・Amazon物販: {amazon:,}円\n'
        f'・note: {note:,}円\n'
        f'・楽天アフィ: {rakuten:,}円\n'
        f'・AdSense: {adsense:,}円\n'
        f'　収入合計: {income:,}円\n\n'
        f'■ 経費\n'
        f'・合計: {cost:,}円\n\n'
        f'📈 純収支: {sign}{abs(net):,}円\n\n'
    )
    if net >= 0:
        msg += '黒字キープ！お疲れさまでした✨'
    else:
        msg += '今月は先行投資フェーズ。固定費を売上が上回れば黒字転換します💪'

    # 履歴に記録
    now_str = datetime.datetime.now(JST).strftime('%Y-%m-%d %H:%M')
    _append_history([
        f'{year}-{month:02d}', ebay_profit, amazon, note, rakuten, adsense,
        income, cost, net, ebay.get('usd', 0), ebay.get('count', 0), now_str,
    ])

    return msg
