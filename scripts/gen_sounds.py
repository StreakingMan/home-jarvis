#!/usr/bin/env python3
"""生成 satellite 的唤醒/结束提示音，落到 ~/apps/jarvis/sounds/。

**为什么需要**：wyoming-satellite 默认**没有任何提示音**。唤醒词命中后它直接开始
录指令，用户完全感知不到 —— 表现是「喊了没反应」，但日志里唤醒是成功的。
排查这个问题浪费了不少时间，所以提示音不是可选项，是必需品。

wyoming-satellite 自己不带音效文件（wheel 里没有 wav），所以这里现生成。

⚠️ 采样率要跟 `run_satellite.sh` 里的 `--snd-command-rate` 一致（22050），
   否则播出来是变调的。

⚠️ 必须带淡入淡出：方波式的突起会在扬声器上爆出「咔」的一声。
"""

import argparse
import wave
from pathlib import Path

import numpy as np

RATE = 22050


def tone(freq: float, ms: int, volume: float = 0.35) -> np.ndarray:
    n = int(RATE * ms / 1000)
    t = np.arange(n) / RATE
    wave_ = np.sin(2 * np.pi * freq * t)
    # 两端各 5 ms 淡入淡出，消掉爆音
    fade = int(RATE * 0.005)
    env = np.ones(n)
    env[:fade] = np.linspace(0, 1, fade)
    env[-fade:] = np.linspace(1, 0, fade)
    return wave_ * env * volume


def write(path: Path, samples: np.ndarray) -> None:
    pcm = (np.clip(samples, -1, 1) * 32767).astype(np.int16)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(pcm.tobytes())
    print(f"{path}  {len(samples) / RATE * 1000:.0f} ms")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, default=Path.home() / "apps/jarvis/sounds")
    args = ap.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)

    # 唤醒：上行两音，「我在听」
    write(args.out / "awake.wav", np.concatenate([tone(880, 70), tone(1318, 100)]))
    # 结束：下行两音，「收到，去处理了」
    write(args.out / "done.wav", np.concatenate([tone(1318, 70), tone(880, 100)]))


if __name__ == "__main__":
    main()
