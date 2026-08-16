#!/usr/bin/env python3
"""为中文自定义唤醒词生成训练用的正/负样本音频。

背景：openWakeWord 自带的 5 个唤醒词（hey_jarvis / alexa / ok_nabu / hey_mycroft /
hey_rhasspy）全是英文，要「你好曼波」必须自己训。官方训练流程用
piper-sample-generator 合成正样本，那是英文 LibriTTS 模型，中文用不了。

⚠️ **说话人多样性是这件事的成败关键**。只用一种音色合成，训出来的模型
只认那一种声音 —— 家里人换个人喊就不响了。所以这里用 edge-tts 的 14 个中文音色
（zh-CN ×6、zh-HK ×3、zh-TW ×3、辽宁 ×1、陕西 ×1），再叠语速/音高/音量变化。

⚠️ **必须生成「对抗负样本」**：只喂正样本 + 随机噪声，模型会学成「听到人说话就响」。
真正压误唤醒的是那些**听起来很像但不是**的短语 —— 「你好」「曼波」「慢波」
「你好小爱」…… 这些要显式合成进负样本。

用法：
    python scripts/gen_wakeword_clips.py --out ~/apps/wakeword/data
    python scripts/gen_wakeword_clips.py --out ... --phrase 你好曼波 --dry-run

⚠️ edge-tts 走微软的在线合成服务。合成内容只有唤醒词这几个字，不涉及隐私；
   但如果要求全链路离线，可以改用本机的 GPT-SoVITS 零样本克隆多个参考音色
   （见文件末尾的说明）。
"""
import argparse
import asyncio
import itertools
import os
import random
import sys

# 唤醒词。改这个就能训别的词
PHRASE = "你好曼波"

# 对抗负样本：跟唤醒词部分重叠、或整体音近的短语。
# 这些是误唤醒的主要来源，必须显式喂给模型当反例。
ADVERSARIAL = [
    "你好", "曼波", "慢波", "馒波", "曼博", "漫步",
    "你好小爱", "你好小度", "你好天猫", "小爱同学",
    "你好呀", "你在吗", "你好吗", "曼波你好",
    "你好曼谷", "你好慢慢", "好曼波", "尼好曼波",
    "帮我开灯", "把灯关了", "现在几点", "今天天气怎么样",
]

RATES = ["-20%", "-10%", "+0%", "+10%", "+25%"]
PITCHES = ["-15Hz", "-5Hz", "+0Hz", "+10Hz", "+20Hz"]
VOLUMES = ["-20%", "+0%", "+15%"]


async def voices():
    import edge_tts
    vs = await edge_tts.list_voices(proxy=PROXY) if PROXY else await edge_tts.list_voices()
    return sorted(v["ShortName"] for v in vs if v["Locale"].startswith("zh-"))


# ⚠️ edge-tts 的合成走 **WebSocket**（wss://speech.platform.bing.com），
#    而它的 aiohttp **不读环境变量里的代理** —— 表现是 list_voices() 正常
#    （那是普通 HTTPS）但 save() 一律 ConnectionTimeoutError。必须显式传 proxy=。
PROXY = (os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy")
         or os.environ.get("HTTP_PROXY") or os.environ.get("http_proxy"))


async def synth(text, voice, rate, pitch, volume, path):
    import edge_tts
    kw = {"proxy": PROXY} if PROXY else {}
    c = edge_tts.Communicate(text, voice, rate=rate, pitch=pitch, volume=volume, **kw)
    await c.save(path)


async def gen(kind, texts, vlist, out_dir, per_text, sem):
    """为每条文本 × 每个音色，随机抽若干组语速/音高/音量组合"""
    os.makedirs(out_dir, exist_ok=True)
    combos = list(itertools.product(RATES, PITCHES, VOLUMES))
    tasks, n = [], 0

    async def one(text, v, rate, pitch, vol, idx):
        nonlocal n
        p = os.path.join(out_dir, f"{kind}_{idx:05d}.mp3")
        async with sem:
            for attempt in range(3):
                try:
                    await synth(text, v, rate, pitch, vol, p)
                    n += 1
                    return
                except Exception as e:
                    if attempt == 2:
                        print(f"  ✗ {text}/{v}: {type(e).__name__}", file=sys.stderr)
                    await asyncio.sleep(1.5 * (attempt + 1))

    idx = 0
    for text in texts:
        for v in vlist:
            for rate, pitch, vol in random.sample(combos, min(per_text, len(combos))):
                tasks.append(one(text, v, rate, pitch, vol, idx))
                idx += 1
    await asyncio.gather(*tasks)
    return n


async def main(a):
    vlist = await voices()
    print(f"中文音色 {len(vlist)} 个: {', '.join(v.split('-',2)[-1] for v in vlist)}")
    pos_n = len(vlist) * a.per_voice
    neg_n = len(ADVERSARIAL) * len(vlist) * a.per_voice_neg
    print(f"计划生成：正样本 {pos_n} 条（「{a.phrase}」）／对抗负样本 {neg_n} 条"
          f"（{len(ADVERSARIAL)} 种说法）")
    if a.dry_run:
        print("--dry-run，不实际合成")
        return
    sem = asyncio.Semaphore(a.concurrency)
    p = await gen("positive", [a.phrase], vlist,
                  os.path.join(a.out, "positive"), a.per_voice, sem)
    print(f"  正样本 {p}/{pos_n}")
    q = await gen("adversarial", ADVERSARIAL, vlist,
                  os.path.join(a.out, "adversarial"), a.per_voice_neg, sem)
    print(f"  对抗负样本 {q}/{neg_n}")
    print(f"\n落盘于 {a.out}。下一步：加混响/背景噪声做增强，再训分类头。")


ap = argparse.ArgumentParser()
ap.add_argument("--out", default=os.path.expanduser("~/apps/wakeword/data"))
ap.add_argument("--phrase", default=PHRASE)
ap.add_argument("--per-voice", type=int, default=12, help="每个音色为唤醒词生成几条")
ap.add_argument("--per-voice-neg", type=int, default=2, help="每个音色为每条负样本文本生成几条")
ap.add_argument("--concurrency", type=int, default=6)
ap.add_argument("--dry-run", action="store_true")
ap.add_argument("--proxy", default=None, help="覆盖自动探测的代理")
_a = ap.parse_args()
if _a.proxy:
    PROXY = _a.proxy
asyncio.run(main(_a))

# 全离线的替代方案（不依赖 edge-tts）：
#   本机已经跑着 GPT-SoVITS（:9880），它是零样本克隆 —— 给一段几秒的参考音频
#   就能用那个音色说任意文本。所以只要凑到 N 段不同人的参考音频，
#   就能得到 N 个说话人的「你好曼波」。参考音频可以来自公开中文语音数据集
#   （AISHELL-3 有 218 个说话人），代价是要下十几 GB。
#   edge-tts 的 14 个音色 + 增强，对家用场景通常已经够。
