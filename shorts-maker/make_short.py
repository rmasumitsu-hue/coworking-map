#!/usr/bin/env python3
"""script.txt から縦型ショート動画（1080x1920）を自動生成する。

使い方:
    python make_short.py                      # script.txt → output/short.mp4
    python make_short.py scripts/ep02.txt     # → output/ep02.mp4
    python make_short.py a.txt b.txt c.txt    # まとめて量産
    python make_short.py --mock-tts           # VOICEVOXなしで動作確認（無音）
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import sys
import wave
from pathlib import Path

import numpy as np
import requests
from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent
W, H = 1080, 1920

# ［SE:ドン］ / ［テロップ:〇〇］（全角・半角の括弧とコロンどちらも可）
DIRECTIVE_RE = re.compile(r"[［\[]\s*(SE|ＳＥ|テロップ)\s*[:：]\s*(.+?)\s*[］\]]")
# キャラ名：セリフ
LINE_RE = re.compile(r"^(?P<name>[^：:\s［\[]{1,20})\s*[：:]\s*(?P<text>.+)$")
# 行頭に来てはいけない文字（禁則処理）
NO_HEAD = set("、。，．,.・：；:;？！?!ー～…‥）)」』】〕〉》’”ぁぃぅぇぉっゃゅょゎァィゥェォッャュョヮ")
IMAGE_EXTS = (".png", ".jpg", ".jpeg", ".webp")
SOUND_EXTS = (".mp3", ".wav", ".m4a", ".ogg")
FONT_CANDIDATES = [
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Black.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
    "/usr/share/fonts/noto-cjk/NotoSansCJK-Bold.ttc",
    "/usr/share/fonts/truetype/fonts-japanese-gothic.ttf",
    "/usr/share/fonts/opentype/ipaexfont-gothic/ipaexg.ttf",
    "/usr/share/fonts/opentype/ipafont-gothic/ipag.ttf",
    "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
    "/System/Library/Fonts/ヒラギノ角ゴシック W8.ttc",
    "/System/Library/Fonts/ヒラギノ角ゴシック W6.ttc",
    "/System/Library/Fonts/Hiragino Sans GB.ttc",
    "C:/Windows/Fonts/YuGothB.ttc",
    "C:/Windows/Fonts/meiryob.ttc",
    "C:/Windows/Fonts/msgothic.ttc",
]


def log(msg: str) -> None:
    print(msg, flush=True)


def warn(msg: str) -> None:
    print(f"[警告] {msg}", file=sys.stderr, flush=True)


# --------------------------------------------------------------------------
# 1. 台本の解析
# --------------------------------------------------------------------------
def parse_script(raw: str, aliases: dict[str, str]) -> list[dict]:
    """台本を セリフ(line) / 効果音(se) / テロップ(telop) のイベント列にする。"""
    events: list[dict] = []
    for no, line in enumerate(raw.splitlines(), 1):
        line = line.strip()
        if not line or line.startswith(("```", "#", "//")):
            continue
        for m in DIRECTIVE_RE.finditer(line):
            kind, value = m.groups()
            if kind == "テロップ":
                events.append({"type": "telop", "text": value, "src_line": no})
            else:
                events.append({"type": "se", "name": value, "src_line": no})
        rest = DIRECTIVE_RE.sub("", line).strip()
        if not rest:
            continue
        m = LINE_RE.match(rest)
        if not m:
            warn(f"{no}行目を解釈できないため飛ばします: {rest}")
            continue
        name = m["name"].strip()
        events.append({
            "type": "line",
            "character": aliases.get(name, name),
            "text": m["text"].strip(),
            "src_line": no,
        })
    return events


# --------------------------------------------------------------------------
# 2. VOICEVOX
# --------------------------------------------------------------------------
class Voicevox:
    def __init__(self, url: str):
        self.url = url.rstrip("/")
        self._speakers: list[dict] | None = None

    def _get(self, path: str, **kw):
        try:
            r = requests.get(f"{self.url}{path}", timeout=15, **kw)
        except requests.ConnectionError:
            sys.exit(f"VOICEVOX Engine（{self.url}）に接続できません。起動しているか確認してください。"
                     "\n動作確認だけなら --mock-tts を付けて実行できます。")
        r.raise_for_status()
        return r

    def _post(self, path: str, **kw):
        try:
            r = requests.post(f"{self.url}{path}", timeout=120, **kw)
        except requests.ConnectionError:
            sys.exit(f"VOICEVOX Engine（{self.url}）に接続できません。")
        r.raise_for_status()
        return r

    def speakers(self) -> list[dict]:
        if self._speakers is None:
            self._speakers = self._get("/speakers").json()
        return self._speakers

    def style_id(self, speaker: str, style: str | None) -> int:
        """/speakers から「話者名＋スタイル名」のIDを調べる。"""
        names = []
        for sp in self.speakers():
            names.append(sp["name"])
            if sp["name"] != speaker:
                continue
            for st in sp["styles"]:
                if not style or st["name"] == style:
                    return st["id"]
            styles = "、".join(s["name"] for s in sp["styles"])
            sys.exit(f"{speaker} にスタイル「{style}」がありません（あるもの: {styles}）")
        sys.exit(f"VOICEVOXに話者「{speaker}」が見つかりません（あるもの: {'、'.join(names)}）")

    def synthesize(self, text: str, style_id: int, speed: float, out: Path) -> None:
        query = self._post("/audio_query", params={"text": text, "speaker": style_id}).json()
        query["speedScale"] = speed
        wav = self._post("/synthesis", params={"speaker": style_id}, json=query).content
        out.write_bytes(wav)


def write_mock_wav(text: str, speed: float, out: Path) -> None:
    """VOICEVOXなしで動作確認するための無音WAV（文字数から長さを推定）。"""
    sec = max(0.6, len(text) * 0.13 / speed)
    rate = 24000
    with wave.open(str(out), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(b"\x00\x00" * int(sec * rate))


def wav_duration(path: Path) -> float:
    with wave.open(str(path), "rb") as w:
        return w.getnframes() / w.getframerate()


def synthesize_all(events, cfg, speed, cache_dir: Path, mock: bool) -> None:
    """全セリフの音声を作り、event に audio / duration を書き込む（キャッシュあり）。"""
    vv = None if mock else Voicevox(cfg["voicevox_url"])
    style_ids: dict[str, int] = {}
    lines = [e for e in events if e["type"] == "line"]
    for i, ev in enumerate(lines, 1):
        ch = cfg["characters"][ev["character"]]
        if mock:
            sid = -1
        else:
            if ev["character"] not in style_ids:
                style_ids[ev["character"]] = vv.style_id(ch["speaker"], ch.get("style"))
                log(f"  {ev['character']} → {ch['speaker']}（{ch.get('style') or '先頭スタイル'}） ID={style_ids[ev['character']]}")
            sid = style_ids[ev["character"]]
        key = hashlib.sha1(f"{mock}|{sid}|{speed:.3f}|{ev['text']}".encode()).hexdigest()[:16]
        out = cache_dir / f"{key}.wav"
        if not out.exists():
            if mock:
                write_mock_wav(ev["text"], speed, out)
            else:
                vv.synthesize(ev["text"], sid, speed, out)
        ev["audio"] = str(out)
        ev["duration"] = round(wav_duration(out), 3)
        print(f"\r  音声 {i}/{len(lines)}", end="", flush=True)
    print()


# --------------------------------------------------------------------------
# 3. タイムライン
# --------------------------------------------------------------------------
def find_file(base: Path, exts) -> Path | None:
    if base.suffix and base.exists():
        return base
    for ext in exts:
        p = base.with_name(base.stem + ext) if base.suffix in exts else base.with_name(base.name + ext)
        if p.exists():
            return p
    return None


def sound_duration(path: Path) -> float:
    from moviepy import AudioFileClip
    clip = AudioFileClip(str(path))
    d = clip.duration
    clip.close()
    return d


def build_timeline(events, cfg) -> dict:
    """セリフは音声の長さぶん順番に並べ、SE・テロップは次のセリフの頭に重ねる。"""
    t = 0.0
    segments, telops, ses = [], [], []
    pause = cfg["pause_after_line"]
    se_cache: dict[str, tuple[Path | None, float]] = {}
    for ev in events:
        if ev["type"] == "line":
            dur = ev["duration"] + pause
            segments.append({"start": round(t, 3), "duration": round(dur, 3),
                             "character": ev["character"], "text": ev["text"], "audio": ev["audio"]})
            t += dur
        elif ev["type"] == "telop":
            telops.append({"start": round(t, 3), "duration": cfg["telop_duration"], "text": ev["text"]})
        elif ev["type"] == "se":
            name = ev["name"]
            if name not in se_cache:
                p = find_file(ROOT / cfg["sounds_dir"] / name, SOUND_EXTS)
                if p is None:
                    warn(f"効果音 {cfg['sounds_dir']}/{name}.mp3 がないので鳴らしません")
                se_cache[name] = (p, sound_duration(p) if p else 0.0)
            p, d = se_cache[name]
            if p:
                ses.append({"start": round(t, 3), "duration": round(d, 3), "name": name, "path": str(p)})
    end = max([t] + [x["start"] + x["duration"] for x in telops + ses])
    voice = sum(s["duration"] - pause for s in segments)
    return {"duration": round(end, 3), "voice_duration": round(voice, 3),
            "segments": segments, "telops": telops, "se": ses}


# --------------------------------------------------------------------------
# 4. 画面づくり（Pillow）
# --------------------------------------------------------------------------
class Fonts:
    def __init__(self, path: str | None):
        self.path = self._resolve(path)
        self._cache: dict[int, ImageFont.FreeTypeFont] = {}
        log(f"フォント: {self.path}")

    @staticmethod
    def _resolve(path: str | None) -> str:
        if path:
            p = Path(path)
            p = p if p.is_absolute() else ROOT / p
            if p.exists():
                return str(p)
            warn(f"指定フォント {path} が見つかりません。自動で探します")
        local = sorted((ROOT / "fonts").glob("*.[ot]t[fc]")) if (ROOT / "fonts").exists() else []
        for cand in [*map(str, local), *FONT_CANDIDATES]:
            if Path(cand).exists():
                return cand
        sys.exit("日本語フォントが見つかりません。fonts/ にフォントを置くか config.json の font を指定してください。")

    def get(self, size: int) -> ImageFont.FreeTypeFont:
        if size not in self._cache:
            self._cache[size] = ImageFont.truetype(self.path, size)
        return self._cache[size]


def wrap_text(text: str, n: int) -> list[str]:
    """n字ごとに改行（句読点や閉じカッコが行頭に来ないようにする）。"""
    lines, cur = [], ""
    for ch in text:
        if len(cur) >= n and ch not in NO_HEAD:
            lines.append(cur)
            cur = ""
        cur += ch
    if cur:
        lines.append(cur)
    return lines


def fit_font(fonts: Fonts, lines: list[str], size: int, max_w: int) -> ImageFont.FreeTypeFont:
    while size > 20:
        f = fonts.get(size)
        if max(f.getlength(l) for l in lines) <= max_w:
            return f
        size -= 4
    return fonts.get(size)


def draw_lines(draw, lines, font, center_x, center_y, fill, stroke_w, stroke_fill, spacing=1.25):
    lh = font.size * spacing
    top = center_y - lh * len(lines) / 2 + lh / 2
    for i, line in enumerate(lines):
        draw.text((center_x, top + i * lh), line, font=font, fill=fill, anchor="mm",
                  stroke_width=stroke_w, stroke_fill=stroke_fill)


def load_background(cfg) -> Image.Image:
    p = find_file(ROOT / cfg["background"], IMAGE_EXTS)
    if p is None:
        warn(f"背景画像 {cfg['background']} がないので、グラデーション背景を使います")
        grad = np.linspace(0, 1, H)[:, None, None]
        arr = (np.array([40, 30, 70]) * (1 - grad) + np.array([15, 10, 25]) * grad)
        return Image.fromarray(np.broadcast_to(arr, (H, W, 3)).astype(np.uint8), "RGB")
    img = Image.open(p).convert("RGB")
    scale = max(W / img.width, H / img.height)  # 画面いっぱいに拡大して中央を切り抜き
    img = img.resize((math.ceil(img.width * scale), math.ceil(img.height * scale)), Image.LANCZOS)
    left, top = (img.width - W) // 2, (img.height - H) // 2
    return img.crop((left, top, left + W, top + H))


def load_character_images(cfg) -> dict[str, Image.Image | None]:
    lay = cfg["layout"]
    out = {}
    for name, ch in cfg["characters"].items():
        rel = ch.get("image", f"{cfg['images_dir']}/{name}.png")
        if not rel:
            out[name] = None
            continue
        p = find_file(ROOT / rel, IMAGE_EXTS)
        if p is None:
            warn(f"{name} の画像 {rel} がないので、キャラなしで表示します")
            out[name] = None
            continue
        img = Image.open(p).convert("RGBA")
        scale = min(lay["character_max_width"] / img.width, lay["character_max_height"] / img.height)
        out[name] = img.resize((round(img.width * scale), round(img.height * scale)), Image.LANCZOS)
    return out


def render_frame(bg, char_img, text, fonts, cfg) -> np.ndarray:
    lay, sub = cfg["layout"], cfg["subtitle"]
    frame = bg.copy().convert("RGBA")
    if char_img is not None:
        x = (W - char_img.width) // 2
        y = lay["character_bottom"] - char_img.height
        frame.alpha_composite(char_img, (x, y))
    if text:
        lines = wrap_text(text, sub["chars_per_line"])
        font = fit_font(fonts, lines, sub["font_size"], W - 2 * sub["margin_x"])
        draw_lines(ImageDraw.Draw(frame), lines, font, W // 2, lay["subtitle_center_y"],
                   sub["color"], sub["stroke_width"], sub["stroke_color"])
    return np.array(frame.convert("RGB"))


def render_telop(text, fonts, cfg) -> np.ndarray:
    tc = cfg["telop"]
    lines = wrap_text(text, tc["chars_per_line"])
    font = fit_font(fonts, lines, tc["font_size"], W - 2 * tc["margin_x"])
    lh = font.size * 1.25
    pad = tc["stroke_width"] + 10
    width = int(max(font.getlength(l) for l in lines) + pad * 2)
    height = int(lh * len(lines) + pad * 2)
    img = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    draw_lines(ImageDraw.Draw(img), lines, font, width / 2, height / 2,
               tc["color"], tc["stroke_width"], tc["stroke_color"])
    return np.array(img)


# --------------------------------------------------------------------------
# 5. 書き出し（MoviePy）
# --------------------------------------------------------------------------
def render_video(timeline, cfg, out_path: Path) -> None:
    from moviepy import (AudioFileClip, CompositeAudioClip, ImageClip,
                         concatenate_videoclips, afx)

    fonts = Fonts(cfg.get("font"))
    bg = load_background(cfg)
    chars = load_character_images(cfg)

    log("画面を作成中...")
    total = timeline["duration"]
    segs, telops = timeline["segments"], timeline["telops"]
    telop_imgs = [Image.fromarray(render_telop(t["text"], fonts, cfg), "RGBA") for t in telops]

    # 画面が変わる時刻（セリフ・テロップの開始/終了）で区切り、区間ごとに1枚の静止画にする。
    # テロップも静止画に焼き込むので、毎フレームの合成が不要になり書き出しが速い。
    cuts = sorted({0.0, total, *(s["start"] for s in segs),
                   *(t["start"] for t in telops), *(min(total, t["start"] + t["duration"]) for t in telops)})
    base, cache = [], {}
    for a, b in zip(cuts, cuts[1:]):
        if b - a < 1e-3:
            continue
        mid = (a + b) / 2
        si = max((i for i, s in enumerate(segs) if s["start"] <= mid), default=0)
        seg = segs[si]
        talking = mid < seg["start"] + seg["duration"]  # 最後のセリフの後は字幕なし
        active = tuple(i for i, t in enumerate(telops) if t["start"] <= mid < t["start"] + t["duration"])
        key = (si, talking, active)
        if key not in cache:
            frame = render_frame(bg, chars.get(seg["character"]), seg["text"] if talking else "", fonts, cfg)
            if active:
                img = Image.fromarray(frame).convert("RGBA")
                for i in active:
                    ti = telop_imgs[i]
                    img.alpha_composite(ti, ((W - ti.width) // 2, (H - ti.height) // 2))
                frame = np.array(img.convert("RGB"))
            cache[key] = frame
        base.append(ImageClip(cache[key]).with_duration(b - a))
    video = concatenate_videoclips(base, method="chain").with_duration(total)

    audio = [AudioFileClip(s["audio"]).with_start(s["start"]) for s in timeline["segments"]]
    audio += [AudioFileClip(s["path"]).with_effects([afx.MultiplyVolume(cfg["se_volume"])])
              .with_start(s["start"]) for s in timeline["se"]]
    bgm_path = find_file(ROOT / cfg["bgm"], SOUND_EXTS)
    if bgm_path:
        bgm = AudioFileClip(str(bgm_path)).with_effects([
            afx.AudioLoop(duration=total),
            afx.MultiplyVolume(cfg["bgm_volume"]),
            afx.AudioFadeOut(min(1.5, total / 4)),
        ])
        audio.append(bgm)
    else:
        warn(f"BGM {cfg['bgm']} がないので、BGMなしで作ります")
    video = video.with_audio(CompositeAudioClip(audio).with_duration(total))

    out_path.parent.mkdir(parents=True, exist_ok=True)
    video.write_videofile(str(out_path), fps=cfg["fps"], codec="libx264", audio_codec="aac",
                          audio_fps=44100, preset="medium", threads=4, pixel_format="yuv420p")
    video.close()


def measured_duration(path: Path) -> float:
    from moviepy import VideoFileClip
    clip = VideoFileClip(str(path))
    d = clip.duration
    clip.close()
    return d


# --------------------------------------------------------------------------
# メイン
# --------------------------------------------------------------------------
def load_config(path: Path) -> dict:
    cfg = json.loads(path.read_text(encoding="utf-8"))
    cfg.setdefault("aliases", {})
    return cfg


def make_one(script: Path, out: Path, cfg: dict, mock: bool, no_render: bool) -> None:
    log(f"\n=== {script.name} → {out.relative_to(ROOT) if out.is_relative_to(ROOT) else out} ===")
    events = parse_script(script.read_text(encoding="utf-8"), cfg["aliases"])
    unknown = sorted({e["character"] for e in events if e["type"] == "line"} - set(cfg["characters"]))
    if unknown:
        sys.exit(f"config.json に登録されていないキャラがいます: {'、'.join(unknown)}\n"
                 "characters に追加するか、aliases で既存キャラに割り当ててください。")
    if not any(e["type"] == "line" for e in events):
        sys.exit("セリフが1行もありません。")

    out.parent.mkdir(parents=True, exist_ok=True)
    json_path = out.with_suffix(".json")
    json_path.write_text(json.dumps({"script": str(script), "events": events},
                                    ensure_ascii=False, indent=2), encoding="utf-8")
    counts = {k: sum(e["type"] == k for e in events) for k in ("line", "se", "telop")}
    log(f"解析: セリフ{counts['line']}行 / SE{counts['se']}個 / テロップ{counts['telop']}個 → {json_path.name}")

    cache = ROOT / "output" / "cache"
    cache.mkdir(parents=True, exist_ok=True)
    limit, speed = cfg["max_duration"], cfg["speed"]
    while True:
        log(f"音声を作成中（話速 {speed:.2f}）...")
        synthesize_all(events, cfg, speed, cache, mock)
        tl = build_timeline(events, cfg)
        log(f"[長さ] {tl['duration']:.2f}秒（話速 {speed:.2f}）")
        if tl["duration"] <= limit:
            break
        if speed >= cfg["max_speed"]:
            warn(f"話速 {speed:.2f}（上限）でも {limit}秒を超えています。台本を短くしてください。")
            break
        # 声の部分だけが話速で縮むとみなして、必要な話速を見積もる
        fixed = tl["duration"] - tl["voice_duration"]
        need = speed * tl["voice_duration"] / max(limit - fixed - 0.3, 0.1)
        speed = min(cfg["max_speed"], max(speed + cfg["speed_step"], math.ceil(need * 20) / 20))
        log(f"{limit}秒を超えたので、話速を {speed:.2f} に上げて作り直します")
    if speed > 1.6:
        warn(f"話速 {speed:.2f} は聞き取りにくいかもしれません。台本を削るのがおすすめです。")

    json_path.write_text(json.dumps({"script": str(script), "speed": speed, "events": events,
                                     "timeline": tl}, ensure_ascii=False, indent=2), encoding="utf-8")
    if no_render:
        return
    render_video(tl, cfg, out)
    log(f"完成: {out}  長さ {measured_duration(out):.2f}秒（話速 {speed:.2f}）")


def main() -> None:
    ap = argparse.ArgumentParser(description="台本から縦型ショート動画を自動生成")
    ap.add_argument("scripts", nargs="*", default=[str(ROOT / "script.txt")], help="台本ファイル（複数可）")
    ap.add_argument("-o", "--output", help="出力mp4（台本が1つのときだけ）")
    ap.add_argument("-c", "--config", default=str(ROOT / "config.json"))
    ap.add_argument("--speed", type=float, help="話速の初期値（config.json より優先）")
    ap.add_argument("--mock-tts", action="store_true", help="VOICEVOXを使わず無音で動作確認")
    ap.add_argument("--no-render", action="store_true", help="JSONと音声だけ作り、動画は書き出さない")
    args = ap.parse_args()

    cfg = load_config(Path(args.config))
    if args.speed:
        cfg["speed"] = args.speed
    scripts = [Path(s).resolve() for s in args.scripts]
    if args.output and len(scripts) > 1:
        sys.exit("-o は台本が1つのときだけ使えます。")
    for s in scripts:
        if not s.exists():
            sys.exit(f"台本が見つかりません: {s}")
        if args.output:
            out = Path(args.output).resolve()
        elif s.name == "script.txt":
            out = ROOT / "output" / "short.mp4"
        else:
            out = ROOT / "output" / f"{s.stem}.mp4"
        make_one(s, out, cfg, args.mock_tts, args.no_render)


if __name__ == "__main__":
    main()
