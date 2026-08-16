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

# 句末标点。流式模式下攒到这些字符就立刻送合成
SENT_END = "。！？；.!?;\n"
SENT_SPLIT = re.compile(r"(?<=[。！？；.!?;])")

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
        self._buf = ""        # 流式模式下未成句的残余文本
        self._started = False  # 是否已发过 AudioStart

    # ---------- 公共 ----------

    async def _speak(self, text: str):
        """合成一句并吐出去；首次调用时补发 AudioStart"""
        text = text.strip()
        if not text:
            return
        try:
            pcm, rate, width, ch = await asyncio.get_running_loop().run_in_executor(
                None, synth, text)
        except Exception:
            _LOG.exception("合成失败，跳过：%s", text[:20])
            return

        if not self._started:
            await self.write_event(AudioStart(rate=rate, width=width, channels=ch).event())
            self._started = True

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
            return True

        if SynthesizeChunk.is_type(event.type):
            self._buf += SynthesizeChunk.from_event(event).text
            # 攒够一句就立刻送合成，不等全文
            while True:
                idx = next((i for i, c in enumerate(self._buf) if c in SENT_END), -1)
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
            _LOG.info("流式合成结束")
            return True

        # ---- 整段：HA 不支持流式或未启用时走这里 ----
        if Synthesize.is_type(event.type):
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
    logging.basicConfig(level=logging.DEBUG if args.debug else logging.INFO)
    _LOG.info("Wyoming 曼波 TTS 启动于 %s，上游 %s（流式已启用）", args.uri, UPSTREAM)

    server = AsyncServer.from_uri(args.uri)
    await server.run(lambda r, w: ManboHandler(r, w))


if __name__ == "__main__":
    asyncio.run(main())
