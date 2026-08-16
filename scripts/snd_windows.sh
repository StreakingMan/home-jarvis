#!/usr/bin/env bash
# satellite 的播放命令：从 stdin 读裸 PCM，**交给 Windows 原生播放**，绕开 WSLg。
#
# **为什么不用 WSLg 的 RDPSink**（实测，8 秒音频，5 次取中位）：
#
#   | 播放工具 | 语音栈停止 | 语音栈运行 |
#   |---|---|---|
#   | ffmpeg   | 4 秒（没播完就退出，砍掉后半段） | 34 秒 |
#   | paplay   | 8 秒（正确） | 20 秒 |
#
# 只要麦克风在采集，WSLg 的播放就慢 2.5~4 倍，**跟用什么工具无关** —— 那座桥
# 没法同时好好做采集和播放。表现是提示音延迟十几秒、TTS 回复断成两段。
# 采集方向走 WSLg 是好的（STT 识别正常），所以只把**输出**挪出去。
#
# ⚠️ 代价：不是流式。要等音频收完才开始播，回复长则起播晚。换来的是能听清。
set -euo pipefail

RATE="${SND_RATE:-22050}"
# 必须落在 Windows 看得见的路径上 —— PowerShell 读不了 WSL 的文件系统
WIN_DIR="${SND_WIN_DIR:-/mnt/c/Users/40344/AppData/Local/Temp}"
WSL_WAV="$WIN_DIR/jarvis_tts.wav"
WIN_WAV='C:\Users\40344\AppData\Local\Temp\jarvis_tts.wav'

FFMPEG="${FFMPEG:-$HOME/miniconda3/envs/wakeword/bin/ffmpeg}"

# stdin 的裸 PCM 转成 wav。satellite 会在音频结束时关闭管道，ffmpeg 随之退出
"$FFMPEG" -hide_banner -loglevel error -f s16le -ar "$RATE" -ac 1 -i - -y "$WSL_WAV"

# PlaySync 会等播放完成再返回，这样 satellite 知道什么时候放完了
powershell.exe -NoProfile -Command \
  "(New-Object Media.SoundPlayer '$WIN_WAV').PlaySync()" > /dev/null 2>&1
