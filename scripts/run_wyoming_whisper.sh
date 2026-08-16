#!/usr/bin/env bash
# 启动 wyoming-faster-whisper（STT，Wyoming 协议）。供 systemd 调用，也可手跑。
#
# 与 run_gptsovits_api.sh 同理，这个包装脚本存在的理由是环境变量：
#   - LD_LIBRARY_PATH：CTranslate2 要 cuDNN/cuBLAS，pip 版 nvidia 包装在
#     site-packages/nvidia/*/lib 下，不在默认搜索路径里
#   - HF_ENDPOINT：首次启动要从 HuggingFace 下模型，国内走镜像
set -euo pipefail

CONDA_ROOT="${CONDA_ROOT:-$HOME/miniconda3}"
ENV="${WHISPER_ENV:-whisper}"
DATA="${WHISPER_DATA:-$HOME/apps/whisper}"

# 绑 0.0.0.0：调用方是 NUC 上的 HA，跨机（同 ollama，异于 gptsovits-api）
URI="${WHISPER_URI:-tcp://0.0.0.0:10300}"
MODEL="${WHISPER_MODEL:-large-v3-turbo}"
LANG="${WHISPER_LANG:-zh}"

# shellcheck disable=SC1091
source "$CONDA_ROOT/etc/profile.d/conda.sh"
conda activate "$ENV"

SP="$CONDA_PREFIX/lib/python3.11/site-packages"
NVLIBS="$(find "$SP/nvidia" -maxdepth 2 -name lib -type d 2>/dev/null | tr '\n' ':')"
export LD_LIBRARY_PATH="${NVLIBS}$CONDA_PREFIX/lib:${LD_LIBRARY_PATH:-}"
# 不默认走 hf-mirror：它对 HEAD 请求返回 308 重定向，导致小文件（config/tokenizer/
# vocabulary 等）反复失败，而大文件 model.bin 反而能下成。既然 unit 里已经通过
# EnvironmentFile 给了 Clash 代理，直连 huggingface.co 更可靠。
# 需要时可显式 export HF_ENDPOINT 覆盖。
[ -n "${HF_ENDPOINT:-}" ] && export HF_ENDPOINT

mkdir -p "$DATA"
exec python -m wyoming_faster_whisper \
  --uri "$URI" \
  --model "$MODEL" \
  --language "$LANG" \
  --device cuda \
  --compute-type float16 \
  --beam-size 5 \
  --data-dir "$DATA" \
  --download-dir "$DATA"
