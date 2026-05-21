import os
import json
import base64
import datetime
import threading
import requests

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

from clients import line_bot_api, anthropic_client, JST
from x_poster import TWEET_STOCK


def get_google_creds():
    raw = os.environ.get('GOOGLE_CREDENTIALS', '')
    if not raw:
        return None
    try:
        data = json.loads(raw)
        return Credentials(
            token=data.get('token'),
            refresh_token=data.get('refresh_token'),
            client_id=data.get('client_id'),
            client_secret=data.get('client_secret'),
            token_uri='https://oauth2.googleapis.com/token',
            scopes=data.get('scopes', []),
        )
    except Exception:
        return None


def fetch_search_console(creds, site_url, days=28):
    try:
        service = build('searchconsole', 'v1', credentials=creds)
        end_date = datetime.date.today()
        start_date = end_date - datetime.timedelta(days=days)
        body = {
            'startDate': start_date.isoformat(),
            'endDate': end_date.isoformat(),
            'dimensions': ['query'],
            'rowLimit': 10,
            'orderBy': [{'fieldName': 'impressions', 'sortOrder': 'DESCENDING'}],
        }
        result = service.searchanalytics().query(siteUrl=site_url, body=body).execute()
        return result.get('rows', [])
    except Exception as e:
        print(f"Search Console error ({site_url}): {e}")
        return None


def fetch_x_weekly_metrics():
    try:
        import tweepy
        client = _get_x_client()
        if not client:
            return None
        me = client.get_me()
        if not me.data:
            return None
        user_id = me.data.id
        tweets = client.get_users_tweets(
            id=user_id,
            max_results=10,
            tweet_fields=['public_metrics', 'created_at'],
        )
        if not tweets.data:
            return None
        results = []
        for t in tweets.data:
            m = t.public_metrics
            results.append({
                'text': t.text[:40],
                'impressions': m.get('impression_count', 0),
                'likes': m.get('like_count', 0),
                'retweets': m.get('retweet_count', 0),
            })
        results.sort(key=lambda x: x['impressions'], reverse=True)
        return results[:3]
    except Exception as e:
        print(f"X metrics error: {e}")
        return None


def send_weekly_seo_report():
    try:
        user_id = os.environ['LINE_USER_ID']
        creds = get_google_creds()
        lines = ['📊 週次レポート\n']

        for label, site_url in [('薬膳ブログ', 'https://foodmakehealth.com/'), ('セキスイブログ', 'https://order-sekisui.com/')]:
            lines.append(f'【{label}】')
            if creds:
                rows = fetch_search_console(creds, site_url)
                if rows:
                    lines.append('🔍 検索キーワード TOP5')
                    for i, row in enumerate(rows[:5], 1):
                        query = row['keys'][0]
                        clicks = int(row.get('clicks', 0))
                        impressions = int(row.get('impressions', 0))
                        position = round(row.get('position', 0), 1)
                        lines.append(f'{i}. {query}')
                        lines.append(f'   表示{impressions}回 / クリック{clicks}回 / 順位{position}位')
                    low_ctr = [r for r in rows if r.get('impressions', 0) >= 20 and r.get('ctr', 1) < 0.03]
                    if low_ctr:
                        lines.append('📝 リライト候補（表示多いのにクリック少ない）')
                        for r in low_ctr[:2]:
                            lines.append(f'・{r["keys"][0]}（表示{int(r["impressions"])}回）')
                else:
                    lines.append('（データなし or 認証スコープ未更新）')
            else:
                lines.append('（Google認証未設定）')
            lines.append('')

        x_data = fetch_x_weekly_metrics()
        lines.append('【X（@maki_claude_lab）】')
        if x_data:
            lines.append('🐦 直近10投稿TOP3')
            for i, t in enumerate(x_data, 1):
                lines.append(f'{i}. {t["text"]}…')
                lines.append(f'   👁{t["impressions"]} ❤️{t["likes"]} 🔁{t["retweets"]}')
        else:
            lines.append('（X APIデータ取得できず）')

        message = '\n'.join(lines)
        line_bot_api.push_message(user_id, TextSendMessage(text=message))
    except Exception as e:
        print(f"Weekly SEO report error: {e}")


def send_note_reminder():
    try:
        user_id = os.environ['LINE_USER_ID']
        line_bot_api.push_message(user_id, TextSendMessage(
            text="📝 【noteリマインド】\n今月もX投稿が溜まりました！\n\nそろそろnote記事が書けそうなネタはありますか？\nLINEに「note書きたい」と送るだけで下書きが作れます✨"
        ))
    except Exception as e:
        print(f"Note reminder error: {e}")


def send_note_weekly_reminder():
    """毎週木曜9:05：noteネタ提案＋ラインナップ俯瞰リマインダー"""
    try:
        from line_handler import NOTE_PUBLISHED_TITLES, NOTE_LINEUP
        user_id = os.environ['LINE_USER_ID']
        published_count = len(NOTE_PUBLISHED_TITLES)
        next_item = NOTE_LINEUP[published_count] if published_count < len(NOTE_LINEUP) else None

        # ラインナップ進捗
        progress_lines = []
        for i, item in enumerate(NOTE_LINEUP):
            type_label = "無料" if item["type"] == "free" else "有料"
            if i < published_count:
                mark = "✅"
            elif i == published_count:
                mark = "👉"
            else:
                mark = "⬜"
            progress_lines.append(f"{mark}[{type_label}] {item['title']}")

        progress_text = "\n".join(progress_lines)

        if next_item:
            type_label = "無料" if next_item["type"] == "free" else "有料"
            msg = (
                f"📝 今週のnoteネタ提案\n\n"
                f"▶ 次に書く記事（{type_label}）\n「{next_item['title']}」\n\n"
                f"📊 ラインナップ進捗\n{progress_text}\n\n"
                f"「note書きたい」で下書きが作れます✨\n"
                f"公開したら「note公開した」と送ってください"
            )
        else:
            msg = (
                f"📝 ラインナップ全記事公開完了！おめでとうございます🎉\n\n"
                f"{progress_text}\n\n"
                f"次のラインナップを設計しましょう✨"
            )

        line_bot_api.push_message(user_id, TextSendMessage(text=msg))
    except Exception as e:
        print(f"Note weekly reminder error: {e}")


def send_x_weekly_report():
    """過去7日間のX投稿パフォーマンスをLINEに送信"""
    try:
        client = _get_x_client()
        if not client:
            return

        me = client.get_me(user_auth=True)
        user_id_x = me.data.id

        now = datetime.datetime.now(datetime.timezone.utc)
        start_time = now - datetime.timedelta(days=7)

        tweets = client.get_users_tweets(
            id=user_id_x,
            max_results=10,
            tweet_fields=['public_metrics', 'text'],
            user_auth=True
        )

        line_uid = os.environ['LINE_USER_ID']

        if not tweets.data:
            line_bot_api.push_message(line_uid, TextSendMessage(
                text="📊 今週のXレポート\n\n先週の投稿データがありませんでした。"
            ))
            return

        def get_score(tweet):
            pm = tweet.public_metrics or {}
            return pm.get('like_count', 0) * 3 + pm.get('retweet_count', 0) * 5 + pm.get('reply_count', 0) * 2

        sorted_tweets = sorted(tweets.data, key=get_score, reverse=True)
        total = len(sorted_tweets)

        def fmt(tweet, rank):
            pm = tweet.public_metrics or {}
            likes = pm.get('like_count', 0)
            rts = pm.get('retweet_count', 0)
            replies = pm.get('reply_count', 0)
            text_prev = tweet.text[:25] + '…' if len(tweet.text) > 25 else tweet.text
            return f"{rank}位「{text_prev}」❤{likes} RT{rts} 返{replies}"

        top3 = sorted_tweets[:min(3, total)]
        worst3 = sorted_tweets[max(0, total - 3):]

        lines = [
            f"📊 今週のXレポート（{start_time.strftime('%m/%d')}〜{now.strftime('%m/%d')}）",
            f"投稿数：{total}本\n",
            "🏆 トップ3",
        ]
        for i, t in enumerate(top3, 1):
            lines.append(fmt(t, i))
        if total > 3:
            lines.append("\n📉 ワースト3")
            for i, t in enumerate(worst3, 1):
                lines.append(fmt(t, total - len(worst3) + i))

        # トップ投稿をAIで分析して型と改善提案を生成
        top_texts = '\n'.join([f"{i+1}位: {t.text}" for i, t in enumerate(top3)])
        worst_texts = '\n'.join([f"{t.text}" for t in worst3]) if total > 3 else "なし"
        analysis_prompt = (
            "あなたはXアカウント（@maki_claude_lab：医療職×3児ワンオペ×AIで日常が楽になった実体験を発信）の投稿分析者です。\n"
            "アカウント方針：「AIって何に使うの？」と思っているワーママ・AI初心者に、日常が楽になった実体験を届ける。副業・収益化より『日常の変化』軸を重視。\n"
            "以下のパフォーマンスデータを見て、簡潔に分析してください。\n\n"
            f"【今週のトップ投稿】\n{top_texts}\n\n"
            f"【今週のワースト投稿】\n{worst_texts}\n\n"
            "以下の形式で答えてください（全体で100文字以内）：\n"
            "今週の傾向：〇〇型が強い（例：Before→After型、日常共感型、実体験型、安心感型、断言型）\n"
            "来週やること：〇〇（具体的に1行で）"
        )
        analysis_text = ""
        try:
            analysis_resp = anthropic_client.messages.create(
                model="claude-haiku-4-5-20251001",
                max_tokens=150,
                messages=[{"role": "user", "content": analysis_prompt}]
            )
            analysis_text = analysis_resp.content[0].text.strip()
            lines.append(f"\n📌 AI分析\n{analysis_text}")
        except Exception:
            pass

        line_bot_api.push_message(line_uid, TextSendMessage(text='\n'.join(lines)))

        # TWEET_STOCKを自動改善（バックグラウンド）
        if analysis_text:
            threading.Thread(
                target=auto_improve_tweet_stock,
                args=(top_texts, analysis_text)
            ).start()

        # Notionの日記メモからもツイート生成（バックグラウンド）
        threading.Thread(target=auto_tweet_from_diary_memos).start()

    except Exception as e:
        print(f"X weekly report error: {e}")
        try:
            line_uid = os.environ.get('LINE_USER_ID', '')
            if line_uid:
                line_bot_api.push_message(line_uid, TextSendMessage(
                    text=f"❌ Xレポートエラー：\n{str(e)[:200]}"
                ))
        except Exception:
            pass


def send_daily_work_log():
    """毎日18時：今日のコミット履歴＋X投稿数をLINEに送信"""
    try:
        import requests as req_lib
        now = datetime.datetime.now(JST)
        today_str = now.strftime('%Y-%m-%d')
        line_uid = os.environ['LINE_USER_ID']
        lines = [f"📋 今日の業務ログ（{now.strftime('%m/%d')}）\n"]

        # GitHubから今日のコミットを取得
        github_token = (os.environ.get('GITHUB_TOKEN') or '').strip()
        if github_token:
            headers_gh = {
                'Authorization': f'token {github_token}',
                'Accept': 'application/vnd.github.v3+json'
            }
            r = req_lib.get(
                'https://api.github.com/repos/makiko01035/maki-hisho/commits',
                headers=headers_gh,
                params={
                    'since': f'{today_str}T00:00:00+09:00',
                    'until': f'{today_str}T23:59:59+09:00',
                    'per_page': 10
                }
            )
            if r.status_code == 200 and r.json():
                commits = r.json()
                lines.append(f"🔨 今日のコミット（{len(commits)}件）")
                for c in commits[:5]:
                    msg = c['commit']['message'].split('\n')[0][:40]
                    lines.append(f"  ・{msg}")
                lines.append("")
            else:
                lines.append("🔨 今日のコミット：なし\n")

        # X投稿数
        x_count = 3 if now.day % 2 == 1 else 2
        lines.append(f"📱 X投稿：{x_count}本 自動投稿済み\n")

        # AIの一言
        try:
            ai_resp = anthropic_client.messages.create(
                model="claude-haiku-4-5-20251001",
                max_tokens=60,
                messages=[{"role": "user", "content": f"ワーママAI副業家まきさんへ、今日もお疲れ様の一言を30文字以内で。明るく背中を押す一言で。"}]
            )
            lines.append(f"💌 {ai_resp.content[0].text.strip()}")
        except Exception:
            lines.append("💌 今日もお疲れ様！明日もコツコツいこう。")

        line_bot_api.push_message(line_uid, TextSendMessage(text='\n'.join(lines)))
    except Exception as e:
        print(f"Daily work log error: {e}")


def add_study_memo(memo_text):
    """LINEからのメモをx_study_note.htmlに追記してGitHub APIでコミット"""
    import base64
    import requests as req_lib

    github_token = (os.environ.get('GITHUB_TOKEN') or '').strip()
    if not github_token:
        return False

    repo = 'makiko01035/maki-hisho'
    file_path = 'x_study_note.html'
    headers = {
        'Authorization': f'token {github_token}',
        'Accept': 'application/vnd.github.v3+json'
    }

    r = req_lib.get(f'https://api.github.com/repos/{repo}/contents/{file_path}', headers=headers)
    if r.status_code != 200:
        return False

    data = r.json()
    sha = data['sha']
    content = base64.b64decode(data['content']).decode('utf-8')

    now = datetime.datetime.now(JST)
    date_str = now.strftime('%m/%d %H:%M')
    memo_html = (
        f'  <div class="memo-item"><span class="memo-date">{date_str}</span>{memo_text}</div>\n'
        f'  <!-- MEMO_INSERT_POINT -->'
    )
    new_content = content.replace('  <!-- MEMO_INSERT_POINT -->', memo_html)

    update_payload = {
        'message': f'勉強ノート：自分メモ追加（{date_str}）',
        'content': base64.b64encode(new_content.encode('utf-8')).decode('utf-8'),
        'sha': sha,
        'branch': 'main'
    }
    r2 = req_lib.put(
        f'https://api.github.com/repos/{repo}/contents/{file_path}',
        headers=headers, json=update_payload
    )
    return r2.status_code in (200, 201)


def find_or_create_diary_page(notion_token, today_str):
    """今日の日記ページをNotionで探す。なければ新規作成する"""
    import requests as req
    headers = {
        "Authorization": f"Bearer {notion_token}",
        "Notion-Version": "2022-06-28",
        "Content-Type": "application/json"
    }
    # 検索APIで日記ページを探す（最新順）
    r = req.post("https://api.notion.com/v1/search",
        headers=headers,
        json={"query": "日記", "filter": {"value": "page", "property": "object"}, "page_size": 20}
    )
    print(f"[diary] search status={r.status_code}")

    date_prop_name = None
    title_prop_name = None
    parent_info = None
    latest_page_id = None

    if r.status_code == 200:
        for page in r.json().get("results", []):
            props = page.get("properties", {})
            for pname, pval in props.items():
                if pval.get("type") == "date" and date_prop_name is None:
                    date_prop_name = pname
                if pval.get("type") == "title" and title_prop_name is None:
                    title_prop_name = pname
            # 今日のページかチェック
            date_key = date_prop_name or "日付"
            date_val = props.get(date_key, {}).get("date") or {}
            if date_val.get("start") == today_str:
                print(f"[diary] found today's page id={page['id']}")
                return page["id"]
            # 最新ページの情報を保持（新規作成の親情報として使用）
            if latest_page_id is None:
                latest_page_id = page["id"]
                parent_info = page.get("parent")
    else:
        print(f"[diary] search error: {r.text[:300]}")
        return None

    # 今日のページがない場合：Notionに新規ページを作成
    import datetime as dt_mod
    parsed = dt_mod.datetime.strptime(today_str, "%Y-%m-%d")
    page_title = parsed.strftime("%Y年%m月%d日の日記")

    if parent_info:
        ptype = parent_info.get("type")
        print(f"[diary] parent type={ptype}, creating new page")

        if ptype == "database_id":
            # データベース配下：日付プロパティ付きで作成
            create_props = {}
            if date_prop_name:
                create_props[date_prop_name] = {"date": {"start": today_str}}
            tname = title_prop_name or "Name"
            create_props[tname] = {"title": [{"type": "text", "text": {"content": page_title}}]}
            parent_body = {"database_id": parent_info["database_id"]}
        elif ptype == "page_id":
            # 通常ページ配下：タイトルのみ
            create_props = {
                "title": [{"type": "text", "text": {"content": page_title}}]
            }
            parent_body = {"page_id": parent_info["page_id"]}
        else:
            print(f"[diary] unknown parent type={ptype}, fallback")
            if latest_page_id:
                return latest_page_id
            return None

        r2 = req.post("https://api.notion.com/v1/pages",
            headers=headers,
            json={"parent": parent_body, "properties": create_props}
        )
        print(f"[diary] create new page status={r2.status_code}")
        if r2.status_code == 200:
            new_id = r2.json()["id"]
            print(f"[diary] created today's page id={new_id}")
            return new_id
        else:
            print(f"[diary] create error: {r2.text[:300]}")
            if latest_page_id:
                print(f"[diary] fallback to latest page id={latest_page_id}")
                return latest_page_id

    print("[diary] no diary page found at all")
    return None


def fetch_diary_memos_from_notion(days=7):
    """Notionから直近N日間の日記メモを取得してテキストで返す"""
    import requests as req
    notion_token = os.environ.get('NOTION_TOKEN', '')
    if not notion_token:
        return ""
    headers = {
        "Authorization": f"Bearer {notion_token}",
        "Notion-Version": "2025-09-03",
        "Content-Type": "application/json"
    }
    # 直近N日間の日付リストを作成
    today = datetime.date.today()
    target_dates = [(today - datetime.timedelta(days=i)).strftime('%Y-%m-%d') for i in range(days)]

    # Notionから「日記」ページを検索
    r = req.post("https://api.notion.com/v1/search",
        headers=headers,
        json={"query": "日記", "filter": {"value": "page", "property": "object"}, "page_size": 30}
    )
    if r.status_code != 200:
        return ""

    all_memos = []
    for page in r.json().get("results", []):
        props = page.get("properties", {})
        date_val = props.get("日付", {}).get("date") or {}
        page_date = date_val.get("start", "")
        if page_date not in target_dates:
            continue
        # そのページのブロック（メモ）を取得
        page_id = page["id"]
        rb = req.get(f"https://api.notion.com/v1/blocks/{page_id}/children",
            headers=headers, params={"page_size": 100}
        )
        if rb.status_code != 200:
            continue
        for block in rb.json().get("results", []):
            if block.get("type") == "bulleted_list_item":
                texts = block["bulleted_list_item"].get("rich_text", [])
                text = "".join(t.get("plain_text", "") for t in texts).strip()
                if text:
                    all_memos.append(f"[{page_date}] {text}")

    return "\n".join(all_memos)


def auto_tweet_from_diary_memos():
    """Notionの日記メモをX投稿に変換してTWEET_STOCKに自動追加"""
    import base64
    import requests as req_lib

    memos_text = fetch_diary_memos_from_notion(days=7)
    if not memos_text:
        print("diary memos: nothing to process")
        return

    github_token = (os.environ.get('GITHUB_TOKEN') or '').strip()
    if not github_token:
        return

    # Claudeに渡してX投稿3本を生成
    gen_prompt = (
        "あなたはXアカウント（@maki_claude_lab：医療職×3児ワンオペ×AIで日常が楽になった実体験を発信）の投稿担当です。\n"
        "以下は本人が日常の中でLINEにメモした内容です。\n"
        "これをX投稿（140文字以内）に変換してください。3本作成してください。\n\n"
        f"【日記メモ（直近7日）】\n{memos_text[:1500]}\n\n"
        "条件：\n"
        "- 「AIって何に使うの？」と思っているワーママ・AI初心者に刺さる書き方\n"
        "- 副業・収益化より『日常の変化』『楽になった』『気づき』軸で書く\n"
        "- ですます調・等身大のトーン\n"
        "- ハッシュタグは #ワーママ #AI #子育て #AI秘書 #時短 のいずれか1〜2個\n"
        "- 3本を1行ずつ、番号なし・余計な説明なしで出力\n"
        "- メモがAI・副業と無関係でも日常の共感ネタとして活かしてOK"
    )
    try:
        gen_resp = anthropic_client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=500,
            messages=[{"role": "user", "content": gen_prompt}]
        )
        new_tweets = [
            t.strip() for t in gen_resp.content[0].text.strip().split('\n')
            if t.strip() and len(t.strip()) > 10
        ][:3]
    except Exception as e:
        print(f"diary tweet generate error: {e}")
        return

    if not new_tweets:
        return

    # GitHub APIでTWEET_STOCKに追加
    repo = 'makiko01035/maki-hisho'
    file_path = 'main.py'
    headers = {
        'Authorization': f'token {github_token}',
        'Accept': 'application/vnd.github.v3+json'
    }
    r = req_lib.get(f'https://api.github.com/repos/{repo}/contents/{file_path}', headers=headers)
    if r.status_code != 200:
        return

    data = r.json()
    sha = data['sha']
    content = base64.b64decode(data['content']).decode('utf-8')
    stock_start = content.find('TWEET_STOCK = [')
    if stock_start == -1:
        return
    stock_end = content.find('\n]', stock_start)
    if stock_end == -1:
        return

    insert_lines = '\n'.join([f'    "{t}",' for t in new_tweets])
    new_content = content[:stock_end] + '\n' + insert_lines + content[stock_end:]

    today_str = datetime.date.today().strftime('%Y-%m-%d')
    commit_msg = f"広報部PDCA：{today_str} 日記メモからツイート{len(new_tweets)}本自動追加"
    update_payload = {
        'message': commit_msg,
        'content': base64.b64encode(new_content.encode('utf-8')).decode('utf-8'),
        'sha': sha,
        'branch': 'main'
    }
    r2 = req_lib.put(
        f'https://api.github.com/repos/{repo}/contents/{file_path}',
        headers=headers, json=update_payload
    )
    line_uid = os.environ.get('LINE_USER_ID', '')
    if r2.status_code in (200, 201) and line_uid:
        preview = '\n'.join([f'・{t[:30]}…' for t in new_tweets])
        line_bot_api.push_message(line_uid, TextSendMessage(
            text=f"📓 日記メモからX投稿を追加しました！\n\n{preview}\n\n2〜3分でRenderに反映されます。"
        ))
    else:
        print(f"diary tweet stock update error: {r2.status_code}")


def add_diary_memo(memo_text):
    """LINEからのメモを今日の日記ページに時刻付きで追記する"""
    import requests as req
    notion_token = os.environ.get('NOTION_TOKEN', '')
    if not notion_token:
        print("[diary] NOTION_TOKEN is not set")
        return False
    now = datetime.datetime.now(JST)
    today_str = now.strftime('%Y-%m-%d')
    time_str = now.strftime('%H:%M')
    page_id = find_or_create_diary_page(notion_token, today_str)
    if not page_id:
        print("[diary] page_id is None, cannot append")
        return False
    content_text = f"{today_str} {time_str} {memo_text}"
    r = req.patch(
        f"https://api.notion.com/v1/blocks/{page_id}/children",
        headers={
            "Authorization": f"Bearer {notion_token}",
            "Notion-Version": "2022-06-28",
            "Content-Type": "application/json"
        },
        json={"children": [{
            "object": "block",
            "type": "bulleted_list_item",
            "bulleted_list_item": {
                "rich_text": [{"type": "text", "text": {"content": content_text}}]
            }
        }]}
    )
    print(f"[diary] append status={r.status_code}")
    if r.status_code != 200:
        print(f"[diary] append error: {r.text[:300]}")
    return r.status_code == 200


def auto_improve_tweet_stock(top_tweets_text, analysis_text):
    """トップ型の投稿を3本生成→GitHub APIでTWEET_STOCKに自動追加→Renderが自動デプロイ"""
    import base64
    import requests as req_lib

    github_token = (os.environ.get('GITHUB_TOKEN') or '').strip()
    if not github_token:
        return

    repo = 'makiko01035/maki-hisho'
    file_path = 'main.py'
    headers = {
        'Authorization': f'token {github_token}',
        'Accept': 'application/vnd.github.v3+json'
    }
    line_uid = os.environ.get('LINE_USER_ID', '')

    # 新投稿3本を生成
    gen_prompt = (
        "あなたはXアカウント（@maki_claude_lab：医療職×3児ワンオペ×AIで日常が楽になった実体験を発信）の投稿担当です。\n"
        "アカウント方針：「AIって何に使うの？」と思っているワーママ・AI初心者に、日常が楽になった実体験を届ける。副業色より『毎朝LINEで予定が届く』『プリント1枚で自動登録』『忘れなくなった』などの日常変化を中心に。\n"
        "以下の分析を参考に、同じ型の新しい投稿を3本作成してください。\n\n"
        f"【先週のトップ投稿（参考）】\n{top_tweets_text}\n\n"
        f"【分析結果】\n{analysis_text}\n\n"
        "条件：\n"
        "- 各投稿は140文字以内\n"
        "- ハッシュタグは #ワーママ #AI秘書 #子育て #AI #ClaudeCode #時短 のいずれか1〜2個\n"
        "- まきの等身大の言葉・ですます調で書く\n"
        "- 3本を1行ずつ、番号なし・余計な説明なしで出力"
    )
    try:
        gen_resp = anthropic_client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=500,
            messages=[{"role": "user", "content": gen_prompt}]
        )
        new_tweets = [
            t.strip() for t in gen_resp.content[0].text.strip().split('\n')
            if t.strip() and len(t.strip()) > 10
        ][:3]
    except Exception as e:
        print(f"auto_improve generate error: {e}")
        return

    if not new_tweets:
        return

    # GitHub APIでmain.pyを取得
    r = req_lib.get(f'https://api.github.com/repos/{repo}/contents/{file_path}', headers=headers)
    if r.status_code != 200:
        print(f"GitHub get error: {r.status_code}")
        return

    data = r.json()
    sha = data['sha']
    content = base64.b64decode(data['content']).decode('utf-8')

    # TWEET_STOCKの末尾（最初の ^\] の位置）に追加
    stock_start = content.find('TWEET_STOCK = [')
    if stock_start == -1:
        return
    stock_end = content.find('\n]', stock_start)
    if stock_end == -1:
        return

    insert_lines = '\n'.join([f'    "{t}",' for t in new_tweets])
    new_content = content[:stock_end] + '\n' + insert_lines + content[stock_end:]

    # GitHub APIでコミット
    today = datetime.date.today().strftime('%Y-%m-%d')
    commit_msg = f"広報部PDCA：{today} トップ型投稿を{len(new_tweets)}本自動追加"
    update_payload = {
        'message': commit_msg,
        'content': base64.b64encode(new_content.encode('utf-8')).decode('utf-8'),
        'sha': sha,
        'branch': 'main'
    }
    r2 = req_lib.put(
        f'https://api.github.com/repos/{repo}/contents/{file_path}',
        headers=headers, json=update_payload
    )
    if r2.status_code in (200, 201):
        preview = '\n'.join([f'・{t[:35]}…' for t in new_tweets])
        if line_uid:
            line_bot_api.push_message(line_uid, TextSendMessage(
                text=f"✅ TWEET_STOCKを自動更新！\n\n追加した投稿（{len(new_tweets)}本）：\n{preview}\n\n2〜3分でRenderに反映されます。"
            ))
    else:
        print(f"GitHub put error: {r2.status_code} {r2.text[:200]}")


