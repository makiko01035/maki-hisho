import requests
import json
import sys
import re

RENDER_URL = "https://maki-hisho.onrender.com/post-yakuzen-direct"
SECRET = "U16db70df5ef0ed2d73189eee5620669e"

md_path = sys.argv[1] if len(sys.argv) > 1 else None
slug = sys.argv[2] if len(sys.argv) > 2 else ""
update_id = sys.argv[3] if len(sys.argv) > 3 else ""  # 既存記事IDを渡すと更新

if not md_path:
    print("使い方: python post_yakuzen_direct.py <記事.md> [スラッグ] [更新するpost_id]")
    sys.exit(1)

with open(md_path, "r", encoding="utf-8") as f:
    raw = f.read()

# コメント行（<!-- ... -->）でスラッグ等のメタ情報を除去
def strip_meta_comments(text):
    return re.sub(r'<!-- パーマリンク.*?-->\n?', '', text)

raw = strip_meta_comments(raw)

# **太字** → <strong>（divの中でmarkdownが効かないので先に変換）
def convert_bold(text):
    return re.sub(r'\*\*(.+?)\*\*', r'<strong>\1</strong>', text)

# ボックスコメントマーカーをCSSクラス付きdivに変換
def convert_boxes(text):
    for box in ['conclusion', 'doctor', 'yakuzen']:
        pattern = (
            r'<!-- ▼ box-' + box + r' クラスのブロックに追加 -->\n'
            r'\*\*.*?\*\*\n\n'  # 太字タイトル行（CSSのbeforeで表示するので除去）
            r'(.*?)'
            r'<!-- ▲ box-' + box + r' ここまで -->'
        )
        def wrap(m, b=box):
            inner = convert_bold(m.group(1).strip())
            return f'<div class="box-{b}">\n{inner}\n</div>'
        text = re.sub(pattern, wrap, text, flags=re.DOTALL)
    # 残ったコメントマーカーを除去
    text = re.sub(r'<!-- [▼▲][^>]*-->\n?', '', text)
    return text

raw = convert_boxes(raw)

# タイトル抽出（1行目の # を除去）
lines = raw.split("\n")
title = lines[0].lstrip("# ").strip()
body = "\n".join(lines[1:]).lstrip("\n")

# スラッグ自動推定（引数なしの場合）
if not slug:
    slug = "pillow-mattress-ranking"

payload = json.dumps(
    {"title": title, "content_md": body, "slug": slug,
     "update_id": update_id},
    ensure_ascii=False
)
headers = {
    "Content-Type": "application/json; charset=utf-8",
    "X-Secret": SECRET,
}

print(f"投稿中: {title}")
print(f"スラッグ: {slug}")
r = requests.post(RENDER_URL, data=payload.encode("utf-8"), headers=headers, timeout=120)
print(r.status_code, r.text[:300])
