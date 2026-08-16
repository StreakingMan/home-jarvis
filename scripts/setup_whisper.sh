#!/usr/bin/env bash
# 装 wyoming-faster-whisper（STT，Wyoming 协议 :10300）
#
# 独立 conda 环境，不与 GPTSoVits 共用 —— faster-whisper 走 CTranslate2，
# GPT-SoVITS 走 PyTorch，两者对 cuDNN/cuBLAS 的版本要求不一定一致，隔开更省事。
#
# 模型选 large-v3-turbo：809M 参数，比 large-v3 快约 8 倍，中文质量损失很小，
# fp16 约 1.6GB 显存。别用 base/small —— 中文识别质量会毁掉整条链路，
# 前面再准后面也救不回来。
set -euo pipefail

CONDA_ROOT="${CONDA_ROOT:-$HOME/miniconda3}"
ENV="${WHISPER_ENV:-whisper}"
DATA="${WHISPER_DATA:-$HOME/apps/whisper}"

# shellcheck disable=SC1091
source "$CONDA_ROOT/etc/profile.d/conda.sh"

if ! conda env list | grep -q "^$ENV "; then
  echo "=== 创建环境 $ENV (python 3.11) ==="
  conda create -n "$ENV" --override-channels -c conda-forge -y python=3.11
fi
conda activate "$ENV"

echo "=== 安装 wyoming-faster-whisper ==="
pip install -q --upgrade pip
pip install -q wyoming-faster-whisper

# CTranslate2 需要 cuDNN / cuBLAS，pip 版 nvidia 包提供，靠 LD_LIBRARY_PATH 找到
echo "=== 安装 CUDA 运行库 ==="
pip install -q nvidia-cudnn-cu12 nvidia-cublas-cu12

mkdir -p "$DATA"
echo "=== 版本 ==="
python -c "import faster_whisper, wyoming; print('  faster-whisper OK')"
du -sh "$CONDA_ROOT/envs/$ENV"

echo
echo "接下来："
echo "  cp deploy/systemd/wyoming-whisper.service ~/.config/systemd/user/"
echo "  systemctl --user daemon-reload && systemctl --user enable --now wyoming-whisper"
echo "首次启动会下载模型（large-v3-turbo，约 1.6GB），走 HF 镜像。"
