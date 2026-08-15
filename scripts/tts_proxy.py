#!/usr/bin/env python3
"""GPT-SoVITS 前置缓冲代理（默认 :9881）

**存在的理由**：GPT-SoVITS 自带的 `api.py` 用 `transfer-encoding: chunked`
返回音频且不带 `Content-Length`。播放器（HASS.Agent、部分 media_player）
算不出时长，读完第一个 chunk 就停止 —— 实测表现为「一句话只播出第一个字」。
本代理把音频完整缓冲后带 `Content-Length` 一次性返回，播放器即可正常播完。

附带按内容哈希的磁盘缓存：固定播报（「猫砂快用完了」之类）只合成一次。

用法:
    uvicorn tts_proxy:app --host 127.0.0.1 --port 9881
    curl 'http://127.0.0.1:9881/?text=你好&text_language=zh'

环境变量:
    TTS_UPSTREAM   上游 GPT-SoVITS api.py 地址（默认 http://127.0.0.1:9880/）
    TTS_CACHE_DIR  缓存目录（默认 ~/apps/jarvis/cache/tts）
"""
import hashlib
import os

import requests
from fastapi import FastAPI, HTTPException, Query, Response

UPSTREAM = os.environ.get("TTS_UPSTREAM", "http://127.0.0.1:9880/")
CACHE_DIR = os.environ.get(
    "TTS_CACHE_DIR", os.path.expanduser("~/apps/jarvis/cache/tts")
)
os.makedirs(CACHE_DIR, exist_ok=True)

app = FastAPI(title="GPT-SoVITS buffered proxy")

# 必须绕开系统 HTTP 代理：Clash 会把发往 127.0.0.1:9880 的请求也吃掉并返回 502。
# no_proxy 里常见的 "127.*" 是通配格式，Python 的 proxy_bypass 不认，只能显式关。
SESSION = requests.Session()
SESSION.trust_env = False


@app.get("/healthz")
def healthz():
    return {"ok": True, "upstream": UPSTREAM, "cache": CACHE_DIR}


@app.get("/")
def say(
    text: str = Query(..., min_length=1),
    text_language: str = "zh",
    speed: float = 1.0,
    cut_punc: str = "",
):
    key = hashlib.sha256(
        f"{text}|{text_language}|{speed}|{cut_punc}".encode()
    ).hexdigest()[:32]
    path = os.path.join(CACHE_DIR, key + ".wav")

    if os.path.exists(path) and os.path.getsize(path) > 44:
        audio = open(path, "rb").read()
    else:
        params = {"text": text, "text_language": text_language, "speed": speed}
        if cut_punc:
            params["cut_punc"] = cut_punc
        try:
            r = SESSION.get(UPSTREAM, params=params, timeout=180)
        except Exception as e:
            raise HTTPException(502, f"上游不可达: {e}")
        if r.status_code != 200 or not r.content.startswith(b"RIFF"):
            raise HTTPException(
                502, f"上游返回异常 status={r.status_code} len={len(r.content)}"
            )
        audio = r.content
        tmp = path + ".part"
        with open(tmp, "wb") as f:
            f.write(audio)
        os.replace(tmp, path)

    # 关键：用 bytes 构造 Response -> Starlette 自动写 Content-Length，不走 chunked
    return Response(
        content=audio,
        media_type="audio/wav",
        headers={
            "Content-Length": str(len(audio)),
            "Accept-Ranges": "none",
            "Cache-Control": "public, max-age=86400",
        },
    )
