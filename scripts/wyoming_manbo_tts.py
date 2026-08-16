#!/usr/bin/env python3
"""把 GPT-SoVITS「曼波」音色包成 Wyoming TTS 服务（默认 :10200）。

**为什么需要**：GPT-SoVITS 只是「一个能返回 wav 的 URL」，靠 `media_player.play_media`
播。那条路能做主动播报，但**接不进 Assist 管线** —— 管线要的是一个真正的
`tts.*` 实体。包成 Wyoming TTS 之后，HA 会把它注册成 `tts.manbo`。

**两种模式**：

1. `Synthesize`（整段）—— HA 等对话代理完全生成完，再把全文交过来。
   我们内部按句切分并发合成，但**省不掉前面等 LLM 的时间**，
   回复越长等得越久。

2. `SynthesizeStart` / `SynthesizeChunk` / `SynthesizeStop`（**流式**）——
   HA 边收 LLM 的 token 边把文本片段推过来。我们攒够一句就立刻合成、
   立刻吐 AudioChunk，**LLM 还在生成后半句时，前半句已经在播了**。
   要走这条路必须在 Info 里声明 `supports_synthesize_streaming=True`，
   否则 HA 一律用模式 1。

上游是 tts_proxy(:9881) 而非 api.py(:9880) —— 代理补了 Content-Length 并带磁盘缓存。
"""
import argparse
import asyncio
import io
import logging
import re
import time
import wave

import requests
from wyoming.audio import AudioChunk, AudioStart, AudioStop
from wyoming.event import Event
from wyoming.info import Attribution, Describe, Info, TtsProgram, TtsVoice
from wyoming.server import AsyncEventHandler, AsyncServer
from wyoming.tts import (Synthesize, SynthesizeChunk, SynthesizeStart,
                         SynthesizeStop, SynthesizeStopped)

_LOG = logging.getLogger("wyoming_manbo")

UPSTREAM = "http://127.0.0.1:9881/"
SESSION = requests.Session()
SESSION.trust_env = False  # 绕开 Clash，同 tts_proxy.py

# 句末标点：见到就立刻送合成
SENT_END = "。！？；：.!?;:\n"
# 次级停顿：没有句末标点时，在这些位置断句 —— 否则第一句太长会拖慢开口
PAUSE = "，,、"
# 第一段的目标长度：越短越早开口。实测「从 1 数到 100」这类没有句号的长回复，
# 第一段被撑到 15.6 秒音频时首块延迟 5.5 秒，切短后能压到 1 秒级
FIRST_SEG = 6
# 后续段落可以长一些，太碎会让语气断断续续，也增加每次合成的固定开销
MIN_SEG = 14
# 连一个停顿都没有的超长串（数字、英文、代码）兜底硬切，否则会一直等下去
HARD_MAX = 60
SENT_SPLIT = re.compile(r"(?<=[。！？；：.!?;:])")

# Markdown 念出来全是噪音。模型偶尔还是会输出，这里兜底清掉
MD_STRIP = [
    (re.compile(r"\*\*(.+?)\*\*"), r"\1"),      # 粗体
    (re.compile(r"(?m)^\s*[-*+]\s+"), ""),      # 列表符号
    (re.compile(r"(?m)^\s*#+\s*"), ""),         # 标题
    (re.compile(r"`([^`]*)`"), r"\1"),           # 行内代码
]


def clean(text: str) -> str:
    for pat, rep in MD_STRIP:
        text = pat.sub(rep, text)
    return text.strip()

INFO = Info(
    tts=[
        TtsProgram(
            name="manbo",
            description="GPT-SoVITS v2Pro「曼波」音色",
            attribution=Attribution(name="GPT-SoVITS", url="https://github.com/RVC-Boss/GPT-SoVITS"),
            installed=True,
            version="1.1",
            supports_synthesize_streaming=True,
            voices=[
                TtsVoice(
                    name="manbo",
                    description="曼波（赛马娘·待兼唐怀瑟）",
                    attribution=Attribution(name="MamboTTS", url="https://github.com/Tsukimisaka/MamboTTS"),
                    installed=True,
                    version=None,
                    languages=["zh-cn", "zh"],
                )
            ],
        )
    ]
)


def synth(text: str):
    """调上游合成一句，返回 (pcm, rate, width, channels)"""
    r = SESSION.get(UPSTREAM, params={"text": text, "text_language": "zh"}, timeout=300)
    r.raise_for_status()
    with wave.open(io.BytesIO(r.content), "rb") as w:
        return (w.readframes(w.getnframes()), w.getframerate(),
                w.getsampwidth(), w.getnchannels())


class ManboHandler(AsyncEventHandler):
    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self._buf = ""         # 流式模式下未成句的残余文本
        self._started = False  # 是否已发过 AudioStart
        self._streaming = False  # 是否处在流式会话中
        # 计时：流式的全部价值在于「第一块音频多久吐出来」，不测就不知道有没有生效
        self._t0 = 0.0         # 本轮合成开始的时刻
        self._sentences = 0    # 本轮合成了几句
        self._audio_sec = 0.0  # 本轮产出的音频总时长

    # ---------- 公共 ----------

    def _split_point(self) -> int:
        """在缓冲区里找断句位置，返回下标；-1 表示还不该切。

        ⚠️ 关键是取**最早**可用的断点，不是最晚的。早先这里用 `max()` 取缓冲区里
        最后一个停顿，本意「缓冲太长就断开」被写成了「尽可能多攒」——LLM 吐字快时
        第一段被撑到 15 秒音频，首块延迟 5.5 秒。「从 1 数到 100」这种没有句号的
        回复受害最重。
        """
        buf = self._buf
        # 句末标点：第一个出现的位置就断
        for i, c in enumerate(buf):
            if c in SENT_END:
                return i
        # 没有句末标点：够长之后的第一个次级停顿。首段阈值更低，为的是尽快开口
        limit = FIRST_SEG if not self._started else MIN_SEG
        if len(buf) >= limit:
            for i, c in enumerate(buf):
                if i + 1 >= limit and c in PAUSE:
                    return i
        # 一个停顿都没有的超长串，硬切兜底
        if len(buf) >= HARD_MAX:
            return HARD_MAX - 1
        return -1

    async def _speak(self, text: str):
        """合成一句并吐出去；首次调用时补发 AudioStart"""
        text = clean(text)
        if not text:
            return
        t_start = time.monotonic()
        try:
            pcm, rate, width, ch = await asyncio.get_running_loop().run_in_executor(
                None, synth, text)
        except Exception:
            _LOG.exception("合成失败，跳过：%s", text[:20])
            return
        synth_sec = time.monotonic() - t_start
        audio_sec = len(pcm) / (rate * width * ch)
        self._sentences += 1
        self._audio_sec += audio_sec

        if not self._started:
            await self.write_event(AudioStart(rate=rate, width=width, channels=ch).event())
            self._started = True
            # 首块延迟：从收到第一个文本片段到第一块音频出门。这个数字才决定「听起来快不快」
            _LOG.info(
                "首块音频 %.2fs（合成 %.2fs / 音频 %.2fs）｜%s",
                time.monotonic() - self._t0, synth_sec, audio_sec, text[:20],
            )
        else:
            _LOG.debug(
                "第 %d 句 合成 %.2fs / 音频 %.2fs（实时率 %.1fx）｜%s",
                self._sentences, synth_sec, audio_sec,
                audio_sec / synth_sec if synth_sec else 0, text[:20],
            )

        step = rate * width * ch  # 约 1 秒一块
        for off in range(0, len(pcm), step):
            await self.write_event(
                AudioChunk(rate=rate, width=width, channels=ch,
                           audio=pcm[off:off + step]).event())

    async def _finish(self):
        if not self._started:
            # 一句都没成也要收尾，否则 HA 会一直等
            await self.write_event(AudioStart(rate=32000, width=2, channels=1).event())
        await self.write_event(AudioStop().event())
        self._started = False
        self._buf = ""

    # ---------- 事件分发 ----------

    async def handle_event(self, event: Event) -> bool:
        if Describe.is_type(event.type):
            await self.write_event(INFO.event())
            return True

        # ---- 流式：LLM 边生成边推文本 ----
        if SynthesizeStart.is_type(event.type):
            _LOG.info("流式合成开始")
            self._buf = ""
            self._started = False
            self._streaming = True
            self._t0 = time.monotonic()
            self._sentences = 0
            self._audio_sec = 0.0
            return True

        if SynthesizeChunk.is_type(event.type):
            self._buf += SynthesizeChunk.from_event(event).text
            # 攒够一句就立刻送合成，不等全文
            while True:
                idx = self._split_point()
                if idx < 0:
                    break
                sent, self._buf = self._buf[:idx + 1], self._buf[idx + 1:]
                await self._speak(sent)
            return True

        if SynthesizeStop.is_type(event.type):
            if self._buf.strip():
                await self._speak(self._buf)   # 最后不成句的残余
            await self._finish()
            await self.write_event(SynthesizeStopped().event())
            self._streaming = False
            wall = time.monotonic() - self._t0
            _LOG.info(
                "流式合成结束：%d 句 / 音频 %.1fs / 耗时 %.1fs（%s）",
                self._sentences, self._audio_sec, wall,
                "赶得上播放" if wall < self._audio_sec else "⚠️ 比播放还慢，会卡顿",
            )
            return True

        # ---- 整段：HA 不支持流式或未启用时走这里 ----
        if Synthesize.is_type(event.type):
            # ⚠️ 流式会话里 HA **也会**发一份完整的 Synthesize（为兼容不支持流式的
            # 服务端）。不忽略它就会合成两遍、播两遍 —— 实测就是这个 bug。
            if self._streaming:
                _LOG.debug("流式会话中，忽略整段 Synthesize")
                return True
            text = " ".join(Synthesize.from_event(event).text.strip().splitlines())
            _LOG.info("整段合成：%s", text[:40])
            self._started = False
            for sent in [p.strip() for p in SENT_SPLIT.split(text) if p.strip()] or [text]:
                await self._speak(sent)
            await self._finish()
            return True

        return True


async def main():
    global UPSTREAM

    ap = argparse.ArgumentParser()
    ap.add_argument("--uri", default="tcp://0.0.0.0:10200")
    ap.add_argument("--upstream", default=UPSTREAM)
    ap.add_argument("--debug", action="store_true")
    args = ap.parse_args()

    UPSTREAM = args.upstream
    logging.basicConfig(
        level=logging.DEBUG if args.debug else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        datefmt="%H:%M:%S",
    )
    _LOG.info("Wyoming 曼波 TTS 启动于 %s，上游 %s（流式已启用）", args.uri, UPSTREAM)

    server = AsyncServer.from_uri(args.uri)
    await server.run(lambda r, w: ManboHandler(r, w))


if __name__ == "__main__":
    asyncio.run(main())
