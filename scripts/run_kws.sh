#!/usr/bin/env bash
# 启动 wyoming_kws（sherpa-onnx 中文唤醒词，Wyoming 协议）。供 systemd 调用，也可手跑。
#
# 顶掉 run_wakeword.sh 的 openWakeWord：那个没有中文词，自训要 16.5 GB 数据 + 几小时，
# 换个词从头再来。sherpa 的 KWS 是开放词表，唤醒词改一行文本就生效。
#
# ⚠️ 唤醒词内部不是汉字，是**带调拼音声韵母**（「你好曼波」-> `n ǐ h ǎo m àn b ō`）。
#    这里写中文即可，wyoming_kws.py 会用 sherpa_onnx.text2token 自动转。
#
# ⚠️ SCORE 不是越大越好：实测 score 3.0 + threshold 0.45 召回率从 73% 崩到 40%。
#    平台区在 score 1~2、threshold 0.25，别乱调；改之前先跑 scripts/eval_kws.py。
#
# ⚠️ 与 run_wakeword.sh **抢同一个 10400 端口**，两个服务不能同时开。
set -euo pipefail

CONDA_ROOT="${CONDA_ROOT:-$HOME/miniconda3}"
# 独立的精简环境（约 380 MB）：只有 sherpa-onnx + wyoming，不含训练那套
ENV="${KWS_ENV:-kws}"

URI="${KWS_URI:-tcp://0.0.0.0:10400}"
MODEL_DIR="${KWS_MODEL_DIR:-$HOME/apps/wakeword/sherpa/sherpa-onnx-kws-zipformer-zh-en-3M-2025-12-20}"
KEYWORD="${KWS_KEYWORD:-你好曼波}"
THRESHOLD="${KWS_THRESHOLD:-0.25}"
SCORE="${KWS_SCORE:-1.5}"
# chunk-8 / chunk-16 × fp32 / int8 四种组合实测**完全一样**（普通话召回 100%、
# 误唤醒 0.17%），所以选延迟最低、内存最小的 chunk-8 + int8
CHUNK="${KWS_CHUNK:-8}"

# shellcheck disable=SC1091
source "$CONDA_ROOT/etc/profile.d/conda.sh"
conda activate "$ENV"

exec python "$(dirname "$0")/wyoming_kws.py" \
  --uri "$URI" \
  --model-dir "$MODEL_DIR" \
  --keyword "$KEYWORD" \
  --threshold "$THRESHOLD" \
  --score "$SCORE" \
  --chunk "$CHUNK" \
  --int8
