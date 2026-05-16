import os
import requests
import markdown
import tempfile
from dotenv import load_dotenv

load_dotenv()

WP_URL = os.getenv("WP_URL")
WP_USER = os.getenv("WP_USER")
WP_APP_PASSWORD = os.getenv("WP_APP_PASSWORD")

PEXELS_API_KEY = os.getenv("PEXELS_API_KEY")


def fetch_pexels_image(keyword: str) -> tuple[bytes, str] | None:
    """Pexelsでキーワード検索し、画像データとファイル名を返す"""
    headers = {"Authorization": PEXELS_API_KEY}
    res = requests.get(
        "https://api.pexels.com/v1/search",
        headers=headers,
        params={"query": keyword, "per_page": 1, "orientation": "landscape"},
    )
    if res.status_code != 200:
        print(f"Pexels検索エラー: {res.status_code}")
        return None

    photos = res.json().get("photos", [])
    if not photos:
        print(f"Pexels: '{keyword}' の画像が見つかりませんでした")
        return None

    photo = photos[0]
    img_url = photo["src"]["large2x"]
    img_res = requests.get(img_url)
    if img_res.status_code != 200:
        print("画像ダウンロード失敗")
        return None

    filename = f"pexels_{photo['id']}.jpg"
    return img_res.content, filename


def upload_featured_image(wp_url: str, wp_user: str, wp_pass: str, keyword: str) -> int | None:
    """Pexelsから画像を取得してWPメディアにアップロード、media_idを返す"""
    result = fetch_pexels_image(keyword)
    if result is None:
        return None

    img_data, filename = result

    res = requests.post(
        f"{wp_url}/wp-json/wp/v2/media",
        auth=(wp_user, wp_pass),
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Content-Type": "image/jpeg",
        },
        data=img_data,
    )

    if res.status_code == 201:
        media_id = res.json()["id"]
        print(f"アイキャッチ画像アップロード成功 (media_id: {media_id})")
        return media_id
    else:
        print(f"画像アップロードエラー: {res.status_code}")
        print(res.json())
        return None


def publish_post_by_id(post_id: int, wp_url: str = None, wp_user: str = None, wp_pass: str = None) -> bool:
    """下書き記事をIDで公開する"""
    _url = wp_url or WP_URL
    _user = wp_user or WP_USER
    _pass = wp_pass or WP_APP_PASSWORD

    response = requests.post(
        f"{_url}/wp-json/wp/v2/posts/{post_id}",
        auth=(_user, _pass),
        json={"status": "publish"},
    )
    if response.status_code == 200:
        post = response.json()
        print(f"公開完了: {post['title']['rendered']}")
        print(f"  URL: {post['link']}")
        return True
    else:
        print(f"公開エラー (ID:{post_id}): {response.status_code}")
        return False


def post_draft(title: str, content_md: str, slug: str = None, image_keyword: str = None,
               wp_url: str = None, wp_user: str = None, wp_pass: str = None,
               status: str = "draft", categories: list = None):
    """記事をWordPressに投稿する（status: draft/publish）。(post_id, post_url) を返す"""
    _url = wp_url or WP_URL
    _user = wp_user or WP_USER
    _pass = wp_pass or WP_APP_PASSWORD

    html = markdown.markdown(content_md, extensions=["tables", "nl2br"])

    data = {
        "title": title,
        "content": html,
        "status": status,
    }
    if slug:
        data["slug"] = slug
    if categories:
        data["categories"] = categories

    # アイキャッチ画像（キーワード未指定ならタイトルで検索）
    keyword = image_keyword or title
    media_id = upload_featured_image(_url, _user, _pass, keyword)
    if media_id:
        data["featured_media"] = media_id

    response = requests.post(
        f"{_url}/wp-json/wp/v2/posts",
        auth=(_user, _pass),
        json=data,
    )

    if response.status_code == 201:
        post = response.json()
        post_id = post["id"]
        post_url = post.get("link", "")
        print("OK: 投稿成功！")
        print(f"   タイトル: {post['title']['rendered']}")
        print(f"   URL: {post_url}")
        return post_id, post_url
    else:
        print(f"ERROR: {response.status_code}")
        print(response.json())
        return None, None


# ===== 記事内容 =====
TITLE = "薬膳コーディネーターとは【ユーキャンで取れる？費用・難易度を解説】"
SLUG = "yakuzen-coordinator-toha"
IMAGE_KEYWORD = "herbal medicine food"  # Pexels検索キーワード（英語推奨）

CONTENT = """
薬膳に興味があって「資格を取ってみたい」と思ったとき、多くの人がまず目にするのが**薬膳コーディネーター**という資格です。
"""

if __name__ == "__main__":
    post_draft(TITLE, CONTENT, SLUG, image_keyword=IMAGE_KEYWORD)
