# Footprint Art（YouTubeショート用アニメーション）

真っ白な無限の空間を真上から見下ろし、人のシルエットがゆっくり歩きます。一歩ごとに足跡が残り、その足跡からインクや花びらの模様がじわっと広がります。最後には床全体が一枚の絵として完成します。

- 1080×1920 / 30fps / 20秒（600フレーム）/ BGM・文字なし
- 0〜1秒: 最初の足跡が色づいて広がる（冒頭の引き込み）
- 〜15.6秒: 蛇行しながら歩き、足跡ごとに花・放射・しずくの模様が開く
- 15.7〜18秒: 残った白い隙間に花が咲き、床全体が埋まる
- 18〜20秒: ゆっくりズームアウトして完成した絵を見せる
- 配色: シードごとに5種類の配色ルール（類似色・分裂補色・トライアド・寒暖・テトラード）から1つを選び、明度と彩度の幅を揃えて統一感を出す

## 必要なもの
Python 3, numpy, Pillow, ffmpeg（libx264）

## 使い方
```bash
python3 footprint_art.py                      # seed=1 → footprint_art.mp4
./make_variations.sh                          # seed 2, 3 → footprint_art_seed2.mp4 / footprint_art_seed3.mp4
./make_variations.sh 10 11 12                 # 好きなシードで何本でも
python3 footprint_art.py --seed 5 --out a.mp4 # シードと出力名を指定
python3 footprint_art.py --seed 5 --preview 0,300,599   # 指定フレームだけPNGで確認
python3 footprint_art.py --frames-dir frames  # 連番PNGも保存
```
同じシードなら同じ動画になります。1本あたり約2分（4コア）です。
