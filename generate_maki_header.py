from PIL import Image, ImageDraw, ImageFont
import math, os

W, H = 1500, 500
img = Image.new("RGB", (W, H))
draw = ImageDraw.Draw(img)

# --- 背景グラデーション（上：薄いラベンダー → 下：温かいピーチ）---
for y in range(H):
    t = y / H
    r = int(235 + (255 - 235) * t)
    g = int(210 + (230 - 210) * t)
    b = int(240 + (205 - 240) * t)
    draw.line([(0, y), (W, y)], fill=(r, g, b))

# --- 右上：朝日グロー ---
glow = Image.new("RGBA", (W, H), (0, 0, 0, 0))
gdata = glow.load()
sun_cx, sun_cy = 1360, -20
for py in range(min(H, 300)):
    for px in range(max(0, 900), W):
        dist = math.sqrt((px - sun_cx) ** 2 + (py - sun_cy) ** 2)
        if dist < 420:
            a = int(55 * (1 - dist / 420) ** 1.5)
            gdata[px, py] = (255, 200, 130, a)
img_rgba = img.convert("RGBA")
img_rgba = Image.alpha_composite(img_rgba, glow)
img = img_rgba.convert("RGB")
draw = ImageDraw.Draw(img)

# --- 光の粒（朝のほこり感）---
import random
random.seed(42)
dots = Image.new("RGBA", (W, H), (0, 0, 0, 0))
ddraw = ImageDraw.Draw(dots)
for _ in range(120):
    dx = random.randint(0, W)
    dy = random.randint(0, H)
    dr = random.randint(1, 3)
    da = random.randint(15, 50)
    ddraw.ellipse([dx - dr, dy - dr, dx + dr, dy + dr], fill=(255, 235, 200, da))
img_rgba = img.convert("RGBA")
img_rgba = Image.alpha_composite(img_rgba, dots)
img = img_rgba.convert("RGB")
draw = ImageDraw.Draw(img)

# --- 細い縦ライン装飾 ---
line_layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
ldraw = ImageDraw.Draw(line_layer)
for yi in range(60, H - 60):
    t = (yi - 60) / (H - 120)
    if t < 0.3:
        a = int(80 * t / 0.3)
    elif t > 0.7:
        a = int(80 * (1 - t) / 0.3)
    else:
        a = 80
    ldraw.line([(348, yi), (350, yi)], fill=(200, 160, 190, a))
img_rgba = img.convert("RGBA")
img_rgba = Image.alpha_composite(img_rgba, line_layer)
img = img_rgba.convert("RGB")
draw = ImageDraw.Draw(img)

# --- フォント ---
font_paths_min = [
    "C:/Windows/Fonts/YuMinL.ttc",
    "C:/Windows/Fonts/yumin.ttf",
    "C:/Windows/Fonts/msgothic.ttc",
]
font_paths_goth = [
    "C:/Windows/Fonts/meiryo.ttc",
    "C:/Windows/Fonts/msgothic.ttc",
    "C:/Windows/Fonts/YuGothM.ttc",
]

def load_font(paths, size):
    for p in paths:
        if os.path.exists(p):
            try:
                return ImageFont.truetype(p, size)
            except:
                pass
    return ImageFont.load_default()

font_tag   = load_font(font_paths_goth, 20)
font_title = load_font(font_paths_min,  64)
font_sub   = load_font(font_paths_goth, 22)
font_badge = load_font(font_paths_goth, 15)
font_site  = load_font(font_paths_goth, 14)

# --- テキスト（x=370からアイコン被り回避）---
tx = 370

# タグ行
draw.text((tx, 90), "まき｜医療職×3児ワンオペ", font=font_tag, fill=(160, 100, 140))

# メインコピー（2行）
draw.text((tx, 128), "AIで、毎日が",      font=font_title, fill=(90, 60, 100))
draw.text((tx, 200), "ちょっと楽になった。", font=font_title, fill=(200, 100, 140))

# サブコピー
draw.text((tx, 290), "「AIって何に使うの？」と思っていた私が、", font=font_sub, fill=(120, 80, 110))
draw.text((tx, 322), "仕組みで日常を変えるまでのリアルな記録。",  font=font_sub, fill=(140, 100, 125))

# バッジ
badges = ["#ワーママ", "#AI秘書", "#子育て", "#プログラミングゼロ"]
badge_layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
bdraw = ImageDraw.Draw(badge_layer)
bx, by = tx, 370
for b in badges:
    bw = int(font_badge.getlength(b)) + 28
    bdraw.rounded_rectangle([bx, by, bx + bw, by + 28], radius=14,
                             fill=(200, 130, 170, 25), outline=(200, 130, 170, 100))
    bdraw.text((bx + 14, by + 7), b, font=font_badge, fill=(160, 100, 140, 210))
    bx += bw + 10
img_rgba = img.convert("RGBA")
img_rgba = Image.alpha_composite(img_rgba, badge_layer)
img = img_rgba.convert("RGB")
draw = ImageDraw.Draw(img)

# note URL
draw.text((W - 260, H - 38), "note.com/maki_claude_lab", font=font_site, fill=(160, 120, 150))

out = r"C:\Users\nyank\Documents\maki-hisho\maki_header.png"
img.save(out, "PNG")
print(f"完了: {out}  ({W}x{H})")
