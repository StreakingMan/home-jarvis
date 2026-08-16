#!/usr/bin/env python3
"""端到端语音管线基准：喂音频进 HA 的 Assist 管线，量每一段耗时。

走 HA 的 WebSocket `assist_pipeline/run`，完整链路：
    音频 → STT(faster-whisper) → 对话代理(Qwen3) → 执行 → TTS(曼波)

比逐个服务单测更有价值 —— 单测各段都快，但管线里还有 HA 自身的调度开销，
以及 VAD 判定说话结束的时间。这个脚本量的是用户真实感受到的延迟。

用法：
    python pipeline_bench.py                      # 用默认指令
    python pipeline_bench.py "把客厅的灯打开"      # 自定义

输入音频由曼波 TTS 现场合成（没有真人录音时的替代方案）。
⚠️ 因此 STT 段的准确率会受 TTS 发音影响，不代表真人说话的准确率。
"""
import asyncio
import audioop
import io
import json
import os
import sys
import time
import wave

import websockets
from wyoming.audio import AudioChunk, AudioStart, AudioStop
from wyoming.client import AsyncTcpClient
from wyoming.tts import Synthesize

HA = os.environ["HA_URL"].rstrip("/")
T = os.environ["HA_TOKEN"]
TTS_URI = ("127.0.0.1", 10200)
TEXT = sys.argv[1] if len(sys.argv) > 1 else "把书房的灯关掉"


async def make_audio(text):
    """用曼波 TTS 合成一段指令音频，重采样到 16k 单声道供 STT 用"""
    async with AsyncTcpClient(*TTS_URI) as c:
        await c.write_event(Synthesize(text=text).event())
        pcm, rate, width, ch = bytearray(), None, None, None
        while True:
            ev = await c.read_event()
            if ev is None or AudioStop.is_type(ev.type):
                break
            if AudioStart.is_type(ev.type):
                a = AudioStart.from_event(ev)
                rate, width, ch = a.rate, a.width, a.channels
            elif AudioChunk.is_type(ev.type):
                pcm += AudioChunk.from_event(ev).audio
    dur = len(pcm) / (rate * width * ch)
    pcm16, _ = audioop.ratecv(bytes(pcm), width, ch, rate, 16000, None)
    if ch == 2:
        pcm16 = audioop.tomono(pcm16, width, 1, 1)
    return pcm16, dur


async def main():
    pcm, in_dur = await make_audio(TEXT)
    print(f"输入：「{TEXT}」  音频 {in_dur:.2f}s（曼波合成，16kHz 单声道）\n")

    url = HA.replace("http", "ws", 1) + "/api/websocket"
    async with websockets.connect(url, max_size=64 * 1024 * 1024) as ws:
        await ws.recv()
        await ws.send(json.dumps({"type": "auth", "access_token": T}))
        await ws.recv()

        await ws.send(json.dumps({
            "id": 1, "type": "assist_pipeline/run",
            "start_stage": "stt", "end_stage": "tts",
            "input": {"sample_rate": 16000},
        }))
        # 先等 run-start 拿到二进制通道号
        handler = None
        t0 = time.time()
        marks = {}
        while True:
            msg = json.loads(await ws.recv())
            if msg.get("type") == "result" and not msg.get("success", True):
                print("  启动失败:", msg.get("error"))
                return
            ev = msg.get("event") or {}
            et, data = ev.get("type"), ev.get("data") or {}
            if et == "run-start":
                handler = data["runner_data"]["stt_binary_handler_id"]
                marks[et] = time.time() - t0
                # 推音频：每 100ms 一帧，模拟真实说话速率
                step = int(16000 * 2 * 0.1)
                for off in range(0, len(pcm), step):
                    await ws.send(bytes([handler]) + pcm[off:off + step])
                    await asyncio.sleep(0.01)   # 加速推送，不必真等 100ms
                await ws.send(bytes([handler]))  # 空帧 = 说完了
                continue
            if et:
                marks[et] = time.time() - t0
            if et == "stt-end":
                print(f"  STT   {marks[et]:.2f}s  → 「{data['stt_output']['text']}」")
            elif et == "intent-end":
                sp = (data["intent_output"].get("response", {})
                      .get("speech", {}).get("plain", {}).get("speech", ""))
                print(f"  LLM   {marks[et]:.2f}s  → 「{sp}」")
            elif et == "tts-end":
                print(f"  TTS   {marks[et]:.2f}s  → {data['tts_output'].get('url','')[:60]}")
            elif et == "error":
                print(f"  ❌ {data.get('code')}: {data.get('message','')[:80]}")
            elif et == "run-end":
                break

        print(f"\n  总计 {marks.get('run-end', 0):.2f}s")
        seg = [("STT", "stt-start", "stt-end"),
               ("LLM", "intent-start", "intent-end"),
               ("TTS", "tts-start", "tts-end")]
        for name, a, b in seg:
            if a in marks and b in marks:
                print(f"    {name} 段耗时 {marks[b] - marks[a]:.2f}s")


asyncio.run(main())
