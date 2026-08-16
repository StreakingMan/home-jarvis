"""端到端冒烟测试 wyoming_kws：Describe 拿 Info，再按 satellite 的节奏推真实音频进去。

**不依赖麦克风**，所以能在无人值守时跑。用来回答「服务是不是真的在工作」——
`ss` 看到端口在听、Describe 有回应，都**不代表它认得出唤醒词**（第五个静默失败就是
端口正常、Describe 正常、就是永远不响）。

    ~/miniconda3/envs/kws/bin/python scripts/smoke_kws.py [端口]

默认打 10400（线上）。测试实例用 10401 之类的另起端口，免得动到在跑的语音栈。
"""
import asyncio, sys
from pathlib import Path

import numpy as np
import soundfile as sf
import soxr
from wyoming.audio import AudioChunk, AudioStart, AudioStop
from wyoming.client import AsyncTcpClient
from wyoming.info import Describe, Info
from wyoming.wake import Detection, NotDetected

URI = ("127.0.0.1", int(sys.argv[1]) if len(sys.argv) > 1 else 10400)
RATE = 16000
CHUNK_MS = 30  # satellite 大致就是这个粒度
DATA = Path.home() / "apps/wakeword/data"


def load(path):
    data, sr = sf.read(str(path), dtype="float32", always_2d=True)
    s = data.mean(axis=1)
    if sr != RATE:
        s = soxr.resample(s, sr, RATE)
    return (np.clip(s, -1, 1) * 32767).astype(np.int16).tobytes()


async def describe():
    async with AsyncTcpClient(*URI) as c:
        await c.write_event(Describe().event())
        while True:
            e = await c.read_event()
            if e is None:
                return None
            if Info.is_type(e.type):
                return Info.from_event(e)


async def run_clip(path):
    pcm = load(path)
    step = int(RATE * 2 * CHUNK_MS / 1000)
    async with AsyncTcpClient(*URI) as c:
        await c.write_event(AudioStart(rate=RATE, width=2, channels=1).event())
        for off in range(0, len(pcm), step):
            await c.write_event(
                AudioChunk(rate=RATE, width=2, channels=1, audio=pcm[off:off + step]).event()
            )
        # 尾部补静音，流式模型要它才吐最后一个 token
        pad = b"\x00" * int(RATE * 2 * 0.7)
        for off in range(0, len(pad), step):
            await c.write_event(
                AudioChunk(rate=RATE, width=2, channels=1, audio=pad[off:off + step]).event()
            )
        await c.write_event(AudioStop().event())

        hits = []
        while True:
            e = await asyncio.wait_for(c.read_event(), timeout=10)
            if e is None:
                break
            if Detection.is_type(e.type):
                # 真实 satellite 收到 Detection 就结束本轮，不会再等 NotDetected
                hits.append(Detection.from_event(e).name)
                break
            if NotDetected.is_type(e.type):
                break
        return hits


async def main():
    info = await describe()
    print("=== Describe ===")
    if info and info.wake:
        for p in info.wake:
            print(f"  program: {p.name}  models: {[m.name for m in p.models]}  phrase: {[m.phrase for m in p.models]}")
    else:
        print("  没拿到 wake info！")
        sys.exit(1)

    # 只取前 12 条：这是冒烟测试不是评测。全量跑 scripts/eval_kws.py
    pos = sorted((DATA / "positive").glob("*.mp3"))[:12]
    adv = sorted((DATA / "adversarial").glob("*.mp3"))[:12]

    print("\n=== 正样本（应触发）===")
    ok = 0
    for p in pos:
        h = await run_clip(p)
        ok += bool(h)
        print(f"  {p.name:22} -> {h}")
    print(f"  命中 {ok}/{len(pos)}")

    print("\n=== 对抗样本（不应触发）===")
    bad = 0
    for p in adv:
        h = await run_clip(p)
        bad += bool(h)
        print(f"  {p.name:26} -> {h}")
    print(f"  误触发 {bad}/{len(adv)}")


asyncio.run(main())
