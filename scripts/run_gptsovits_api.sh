#!/usr/bin/env bash
# 启动 GPT-SoVITS api.py（曼波音色）。供 systemd 调用，也可直接手跑。
#
# 这个包装脚本存在的唯一理由是 LD_LIBRARY_PATH —— 光激活 conda 环境不够：
#   1) torchcodec 的 .so 没有指向 conda lib 的 RPATH，找不到 FFmpeg 的 libav*
#      （conda-forge 默认会装 ffmpeg 9，而 torchcodec 只支持 4-8，安装时已降级到 7）
#   2) libnppicc.so.12 等 NVIDIA NPP 库不在 torch 的预加载清单里，也要手动加进来
# 缺任一条，api.py 会以「HTTP 200 + 0 字节」的形式静默失败，不报错。
set -euo pipefail

GS_ROOT="${GS_ROOT:-$HOME/apps/GPT-SoVITS}"
CONDA_ROOT="${CONDA_ROOT:-$HOME/miniconda3}"
CONDA_ENV="${CONDA_ENV:-GPTSoVits}"
BIND_ADDR="${GS_BIND:-127.0.0.1}"   # 需要局域网访问时改 0.0.0.0（并开防火墙）
BIND_PORT="${GS_PORT:-9880}"

SOVITS="${GS_SOVITS:-$GS_ROOT/SoVITS_weights_v2Pro/manbo_e8_s168.pth}"
GPT="${GS_GPT:-$GS_ROOT/GPT_weights_v2Pro/manbo-e10.ckpt}"
REF_WAV="${GS_REF_WAV:-$GS_ROOT/refer/manbo_refer.wav}"
REF_TEXT="${GS_REF_TEXT:-大家好，欢迎来到我的频道，今天给大家分享一个有趣的内容}"

# shellcheck disable=SC1091
source "$CONDA_ROOT/etc/profile.d/conda.sh"
conda activate "$CONDA_ENV"

SP="$CONDA_PREFIX/lib/python3.10/site-packages"
NVLIBS="$(find "$SP/nvidia" -maxdepth 2 -name lib -type d 2>/dev/null | tr '\n' ':')"
export LD_LIBRARY_PATH="$CONDA_PREFIX/lib:${NVLIBS}${LD_LIBRARY_PATH:-}"

cd "$GS_ROOT"
exec python api.py \
  -s "$SOVITS" \
  -g "$GPT" \
  -dr "$REF_WAV" \
  -dt "$REF_TEXT" \
  -dl zh -d cuda \
  -a "$BIND_ADDR" -p "$BIND_PORT"
