#!/usr/bin/env python3
"""把 sherpa-onnx 关键词检测包成 Wyoming 唤醒词服务（默认 :10400，顶掉 openWakeWord）。

**为什么换掉 openWakeWord**：它没有中文唤醒词，自训要 16.5 GB 训练数据 + 几小时训练，
换个词就得从头再来。sherpa-onnx 的 KWS 是**开放词表**的 —— 模型只认音素，唤醒词写进
一行文本就生效，换词改一行、立刻见效。代价是官方没有 Wyoming 封装，就是这个文件。

**唤醒词不是汉字**：模型 tokens.txt 里是 ARPAbet（英文）+ 带调拼音声韵母（中文），
「你好曼波」内部是 `n ǐ h ǎo m àn b ō`。命令行上直接写中文，本脚本自动转。

**两个旋钮的实测结论**（`scripts/eval_kws.py`，168 正样本 / 616 对抗样本）：
  --threshold  0.15~0.40 之间**结果完全一样**，这个旋钮在本模型上基本是死的
  --score      不是越大越好：score 3.0 + threshold 0.45 召回率从 73% 崩到 40%。
               平台区在 score 1~2、threshold 0.25
  chunk-8/16 × fp32/int8 四种组合结果一致，故取延迟最低的 chunk-8 + int8

协议上游是 wyoming-satellite：它连上来后先发 Detect（可选，声明要听哪几个词），
然后持续推 AudioChunk。我们**边收边解码**，一旦命中就立刻回 Detection。
"""

from __future__ import annotations

import argparse
import asyncio
import logging
from pathlib import Path

import numpy as np
import sherpa_onnx
from wyoming.audio import AudioChunk, AudioChunkConverter, AudioStart, AudioStop
from wyoming.event import Event
from wyoming.info import Attribution, Describe, Info, WakeModel, WakeProgram
from wyoming.server import AsyncEventHandler, AsyncServer
from wyoming.wake import Detect, Detection, NotDetected

_LOG = logging.getLogger("wyoming_kws")

SAMPLE_RATE = 16000
ATTRIBUTION = Attribution(name="k2-fsa/sherpa-onnx", url="https://github.com/k2-fsa/sherpa-onnx")

# 进程级共享：模型只加载一次，每条连接各开自己的 stream
_SPOTTER: sherpa_onnx.KeywordSpotter | None = None
_INFO: Info | None = None


def pick(model_dir: Path, pattern: str) -> str:
    hits = sorted(model_dir.glob(pattern))
    if not hits:
        raise SystemExit(f"模型目录里找不到 {pattern}：{model_dir}")
    return str(hits[0])


def to_tokens(model_dir: Path, keyword: str) -> str:
    """汉字 -> 带调拼音声韵母。已经是空格分隔的音素串就原样返回。"""
    if " " in keyword:
        return keyword
    out = sherpa_onnx.text2token(
        [keyword], tokens=str(model_dir / "tokens.txt"), tokens_type="ppinyin"
    )
    return " ".join(out[0])


class KwsHandler(AsyncEventHandler):
    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self._stream = None
        self._converter = AudioChunkConverter(rate=SAMPLE_RATE, width=2, channels=1)
        self._detected = False

    def _reset(self) -> None:
        assert _SPOTTER is not None
        self._stream = _SPOTTER.create_stream()
        self._detected = False

    async def handle_event(self, event: Event) -> bool:
        assert _SPOTTER is not None and _INFO is not None

        if Describe.is_type(event.type):
            await self.write_event(_INFO.event())
            return True

        if Detect.is_type(event.type):
            # satellite 用它声明要听哪些词。我们把所有配置的词都常驻在 keywords 文件里，
            # 单独关掉某个词做不到 —— 真要分词控制得按 name 过滤 Detection，暂无需求
            names = Detect.from_event(event).names
            _LOG.debug("Detect names=%s", names)
            self._reset()
            return True

        if AudioStart.is_type(event.type):
            self._reset()
            return True

        if AudioChunk.is_type(event.type):
            if self._stream is None:
                self._reset()
            chunk = self._converter.convert(AudioChunk.from_event(event))
            samples = np.frombuffer(chunk.audio, dtype=np.int16).astype(np.float32) / 32768.0
            self._stream.accept_waveform(SAMPLE_RATE, samples)

            while _SPOTTER.is_ready(self._stream):
                _SPOTTER.decode_stream(self._stream)
                result = _SPOTTER.get_result(self._stream)
                if result:
                    _LOG.info("命中唤醒词：%s", result)
                    self._detected = True
                    await self.write_event(
                        Detection(name=result, timestamp=chunk.timestamp).event()
                    )
                    # 不重置就会在同一段语音里反复触发
                    _SPOTTER.reset_stream(self._stream)
            return True

        if AudioStop.is_type(event.type):
            if not self._detected:
                await self.write_event(NotDetected().event())
            self._stream = None
            return True

        return True


async def main() -> None:
    global _SPOTTER, _INFO

    ap = argparse.ArgumentParser()
    ap.add_argument("--uri", default="tcp://0.0.0.0:10400")
    ap.add_argument(
        "--model-dir",
        default="~/apps/wakeword/sherpa/sherpa-onnx-kws-zipformer-zh-en-3M-2025-12-20",
    )
    ap.add_argument("--keyword", action="append", default=None, help="唤醒词，可重复；直接写中文")
    ap.add_argument("--threshold", type=float, default=0.25, help="触发概率阈值")
    ap.add_argument("--score", type=float, default=1.0, help="关键词 boosting 分数")
    ap.add_argument("--chunk", default="8", choices=["8", "16"], help="流式块大小；8 延迟更低")
    ap.add_argument("--int8", action="store_true", help="用 int8 量化 encoder")
    ap.add_argument("--num-threads", type=int, default=2)
    ap.add_argument("--debug", action="store_true")
    args = ap.parse_args()

    logging.basicConfig(level=logging.DEBUG if args.debug else logging.INFO)

    model_dir = Path(args.model_dir).expanduser()
    keywords = args.keyword or ["你好曼波"]

    # keywords 文件必须落盘（sherpa 只收路径），放模型目录旁边便于排查
    keywords_file = model_dir / "keywords_active.txt"
    lines = []
    for kw in keywords:
        tokens = to_tokens(model_dir, kw)
        _LOG.info("唤醒词「%s」-> %s", kw, tokens)
        lines.append(f"{tokens} @{kw}")
    keywords_file.write_text("\n".join(lines) + "\n", encoding="utf-8")

    suffix = ".int8.onnx" if args.int8 else ".onnx"
    _SPOTTER = sherpa_onnx.KeywordSpotter(
        tokens=str(model_dir / "tokens.txt"),
        encoder=pick(model_dir, f"encoder*chunk-{args.chunk}-*{suffix}"),
        decoder=pick(model_dir, f"decoder*chunk-{args.chunk}-*.onnx"),  # decoder 无 int8 版
        joiner=pick(model_dir, f"joiner*chunk-{args.chunk}-*{suffix}"),
        keywords_file=str(keywords_file),
        num_threads=args.num_threads,
        keywords_threshold=args.threshold,
        keywords_score=args.score,
        provider="cpu",
    )

    _INFO = Info(
        wake=[
            WakeProgram(
                name="sherpa-kws",
                description="sherpa-onnx 中英文关键词检测（开放词表）",
                attribution=ATTRIBUTION,
                installed=True,
                version="1.0",
                models=[
                    WakeModel(
                        name=kw,
                        description=f"唤醒词「{kw}」",
                        attribution=ATTRIBUTION,
                        installed=True,
                        version=None,
                        languages=["zh-cn", "zh"],
                        # HA 的语音助手界面按 phrase 显示「说：xxx」
                        phrase=kw,
                    )
                    for kw in keywords
                ],
            )
        ]
    )

    _LOG.info(
        "Wyoming KWS 启动于 %s，唤醒词 %s，threshold=%.2f score=%.1f chunk-%s",
        args.uri,
        keywords,
        args.threshold,
        args.score,
        args.chunk,
    )
    server = AsyncServer.from_uri(args.uri)
    await server.run(lambda r, w: KwsHandler(r, w))


if __name__ == "__main__":
    asyncio.run(main())
