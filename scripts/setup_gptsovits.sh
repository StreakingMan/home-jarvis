#!/bin/bash
# GPT-SoVITS 本地部署 — 后台安装脚本
# 目标: ~/apps/GPT-SoVITS  |  conda 环境: GPTSoVits (python 3.10)
# 设备: CU128 (RTX 5060 Ti / Blackwell sm_120)  |  模型源: ModelScope (国内)
set -o pipefail

LOG_STEP() { echo -e "\n\033[1;32m===== [$(date +%H:%M:%S)] $* =====\033[0m"; }

MC=$HOME/miniconda3

# ---------- 1. Miniconda ----------
if [ ! -x "$MC/bin/conda" ]; then
  LOG_STEP "安装 Miniconda 到 $MC"
  # 注意: WSL 直连外网不通(TUNA 直连返回空), 必须走 Clash 代理; 用 wget -c 断点续传
  # 上一次 curl 在 47% 被 SSL_read unexpected eof 掐断, 故改用可续传方式并多次重试
  SH=/tmp/miniconda.sh
  ok=false
  for url in \
    "https://mirrors.tuna.tsinghua.edu.cn/anaconda/miniconda/Miniconda3-latest-Linux-x86_64.sh" \
    "https://repo.anaconda.com/miniconda/Miniconda3-latest-Linux-x86_64.sh"; do
    echo "尝试: $url"
    if wget -c --tries=10 --waitretry=5 --read-timeout=60 --progress=dot:giga -O "$SH" "$url"; then
      # 校验: 安装脚本应 >100MB 且是 shell 脚本
      sz=$(stat -c%s "$SH" 2>/dev/null || echo 0)
      if [ "$sz" -gt 100000000 ]; then ok=true; echo "下载完成 ${sz}B"; break; fi
      echo "文件过小(${sz}B), 换源重试"; rm -f "$SH"
    fi
  done
  [ "$ok" = true ] || exit 11
  # -b 批处理模式：不改任何 shell rc，不污染现有环境
  bash "$SH" -b -p "$MC" || exit 12
  rm -f "$SH"
else
  LOG_STEP "Miniconda 已存在，跳过"
fi

# shellcheck disable=SC1091
source "$MC/etc/profile.d/conda.sh" || exit 13
conda --version

# ---------- 2. 环境 ----------
if ! conda env list | grep -q '^GPTSoVits '; then
  LOG_STEP "创建 conda 环境 GPTSoVits (python 3.10)"
  # 只用 conda-forge 并 --override-channels: 绕开 repo.anaconda.com 的 ToS 门槛
  # (不代用户接受 Anaconda 服务条款; install.sh 本身也只用 conda-forge)
  conda create -n GPTSoVits python=3.10 -y -c conda-forge --override-channels || exit 21
else
  LOG_STEP "环境 GPTSoVits 已存在，跳过"
fi
conda activate GPTSoVits || exit 22
python --version

# ---------- 3. 官方安装脚本 ----------
LOG_STEP "运行 install.sh --device CU128 --source ModelScope --download-uvr5"
cd "$HOME/apps/GPT-SoVITS" || exit 31
# --download-uvr5: 顺带装人声分离模型，后面从视频里扒参考音频要用
bash install.sh --device CU128 --source ModelScope --download-uvr5 || exit 32

# ---------- 4. 自检 ----------
LOG_STEP "自检"
python - <<'PY'
import torch, sys
print("python      :", sys.version.split()[0])
print("torch       :", torch.__version__)
print("cuda avail  :", torch.cuda.is_available())
if torch.cuda.is_available():
    print("gpu         :", torch.cuda.get_device_name(0))
    print("capability  :", torch.cuda.get_device_capability(0), "(5060 Ti 应为 (12,0))")
PY
echo "--- 底模文件 ---"
find "$HOME/apps/GPT-SoVITS/GPT_SoVITS/pretrained_models" -maxdepth 1 -mindepth 1 2>/dev/null | sort
echo "--- 占用 ---"
du -sh "$HOME/apps/GPT-SoVITS" "$MC" 2>/dev/null

LOG_STEP "全部完成"
