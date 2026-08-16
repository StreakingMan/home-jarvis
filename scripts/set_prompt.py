#!/usr/bin/env python3
"""读写对话代理子条目的系统提示词。

为什么要脚本：Ollama 集成把对话代理做成了 **subentry**，提示词既不在主条目
`data` 里，也不在 `config_entries/subentries/list` 的返回里 —— 只能走
reconfigure flow 才读得到写得进。UI 上改一次要点五六下，提示词又要反复迭代。

⚠️ `deploy/Modelfile.qwen3-jarvis` 里的 `SYSTEM` **不生效**，HA 会用这里的
`prompt` 覆盖它。行为约束只能写在这儿。

用法：
    python scripts/set_prompt.py --show                    打印当前提示词
    python scripts/set_prompt.py --file prompts/v8.txt     写入
    python scripts/set_prompt.py --show --title "Ollama Conversation"
"""
import argparse
import asyncio
import json
import os
import sys

import urllib.request

import websockets

HA = os.environ["HA_URL"].rstrip("/")
T = os.environ["HA_TOKEN"]
WS = HA.replace("https://", "wss://").replace("http://", "ws://") + "/api/websocket"


def rest(method, path, body=None):
    req = urllib.request.Request(
        HA + path, method=method,
        data=json.dumps(body).encode() if body is not None else None,
        headers={"Authorization": f"Bearer {T}", "Content-Type": "application/json"})
    op = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    return json.loads(op.open(req, timeout=60).read())


async def run(args):
    async with websockets.connect(WS, max_size=None) as w:
        await w.recv()
        await w.send(json.dumps({"type": "auth", "access_token": T}))
        await w.recv()
        n = [0]

        async def cmd(**kw):
            n[0] += 1
            kw["id"] = n[0]
            await w.send(json.dumps(kw))
            while True:
                m = json.loads(await w.recv())
                if m.get("id") == n[0] and m["type"] == "result":
                    if not m.get("success"):
                        raise RuntimeError(m.get("error"))
                    return m["result"]

        entries = [e for e in await cmd(type="config_entries/get")
                   if e["domain"] == args.domain]
        if not entries:
            sys.exit(f"没有 {args.domain} 集成")
        entry = entries[0]["entry_id"]
        subs = await cmd(type="config_entries/subentries/list", entry_id=entry)
        subs = [s for s in subs if s["subentry_type"] == "conversation"]
        if args.title:
            subs = [s for s in subs if s["title"] == args.title]
        if not subs:
            sys.exit("找不到匹配的对话子条目")
        sub = subs[0]

        # ⚠️ 子条目的 flow 只有 HTTP 端点，WebSocket 上没有对应命令
        #    （`config_entries/subentries/flow` 会返回 unknown_command）
        flow = rest("POST", "/api/config/config_entries/subentries/flow",
                    {"handler": [entry, "conversation"],
                     "subentry_id": sub["subentry_id"]})
        cur = {}
        for f in flow.get("data_schema", []):
            k = f["name"]
            if "description" in f and "suggested_value" in f["description"]:
                cur[k] = f["description"]["suggested_value"]
            elif "default" in f:
                cur[k] = f["default"]

        if args.show:
            print(f"# 子条目：{sub['title']}  ({sub['subentry_id']})")
            for k, v in cur.items():
                if k == "prompt":
                    print(f"\n----8<---- prompt ----8<----\n{v}\n---->8--------------------->8----\n")
                else:
                    print(f"{k} = {v!r}")
            return

        new = dict(cur)
        new["prompt"] = open(args.file, encoding="utf-8").read().strip()
        r = rest("POST", "/api/config/config_entries/subentries/flow/" + flow["flow_id"], new)
        if r.get("type") == "form":
            sys.exit(f"表单没过：{r.get('errors')}")
        print(f"✅ 已写入 {sub['title']}（{len(new['prompt'])} 字）")


ap = argparse.ArgumentParser()
ap.add_argument("--show", action="store_true")
ap.add_argument("--file")
ap.add_argument("--domain", default="ollama")
ap.add_argument("--title", default="Ollama Conversation")
a = ap.parse_args()
if not a.show and not a.file:
    ap.error("要么 --show，要么 --file")
asyncio.run(run(a))
