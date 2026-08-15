#!/usr/bin/env python3
"""流式 TTS 可行性基准。

模拟 LLM 逐 token 吐字、按句边界切分并立即送合成，测量两个关键指标：
  1. 首句可播时刻 —— 用户等多久才听到第一个字
  2. 是否断流 —— 第 N 句就绪时刻 是否早于 第 N 句应播时刻

对照组是「整段一次性合成」。

2026-08-16 在 5060 Ti + Qwen3 假定速率下的实测（曼波音色 GPT-SoVITS v2Pro）：
    整段一次合成  首字延迟 3.98s
    句级流式      首字延迟 1.15s，四句全部提前就绪，零断流
    合成实时率    6.4x —— 远快于播放速度，所以只要第一句能开口，后面永远追得上
结论：首句越短开口越快，配合人设提示词要求「先应一声」可压到 1s 内。

用法: python tts_stream_bench.py ["自定义文本"]
"""
import io
import queue
import re
import sys
import threading
import time
import wave

import requests

PROXY = "http://127.0.0.1:9881/"
SAMPLE_RATE = 32000
TOK_PER_SEC = 45.0        # Qwen3-8B Q4 在 5060 Ti 上的典型出字速度
FIRST_TOK_LATENCY = 0.30  # 首 token 延迟

DEFAULT_TEXT = (
    "好的，我看了一下。书房的灯已经关好了。"
    "另外提醒你一句，猫砂盆那边余量只剩百分之十五了，"
    "按这两天的使用速度，大概还能撑三天。"
    "要不要我下单一袋？"
)

S = requests.Session()
S.trust_env = False  # 绕开 Clash，同 tts_proxy.py


def split_sentences(t):
    return [p for p in (x.strip() for x in re.split(r"(?<=[。！？；])", t)) if p]


def synth(text):
    t0 = time.time()
    r = S.get(PROXY, params={"text": text, "text_language": "zh"}, timeout=300)
    r.raise_for_status()
    dur = wave.open(io.BytesIO(r.content)).getnframes() / SAMPLE_RATE
    return time.time() - t0, dur, len(r.content)


def main():
    text = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_TEXT
    sents = split_sentences(text)
    print(f"文本共 {len(text)} 字，切成 {len(sents)} 句\n")

    print("=== 对照组：整段一次性合成 ===")
    cost, dur, _ = synth(text)
    print(f"  合成耗时 {cost:.2f}s   音频 {dur:.2f}s   实时率 {dur / cost:.1f}x")
    total = FIRST_TOK_LATENCY + len(text) / TOK_PER_SEC + cost
    print(f"  → 从 LLM 开始生成到听见第一个字: {total:.2f}s\n")

    print("=== 实验组：句级流式（边生成边合成）===")
    results = queue.Queue()
    t_start = time.time()

    def worker(idx, sent, emit_at):
        wait = emit_at - (time.time() - t_start)
        if wait > 0:
            time.sleep(wait)
        c, d, _ = synth(sent)
        results.put((idx, sent, emit_at, time.time() - t_start, c, d))

    threads, cum = [], 0
    for i, s in enumerate(sents):
        cum += len(s)
        t = threading.Thread(
            target=worker, args=(i, s, FIRST_TOK_LATENCY + cum / TOK_PER_SEC)
        )
        t.start()
        threads.append(t)
    for t in threads:
        t.join()

    rows = sorted(results.get() for _ in sents)
    cursor = None
    header = f"  {'#':<3}{'句子':<30}{'LLM吐完':>9}{'合成好':>9}{'耗时':>7}{'音频':>7}{'应播':>9}  状态"
    print(header)
    for i, s, emit, ready, cost, dur in rows:
        should = ready if cursor is None else cursor
        gap = should - ready
        status = "✅ 已就绪" if gap >= 0 else f"⚠️ 迟 {-gap:.2f}s（断流）"
        disp = (s[:13] + "…") if len(s) > 14 else s
        print(
            f"  {i:<3}{disp:<30}{emit:>8.2f}s{ready:>8.2f}s"
            f"{cost:>6.2f}s{dur:>6.2f}s{should:>8.2f}s  {status}"
        )
        cursor = max(should, ready) + dur

    print(f"\n  → 从 LLM 开始生成到听见第一个字: {rows[0][3]:.2f}s")
    print(f"  → 全部播完: {cursor:.2f}s")


if __name__ == "__main__":
    main()
