#!/usr/bin/env bash
# 启动 wyoming-satellite（麦克风卫星）。供 systemd 调用，也可手跑。
#
# 它做三件事：持续采集麦克风 → 送给本机的 openwakeword 判唤醒词 →
# 命中后把后续音频流给 HA 的 Assist 管线，并把 TTS 音频播回来。
# HA 不会主动来拉麦克风，必须有这么个进程往里推。
#
# ⚠️ 为什么用 ffmpeg 而不是官方文档里的 arecord/aplay：
#    装 alsa-utils 要 sudo（本机 sudo 需要密码，服务一律用户级）。
#    ffmpeg 从 conda-forge 装进同一个环境，自带 pulse 输入输出，零系统依赖。
#
# ⚠️ 不加 `--vad`：本地唤醒词模式下它是空转，日志会明说
#    「VAD is not used with local wake word detection」。
#    silerovad 那个 extra 还是装着的，将来若改成 HA 侧唤醒词就用得上。
#
# ⚠️ 音频走的是 WSLg 的 PulseAudio 桥（`/mnt/wslg/PulseServer`），
#    源名固定叫 `RDPSource` —— 那是 WSLg 转发 **Windows 当前默认录音设备**的虚拟通道。
#    所以在 Windows 声音设置里换默认麦克风，这边不用改配置；
#    反过来，Windows 那边默认设备变了这边就跟着变，排查时先看 Windows。
set -euo pipefail

CONDA_ROOT="${CONDA_ROOT:-$HOME/miniconda3}"
ENV="${WAKEWORD_ENV:-wakeword}"

NAME="${SATELLITE_NAME:-书房卫星}"
URI="${SATELLITE_URI:-tcp://0.0.0.0:10700}"
WAKE_URI="${SATELLITE_WAKE_URI:-tcp://127.0.0.1:10400}"
# 10400 上现在跑的是 wyoming_kws（sherpa-onnx 中文），词名就是唤醒词本身。
# 换回 openWakeWord 的话这里要改成 hey_jarvis
WAKE_WORD="${SATELLITE_WAKE_WORD:-你好曼波}"

# shellcheck disable=SC1091
source "$CONDA_ROOT/etc/profile.d/conda.sh"
conda activate "$ENV"

export PULSE_SERVER="${PULSE_SERVER:-unix:/mnt/wslg/PulseServer}"

# 采集：16kHz 单声道 16bit 裸 PCM 到 stdout，这是 Wyoming 约定的格式。
# 采集方向走 WSLg 没问题（STT 识别正常），所以保持不动
MIC_CMD="ffmpeg -hide_banner -loglevel error -f pulse -i RDPSource -ac 1 -ar 16000 -f s16le -"

# ⚠️ 播放**不能**走 WSLg 的 RDPSink。实测 8 秒音频、5 次取中位：
#
#   | 播放工具 | 语音栈停止 | 语音栈运行 |
#   |---|---|---|
#   | ffmpeg → RDPSink | 4 秒（没播完就退出，砍掉后半段） | 34 秒 |
#   | paplay → RDPSink | 8 秒（正确） | 20 秒 |
#   | **snd_windows.sh** | — | **8 秒** ✓ |
#
# 只要麦克风在采集，WSLg 的播放就慢 2.5~4 倍，换工具救不了 —— 那座桥没法同时
# 好好做采集和播放。表现是提示音延迟十几秒、TTS 回复断成两段甚至完全听不到。
# snd_windows.sh 把音频交给 Windows 原生播放，绕开这座桥。
SND_CMD="${SATELLITE_SND_CMD:-$(dirname "$0")/snd_windows.sh}"

# ⚠️ 提示音不是锦上添花。satellite 默认**一声不吭**：唤醒词命中后直接开始录指令，
#    人完全感知不到，表现就是「喊了没反应」，而日志里唤醒明明是成功的。
#    没有它排查唤醒问题基本靠猜。用 scripts/gen_sounds.py 生成。
SOUNDS="${SATELLITE_SOUNDS:-$HOME/apps/jarvis/sounds}"

exec python -m wyoming_satellite \
  --name "$NAME" \
  --uri "$URI" \
  --mic-command "$MIC_CMD" \
  --snd-command "$SND_CMD" \
  --snd-command-rate 22050 \
  --wake-uri "$WAKE_URI" \
  --wake-word-name "$WAKE_WORD" \
  --awake-wav "$SOUNDS/awake.wav" \
  --done-wav "$SOUNDS/done.wav"
