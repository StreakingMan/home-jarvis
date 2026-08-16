#!/usr/bin/env python3
"""sherpa-onnx 关键词检测评测：正样本量召回率，对抗样本量误唤醒率。

**为什么不用自训 openWakeWord**：那条路要 16.5 GB 训练数据 + 几小时训练，换个唤醒词
就得从头再来。sherpa-onnx 的 KWS 是**开放词表**的 —— 模型只认「带调拼音音素」，
唤醒词写进一行文本就生效，换词改一行。代价是没有现成的 Wyoming 封装（见
`scripts/wyoming_kws.py`）。

**关键：唤醒词不是汉字**。模型的 tokens.txt 里是 ARPAbet（英文）+ 带调拼音声韵母
（中文），「你好曼波」必须写成 `n ǐ h ǎo m àn b ō`。本脚本用 sherpa_onnx.text2token
自动转，所以命令行上直接传中文即可。

用法：
    ~/miniconda3/envs/kws/bin/python scripts/eval_kws.py \
        --model-dir ~/apps/wakeword/sherpa/sherpa-onnx-kws-zipformer-zh-en-3M-2025-12-20 \
        --positive ~/apps/wakeword/data/positive \
        --adversarial ~/apps/wakeword/data/adversarial \
        --keyword 你好曼波

传多个 --keyword 会各自独立评测一遍，用来横向比候选唤醒词。
"""

from __future__ import annotations

import argparse
import sys
import tempfile
from pathlib import Path

import numpy as np
import sherpa_onnx
import soundfile as sf

SAMPLE_RATE = 16000
# KWS 是流式模型，尾部要补静音才会吐出最后一个 token，否则短音频永远触发不了
TAIL_PADDING_SEC = 0.66


def load_audio(path: Path) -> np.ndarray:
    """读成 16 kHz 单声道 float32。soundfile 直接支持 mp3，不需要 ffmpeg。"""
    data, sr = sf.read(str(path), dtype="float32", always_2d=True)
    samples = data.mean(axis=1)
    if sr != SAMPLE_RATE:
        # 用 soxr 而不是 librosa：后者带 numba，几百 MB，只为重采样不值当
        import soxr

        samples = soxr.resample(samples, sr, SAMPLE_RATE)
    return np.ascontiguousarray(samples, dtype=np.float32)


def pick(model_dir: Path, pattern: str) -> str:
    hits = sorted(model_dir.glob(pattern))
    if not hits:
        sys.exit(f"模型目录里找不到 {pattern}：{model_dir}")
    return str(hits[0])


def to_tokens(model_dir: Path, keyword: str) -> str:
    """汉字 -> 带调拼音声韵母。已经是空格分隔的音素串就原样返回。"""
    if " " in keyword:
        return keyword
    out = sherpa_onnx.text2token(
        [keyword], tokens=str(model_dir / "tokens.txt"), tokens_type="ppinyin"
    )
    return " ".join(out[0])


def build_spotter(
    model_dir: Path,
    keywords_file: Path,
    threshold: float,
    score: float,
    num_threads: int,
    chunk: str,
    int8: bool,
) -> sherpa_onnx.KeywordSpotter:
    suffix = ".int8.onnx" if int8 else ".onnx"
    return sherpa_onnx.KeywordSpotter(
        tokens=str(model_dir / "tokens.txt"),
        encoder=pick(model_dir, f"encoder*chunk-{chunk}-*{suffix}"),
        decoder=pick(model_dir, f"decoder*chunk-{chunk}-*.onnx"),  # decoder 没有 int8 版
        joiner=pick(model_dir, f"joiner*chunk-{chunk}-*{suffix}"),
        keywords_file=str(keywords_file),
        num_threads=num_threads,
        keywords_threshold=threshold,
        keywords_score=score,
        provider="cpu",
    )


def detect(spotter: sherpa_onnx.KeywordSpotter, samples: np.ndarray) -> bool:
    """整段喂进去，返回这段音频里是否触发过关键词。"""
    stream = spotter.create_stream()
    stream.accept_waveform(SAMPLE_RATE, samples)
    stream.accept_waveform(
        SAMPLE_RATE, np.zeros(int(TAIL_PADDING_SEC * SAMPLE_RATE), dtype=np.float32)
    )
    stream.input_finished()

    while spotter.is_ready(stream):
        spotter.decode_stream(stream)
        if spotter.get_result(stream):
            return True
    return False


def clips(directory: Path) -> list[Path]:
    return sorted(
        p for p in directory.iterdir() if p.suffix.lower() in {".wav", ".mp3", ".flac", ".ogg"}
    )


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model-dir", type=Path, required=True)
    ap.add_argument("--positive", type=Path, required=True, help="应该触发的样本目录")
    ap.add_argument("--adversarial", type=Path, required=True, help="不该触发的样本目录")
    ap.add_argument(
        "--keyword", action="append", default=None, help="唤醒词，可重复；直接写中文即可"
    )
    ap.add_argument(
        "--thresholds", default="0.15,0.25,0.35,0.45", help="要扫的触发阈值"
    )
    ap.add_argument("--scores", default="1.0", help="要扫的 boosting 分数，逗号分隔")
    ap.add_argument("--num-threads", type=int, default=4)
    ap.add_argument(
        "--chunk", default="16", choices=["8", "16"], help="流式块大小；8 延迟更低"
    )
    ap.add_argument("--int8", action="store_true", help="用 int8 量化 encoder")
    args = ap.parse_args()

    model_dir = args.model_dir.expanduser()
    keywords = args.keyword or ["你好曼波"]

    pos = clips(args.positive.expanduser())
    adv = clips(args.adversarial.expanduser())
    if not pos or not adv:
        sys.exit(f"样本为空：positive={len(pos)} adversarial={len(adv)}")

    print(f"模型 chunk-{args.chunk}{' int8' if args.int8 else ''}")
    print(f"正样本 {len(pos)} 条，对抗样本 {len(adv)} 条")
    print("解码音频…", flush=True)
    pos_audio = [load_audio(p) for p in pos]
    adv_audio = [load_audio(p) for p in adv]
    adv_sec = sum(len(a) for a in adv_audio) / SAMPLE_RATE
    print(f"对抗样本总时长 {adv_sec / 60:.1f} 分钟")

    for keyword in keywords:
        tokens = to_tokens(model_dir, keyword)
        print(f"\n关键词「{keyword}」-> {tokens}")
        print(f"{'score':>6} {'阈值':>6}  {'召回率':>17}  {'误唤醒率':>17}  {'误唤醒/小时':>11}")
        print("-" * 70)

        with tempfile.TemporaryDirectory() as tmp:
            keywords_file = Path(tmp) / "keywords.txt"
            keywords_file.write_text(f"{tokens} @{keyword}\n", encoding="utf-8")

            for raw_score in args.scores.split(","):
                score = float(raw_score)
                for raw in args.thresholds.split(","):
                    threshold = float(raw)
                    spotter = build_spotter(
                        model_dir,
                        keywords_file,
                        threshold,
                        score,
                        args.num_threads,
                        args.chunk,
                        args.int8,
                    )
                    hits = sum(1 for a in pos_audio if detect(spotter, a))
                    fa = sum(1 for a in adv_audio if detect(spotter, a))
                    per_hour = fa / (adv_sec / 3600) if adv_sec else 0.0
                    print(
                        f"{score:>6.1f} {threshold:>6.2f}"
                        f"  {hits / len(pos_audio):>8.1%} ({hits:>3}/{len(pos_audio):<3})"
                        f"  {fa / len(adv_audio):>8.1%} ({fa:>3}/{len(adv_audio):<3})  {per_hour:>11.1f}",
                        flush=True,
                    )


if __name__ == "__main__":
    main()
