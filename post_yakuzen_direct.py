import requests
import json
import sys
import re
import markdown as md_lib

RENDER_URL = "https://maki-hisho.onrender.com/post-yakuzen-direct"
SECRET = "U16db70df5ef0ed2d73189eee5620669e"

# カテゴリースラッグ→ID マッピング（foodmakehealth.com）
CATEGORY_MAP = {
    "sleep": 219,
    "kids-sleep": 215,
    "menopause": 216,
    "oriental-medicine": 217,
}

md_path = sys.argv[1] if len(sys.argv) > 1 else None
slug = sys.argv[2] if len(sys.argv) > 2 else ""
update_id = sys.argv[3] if len(sys.argv) > 3 else ""  # 既存記事IDを渡すと更新

if not md_path:
    print("使い方: python post_yakuzen_direct.py <記事.md> [スラッグ] [更新するpost_id]")
    sys.exit(1)

with open(md_path, "r", encoding="utf-8") as f:
    raw = f.read()

# MDコメントからメタ情報を抽出
def extract_meta(text, key):
    m = re.search(rf'<!-- {key}: (.+?) -->', text)
    return m.group(1).strip() if m else ""

meta_category_raw = extract_meta(raw, "カテゴリー")
meta_tags_raw = extract_meta(raw, "タグ")

# カテゴリー名→IDに変換（😴 睡眠の悩み → 219 など）
# カテゴリースラッグまたは名前で検索
CATEGORY_NAME_MAP = {
    "😴 睡眠の悩み": 219,
    "睡眠の悩み": 219,
    "子どもの睡眠": 215,
    "スキンケア": 216,
    "東洋医学": 217,
    "普段使いの薬膳レシピ": 9,
}
categories = []
if meta_category_raw:
    cat_id = CATEGORY_NAME_MAP.get(meta_category_raw)
    if cat_id:
        categories.append(cat_id)

# タグ名リスト抽出（カンマ区切り）
tags = []
if meta_tags_raw:
    tags = [t.strip() for t in meta_tags_raw.split(",") if t.strip()]

# コメント行（<!-- ... -->）でメタ情報を除去（ボックス以外）
def strip_meta_comments(text):
    text = re.sub(r'<!-- パーマリンク.*?-->\n?', '', text)
    text = re.sub(r'<!-- カテゴリー.*?-->\n?', '', text)
    text = re.sub(r'<!-- タグ.*?-->\n?', '', text)
    return text

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

# スラッグ自動推定（引数なしの場合、MDファイル名から）
if not slug:
    import os
    slug = os.path.splitext(os.path.basename(md_path))[0].replace("_article", "").replace("_", "-")

# ローカルでMarkdown→HTML変換（divクラスが消えないように）
final_html = md_lib.markdown(body, extensions=['tables', 'nl2br'])

payload_dict = {
    "title": title,
    "content_html": final_html,
    "slug": slug,
    "update_id": update_id,
}
if categories:
    payload_dict["categories"] = categories
if tags:
    payload_dict["tags"] = tags

payload = json.dumps(payload_dict, ensure_ascii=False)
headers = {
    "Content-Type": "application/json; charset=utf-8",
    "X-Secret": SECRET,
}

print(f"投稿中: {title}")
print(f"スラッグ: {slug}")
if categories:
    print(f"カテゴリーID: {categories}")
if tags:
    print(f"タグ: {tags}")
r = requests.post(RENDER_URL, data=payload.encode("utf-8"), headers=headers, timeout=120)
print(r.status_code, r.text[:300])
