#!/usr/bin/env bash
# 免 root 安装 Ollama 到 ~/apps/ollama
#
# 官方 install.sh 需要 sudo（装到 /usr/local/bin + 建系统级 systemd 服务），
# 这里改用 release tarball 解压到用户目录，配用户级 systemd 服务。
#
# 解压需要 zstd。系统未装 zstd 二进制，但 Python 3.14 自带 compression.zstd（PEP 784），
# 直接拿它解压，省一次 apt。
set -euo pipefail

VER="${OLLAMA_VERSION:-v0.32.13}"
DEST="${OLLAMA_DEST:-$HOME/apps/ollama}"
TMP="${TMPDIR:-/tmp}/ollama-$VER.tar.zst"
URL="https://github.com/ollama/ollama/releases/download/$VER/ollama-linux-amd64.tar.zst"

echo "=== 下载 $VER ==="
[ -s "$TMP" ] || wget -c --tries=10 --read-timeout=60 -O "$TMP" "$URL"
ls -la "$TMP"

echo "=== 解压到 $DEST ==="
mkdir -p "$DEST"
python3 - "$TMP" "$DEST" <<'PY'
import sys, tarfile
from compression import zstd
src, dest = sys.argv[1], sys.argv[2]
with zstd.ZstdFile(src, "rb") as f, tarfile.open(fileobj=f, mode="r|") as t:
    t.extractall(dest, filter="data")
print("  解压完成")
PY

echo "=== 校验 ==="
"$DEST/bin/ollama" --version
du -sh "$DEST"

echo
echo "接下来："
echo "  cp deploy/systemd/ollama.service ~/.config/systemd/user/"
echo "  systemctl --user daemon-reload && systemctl --user enable --now ollama"
echo "  ~/apps/ollama/bin/ollama pull qwen3:8b"
