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
            segments.append({"start": round(t, 3), "duration": round(dur, 3), "voice": ev["duration"],
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


# 差分画像の名前: images/報告者.png を基本に、_口半 / _口開 / _目閉 / _目閉_口半 / _目閉_口開
VARIANT_SUFFIX = {
    ("open", "closed"): "", ("open", "half"): "_口半", ("open", "open"): "_口開",
    ("closed", "closed"): "_目閉", ("closed", "half"): "_目閉_口半", ("closed", "open"): "_目閉_口開",
}


def load_character_images(cfg) -> dict[str, dict | None]:
    """キャラごとに {(目, 口): 画像} を読む。差分がなければ基本画像で代用する。"""
    lay = cfg["layout"]
    out: dict[str, dict | None] = {}
    for name, ch in cfg["characters"].items():
        rel = ch.get("image", f"{cfg['images_dir']}/{name}.png")
        base_path = find_file(ROOT / rel, IMAGE_EXTS) if rel else None
        if base_path is None:
            if rel:
                warn(f"{name} の画像 {rel} がないので、キャラなしで表示します")
            out[name] = None
            continue
        base = Image.open(base_path).convert("RGBA")
        scale = min(lay["character_max_width"] / base.width, lay["character_max_height"] / base.height)
        size = (round(base.width * scale), round(base.height * scale))
        found = {}
        for key, suffix in VARIANT_SUFFIX.items():
            p = base_path if not suffix else find_file(base_path.with_name(base_path.stem + suffix), IMAGE_EXTS)
            if p:
                found[key] = Image.open(p).convert("RGBA").resize(size, Image.LANCZOS)
        sprites = {}
        for eyes, mouth in VARIANT_SUFFIX:
            for cand in [(eyes, mouth), (eyes, "half" if mouth == "open" else mouth), (eyes, "closed"),
                         ("open", mouth), ("open", "half" if mouth == "open" else mouth), ("open", "closed")]:
                if cand in found:
                    sprites[(eyes, mouth)] = found[cand]
                    break
        lip = ("open", "open") in found or ("open", "half") in found
        log(f"  {name}: 差分{len(found)}枚（口パク{'あり' if lip else 'なし'}・まばたき{'あり' if ('closed', 'closed') in found else 'なし'}）")
        out[name] = sprites
    return out


def render_text_layer(text, fonts, style, center_y=None) -> tuple[Image.Image, tuple[int, int]]:
    """字幕・テロップを、文字の大きさぴったりの透明画像にする。"""
    lines = wrap_text(text, style["chars_per_line"])
    font = fit_font(fonts, lines, style["font_size"], W - 2 * style["margin_x"])
    lh = font.size * 1.25
    pad = style["stroke_width"] + 10
    width = int(max(font.getlength(l) for l in lines) + pad * 2)
    height = int(lh * len(lines) + pad * 2)
    img = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    draw_lines(ImageDraw.Draw(img), lines, font, width / 2, height / 2,
               style["color"], style["stroke_width"], style["stroke_color"])
    cy = H // 2 if center_y is None else center_y
    return img, ((W - width) // 2, int(cy - height / 2))


def voice_envelope(path: str, fps: int) -> np.ndarray:
    """音声の大きさ（0〜1）をフレームごとに求める。口パクと弾みに使う。"""
    with wave.open(path, "rb") as w:
        rate, nch = w.getframerate(), w.getnchannels()
        data = np.frombuffer(w.readframes(w.getnframes()), np.int16).astype(np.float32)
    if nch > 1:
        data = data.reshape(-1, nch).mean(axis=1)
    hop = rate / fps
    n = max(1, math.ceil(len(data) / hop))
    env = np.array([np.sqrt(np.mean(data[int(i * hop):int((i + 1) * hop)] ** 2) + 1e-9) for i in range(n)])
    ref = np.percentile(env, 90) if env.max() > 1 else 1.0
    env = np.clip(env / max(ref, 1e-6), 0, 1)
    return np.maximum(env, np.concatenate([[0], env[:-1]]) * 0.7)  # 口がチカチカしないよう少し残す


def ease_out(x: float) -> float:
    x = min(max(x, 0.0), 1.0)
    return 1 - (1 - x) ** 3


class Animator:
    """1フレームごとに画面を作る（背景＋動くキャラ＋字幕＋テロップ＋画面揺れ）。"""

    def __init__(self, timeline, cfg, fonts):
        self.cfg, self.anim, self.lay = cfg, cfg["animation"], cfg["layout"]
        self.fps = cfg["fps"]
        self.total = timeline["duration"]
        self.segs = timeline["segments"]
        self.starts = [s["start"] for s in self.segs]
        self.bg = load_background(cfg).convert("RGBA")
        self.sprites = load_character_images(cfg)
        self.subs = [render_text_layer(s["text"], fonts, cfg["subtitle"], self.lay["subtitle_center_y"])
                     for s in self.segs]
        self.telops = [(t, *render_text_layer(t["text"], fonts, cfg["telop"])) for t in timeline["telops"]]
        self.envs = [voice_envelope(s["audio"], self.fps) for s in self.segs]
        shake = set(self.anim["shake_se"])
        self.shakes = [s["start"] for s in timeline["se"] if s["name"] in shake]
        # 画面に出すキャラ（画像のないナレーターの間は、直前のキャラが待機姿勢で残る）
        self.visible, self.entry, last = [], [], None
        for s in self.segs:
            cur = s["character"] if self.sprites.get(s["character"]) else last
            self.entry.append(s["start"] if cur != last else None)
            self.visible.append(cur)
            last = cur
        self.entry_time = []  # 各セグメントで、今のキャラが登場した時刻
        t_in = 0.0
        for e in self.entry:
            t_in = e if e is not None else t_in
            self.entry_time.append(t_in)

    def blink(self, name: str, t: float) -> bool:
        period = 3.1 + (sum(map(ord, name)) % 17) / 10  # キャラごとにずらす
        phase = (t + (sum(map(ord, name)) % 7) * 0.37) % period
        return phase < 0.1 or (int(t / period) % 3 == 2 and 0.22 < phase < 0.32)  # たまに2回まばたき

    def frame(self, t: float) -> np.ndarray:
        a = self.anim
        si = max(0, np.searchsorted(self.starts, t, side="right") - 1)
        seg = self.segs[si]
        local = t - seg["start"]
        talking = local < seg["voice"]
        img = self.bg.copy()

        name = self.visible[si]
        if name:
            amp = 0.0
            if talking and seg["character"] == name:
                env = self.envs[si]
                amp = float(env[min(int(local * self.fps), len(env) - 1)])
            mouth = "open" if amp > a["mouth_open"] else "half" if amp > a["mouth_half"] else "closed"
            eyes = "closed" if self.blink(name, t) else "open"
            sprite = self.sprites[name][(eyes, mouth)]
            # 呼吸（ゆっくり上下）＋話すときの弾み＋登場のポップ
            dy = math.sin(2 * math.pi * t / a["breath_period"]) * a["breath_px"] - amp * a["bounce_px"]
            since = t - self.entry_time[si]
            if since < a["pop_time"]:
                k = ease_out(since / a["pop_time"])
                s = a["pop_scale"] + (1 - a["pop_scale"]) * k
                sprite = sprite.resize((max(1, int(sprite.width * s)), max(1, int(sprite.height * s))),
                                       Image.BILINEAR)
                dy += (1 - k) * a["pop_px"]
            x = (W - sprite.width) // 2
            y = int(self.lay["character_bottom"] - sprite.height + dy)
            img.alpha_composite(sprite, (max(0, x), max(0, y)), (max(0, -x), max(0, -y)))

        if talking:
            sub, pos = self.subs[si]
            img.alpha_composite(sub, pos)

        for tel, timg, (tx, ty) in self.telops:
            k = t - tel["start"]
            if 0 <= k < tel["duration"]:
                if k < a["telop_pop_time"]:  # ポンと大きく出て、元の大きさに戻る
                    s = a["telop_pop_scale"] + (1 - a["telop_pop_scale"]) * ease_out(k / a["telop_pop_time"])
                    timg2 = timg.resize((int(timg.width * s), int(timg.height * s)), Image.BILINEAR)
                    cx, cy = tx + timg.width // 2, ty + timg.height // 2
                    ox, oy = cx - timg2.width // 2, cy - timg2.height // 2
                    crop = (max(0, -ox), max(0, -oy))
                    img.alpha_composite(timg2, (max(0, ox), max(0, oy)), crop)
                else:
                    img.alpha_composite(timg, (tx, ty))

        out = img.convert("RGB")
        for st in self.shakes:  # ［SE:ドン］などで画面を揺らす
            k = t - st
            if 0 <= k < a["shake_time"]:
                p = a["shake_px"] * (1 - k / a["shake_time"])
                dx, dy = p * math.sin(k * 90), p * math.cos(k * 70)
                m = a["shake_px"] + 2
                out = out.crop((m + dx, m + dy, W - m + dx, H - m + dy)).resize((W, H), Image.BILINEAR)
                break
        return np.asarray(out)


# --------------------------------------------------------------------------
# 5. 書き出し（MoviePy）
# --------------------------------------------------------------------------
def render_video(timeline, cfg, out_path: Path) -> None:
    from moviepy import AudioFileClip, CompositeAudioClip, VideoClip, afx

    fonts = Fonts(cfg.get("font"))
    log("キャラ画像を読み込み中...")
    anim = Animator(timeline, cfg, fonts)
    total = timeline["duration"]
    video = VideoClip(frame_function=anim.frame, duration=total)

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
