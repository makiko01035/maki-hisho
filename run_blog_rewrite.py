"""水・土 8:00 JST 自動実行：カテゴリ9の旧記事をリライトしてWordPressに上書き投稿する"""
import os
import sys
import requests
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / '.env')

NOTIFY_URL = "https://maki-hisho.onrender.com/add-task"
NOTIFY_SECRET = os.environ.get('NOTIFY_SECRET', 'maki2025')


def notify_line(message, task=""):
    try:
        requests.post(
            NOTIFY_URL,
            json={"secret": NOTIFY_SECRET, "message": message, "task": task or message[:30]},
            timeout=15,
        )
    except Exception:
        pass


def main():
    from phases import phase4_rewrite, phase5_quality, phase6_publish

    wp_url  = os.environ.get('YAKUZEN_WP_URL', 'https://foodmakehealth.com')
    wp_user = os.environ.get('YAKUZEN_WP_USER', 'makiko01035')
    wp_pass = os.environ.get('YAKUZEN_WP_APP_PASSWORD', '')

    res = requests.get(
        f"{wp_url}/wp-json/wp/v2/posts",
        auth=(wp_user, wp_pass),
        params={"categories": 9, "orderby": "date", "order": "asc",
                "per_page": 1, "status": "publish", "_fields": "id,title,link"},
        timeout=10,
    )
    posts = res.json()
    if not posts:
        notify_line("⚠️ リライト対象なし：カテゴリ9の記事が0件です")
        print("リライト対象なし。終了")
        return

    post_id = posts[0]["id"]
    title   = posts[0]["title"]["rendered"]
    keyword = ' '.join(title.split()[:5])
    print(f"リライト対象: [{post_id}] {title}")

    try:
        draft, _ = phase4_rewrite.run(keyword, "")
        final, score, passed, _ = phase5_quality.run(keyword, draft)

        if passed or score >= 80:
            phase6_publish.run_update(keyword, final, post_id)
        else:
            msg = f"❌ 自動リライトスキップ（品質{score}点）\n記事ID:{post_id}「{title}」\n手動で確認が必要です"
            print(msg)
            notify_line(msg)

    except Exception as e:
        msg = f"❌ 自動投稿エラー（リライト）\n{str(e)[:200]}"
        print(f"エラー: {e}", file=sys.stderr)
        notify_line(msg)
        sys.exit(1)


if __name__ == '__main__':
    main()
