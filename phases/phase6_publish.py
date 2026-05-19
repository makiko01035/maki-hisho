"""
Phase 6: WordPress自動投稿 + LINE通知
"""
import os
import sys
import requests
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent / ".env")
sys.path.insert(0, str(Path(__file__).parent.parent))

NOTIFY_URL = "https://maki-hisho.onrender.com/add-task"
NOTIFY_SECRET = "maki2025"

CATEGORY_MENOPAUSE = 216
CATEGORY_EASTERN   = 217
CATEGORY_CHILDREN  = 215
CATEGORY_SLEEP     = 219

MENOPAUSE_WORDS = ['更年期', 'プレ更年期', '閉経', 'ほてり', '寝汗', '40代', '50代']
EASTERN_WORDS   = ['漢方', '東洋医学', '五行', '腎虚', '陰虚', '気虚', '血虚', '瘀血', '三焦',
                   'なつめ', '白きくらげ', 'クコ', '薬膳スープ', 'ハーブティー', 'バレリアン',
                   'カモミール', '酸棗仁湯', '加味逍遙散', '柴胡加竜骨牡蛎湯', '桂枝茯苓丸',
                   '半夏厚朴湯', '当帰芍薬散']
CHILDREN_WORDS  = ['子ども', '小学生', '中学生', '乳幼児', '思春期', '起立性調節障害', '受験生', '夜泣き']


def detect_category(keyword: str) -> int:
    for w in MENOPAUSE_WORDS:
        if w in keyword:
            return CATEGORY_MENOPAUSE
    for w in CHILDREN_WORDS:
        if w in keyword:
            return CATEGORY_CHILDREN
    for w in EASTERN_WORDS:
        if w in keyword:
            return CATEGORY_EASTERN
    return CATEGORY_SLEEP


def _get_featured_image_url(wp_url: str, wp_user: str, wp_pass: str, post_id: int) -> str | None:
    """WP記事のアイキャッチ画像URLを返す"""
    try:
        res = requests.get(
            f"{wp_url}/wp-json/wp/v2/posts/{post_id}",
            auth=(wp_user, wp_pass),
            params={"_embed": 1},
            timeout=10
        )
        if res.status_code == 200:
            images = res.json().get("_embedded", {}).get("wp:featuredmedia", [])
            if images:
                return images[0].get("source_url")
    except Exception as e:
        print(f"  アイキャッチURL取得エラー: {e}")
    return None


def send_line_notification(title: str, post_url: str, keyword: str):
    """投稿完了をLINEに通知する"""
    message = (
        f"✅ ブログ投稿完了\n"
        f"「{title}」\n"
        f"{post_url}"
    )
    task = f"SNS投稿: {title[:30]}"
    try:
        res = requests.post(
            NOTIFY_URL,
            json={"secret": NOTIFY_SECRET, "message": message, "task": task},
            timeout=10
        )
        if res.status_code == 200:
            print(f"  LINE通知送信完了")
        else:
            print(f"  LINE通知失敗: {res.status_code}")
    except Exception as e:
        print(f"  LINE通知エラー: {e}")


def run_update(keyword: str, article_md: str, post_id: int) -> tuple:
    """Phase 6（リライト用）: 既存WP記事を上書き更新してLINE通知。(post_id, post_url) を返す"""
    import markdown as md_lib

    print(f"\n[Phase 6] WordPress記事更新（ID:{post_id}）")

    wp_url  = os.getenv("YAKUZEN_WP_URL", "https://foodmakehealth.com")
    wp_user = os.getenv("YAKUZEN_WP_USER", "makiko01035")
    wp_pass = os.getenv("YAKUZEN_WP_APP_PASSWORD", "")

    lines = article_md.strip().split('\n')
    title = lines[0].lstrip('#').strip()
    content_md = '\n'.join(lines[1:]).strip()
    html = md_lib.markdown(content_md, extensions=["tables", "nl2br"])
    category_id = detect_category(keyword)

    res = requests.post(
        f"{wp_url}/wp-json/wp/v2/posts/{post_id}",
        auth=(wp_user, wp_pass),
        json={
            "title": title,
            "content": html,
            "status": "publish",
            "categories": [category_id],
        }
    )

    if res.status_code == 200:
        post = res.json()
        post_url = post.get("link", "")
        print(f"  更新完了 カテゴリID:{category_id}")
        send_line_notification(title, post_url, keyword)
        image_url = _get_featured_image_url(wp_url, wp_user, wp_pass, post_id)
        user_id = os.getenv("LINE_USER_ID", "")
        if user_id:
            try:
                from blog_yakuzen import send_sns_messages
                send_sns_messages(user_id, title, post_url, image_url, content_md)
            except Exception as e:
                print(f"  SNSセット送信エラー: {e}")
        print(f"\n[Phase 6] 完了 ✅  {post_url}")
        return post_id, post_url
    else:
        print(f"\n[Phase 6] 更新失敗 {res.status_code}")
        return None, None


def run(keyword: str, article_md: str, output_dir: str = "articles") -> tuple:
    """Phase 6: WPに公開してLINE通知。(post_id, post_url) を返す"""
    from post_to_wordpress import post_draft

    print(f"\n[Phase 6] WordPress自動投稿")

    lines = article_md.strip().split('\n')
    title = lines[0].lstrip('#').strip()
    content = '\n'.join(lines[1:]).strip()

    category_id = detect_category(keyword)
    slug = keyword.replace(' ', '-').replace('　', '-')[:60]

    post_id, post_url = post_draft(
        title=title,
        content_md=content,
        slug=slug,
        image_keyword=keyword[:30],
        status="publish",
        categories=[category_id],
    )

    if post_id:
        print(f"  カテゴリID: {category_id}")
        send_line_notification(title, post_url, keyword)
        wp_url_env = os.getenv("YAKUZEN_WP_URL", "https://foodmakehealth.com")
        wp_user_env = os.getenv("YAKUZEN_WP_USER", "makiko01035")
        wp_pass_env = os.getenv("YAKUZEN_WP_APP_PASSWORD", "")
        image_url = _get_featured_image_url(wp_url_env, wp_user_env, wp_pass_env, post_id)
        user_id = os.getenv("LINE_USER_ID", "")
        if user_id:
            try:
                from blog_yakuzen import send_sns_messages
                send_sns_messages(user_id, title, post_url, image_url, content)
            except Exception as e:
                print(f"  SNSセット送信エラー: {e}")
        print(f"\n[Phase 6] 完了 ✅  {post_url}")
    else:
        print(f"\n[Phase 6] 投稿失敗 ⚠️")

    return post_id, post_url
