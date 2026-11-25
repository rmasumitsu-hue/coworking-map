# 希望 — 足跡で書く漢字（縦型ショート）

1080×1920 / 30fps / 30秒。Chromium（Canvas2D + WebGL）でフレームを描画し、ffmpeg で mp4 にします。

```
node render.cjs --still 26          # 26秒時点の静止画 → still_26s.png
node render.cjs --video kibou.mp4   # 動画全体（--workers 4 で並列数指定）
```

- `render.cjs` 冒頭の `TEXT = "希望"` を書き換えると文字を差し替えられます（上から縦に並びます）。
- 書き順と画の形は KanjiVG（CC BY-SA 3.0）から取得し、`kanjivg/` にキャッシュします。
- 線は描きません。各画を 2〜3 人のグループが横並びで歩き、残る足跡だけで字ができます。
