import requests
import json
import sys
import re
import markdown as md_lib

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

# **太字** → <strong>
def convert_bold(text):
    return re.sub(r'\*\*(.+?)\*\*', r'<strong>\1</strong>', text)

# ボックスコメントマーカーをCSSクラス付きdivに変換（行単位で処理）
def convert_boxes(text):
    lines = text.split('\n')
    result = []
    i = 0
    while i < len(lines):
        line = lines[i]
        m = re.match(r'<!-- ▼ box-(\w+)', line)
        if m:
            box_type = m.group(1)
            end_marker = f'<!-- ▲ box-{box_type}'
            i += 1
            # タイトル行(**...**)をスキップ
            if i < len(lines) and lines[i].strip().startswith('**'):
                i += 1
            # タイトル後の空行をスキップ
            if i < len(lines) and lines[i].strip() == '':
                i += 1
            # 内容を収集
            content_lines = []
            while i < len(lines) and not lines[i].startswith(end_marker):
                content_lines.append(lines[i])
                i += 1
            i += 1  # 閉じマーカーをスキップ
            inner = convert_bold('\n'.join(content_lines).strip())
            result.append(f'<div class="box-{box_type}">\n{inner}\n</div>')
        else:
            result.append(line)
            i += 1
    return '\n'.join(result)

raw = convert_boxes(raw)

# タイトル抽出（1行目の # を除去）
lines = raw.split("\n")
title = lines[0].lstrip("# ").strip()
body = "\n".join(lines[1:]).lstrip("\n")

# スラッグ自動推定（引数なしの場合）
if not slug:
    slug = "pillow-mattress-ranking"

# ローカルでMarkdown→HTML変換（divクラスが消えないように）
final_html = md_lib.markdown(body, extensions=['tables', 'nl2br'])

payload = json.dumps(
    {"title": title, "content_html": final_html, "slug": slug,
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
