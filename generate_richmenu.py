from PIL import Image, ImageDraw, ImageFont
import os

W, H = 2500, 843
img = Image.new('RGB', (W, H), '#1a1a2e')
draw = ImageDraw.Draw(img)

buttons = [
    (0,    0,   833, 421, 'eBay\nリサーチ', '#16213e', '#4fc3f7'),
    (833,  0,   834, 421, 'roomタグ',       '#16213e', '#4fc3f7'),
    (1667, 0,   833, 421, '利益計算',        '#16213e', '#4fc3f7'),
    (0,    421, 833, 422, '睡眠記事',        '#0f3460', '#81c784'),
    (833,  421, 834, 422, 'セキスイ記事',    '#0f3460', '#81c784'),
    (1667, 421, 833, 422, '📊 売上管理',     '#1b4332', '#a5d6a7'),
]

# フォント
FONT_PATH_JP = None
candidates = [
    'C:/Windows/Fonts/meiryo.ttc',
    'C:/Windows/Fonts/msgothic.ttc',
    'C:/Windows/Fonts/YuGothM.ttc',
]
for c in candidates:
    if os.path.exists(c):
        FONT_PATH_JP = c
        break

try:
    if FONT_PATH_JP:
        font_main = ImageFont.truetype(FONT_PATH_JP, 100)
    else:
        font_main = ImageFont.load_default(size=80)
except Exception:
    font_main = ImageFont.load_default()

GAP = 8
for (x, y, w, h, label, bg, fg) in buttons:
    draw.rectangle([x+GAP, y+GAP, x+w-GAP, y+h-GAP], fill=bg, outline='#3a3a5c', width=4)
    cx = x + w // 2
    cy = y + h // 2

    lines = label.split('\n')
    line_h = 110
    total_h = line_h * len(lines)
    start_y = cy - total_h // 2

    for i, line in enumerate(lines):
        try:
            bbox = draw.textbbox((0, 0), line, font=font_main)
            tw = bbox[2] - bbox[0]
            th = bbox[3] - bbox[1]
            draw.text((cx - tw // 2, start_y + i * line_h - th // 2), line, fill=fg, font=font_main)
        except Exception:
            draw.text((cx - 80, start_y + i * line_h), line, fill=fg)

out = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'static', 'richmenu.png')
img.save(out)
print('saved:', out)
