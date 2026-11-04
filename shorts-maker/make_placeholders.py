#!/usr/bin/env python3
"""動作確認用の仮素材を作る（既にあるファイルは上書きしない。--force で上書き）。

- images/background.png（台所風の背景）
- キャラ画像（オリジナルのアニメ風キャラ。目・口の差分6枚ずつ）
    images/報告者.png, 報告者_口半.png, 報告者_口開.png,
    報告者_目閉.png, 報告者_目閉_口半.png, 報告者_目閉_口開.png
- script.txt に出てくる SE と sounds/bgm.mp3（ffmpeg で作る単純な音）
"""
import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

sys.path.insert(0, str(Path(__file__).resolve().parent))
from make_short import DIRECTIVE_RE, ROOT, VARIANT_SUFFIX  # noqa: E402

SS = 2  # 2倍で描いて縮小し、線をなめらかにする
LINE = (70, 45, 45, 255)
SKIN = (255, 230, 214, 255)
SKIN_SHADE = (240, 200, 185, 255)

# キャラ設定（すべてオリジナルデザイン）
CHARACTERS = {
    "報告者": dict(hair=(150, 95, 60), hair_style="bob", eye=(120, 80, 50), clothes=(250, 190, 200),
                 extra="apron", brow="soft", lid="normal"),
    "義母": dict(hair=(125, 105, 130), hair_style="bun", eye=(90, 60, 90), clothes=(110, 60, 110),
               extra="pearls", brow="angry", lid="smug", glasses=True),
    "アシスタント": dict(hair=(45, 45, 60), hair_style="ponytail", eye=(60, 110, 160), clothes=(245, 245, 245),
                   extra="chef", brow="soft", lid="normal"),
}
SE_TONES = {"ドン": "sine=f=70:d=0.6", "チーン": "sine=f=1800:d=1.2", "ガラッ": "anoisesrc=d=0.4:a=0.3",
            "ガシャーン": "anoisesrc=d=0.8:a=0.5"}


def S(*v):
    return [x * SS for x in v]


def darker(c, k=0.75):
    return tuple(int(x * k) for x in c[:3]) + (255,)


def draw_character(spec: dict, eyes: str, mouth: str) -> Image.Image:
    img = Image.new("RGBA", (900 * SS, 1100 * SS), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    hair = tuple(spec["hair"]) + (255,)
    hair_dark = darker(hair)
    lw = 4 * SS

    # --- 後ろ髪 ---
    style = spec["hair_style"]
    if style == "bob":
        d.rounded_rectangle(S(215, 200, 685, 700), radius=200 * SS, fill=hair, outline=LINE, width=lw)
    elif style == "ponytail":
        d.polygon(S(560, 260, 760, 380, 800, 640, 720, 860, 690, 620, 600, 420), fill=hair, outline=LINE)
        d.ellipse(S(235, 190, 665, 640), fill=hair, outline=LINE, width=lw)
    elif style == "bun":
        d.ellipse(S(375, 70, 525, 220), fill=hair, outline=LINE, width=lw)
        d.ellipse(S(240, 190, 660, 600), fill=hair, outline=LINE, width=lw)

    # --- 体・首 ---
    clothes = tuple(spec["clothes"]) + (255,)
    d.rounded_rectangle(S(185, 730, 715, 1180), radius=150 * SS, fill=clothes, outline=LINE, width=lw)
    d.rectangle(S(405, 610, 495, 760), fill=SKIN)
    d.polygon(S(405, 700, 495, 700, 450, 760), fill=SKIN_SHADE)
    d.line(S(405, 640, 405, 745), fill=LINE, width=lw)
    d.line(S(495, 640, 495, 745), fill=LINE, width=lw)
    # 服の飾り
    if spec["extra"] == "apron":
        d.polygon(S(330, 830, 570, 830, 610, 1100, 290, 1100), fill=(255, 250, 240, 255), outline=LINE)
        d.line(S(330, 830, 300, 745), fill=LINE, width=lw)
        d.line(S(570, 830, 600, 745), fill=LINE, width=lw)
        d.rounded_rectangle(S(390, 900, 510, 980), radius=20 * SS, outline=LINE, width=3 * SS)
    elif spec["extra"] == "pearls":
        d.polygon(S(380, 740, 520, 740, 450, 860), fill=(235, 220, 200, 255), outline=LINE)
        for i in range(9):
            x = 395 + i * 14
            y = 765 + (abs(i - 4) * -6) + 24
            d.ellipse(S(x - 8, y - 8, x + 8, y + 8), fill=(250, 250, 255, 255), outline=(150, 150, 160, 255))
    elif spec["extra"] == "chef":
        d.line(S(450, 760, 450, 1100), fill=(200, 200, 200, 255), width=3 * SS)
        for y in (830, 900, 970):
            for x in (410, 490):
                d.ellipse(S(x - 10, y - 10, x + 10, y + 10), fill=(60, 60, 70, 255))
        d.polygon(S(380, 740, 450, 800, 520, 740), fill=(220, 60, 60, 255), outline=LINE)  # スカーフ

    # --- 顔 ---
    d.ellipse(S(255, 215, 645, 650), fill=SKIN, outline=LINE, width=lw)
    d.polygon(S(300, 560, 450, 690, 600, 560), fill=SKIN)
    d.line(S(300, 560, 450, 690), fill=LINE, width=lw)
    d.line(S(600, 560, 450, 690), fill=LINE, width=lw)
    d.line(S(450, 532, 444, 548), fill=SKIN_SHADE[:3] + (255,), width=3 * SS)  # 鼻

    # ほっぺ（半透明）
    blush = Image.new("RGBA", img.size, (0, 0, 0, 0))
    bd = ImageDraw.Draw(blush)
    bd.ellipse(S(300, 535, 375, 570), fill=(255, 130, 150, 110))
    bd.ellipse(S(525, 535, 600, 570), fill=(255, 130, 150, 110))
    img.alpha_composite(blush.filter(ImageFilter.GaussianBlur(6 * SS)))
    d = ImageDraw.Draw(img)

    # --- 目 ---
    iris = tuple(spec["eye"]) + (255,)
    ry = 50 if spec["lid"] == "smug" else 58
    for cx in (372, 528):
        cy = 475
        if eyes == "open":
            d.ellipse(S(cx - 46, cy - ry, cx + 46, cy + ry), fill=(255, 255, 255, 255))
            d.ellipse(S(cx - 36, cy - ry + 14, cx + 36, cy + ry), fill=iris)
            d.ellipse(S(cx - 17, cy - 6, cx + 17, cy + 30), fill=darker(iris, 0.45))
            d.ellipse(S(cx - 30, cy - ry + 18, cx - 8, cy - ry + 40), fill=(255, 255, 255, 255))
            d.ellipse(S(cx + 8, cy + 18, cx + 20, cy + 30), fill=(255, 255, 255, 230))
            if spec["lid"] == "smug":  # 半目（見下した目つき）
                d.rectangle(S(cx - 50, cy - ry - 6, cx + 50, cy - 12), fill=SKIN)
                d.line(S(cx - 50, cy - 10, cx + 50, cy - 14), fill=LINE, width=9 * SS)
            else:
                d.arc(S(cx - 54, cy - ry - 6, cx + 54, cy + ry + 30), 200, 340, fill=LINE, width=10 * SS)
                wing = 1 if cx > 450 else -1
                d.line(S(cx + 48 * wing, cy - 30, cx + 62 * wing, cy - 42), fill=LINE, width=6 * SS)
            d.arc(S(cx - 40, cy - ry, cx + 40, cy + ry + 4), 60, 120, fill=LINE, width=3 * SS)
        else:  # 閉じた目
            d.arc(S(cx - 46, cy - 30, cx + 46, cy + 20), 20, 160, fill=LINE, width=8 * SS)

    # --- 口 ---
    if mouth == "closed":
        if spec["lid"] == "smug":
            d.line(S(422, 592, 478, 580), fill=LINE, width=5 * SS)
            d.line(S(478, 580, 486, 572), fill=LINE, width=4 * SS)
        else:
            d.arc(S(425, 565, 475, 595), 20, 160, fill=LINE, width=5 * SS)
    elif mouth == "half":
        d.ellipse(S(430, 574, 470, 602), fill=(150, 45, 55, 255), outline=LINE, width=3 * SS)
    else:
        d.ellipse(S(420, 566, 480, 622), fill=(150, 45, 55, 255), outline=LINE, width=3 * SS)
        d.chord(S(432, 594, 468, 620), 0, 180, fill=(240, 120, 130, 255))

    # --- 前髪 ---
    d.chord(S(240, 185, 660, 610), 180, 360, fill=hair, outline=LINE, width=lw)
    tips = [(262, 470), (300, 400), (340, 425), (380, 395), (420, 418), (460, 392),
            (505, 420), (545, 398), (590, 428), (628, 400), (640, 470)]
    pts = [(250, 395)] + tips + [(650, 395)]
    d.polygon(S(*sum(([x, y] for x, y in pts), [])), fill=hair)
    for (x1, y1), (x2, y2) in zip(pts[1:-2], pts[2:-1]):
        d.line(S(x1, y1, x2, y2), fill=LINE, width=3 * SS)
    d.arc(S(300, 230, 470, 330), 200, 300, fill=(255, 255, 255, 120), width=8 * SS)  # 髪のつや
    if style == "bob":  # サイドの髪
        d.polygon(S(250, 395, 232, 640, 290, 610, 282, 420), fill=hair, outline=LINE)
        d.polygon(S(650, 395, 668, 640, 610, 610, 618, 420), fill=hair, outline=LINE)
        d.rounded_rectangle(S(560, 330, 620, 350), radius=8 * SS, fill=(255, 210, 80, 255))  # ヘアピン
    elif style == "ponytail":
        d.polygon(S(252, 395, 245, 560, 285, 530, 282, 420), fill=hair, outline=LINE)
        d.polygon(S(648, 395, 655, 560, 615, 530, 618, 420), fill=hair, outline=LINE)

    # --- 眉 ---
    for cx, side in ((372, -1), (528, 1)):
        if spec["brow"] == "angry":  # 内側が下がった眉
            d.line(S(cx + 45 * side, 392, cx - 40 * side, 410), fill=darker(hair, 0.6), width=8 * SS)
        else:
            d.arc(S(cx - 48, 382, cx + 48, 432), 210, 330, fill=darker(hair, 0.6), width=7 * SS)

    if spec["extra"] == "chef":  # コック帽
        d.rectangle(S(320, 140, 580, 250), fill=(255, 255, 255, 255), outline=LINE, width=lw)
        for x, y, r in ((340, 110, 70), (450, 80, 85), (560, 110, 70)):
            d.ellipse(S(x - r, y - r, x + r, y + r), fill=(255, 255, 255, 255), outline=LINE, width=lw)
        d.rectangle(S(324, 150, 576, 246), fill=(255, 255, 255, 255))

    if spec.get("glasses"):
        for cx in (372, 528):
            d.rounded_rectangle(S(cx - 62, 420, cx + 62, 530), radius=30 * SS, outline=(110, 60, 40, 255),
                                width=6 * SS)
        d.line(S(434, 460, 466, 460), fill=(110, 60, 40, 255), width=6 * SS)

    return img.resize((900, 1100), Image.LANCZOS)


def draw_background() -> Image.Image:
    """台所風の背景（壁・窓・カウンター）。"""
    img = Image.new("RGB", (1080, 1920))
    d = ImageDraw.Draw(img)
    for y in range(1920):
        k = y / 1920
        d.line([(0, y), (1080, y)], fill=(int(255 - 25 * k), int(240 - 40 * k), int(215 - 50 * k)))
    for x in range(0, 1080, 90):  # 壁のタイル
        d.line([(x, 0), (x, 1250)], fill=(235, 215, 190), width=2)
    d.rounded_rectangle([140, 160, 940, 720], radius=24, fill=(190, 225, 245), outline=(150, 110, 80), width=14)
    d.line([(540, 160), (540, 720)], fill=(150, 110, 80), width=10)
    d.line([(140, 440), (940, 440)], fill=(150, 110, 80), width=10)
    d.rectangle([0, 1250, 1080, 1920], fill=(170, 120, 85))
    d.rectangle([0, 1250, 1080, 1290], fill=(130, 90, 60))
    return img.filter(ImageFilter.GaussianBlur(6))  # キャラが目立つように少しぼかす


def save(img, path: Path, force: bool):
    if path.exists() and not force:
        print(f"  あり: {path.relative_to(ROOT)}")
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    img.save(path)
    print(f"  作成: {path.relative_to(ROOT)}")


def ffmpeg_audio(path: Path, src: str, extra=(), force=False):
    if path.exists() and not force:
        print(f"  あり: {path.relative_to(ROOT)}")
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", src, *extra,
                    "-ac", "2", "-ar", "44100", str(path)], check=True)
    print(f"  作成: {path.relative_to(ROOT)}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true", help="既にある素材も上書きする")
    args = ap.parse_args()
    cfg = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))

    save(draw_background(), ROOT / cfg["background"], args.force)
    for name, spec in CHARACTERS.items():
        rel = cfg["characters"].get(name, {}).get("image")
        if not rel:
            continue
        base = ROOT / rel
        for (eyes, mouth), suffix in VARIANT_SUFFIX.items():
            save(draw_character(spec, eyes, mouth), base.with_name(base.stem + suffix + ".png"), args.force)

    if not shutil.which("ffmpeg"):
        sys.exit("ffmpeg がないので音の仮素材は作れません。")
    script = (ROOT / "script.txt").read_text(encoding="utf-8")
    names = {m[1] for m in DIRECTIVE_RE.findall(script) if m[0] in ("SE", "ＳＥ")}
    for name in sorted(names):
        ffmpeg_audio(ROOT / cfg["sounds_dir"] / f"{name}.mp3", SE_TONES.get(name, "sine=f=880:d=0.3"))
    ffmpeg_audio(ROOT / cfg["bgm"], "sine=f=330:d=8", ["-af", "volume=0.5"])


if __name__ == "__main__":
    main()
