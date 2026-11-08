#!/usr/bin/env bash
# 乱数シードを変えて別パターンを書き出す
#   ./make_variations.sh            → seed 2, 3 の2本
#   ./make_variations.sh 10 11 12   → 任意のシードで何本でも
set -euo pipefail
cd "$(dirname "$0")"
seeds=("$@")
[ ${#seeds[@]} -eq 0 ] && seeds=(2 3)
for s in "${seeds[@]}"; do
  python3 footprint_art.py --seed "$s" --out "footprint_art_seed${s}.mp4"
done
