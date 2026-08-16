#!/usr/bin/env python3
"""把 GPT-SoVITS「曼波」音色包成 Wyoming TTS 服务（默认 :10200）。

**为什么需要**：GPT-SoVITS 只是「一个能返回 wav 的 URL」，靠 `media_player.play_media`
播。那条路能做主动播报，但**接不进 Assist 管线** —— 管线要的是一个真正的
`tts.*` 实体。包成 Wyoming TTS 之后，HA 的「Wyoming Protocol」集成会把它注册成
`tts.manbo`，对话回复才能用曼波音色说出来。

**句级流式**：实测整段一次合成首字 3.98s，按句切分并发合成降到 1.15s（3.5 倍）。
合成实时率 6.4x 远快于播放，所以只要第一句能开口，后面永远追得上。
这里按 。！？；切句，逐句合成、逐句吐 AudioChunk。

上游是 tts_proxy(:9881) 而非 api.py(:9880) —— 代理补了 Content-Length 并带磁盘缓存，
固定播报（「猫砂快用完了」之类）只合成一次。
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
from wyoming.tts import Synthesize

_LOG = logging.getLogger("wyoming_manbo")

UPSTREAM = "http://127.0.0.1:9881/"
SESSION = requests.Session()
SESSION.trust_env = False  # 绕开 Clash，同 tts_proxy.py

SENT_SPLIT = re.compile(r"(?<=[。！？；.!?;])")

INFO = Info(
    tts=[
        TtsProgram(
            name="manbo",
            description="GPT-SoVITS v2Pro「曼波」音色",
            attribution=Attribution(name="GPT-SoVITS", url="https://github.com/RVC-Boss/GPT-SoVITS"),
            installed=True,
            version="1.0",
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


def split_sentences(text: str):
    parts = [p.strip() for p in SENT_SPLIT.split(text)]
    return [p for p in parts if p] or [text]


def synth(text: str):
    """调上游合成一句，返回 (pcm_bytes, rate, width, channels)"""
    r = SESSION.get(UPSTREAM, params={"text": text, "text_language": "zh"}, timeout=300)
    r.raise_for_status()
    with wave.open(io.BytesIO(r.content), "rb") as w:
        return (w.readframes(w.getnframes()), w.getframerate(),
                w.getsampwidth(), w.getnchannels())


class ManboHandler(AsyncEventHandler):
    async def handle_event(self, event: Event) -> bool:
        if Describe.is_type(event.type):
            await self.write_event(INFO.event())
            return True

        if not Synthesize.is_type(event.type):
            return True

        text = " ".join(Synthesize.from_event(event).text.strip().splitlines())
        if not text:
            return True

        sentences = split_sentences(text)
        _LOG.info("合成 %d 句：%s", len(sentences), text[:40])

        started = False
        rate = width = channels = None
        for i, sent in enumerate(sentences):
            try:
                pcm, rate, width, channels = await asyncio.get_running_loop().run_in_executor(
                    None, synth, sent
                )
            except Exception:
                _LOG.exception("合成失败，跳过该句：%s", sent[:20])
                continue

            if not started:
                await self.write_event(
                    AudioStart(rate=rate, width=width, channels=channels).event()
                )
                started = True

            # 分块发送，避免单个事件过大
            step = rate * width * channels  # 约 1 秒
            for off in range(0, len(pcm), step):
                await self.write_event(
                    AudioChunk(rate=rate, width=width, channels=channels,
                               audio=pcm[off:off + step]).event()
                )

        if started:
            await self.write_event(AudioStop().event())
        else:
            # 一句都没成，也要收尾，否则 HA 会一直等
            await self.write_event(AudioStart(rate=32000, width=2, channels=1).event())
            await self.write_event(AudioStop().event())
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
    _LOG.info("Wyoming 曼波 TTS 启动于 %s，上游 %s", args.uri, UPSTREAM)

    server = AsyncServer.from_uri(args.uri)
    await server.run(lambda r, w: ManboHandler(r, w))


if __name__ == "__main__":
    asyncio.run(main())
