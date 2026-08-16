#!/usr/bin/env bash
# 启动 wyoming-openwakeword（唤醒词检测，Wyoming 协议）。供 systemd 调用，也可手跑。
#
# ⚠️ numpy 必须 <2：tflite-runtime 是按 numpy 1.x 的 C ABI 编的，装上 numpy 2.x
#    会在**子线程里**炸 `_ARRAY_API not found` / `numpy.core.multiarray failed to import`，
#    而端口照样监听、Describe 照样回全部唤醒词 —— 看起来完全正常，只是永远检测不到。
#    这是本项目第五个「静默失败」。
set -euo pipefail

CONDA_ROOT="${CONDA_ROOT:-$HOME/miniconda3}"
ENV="${WAKEWORD_ENV:-wakeword}"

# 绑 0.0.0.0：虽然目前只有本机的 satellite 连它，但留着以后 HA 侧直连的余地
URI="${WAKEWORD_URI:-tcp://0.0.0.0:10400}"
MODEL="${WAKEWORD_MODEL:-hey_jarvis}"
# 阈值越低越灵敏、误唤醒越多。0.5 是上游默认，实际要按误唤醒率调
THRESHOLD="${WAKEWORD_THRESHOLD:-0.5}"

# shellcheck disable=SC1091
source "$CONDA_ROOT/etc/profile.d/conda.sh"
conda activate "$ENV"

exec python -m wyoming_openwakeword \
  --uri "$URI" \
  --preload-model "$MODEL" \
  --threshold "$THRESHOLD" \
  --custom-model-dir "$HOME/apps/wakeword/models"
