import os
import datetime
import requests
from flask import Blueprint, send_from_directory, send_file, jsonify, request

from clients import JST
from calendar_manager import get_upcoming_events
from blog_yakuzen import get_pinterest_access_token

company_bp = Blueprint('company', __name__)

@company_bp.route('/company')
def company_dashboard():
    now = datetime.datetime.now(JST)
    return f'''<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>まきの会社 | Company Dashboard</title>
<link href="https://fonts.googleapis.com/css2?family=Noto+Sans+JP:wght@300;400;700&family=Playfair+Display:wght@400;700&display=swap" rel="stylesheet">
<style>
  :root {{
    --gold: #c9a84c;
    --gold-light: #e8c96a;
    --black: #0a0a0a;
    --dark: #111111;
    --card: #1a1a1a;
    --border: #2a2a2a;
    --text: #e0e0e0;
    --muted: #888888;
  }}
  * {{ margin: 0; padding: 0; box-sizing: border-box; }}
  body {{
    background: var(--black);
    color: var(--text);
    font-family: 'Noto Sans JP', sans-serif;
    font-weight: 300;
    min-height: 100vh;
  }}

  /* ヘッダー */
  header {{
    border-bottom: 1px solid var(--border);
    padding: 40px 48px 32px;
    display: flex;
    justify-content: space-between;
    align-items: flex-end;
  }}
  .logo {{
    font-family: 'Playfair Display', serif;
    font-size: 28px;
    color: var(--gold);
    letter-spacing: 2px;
  }}
  .logo span {{
    display: block;
    font-family: 'Noto Sans JP', sans-serif;
    font-size: 11px;
    font-weight: 300;
    color: var(--muted);
    letter-spacing: 4px;
    text-transform: uppercase;
    margin-top: 4px;
  }}
  .timestamp {{
    font-size: 12px;
    color: var(--muted);
    letter-spacing: 1px;
    text-align: right;
  }}
  .timestamp strong {{
    display: block;
    font-size: 20px;
    color: var(--gold-light);
    font-weight: 400;
  }}

  /* メインコンテンツ */
  main {{ padding: 48px; }}

  /* ミッション */
  .mission {{
    border-left: 2px solid var(--gold);
    padding-left: 24px;
    margin-bottom: 56px;
  }}
  .mission h2 {{
    font-family: 'Playfair Display', serif;
    font-size: 14px;
    letter-spacing: 4px;
    text-transform: uppercase;
    color: var(--gold);
    margin-bottom: 12px;
  }}
  .mission p {{
    font-size: 22px;
    font-weight: 300;
    line-height: 1.8;
    color: var(--text);
  }}
  .mission p em {{
    font-style: normal;
    color: var(--gold-light);
    font-weight: 400;
  }}

  /* セクションタイトル */
  .section-title {{
    font-size: 11px;
    letter-spacing: 5px;
    text-transform: uppercase;
    color: var(--gold);
    margin-bottom: 24px;
  }}

  /* 目標メーター */
  .goal-section {{ margin-bottom: 56px; }}
  .goal-card {{
    background: var(--card);
    border: 1px solid var(--border);
    border-radius: 2px;
    padding: 32px 40px;
    display: flex;
    align-items: center;
    gap: 48px;
  }}
  .goal-label {{
    font-size: 12px;
    color: var(--muted);
    letter-spacing: 2px;
    text-transform: uppercase;
    margin-bottom: 8px;
  }}
  .goal-amount {{
    font-family: 'Playfair Display', serif;
    font-size: 48px;
    color: var(--gold);
    line-height: 1;
  }}
  .goal-amount span {{ font-size: 18px; color: var(--muted); }}
  .goal-divider {{
    width: 1px;
    height: 60px;
    background: var(--border);
    flex-shrink: 0;
  }}
  .goal-vision {{
    font-size: 14px;
    color: var(--muted);
    line-height: 2;
  }}
  .goal-vision strong {{ color: var(--gold-light); font-weight: 400; }}

  /* 部署カード */
  .departments {{
    display: grid;
    grid-template-columns: repeat(3, 1fr);
    gap: 20px;
    margin-bottom: 56px;
  }}
  .dept-card {{
    background: var(--card);
    border: 1px solid var(--border);
    border-radius: 2px;
    padding: 32px;
    position: relative;
    overflow: hidden;
    transition: border-color 0.3s;
  }}
  .dept-card:hover {{ border-color: var(--gold); }}
  .dept-card::before {{
    content: '';
    position: absolute;
    top: 0; left: 0; right: 0;
    height: 2px;
    background: linear-gradient(90deg, var(--gold), transparent);
  }}
  .dept-priority {{
    font-size: 10px;
    letter-spacing: 3px;
    text-transform: uppercase;
    color: var(--gold);
    margin-bottom: 16px;
  }}
  .dept-name {{
    font-family: 'Playfair Display', serif;
    font-size: 22px;
    color: var(--text);
    margin-bottom: 8px;
  }}
  .dept-target {{
    font-size: 12px;
    color: var(--muted);
    margin-bottom: 20px;
    padding-bottom: 20px;
    border-bottom: 1px solid var(--border);
  }}
  .dept-target strong {{ color: var(--gold-light); font-weight: 400; }}
  .dept-items {{ list-style: none; }}
  .dept-items li {{
    font-size: 13px;
    color: var(--muted);
    padding: 5px 0;
    padding-left: 14px;
    position: relative;
    line-height: 1.6;
  }}
  .dept-items li::before {{
    content: '—';
    position: absolute;
    left: 0;
    color: var(--border);
  }}

  /* 週次スケジュール */
  .schedule-section {{ margin-bottom: 56px; }}
  .schedule-table {{
    width: 100%;
    border-collapse: collapse;
  }}
  .schedule-table th, .schedule-table td {{
    padding: 16px 20px;
    text-align: left;
    font-size: 13px;
    border-bottom: 1px solid var(--border);
  }}
  .schedule-table th {{
    font-size: 10px;
    letter-spacing: 3px;
    text-transform: uppercase;
    color: var(--muted);
    font-weight: 400;
  }}
  .schedule-table td {{ color: var(--text); font-weight: 300; }}
  .schedule-table td:first-child {{
    color: var(--gold);
    font-weight: 400;
    width: 120px;
  }}
  .schedule-table td:nth-child(2) {{
    color: var(--muted);
    width: 140px;
    font-size: 12px;
  }}

  /* LINE機能一覧 */
  .line-section {{ margin-bottom: 56px; }}
  .line-grid {{
    display: grid;
    grid-template-columns: repeat(2, 1fr);
    gap: 1px;
    background: var(--border);
    border: 1px solid var(--border);
    border-radius: 2px;
    overflow: hidden;
  }}
  .line-item {{
    background: var(--card);
    padding: 20px 24px;
    display: flex;
    gap: 16px;
    align-items: flex-start;
  }}
  .line-keyword {{
    font-family: monospace;
    font-size: 12px;
    background: #0f0f0f;
    border: 1px solid var(--border);
    color: var(--gold);
    padding: 4px 10px;
    border-radius: 2px;
    white-space: nowrap;
    flex-shrink: 0;
  }}
  .line-desc {{
    font-size: 13px;
    color: var(--muted);
    line-height: 1.6;
    padding-top: 2px;
  }}

  /* フッター */
  footer {{
    border-top: 1px solid var(--border);
    padding: 24px 48px;
    display: flex;
    justify-content: space-between;
    align-items: center;
  }}
  footer p {{ font-size: 11px; color: var(--muted); letter-spacing: 1px; }}
  .status-dot {{
    display: inline-block;
    width: 6px; height: 6px;
    background: #4caf50;
    border-radius: 50%;
    margin-right: 6px;
    animation: pulse 2s infinite;
  }}
  @keyframes pulse {{
    0%, 100% {{ opacity: 1; }}
    50% {{ opacity: 0.4; }}
  }}

  /* クイックアクセス */
  .quick-section {{ margin-bottom: 56px; }}
  .quick-grid {{
    display: flex;
    gap: 16px;
    flex-wrap: wrap;
  }}
  .quick-btn {{
    display: flex;
    align-items: center;
    gap: 12px;
    background: var(--card);
    border: 1px solid var(--border);
    border-radius: 2px;
    padding: 20px 28px;
    color: var(--text);
    text-decoration: none;
    font-size: 14px;
    font-family: 'Noto Sans JP', sans-serif;
    font-weight: 300;
    transition: border-color 0.3s, color 0.3s;
    letter-spacing: 1px;
  }}
  .quick-btn:hover {{
    border-color: var(--gold);
    color: var(--gold-light);
  }}
  .quick-btn-icon {{
    font-size: 20px;
    line-height: 1;
  }}

  @media (max-width: 768px) {{
    header {{ padding: 24px; flex-direction: column; align-items: flex-start; gap: 16px; }}
    main {{ padding: 24px; }}
    .departments {{ grid-template-columns: 1fr; }}
    .goal-card {{ flex-direction: column; gap: 24px; align-items: flex-start; }}
    .goal-divider {{ width: 40px; height: 1px; }}
    .line-grid {{ grid-template-columns: 1fr; }}
    .quick-grid {{ flex-direction: column; }}
    footer {{ flex-direction: column; gap: 8px; padding: 24px; }}
  }}
</style>
</head>
<body>

<header>
  <div class="logo">
    Maki &amp; Co.
    <span>Private Company Dashboard</span>
  </div>
  <div class="timestamp">
    <strong>{now.strftime('%Y.%m.%d')}</strong>
    {now.strftime('%H:%M')} JST
  </div>
</header>

<main>

  <!-- クイックアクセス -->
  <div class="quick-section">
    <p class="section-title">Quick Access</p>
    <div class="quick-grid">
      <a class="quick-btn" href="/game" target="_blank">
        <span class="quick-btn-icon">🎮</span>
        <span>まるちゃんワールド</span>
      </a>
      <a class="quick-btn" href="/office" target="_blank">
        <span class="quick-btn-icon">🏢</span>
        <span>会社組織図</span>
      </a>
    </div>
  </div>

  <!-- ミッション -->
  <div class="mission">
    <h2>Mission</h2>
    <p>副業収入を<em>月50万円</em>以上に育て、<br>
    <em>海外移住・開業</em>という未来の自由を手に入れる。</p>
  </div>

  <!-- 月収目標 -->
  <div class="goal-section">
    <p class="section-title">Revenue Target</p>
    <div class="goal-card">
      <div>
        <div class="goal-label">Monthly Goal</div>
        <div class="goal-amount">50<span>万円 / 月</span></div>
      </div>
      <div class="goal-divider"></div>
      <div class="goal-vision">
        <strong>物販部</strong>　40〜50万円（最優先）<br>
        <strong>ブログ部</strong>　数万円（蓄積型・長期資産）<br>
        <strong>秘書部</strong>　時間節約・業務自動化
      </div>
    </div>
  </div>

  <!-- 部署 -->
  <div class="departments">
    <div class="dept-card">
      <div class="dept-priority">★★★ 最優先 — 物販部</div>
      <div class="dept-name">eBay Sales</div>
      <div class="dept-target">月収目標 <strong>40〜50万円</strong></div>
      <ul class="dept-items">
        <li>メルカリ仕入れ → eBay販売</li>
        <li>無在庫→有在庫移行中</li>
        <li>4月目標：250品出品</li>
        <li>利益目標：1〜5万円</li>
      </ul>
    </div>
    <div class="dept-card">
      <div class="dept-priority">★★ — ブログ部</div>
      <div class="dept-name">Blog &amp; SEO</div>
      <div class="dept-target">月収目標 <strong>数万円（蓄積型）</strong></div>
      <ul class="dept-items">
        <li>睡眠ブログ：約120記事</li>
        <li>セキスイブログ：34記事</li>
        <li>アフィリエイト収益化進行中</li>
        <li>Search Console流入増が目標</li>
      </ul>
    </div>
    <div class="dept-card">
      <div class="dept-priority">★★ — 秘書部</div>
      <div class="dept-name">Secretary</div>
      <div class="dept-target">役割 <strong>時間節約・完全自動化</strong></div>
      <ul class="dept-items">
        <li>LINEボット稼働中</li>
        <li>毎朝7時：予定通知</li>
        <li>Googleカレンダー管理</li>
        <li>ブログ自動投稿</li>
      </ul>
    </div>
  </div>

  <!-- 週次スケジュール -->
  <div class="schedule-section">
    <p class="section-title">Weekly Schedule</p>
    <table class="schedule-table">
      <thead>
        <tr>
          <th>Day</th>
          <th>Department</th>
          <th>Task</th>
        </tr>
      </thead>
      <tbody>
        <tr><td>毎朝 7:00</td><td>秘書部</td><td>今日の予定をLINEに自動送信</td></tr>
        <tr><td>毎週日曜 20:00</td><td>秘書部</td><td>3日以内の予定リマインド</td></tr>
        <tr><td>月・木 8:00</td><td>ブログ部</td><td>睡眠ブログ 新規記事自動投稿 + SNS投稿セット送信</td></tr>
        <tr><td>水・土 8:00</td><td>ブログ部</td><td>睡眠ブログ リライト自動投稿 + SNS投稿セット送信</td></tr>
        <tr><td>木曜日</td><td>ブログ部</td><td>セキスイブログ 記事投稿</td></tr>
        <tr><td>随時</td><td>物販部</td><td>eBayタイトル生成・出品サポート</td></tr>
      </tbody>
    </table>
  </div>

  <!-- LINE機能 -->
  <div class="line-section">
    <p class="section-title">LINE Functions — @296wjwwj</p>
    <div class="line-grid">
      <div class="line-item">
        <span class="line-keyword">睡眠記事</span>
        <span class="line-desc">睡眠ブログメニュー表示（新規作成 / リライト）</span>
      </div>
      <div class="line-item">
        <span class="line-keyword">セキスイ記事</span>
        <span class="line-desc">セキスイブログ記事作成フロー起動</span>
      </div>
      <div class="line-item">
        <span class="line-keyword">画像送信</span>
        <span class="line-desc">チラシからイベント情報を読み取り</span>
      </div>
      <div class="line-item">
        <span class="line-keyword">登録して</span>
        <span class="line-desc">読み取ったイベントをGoogleカレンダーに登録</span>
      </div>
      <div class="line-item">
        <span class="line-keyword">〇〇の期限 4月10日</span>
        <span class="line-desc">申込期限をカレンダーに登録＋リマインド設定</span>
      </div>
      <div class="line-item">
        <span class="line-keyword">自由入力</span>
        <span class="line-desc">Claudeが返答（カレンダー情報も参照）</span>
      </div>
    </div>
  </div>

</main>

<footer>
  <p><span class="status-dot"></span>All systems operational — Render / maki-hisho.onrender.com</p>
  <p>© 2026 Maki &amp; Co. — Built with Claude Code</p>
</footer>

</body>
</html>'''

DASHBOARD_PASSWORD = os.environ.get('DASHBOARD_PASSWORD', 'maki1234')

@company_bp.route('/dashboard', methods=['GET', 'POST'])
def dashboard():
    pw = request.args.get('pw') or request.form.get('pw', '')
    if pw != DASHBOARD_PASSWORD:
        err = '<p class="err">パスワードが違います</p>' if request.method == 'POST' else ''
        return f'''<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Dashboard — Login</title>
<link href="https://fonts.googleapis.com/css2?family=Noto+Sans+JP:wght@300;400&display=swap" rel="stylesheet">
<style>
  body {{ background: #f4f0eb; display: flex; align-items: center; justify-content: center; min-height: 100vh; font-family: 'Noto Sans JP', sans-serif; margin: 0; }}
  .box {{ background: #fff; border: 1px solid #e2dbd3; border-radius: 10px; padding: 40px 36px; width: 320px; text-align: center; }}
  h2 {{ font-size: 16px; font-weight: 400; color: #3d3530; margin-bottom: 24px; letter-spacing: 1px; }}
  input {{ width: 100%; padding: 10px 14px; border: 1px solid #e2dbd3; border-radius: 6px; font-size: 14px; margin-bottom: 14px; box-sizing: border-box; }}
  button {{ width: 100%; padding: 10px; background: #7a9e6e; border: none; border-radius: 6px; color: #fff; font-size: 14px; cursor: pointer; font-family: inherit; }}
  button:hover {{ background: #5c7d52; }}
  .err {{ color: #b56b5e; font-size: 12px; margin-top: 10px; }}
</style>
</head>
<body>
<div class="box">
  <h2>Maki &amp; Co. Dashboard</h2>
  <form method="POST" action="/dashboard">
    <input type="password" name="pw" placeholder="パスワード" autofocus>
    <button type="submit">ログイン</button>
    {err}
  </form>
</div>
</body>
</html>'''

    try:
        events = get_upcoming_events(days=7)
    except Exception:
        events = []

    now = datetime.datetime.now(JST)
    today = now.date()
    weekdays = ["月", "火", "水", "木", "金", "土", "日"]
    today_str = f"{today.month}月{today.day}日（{weekdays[today.weekday()]}）"

    today_events = []
    future_events_by_date = {}
    for event in events:
        start_raw = event['start'].get('date') or event['start'].get('dateTime', '')[:10]
        try:
            evt_date = datetime.date.fromisoformat(start_raw)
        except Exception:
            continue
        title = event.get('summary', '（タイトルなし）')
        if evt_date == today:
            if 'T' in event['start'].get('dateTime', ''):
                dt = datetime.datetime.fromisoformat(event['start']['dateTime']).astimezone(JST)
                today_events.append(f"{dt.strftime('%H:%M')} {title}")
            else:
                today_events.append(title)
        elif evt_date > today:
            if evt_date not in future_events_by_date:
                future_events_by_date[evt_date] = []
            future_events_by_date[evt_date].append(title)

    today_html = ''.join(f'<li>{e}</li>' for e in today_events) or '<li class="empty">予定なし</li>'
    future_html = ''
    for d in sorted(future_events_by_date.keys()):
        future_html += f'<div class="date-header">{d.month}月{d.day}日（{weekdays[d.weekday()]}）</div>'
        for t in future_events_by_date[d]:
            future_html += f'<div class="event-item">📌 {t}</div>'
    if not future_html:
        future_html = '<div class="empty">今後7日間の予定なし</div>'

    link_groups = [
        ('Blog', [
            ('WordPress（薬膳）', 'https://foodmakehealth.com/wp-admin/', False),
            ('WordPress（セキスイ）', 'https://order-sekisui.com/wp-admin/', False),
            ('Notion', 'https://notion.so/', True),
        ]),
        ('Medical', [
            ('CareNet', 'https://www.carenet.com/', False),
            ('MedPeer', 'https://medpeer.jp/keymessage/list/point3', False),
        ]),
        ('eBay', [
            ('メルハント', 'https://auction2024.com/admin/main.php', True),
            ('物販ブースター', 'https://buppan-booster.com/list-sell', True),
            ('メルカリ', 'https://jp.mercari.com/', False),
            ('eBay出品中', 'https://www.ebay.com/mys/active', True),
            ('eBay売上管理', '/ebay-dashboard', True),
            ('仕入れ記録', '/purchase', True),
            ('利益計算シート', 'https://docs.google.com/spreadsheets/d/1pPAVYxeETPq6VVtg7Jd7eapXZf8lgTttndRN6Cd4wqI/edit?gid=973340213#gid=973340213', True),
            ('CPASS', 'https://cpass.ebay.com/order/paid', True),
            ('add×one', 'https://tools.aione.co.jp/buppan-tool/', True),
        ]),
        ('Amazon', [
            ('Amazon.co.jp', 'https://www.amazon.co.jp/', False),
            ('セラーセントラル', 'https://sellercentral.amazon.co.jp/home', True),
            ('ハピタス', 'https://hapitas.jp/', True),
        ]),
        ('教材', [
            ('Amazon教材', 'https://utage-system.com/members/iMHiJSHzmtIn/login', True),
            ('TikTok×アフィ教材', 'https://utage-system.com/members/EfonMROIkXSq/login', True),
            ('AI×note教材', 'https://utage-system.com/members/hkhySPyvCE3y/login', True),
            ('eBay教科書', 'https://brain-market.com/u/hyexport_ebay/a/bzATO2QjMgoTZsNWa0JXY?discount_code=48de', True),
        ]),
        ('Finance', [
            ('マネーフォワード', 'https://moneyforward.com/', True),
        ]),
        ('まきの会社', [
            ('まるちゃんワールド', 'https://maki-hisho.onrender.com/game', True),
            ('会社組織図', 'https://maki-hisho.onrender.com/office', False),
            ('会社LP', 'https://maki-hisho.onrender.com/company', False),
        ]),
    ]
    links_rows = ''
    for label, items in link_groups:
        btns = ''.join(
            f'<a href="{url}" target="_blank" class="link-btn {"link-btn-hl" if hl else ""}">{name}</a>'
            for name, url, hl in items
        )
        links_rows += f'<div class="links-row"><span class="links-category">{label}</span>{btns}</div>'

    return f'''<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<meta http-equiv="refresh" content="3600">
<title>Maki &amp; Co. | Dashboard</title>
<link href="https://fonts.googleapis.com/css2?family=Noto+Sans+JP:wght@300;400;500&family=Lato:wght@300;400&display=swap" rel="stylesheet">
<style>
  :root {{
    --bg: #f4f0eb; --surface: #faf8f5; --card: #ffffff;
    --border: #e2dbd3; --border-light: #ede8e2;
    --text: #3d3530; --text-sub: #7a6f68; --muted: #b0a89f;
    --accent: #7a9e6e; --accent-dark: #5c7d52; --accent-red: #b56b5e;
    --link-bg: #f0ece7;
  }}
  * {{ margin: 0; padding: 0; box-sizing: border-box; }}
  body {{ background: var(--bg); color: var(--text); font-family: 'Noto Sans JP', sans-serif; font-weight: 300; min-height: 100vh; font-size: 14px; line-height: 1.7; }}
  header {{ background: var(--surface); border-bottom: 1px solid var(--border); padding: 20px 40px; display: flex; justify-content: space-between; align-items: center; }}
  .logo {{ font-family: 'Lato', sans-serif; font-size: 18px; font-weight: 300; color: var(--text); letter-spacing: 3px; text-transform: uppercase; }}
  .logo span {{ display: block; font-size: 10px; color: var(--muted); letter-spacing: 2px; margin-top: 2px; }}
  .date-display {{ text-align: right; }}
  .date-display strong {{ display: block; font-size: 18px; font-weight: 400; }}
  .date-display small {{ font-size: 11px; color: var(--muted); letter-spacing: 1px; }}
  .links-bar {{ background: var(--surface); border-bottom: 2px solid var(--border); padding: 0 40px; }}
  .links-row {{ display: flex; align-items: center; flex-wrap: wrap; padding: 7px 0; border-bottom: 1px solid var(--border-light); gap: 5px; }}
  .links-row:last-child {{ border-bottom: none; }}
  .links-category {{ font-size: 10px; letter-spacing: 2px; text-transform: uppercase; color: var(--muted); min-width: 60px; margin-right: 6px; flex-shrink: 0; }}
  .link-btn {{ display: inline-block; background: var(--link-bg); border: 1px solid var(--border); color: var(--text-sub); padding: 3px 10px; border-radius: 4px; text-decoration: none; font-size: 11px; transition: background 0.15s, color 0.15s; }}
  .link-btn:hover {{ background: var(--accent); border-color: var(--accent); color: #fff; }}
  .link-btn-hl {{ background: #fff3e0; border-color: #ff8c00; color: #d05000; font-weight: 600; }}
  .link-btn-hl:hover {{ background: #ff8c00; border-color: #ff8c00; color: #fff; }}
  main {{ padding: 28px 40px; }}
  .grid {{ display: grid; grid-template-columns: 1fr 1fr; gap: 18px; }}
  .card {{ background: var(--card); border: 1px solid var(--border); border-radius: 8px; overflow: hidden; }}
  .card-header {{ background: var(--link-bg); border-bottom: 1px solid var(--border); padding: 10px 18px; font-size: 11px; letter-spacing: 2px; text-transform: uppercase; color: var(--text-sub); font-weight: 400; }}
  .card-body {{ padding: 16px 18px; }}
  ul {{ list-style: none; }}
  li {{ padding: 6px 0; border-bottom: 1px solid var(--border-light); font-size: 13px; }}
  li:last-child {{ border-bottom: none; }}
  .empty {{ color: var(--muted); font-style: italic; }}
  .date-header {{ font-size: 11px; font-weight: 500; color: var(--accent-dark); margin: 12px 0 4px; letter-spacing: 1px; }}
  .event-item {{ padding: 4px 0 4px 10px; font-size: 13px; border-left: 3px solid var(--accent); margin-left: 2px; border-bottom: 1px solid var(--border-light); }}
  .updated {{ text-align: right; font-size: 11px; color: var(--muted); padding: 12px 40px; border-top: 1px solid var(--border); background: var(--surface); }}
  @media (max-width: 760px) {{
    header, .links-bar, main, .updated {{ padding-left: 16px; padding-right: 16px; }}
    .grid {{ grid-template-columns: 1fr; }}
    header {{ flex-direction: column; align-items: flex-start; gap: 8px; }}
  }}
</style>
</head>
<body>
<header>
  <div class="logo">Maki &amp; Co.<span>Daily Operations Dashboard</span></div>
  <div class="date-display"><strong>{today_str}</strong><small>Today</small></div>
</header>
<div class="links-bar">{links_rows}</div>
<main>
  <div class="grid">
    <div class="card">
      <div class="card-header">Today&#x27;s Schedule</div>
      <div class="card-body"><ul>{today_html}</ul></div>
    </div>
    <div class="card">
      <div class="card-header">Upcoming — Next 7 Days</div>
      <div class="card-body">{future_html}</div>
    </div>
  </div>
</main>
<div class="updated">最終更新: {now.strftime('%Y-%m-%d %H:%M')}</div>
</body>
</html>'''

@company_bp.route('/game')
def game_index():
    return send_from_directory('.', 'index.html')

@company_bp.route('/game/<path:filename>')
def game_files(filename):
    return send_from_directory('.', filename)

@company_bp.route('/office')
def company_office():
    return send_from_directory('.', 'company_office.html')

@company_bp.route('/instagram-roadmap')
def instagram_roadmap():
    return send_from_directory('.', 'instagram_roadmap.html')

@company_bp.route('/x-study-note')
def x_study_note():
    return send_from_directory('.', 'x_study_note.html')

@company_bp.route('/web-marketing')
def web_marketing_notes():
    return send_from_directory('.', 'web_marketing_notes.html')

@company_bp.route('/note-roadmap')
def note_roadmap():
    import markdown
    with open('note_roadmap_pome.md', encoding='utf-8') as f:
        content = f.read()
    html_body = markdown.markdown(content, extensions=['tables'])
    html = f"""<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>noteロードマップまとめ</title>
<style>
  body {{ font-family: -apple-system, BlinkMacSystemFont, 'Hiragino Sans', sans-serif; max-width: 800px; margin: 40px auto; padding: 0 20px; line-height: 1.8; color: #333; }}
  h1 {{ color: #2c3e50; border-bottom: 3px solid #e74c3c; padding-bottom: 10px; }}
  h2 {{ color: #2c3e50; border-left: 4px solid #e74c3c; padding-left: 12px; margin-top: 40px; }}
  h3 {{ color: #555; }}
  table {{ border-collapse: collapse; width: 100%; margin: 16px 0; }}
  th {{ background: #2c3e50; color: white; padding: 10px; text-align: left; }}
  td {{ border: 1px solid #ddd; padding: 10px; }}
  tr:nth-child(even) {{ background: #f9f9f9; }}
  code {{ background: #f4f4f4; padding: 2px 6px; border-radius: 3px; font-family: monospace; }}
  pre {{ background: #f4f4f4; padding: 16px; border-radius: 6px; overflow-x: auto; }}
  blockquote {{ border-left: 4px solid #e74c3c; margin: 0; padding-left: 16px; color: #666; }}
  ul, ol {{ padding-left: 24px; }}
  li {{ margin: 6px 0; }}
</style>
</head>
<body>
{html_body}
</body>
</html>"""
    return html

@company_bp.route('/game/rhythm')
def game_rhythm():
    return send_from_directory('.', 'maruchan_rhythm.html')

@company_bp.route('/auth/pinterest')
def auth_pinterest():
    app_id = os.environ.get('PINTEREST_APP_ID', '')
    if not app_id:
        return 'PINTEREST_APP_ID が設定されていません。Renderに設定してください。', 400
    redirect_uri = 'https://maki-hisho.onrender.com/auth/pinterest/callback'
    auth_url = (
        f"https://www.pinterest.com/oauth/"
        f"?client_id={app_id}"
        f"&redirect_uri={redirect_uri}"
        f"&response_type=code"
        f"&scope=pins:write,boards:read"
        f"&state=maki"
    )
    return f'''<html><body>
<h2>Pinterest認証</h2>
<p><a href="{auth_url}" style="font-size:20px;padding:10px;background:#e60023;color:#fff;text-decoration:none;border-radius:6px;">
Pinterestで認証する</a></p>
</body></html>'''


@company_bp.route('/auth/pinterest/callback')
def auth_pinterest_callback():
    code = request.args.get('code')
    if not code:
        return f'エラー: codeが取得できませんでした。{request.args}', 400
    app_id = os.environ.get('PINTEREST_APP_ID')
    app_secret = os.environ.get('PINTEREST_APP_SECRET')
    redirect_uri = 'https://maki-hisho.onrender.com/auth/pinterest/callback'
    import base64
    creds = base64.b64encode(f"{app_id}:{app_secret}".encode()).decode()
    res = requests.post(
        'https://api.pinterest.com/v5/oauth/token',
        headers={'Authorization': f'Basic {creds}', 'Content-Type': 'application/x-www-form-urlencoded'},
        data={'grant_type': 'authorization_code', 'code': code, 'redirect_uri': redirect_uri},
        timeout=15
    )
    if res.status_code == 200:
        data = res.json()
        return f'''<html><body>
<h2>✅ 認証成功！</h2>
<p>以下をRenderの環境変数にコピペしてください：</p>
<p><b>PINTEREST_REFRESH_TOKEN:</b><br>
<textarea rows="3" cols="80">{data.get('refresh_token', '')}</textarea></p>
<p><small>（access_tokenは自動更新されるので不要です）</small></p>
<p>次に <a href="/auth/pinterest/boards">ボードIDを確認する</a></p>
</body></html>'''
    return f'エラー: {res.status_code} {res.text}', 400


@company_bp.route('/auth/pinterest/boards')
def auth_pinterest_boards():
    access_token = get_pinterest_access_token()
    if not access_token:
        return 'PINTEREST_REFRESH_TOKEN または PINTEREST_ACCESS_TOKEN が設定されていません。', 400
    res = requests.get(
        'https://api.pinterest.com/v5/boards',
        headers={'Authorization': f'Bearer {access_token}'},
        params={'page_size': 25},
        timeout=15
    )
    if res.status_code == 200:
        boards = res.json().get('items', [])
        rows = ''.join(
            f"<tr><td>{b['name']}</td><td><code>{b['id']}</code></td></tr>"
            for b in boards
        )
        return f'''<html><body>
<h2>Pinterestボード一覧</h2>
<table border="1" cellpadding="6">
<tr><th>ボード名</th><th>ID（Renderに設定する値）</th></tr>
{rows}
</table>
<p>対応する環境変数：<br>
季節の養生 → PINTEREST_BOARD_SEASONAL<br>
薬膳レシピ → PINTEREST_BOARD_RECIPE<br>
薬膳の基礎知識 → PINTEREST_BOARD_BASICS<br>
薬膳資格 → PINTEREST_BOARD_QUALIF</p>
</body></html>'''
    return f'エラー: {res.status_code} {res.text}', 400

@company_bp.route('/trigger/morning', methods=['GET', 'POST'])
def trigger_morning():
    return 'Disabled', 410


def create_rich_menu_image():
    img_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'static', 'richmenu.png')
    with open(img_path, 'rb') as f:
        return f.read()


def setup_rich_menu():
    token = os.environ.get('LINE_CHANNEL_ACCESS_TOKEN', '')
    auth = {'Authorization': f'Bearer {token}'}
    try:
        res = requests.get('https://api.line.me/v2/bot/richmenu/list', headers=auth)
        for rm in res.json().get('richmenus', []):
            requests.delete(f"https://api.line.me/v2/bot/richmenu/{rm['richMenuId']}", headers=auth)
    except Exception:
        pass
    rich_menu = {
        "size": {"width": 2500, "height": 843},
        "selected": True,
        "name": "まきの秘書メニュー",
        "chatBarText": "メニュー",
        "areas": [
            {"bounds": {"x": 0,    "y": 0,   "width": 833, "height": 421},
             "action": {"type": "message", "text": "ebayリサーチ"}},
            {"bounds": {"x": 833,  "y": 0,   "width": 834, "height": 421},
             "action": {"type": "message", "text": "roomタグ"}},
            {"bounds": {"x": 1667, "y": 0,   "width": 833, "height": 421},
             "action": {"type": "uri", "uri": "https://maki-hisho.onrender.com/ebay-calc"}},
            {"bounds": {"x": 0,    "y": 421, "width": 833, "height": 422},
             "action": {"type": "message", "text": "睡眠記事"}},
            {"bounds": {"x": 833,  "y": 421, "width": 834, "height": 422},
             "action": {"type": "message", "text": "セキスイ記事"}},
            {"bounds": {"x": 1667, "y": 421, "width": 833, "height": 422},
             "action": {"type": "uri", "uri": "https://maki-hisho.onrender.com/ebay-dashboard"}},
        ]
    }
    res = requests.post('https://api.line.me/v2/bot/richmenu',
                        headers={**auth, 'Content-Type': 'application/json'}, json=rich_menu)
    if res.status_code != 200:
        return False, f"作成失敗: {res.status_code} {res.text}"
    rm_id = res.json()['richMenuId']
    image_data = create_rich_menu_image()
    res = requests.post(f'https://api-data.line.me/v2/bot/richmenu/{rm_id}/content',
                        headers={**auth, 'Content-Type': 'image/png'}, data=image_data)
    if res.status_code != 200:
        return False, f"画像アップロード失敗: {res.status_code} {res.text}"
    res = requests.post(f'https://api.line.me/v2/bot/user/all/richmenu/{rm_id}', headers=auth)
    if res.status_code != 200:
        return False, f"デフォルト設定失敗: {res.status_code} {res.text}"
    return True, rm_id


@company_bp.route('/setup-richmenu')
def setup_richmenu_endpoint():
    ok, result = setup_rich_menu()
    if ok:
        return f'<html><head><meta charset="utf-8"></head><body><h2>✅ リッチメニュー登録完了！</h2><p>ID: {result}</p></body></html>'
    return f'<html><head><meta charset="utf-8"></head><body><h2>❌ エラー</h2><p>{result}</p></body></html>', 500


@company_bp.route('/richmenu-preview')
def richmenu_preview():
    from flask import send_file
    import io, traceback
    try:
        data = create_rich_menu_image()
        return send_file(io.BytesIO(data), mimetype='image/png')
    except Exception:
        return f'<pre>{traceback.format_exc()}</pre>', 500


# ===== 仕入れ記録ページ（PC用） =====

@company_bp.route('/purchase')
def purchase_page():
    return '''<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>仕入れ記録 | まきの会社</title>
<style>
  * { margin: 0; padding: 0; box-sizing: border-box; }
  body { background: #0f0f0f; color: #e0e0e0; font-family: 'Noto Sans JP', sans-serif; min-height: 100vh; display: flex; flex-direction: column; align-items: center; padding: 40px 20px; }
  h1 { font-size: 1.6rem; color: #c9a84c; margin-bottom: 8px; }
  .subtitle { color: #888; font-size: 0.9rem; margin-bottom: 32px; }
  .card { background: #1a1a1a; border: 1px solid #2a2a2a; border-radius: 12px; padding: 28px; width: 100%; max-width: 560px; margin-bottom: 20px; }
  label { display: block; font-size: 0.85rem; color: #aaa; margin-bottom: 8px; }
  .target-btns { display: flex; gap: 12px; margin-bottom: 24px; }
  .target-btn { flex: 1; padding: 12px; border: 2px solid #333; border-radius: 8px; background: transparent; color: #e0e0e0; cursor: pointer; font-size: 0.95rem; transition: all 0.2s; }
  .target-btn.active { border-color: #c9a84c; color: #c9a84c; background: rgba(201,168,76,0.08); }
  .drop-area { border: 2px dashed #333; border-radius: 8px; padding: 40px 20px; text-align: center; cursor: pointer; transition: border-color 0.2s; margin-bottom: 20px; }
  .drop-area:hover, .drop-area.dragover { border-color: #c9a84c; }
  .drop-area p { color: #666; font-size: 0.9rem; margin-top: 8px; }
  #preview { max-width: 100%; border-radius: 8px; margin-top: 12px; display: none; }
  #pdf-name { color: #c9a84c; font-size: 0.9rem; margin-top: 12px; display: none; }
  #file-input { display: none; }
  .btn-primary { width: 100%; padding: 14px; background: #c9a84c; color: #000; border: none; border-radius: 8px; font-size: 1rem; font-weight: bold; cursor: pointer; transition: opacity 0.2s; }
  .btn-primary:hover { opacity: 0.85; }
  .btn-primary:disabled { opacity: 0.4; cursor: not-allowed; }
  .btn-secondary { width: 100%; padding: 12px; background: transparent; color: #c9a84c; border: 1px solid #c9a84c; border-radius: 8px; font-size: 0.95rem; cursor: pointer; margin-top: 10px; }
  .result-card { background: #111; border: 1px solid #2a2a2a; border-radius: 8px; padding: 16px; margin-bottom: 16px; }
  .result-card h3 { color: #c9a84c; font-size: 0.9rem; margin-bottom: 10px; }
  .item-row { display: flex; justify-content: space-between; align-items: baseline; padding: 6px 0; border-bottom: 1px solid #1e1e1e; font-size: 0.9rem; }
  .item-row:last-child { border-bottom: none; }
  .item-label { color: #888; font-size: 0.8rem; }
  .loading { text-align: center; color: #888; padding: 20px; }
  .spinner { border: 3px solid #333; border-top: 3px solid #c9a84c; border-radius: 50%; width: 32px; height: 32px; animation: spin 0.8s linear infinite; margin: 12px auto; }
  @keyframes spin { to { transform: rotate(360deg); } }
  .success { color: #4caf50; text-align: center; font-size: 1.1rem; padding: 20px; }
  .error { color: #f44336; font-size: 0.85rem; margin-top: 8px; }
  a.back { color: #888; font-size: 0.85rem; text-decoration: none; margin-bottom: 24px; }
  a.back:hover { color: #c9a84c; }
</style>
</head>
<body>
<a href="/company" class="back">← ダッシュボードに戻る</a>
<h1>仕入れ記録</h1>
<p class="subtitle">レシートや領収書の画像をアップロードするとスプレッドシートに自動追加します</p>

<div class="card">
  <label>① 追加先リスト</label>
  <div class="target-btns">
    <button class="target-btn active" onclick="selectTarget('amazon', this)">📦 Amazon仕入れ</button>
    <button class="target-btn" onclick="selectTarget('mercari', this)">🛍 メルカリ仕入れ</button>
  </div>

  <label>② レシート・領収書の画像</label>
  <div class="drop-area" id="drop-area" onclick="document.getElementById('file-input').click()">
    <span style="font-size:2rem">📷</span>
    <p>クリックまたはドラッグ＆ドロップで画像・PDFを選択</p>
    <p style="font-size:0.8rem;margin-top:4px">JPG / PNG / WEBP / PDF 対応</p>
    <img id="preview" />
    <p id="pdf-name"></p>
  </div>
  <input type="file" id="file-input" accept="image/*,application/pdf" onchange="onFileSelected(this)">

  <button class="btn-primary" id="ocr-btn" onclick="runOcr()" disabled>読み取り開始</button>
</div>

<div id="result-area" style="display:none;width:100%;max-width:560px"></div>

<script>
let selectedTarget = 'amazon';
let parsedItems = null;
let fileBase64 = null;
let fileMediaType = null;

function selectTarget(t, el) {
  selectedTarget = t;
  document.querySelectorAll('.target-btn').forEach(b => b.classList.remove('active'));
  el.classList.add('active');
}

function onFileSelected(input) {
  const file = input.files[0];
  if (!file) return;
  fileMediaType = file.type === 'application/pdf' ? 'application/pdf' : (file.type || 'image/jpeg');
  const reader = new FileReader();
  reader.onload = e => {
    fileBase64 = e.target.result.split(',')[1];
    const preview = document.getElementById('preview');
    const pdfName = document.getElementById('pdf-name');
    if (fileMediaType === 'application/pdf') {
      preview.style.display = 'none';
      pdfName.textContent = '📄 ' + file.name;
      pdfName.style.display = 'block';
    } else {
      preview.src = e.target.result;
      preview.style.display = 'block';
      pdfName.style.display = 'none';
    }
    document.getElementById('ocr-btn').disabled = false;
  };
  reader.readAsDataURL(file);
}

const drop = document.getElementById('drop-area');
drop.addEventListener('dragover', e => { e.preventDefault(); drop.classList.add('dragover'); });
drop.addEventListener('dragleave', () => drop.classList.remove('dragover'));
drop.addEventListener('drop', e => {
  e.preventDefault();
  drop.classList.remove('dragover');
  const file = e.dataTransfer.files[0];
  if (file) {
    const dt = new DataTransfer();
    dt.items.add(file);
    document.getElementById('file-input').files = dt.files;
    onFileSelected(document.getElementById('file-input'));
  }
});

async function runOcr() {
  if (!fileBase64) return;
  const area = document.getElementById('result-area');
  area.style.display = 'block';
  area.innerHTML = '<div class="loading"><div class="spinner"></div>読み取り中...</div>';
  document.getElementById('ocr-btn').disabled = true;

  try {
    const res = await fetch('/api/purchase/ocr', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ image_base64: fileBase64, media_type: fileMediaType, target: selectedTarget })
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || 'OCRエラー');
    parsedItems = data.items;
    showConfirm(data.items);
  } catch (e) {
    area.innerHTML = `<div class="card"><p class="error">❌ ${e.message}</p></div>`;
    document.getElementById('ocr-btn').disabled = false;
  }
}

function priceStr(item) {
  const unit = Number(item.unit_price ?? item.price ?? 0);
  const qty = Number(item.quantity ?? 1);
  if (qty > 1) return `${unit.toLocaleString()}円 × ${qty}個 = <strong>${(unit * qty).toLocaleString()}円</strong>`;
  return `${unit.toLocaleString()}円`;
}

function toggleAll(checked) {
  document.querySelectorAll('.item-check').forEach(cb => cb.checked = checked);
  updateAddBtn();
}

function updateAddBtn() {
  const any = [...document.querySelectorAll('.item-check')].some(cb => cb.checked);
  document.getElementById('add-btn').disabled = !any;
}

function idRow(item) {
  if (item.asin) return `<div class="item-row"><span class="item-label">ASIN</span><span style="color:#c9a84c;font-family:monospace">${item.asin}</span></div>`;
  if (item.jan)  return `<div class="item-row"><span class="item-label">JAN</span><span style="color:#888">${item.jan}</span></div>`;
  return '';
}

function showConfirm(items) {
  const defTarget = selectedTarget;
  let html = `<div class="card">
    <h3 style="color:#c9a84c;margin-bottom:12px">読み取り結果</h3>
    <div style="display:flex;gap:8px;margin-bottom:16px">
      <button class="btn-secondary" style="margin:0;padding:8px 12px;font-size:0.8rem" onclick="toggleAll(true)">全て選択</button>
      <button class="btn-secondary" style="margin:0;padding:8px 12px;font-size:0.8rem" onclick="toggleAll(false)">全て解除</button>
    </div>`;
  items.forEach((item, i) => {
    html += `<div class="result-card" style="display:flex;gap:12px;align-items:flex-start">
      <input type="checkbox" class="item-check" data-idx="${i}" checked onchange="updateAddBtn()"
        style="margin-top:6px;width:18px;height:18px;accent-color:#c9a84c;flex-shrink:0">
      <div style="flex:1">
        <div class="item-row" style="align-items:center">
          <span class="item-label">商品名</span>
          <input class="name-input" data-idx="${i}" value="${item.name.replace(/"/g,'&quot;')}"
            style="background:#1a1a1a;border:1px solid #444;border-radius:4px;color:#e0e0e0;padding:4px 8px;font-size:0.88rem;width:60%;text-align:right">
        </div>
        <div class="item-row"><span class="item-label">店舗</span><span>${item.store}</span></div>
        <div class="item-row"><span class="item-label">価格</span><span>${priceStr(item)}</span></div>
        <div class="item-row"><span class="item-label">仕入れ日</span><span>${item.date}</span></div>
        ${idRow(item)}
        <div class="item-row" style="margin-top:6px">
          <span class="item-label">追加先</span>
          <span>
            <select class="dest-select" data-idx="${i}"
              style="background:#222;border:1px solid #444;border-radius:4px;color:#e0e0e0;padding:4px 8px;font-size:0.85rem">
              <option value="amazon" ${defTarget==='amazon'?'selected':''}>📦 Amazon</option>
              <option value="mercari" ${defTarget==='mercari'?'selected':''}>🛍 メルカリ</option>
            </select>
          </span>
        </div>
      </div>
    </div>`;
  });
  html += `<button class="btn-primary" id="add-btn" onclick="confirmAdd()" style="margin-top:16px">✅ 選択した商品を追加</button>
    <button class="btn-secondary" onclick="resetForm()">やり直す</button>
  </div>`;
  document.getElementById('result-area').innerHTML = html;
}

async function confirmAdd() {
  const checks = [...document.querySelectorAll('.item-check')].filter(cb => cb.checked);
  if (!checks.length) return;
  // 商品名の編集と追加先を反映
  const amazonItems = [], mercariItems = [];
  checks.forEach(cb => {
    const i = Number(cb.dataset.idx);
    const item = Object.assign({}, parsedItems[i]);
    const nameEl = document.querySelector(`.name-input[data-idx="${i}"]`);
    if (nameEl) item.name = nameEl.value.trim() || item.name;
    const destEl = document.querySelector(`.dest-select[data-idx="${i}"]`);
    const dest = destEl ? destEl.value : selectedTarget;
    if (dest === 'mercari') mercariItems.push(item);
    else amazonItems.push(item);
  });
  const area = document.getElementById('result-area');
  area.innerHTML = '<div class="loading"><div class="spinner"></div>追加中...</div>';
  try {
    let totalCount = 0;
    if (amazonItems.length) {
      const r = await fetch('/api/purchase/confirm', { method:'POST', headers:{'Content-Type':'application/json'},
        body: JSON.stringify({ items: amazonItems, target: 'amazon' }) });
      const d = await r.json();
      if (!r.ok) throw new Error(d.error || 'Amazonへの追加エラー');
      totalCount += d.count;
    }
    if (mercariItems.length) {
      const r = await fetch('/api/purchase/confirm', { method:'POST', headers:{'Content-Type':'application/json'},
        body: JSON.stringify({ items: mercariItems, target: 'mercari' }) });
      const d = await r.json();
      if (!r.ok) throw new Error(d.error || 'メルカリへの追加エラー');
      totalCount += d.count;
    }
    const breakdown = [amazonItems.length && `Amazon ${amazonItems.length}件`, mercariItems.length && `メルカリ ${mercariItems.length}件`].filter(Boolean).join(' / ');
    area.innerHTML = `<div class="card"><p class="success">✅ ${totalCount}件を追加しました！</p>
      <p style="text-align:center;color:#888;font-size:0.85rem;margin-top:4px">${breakdown}</p>
      <a href="https://docs.google.com/spreadsheets/d/1pPAVYxeETPq6VVtg7Jd7eapXZf8lgTttndRN6Cd4wqI/edit" target="_blank" style="display:block;text-align:center;color:#c9a84c;margin-top:12px">スプレッドシートを開く →</a>
      <button class="btn-secondary" style="margin-top:16px" onclick="resetForm()">続けて追加する</button>
    </div>`;
  } catch (e) {
    area.innerHTML = `<div class="card"><p class="error">❌ ${e.message}</p>
      <button class="btn-secondary" onclick="showConfirm(parsedItems)">戻る</button>
    </div>`;
  }
}

function resetForm() {
  parsedItems = null; fileBase64 = null; fileMediaType = null;
  document.getElementById('preview').style.display = 'none';
  document.getElementById('file-input').value = '';
  document.getElementById('ocr-btn').disabled = true;
  document.getElementById('result-area').style.display = 'none';
}
</script>
</body>
</html>'''


@company_bp.route('/api/purchase/ocr', methods=['POST'])
def purchase_ocr():
    import traceback
    try:
        from clients import anthropic_client
        from purchase_receipt import parse_receipt_with_vision, enrich_items_with_asin
        body = request.get_json(force=True)
        image_base64 = body.get('image_base64', '')
        media_type = body.get('media_type', 'image/jpeg')
        if not image_base64:
            return jsonify({'error': '画像データがありません'}), 400
        items = parse_receipt_with_vision(anthropic_client, image_base64, media_type)
        if not items:
            return jsonify({'error': '商品情報が読み取れませんでした。鮮明な画像を使ってください'}), 400
        items = enrich_items_with_asin(items)
        return jsonify({'items': items})
    except Exception as e:
        traceback.print_exc()
        return jsonify({'error': str(e)[:200]}), 500


@company_bp.route('/api/purchase/confirm', methods=['POST'])
def purchase_confirm():
    import traceback
    try:
        from purchase_receipt import append_to_amazon_sheet, append_to_mercari_sheet
        body = request.get_json(force=True)
        items = body.get('items', [])
        target = body.get('target', 'amazon')
        if not items:
            return jsonify({'error': '追加するデータがありません'}), 400
        if target == 'amazon':
            count = append_to_amazon_sheet(items)
        else:
            count = append_to_mercari_sheet(items)
        return jsonify({'count': count})
    except Exception as e:
        traceback.print_exc()
        return jsonify({'error': str(e)[:200]}), 500
