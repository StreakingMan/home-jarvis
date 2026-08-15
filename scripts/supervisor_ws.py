#!/usr/bin/env python3
"""经 HA WebSocket 的 `supervisor/api` 通道操作 Supervisor。

**为什么不用 REST**：长期访问令牌对 `/api/hassio/*` 一律 401，
但 HA 前端本身是通过 WebSocket 的 `supervisor/api` 命令代理过去的，
那条通道不受同样限制。2026-08-16 实测可读可写。

用法:
    python supervisor_ws.py get  /dns/info
    python supervisor_ws.py post /dns/options '{"servers":["dns://223.5.5.5"]}'
    python supervisor_ws.py post /dns/restart
    python supervisor_ws.py post /addons/core_mosquitto/install

令牌从环境变量 HA_URL / HA_TOKEN 取，不落盘、不打印。

注意：脚本内不设代理绕过逻辑，调用方需自行清掉 HTTP(S)_PROXY —— Clash 之类
会把发往内网 HA 的 WebSocket 也吃掉（no_proxy 里的 "192.168.*" 通配格式 Python 不认）。
"""
import asyncio
import json
import os
import sys

import websockets


async def main():
    method = (sys.argv[1] if len(sys.argv) > 1 else "get").lower()
    endpoint = sys.argv[2] if len(sys.argv) > 2 else "/supervisor/info"
    data = json.loads(sys.argv[3]) if len(sys.argv) > 3 else None

    url = os.environ["HA_URL"].rstrip("/").replace("http", "ws", 1) + "/api/websocket"
    async with websockets.connect(url, max_size=64 * 1024 * 1024) as ws:
        await ws.recv()
        await ws.send(json.dumps({"type": "auth", "access_token": os.environ["HA_TOKEN"]}))
        if json.loads(await ws.recv()).get("type") != "auth_ok":
            print("认证失败")
            return 1

        msg = {"id": 1, "type": "supervisor/api", "endpoint": endpoint, "method": method}
        if data is not None:
            msg["data"] = data
        await ws.send(json.dumps(msg))

        # Supervisor 的长任务（装加载项等）可能几分钟才回
        r = json.loads(await asyncio.wait_for(ws.recv(), timeout=900))
        if r.get("success"):
            # 不截断：/store 这类返回上千条，截断会破坏 JSON 结构导致下游解析失败
            print(json.dumps(r.get("result"), ensure_ascii=False, indent=2))
            return 0
        print("失败:", json.dumps(r.get("error"), ensure_ascii=False))
        return 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
