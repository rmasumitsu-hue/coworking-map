#!/usr/bin/env python3
"""動作確認用の仮素材を作る（既にあるファイルは上書きしない）。

- images/background.png と、config.json の各キャラ画像（色付きの人型＋名前）
- script.txt に出てくる SE と sounds/bgm.mp3（ffmpeg で作る単純な音）
"""
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

from PIL import Image, ImageDraw

sys.path.insert(0, str(Path(__file__).resolve().parent))
from make_short import DIRECTIVE_RE, ROOT, Fonts  # noqa: E402

COLORS = [(236, 112, 99), (93, 173, 226), (88, 214, 141), (245, 176, 65), (175, 122, 197)]
SE_TONES = {"ドン": "sine=f=70:d=0.6", "チーン": "sine=f=1800:d=1.2", "ガラッ": "anoisesrc=d=0.4:a=0.3",
            "ガシャーン": "anoisesrc=d=0.8:a=0.5"}


def make_image(path: Path, size, draw_fn):
    if path.exists():
        print(f"  あり: {path.relative_to(ROOT)}")
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    img = Image.new("RGBA", size, (0, 0, 0, 0))
    draw_fn(ImageDraw.Draw(img), img)
    img.save(path)
    print(f"  作成: {path.relative_to(ROOT)}")


def ffmpeg_audio(path: Path, src: str, extra=()):
    if path.exists():
        print(f"  あり: {path.relative_to(ROOT)}")
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", src, *extra,
                    "-ac", "2", "-ar", "44100", str(path)], check=True)
    print(f"  作成: {path.relative_to(ROOT)}")


def main():
    cfg = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
    fonts = Fonts(cfg.get("font"))

    def bg(d, img):
        for y in range(1920):
            k = y / 1920
            d.line([(0, y), (1080, y)], fill=(int(250 - 60 * k), int(225 - 80 * k), int(200 - 40 * k), 255))
        d.text((540, 300), "背景（仮）", font=fonts.get(60), fill=(120, 90, 80, 255), anchor="mm")

    make_image(ROOT / cfg["background"], (1080, 1920), bg)

    for i, (name, ch) in enumerate(cfg["characters"].items()):
        if not ch.get("image"):
            continue
        color = COLORS[i % len(COLORS)] + (255,)

        def person(d, img, color=color, name=name):
            d.ellipse([300, 60, 600, 360], fill=color)                 # 頭
            d.rounded_rectangle([170, 380, 730, 1000], 120, fill=color)  # 体
            d.text((450, 700), name, font=fonts.get(80), fill="white", anchor="mm",
                   stroke_width=4, stroke_fill="black")

        make_image(ROOT / ch["image"], (900, 1000), person)

    if not shutil.which("ffmpeg"):
        sys.exit("ffmpeg がないので音の仮素材は作れません。")
    script = (ROOT / "script.txt").read_text(encoding="utf-8")
    names = {m[1] for m in DIRECTIVE_RE.findall(script) if m[0] in ("SE", "ＳＥ")}
    for name in sorted(names):
        ffmpeg_audio(ROOT / cfg["sounds_dir"] / f"{name}.mp3", SE_TONES.get(name, "sine=f=880:d=0.3"))
    ffmpeg_audio(ROOT / cfg["bgm"], "sine=f=330:d=8", ["-af", "volume=0.5"])


if __name__ == "__main__":
    main()
