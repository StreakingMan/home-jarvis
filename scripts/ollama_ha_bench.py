#!/usr/bin/env python3
"""用真实暴露实体构造 HA 规模的 prompt，测 Ollama 的 prefill 与工具调用能力。

回答两个问题：
  1. 真实 prompt 体积下，首 token 要多久（延迟预算够不够）
  2. 8B 模型能否稳定吐出正确的 tool call（HA 集成能不能用的命门）

工具定义仿照 HA AssistAPI 暴露给 LLM 的 HassTurnOff / HassTurnOn。
"""
import json
import os
import time
import urllib.request

OLLAMA = os.environ.get("OLLAMA_URL", "http://127.0.0.1:11434")
MODEL = os.environ.get("OLLAMA_MODEL", "qwen3:8b")
HA = os.environ["HA_URL"].rstrip("/")
T = os.environ["HA_TOKEN"]

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "HassTurnOff",
            "description": "关闭设备。用于「关掉」「关闭」这类请求。",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "设备名称"},
                    "area": {"type": "string", "description": "区域名称，如 书房、客厅"},
                    "domain": {"type": "array", "items": {"type": "string"},
                               "description": "设备类型，如 light、fan、cover"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "HassTurnOn",
            "description": "打开设备。",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "area": {"type": "string"},
                    "domain": {"type": "array", "items": {"type": "string"}},
                },
            },
        },
    },
]

CASES = [
    ("关掉书房的灯", "HassTurnOff", {"area": "书房", "domain": ["light"]}),
    ("把客厅的灯全部打开", "HassTurnOn", {"area": "客厅", "domain": ["light"]}),
    ("餐厅吊灯关了", "HassTurnOff", {"name": "餐厅吊灯"}),
    ("卧室风扇开一下", "HassTurnOn", {"area": "卧室", "domain": ["fan"]}),
]


def rest(path):
    req = urllib.request.Request(HA + path, headers={"Authorization": f"Bearer {T}"})
    op = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    return json.loads(op.open(req, timeout=30).read())


def post(url, payload, timeout=300):
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"})
    op = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    return json.loads(op.open(req, timeout=timeout).read())


def build_prompt():
    """复刻 HA AssistAPI 给 LLM 的实体清单"""
    import asyncio
    import websockets

    async def exposed():
        url = HA.replace("http", "ws", 1) + "/api/websocket"
        async with websockets.connect(url, max_size=64 * 1024 * 1024) as w:
            await w.recv()
            await w.send(json.dumps({"type": "auth", "access_token": T}))
            await w.recv()
            await w.send(json.dumps({"id": 1, "type": "homeassistant/expose_entity/list"}))
            r = json.loads(await w.recv())
            return {k for k, v in (r["result"]["exposed_entities"] or {}).items()
                    if v.get("conversation")}

    ids = asyncio.run(exposed())
    states = {s["entity_id"]: s for s in rest("/api/states")}
    lines = ["你是一个智能家居助手。以下是这个家里可控制的设备：", ""]
    for eid in sorted(ids):
        s = states.get(eid)
        if not s:
            continue
        a = s["attributes"]
        lines.append(f"- names: {a.get('friendly_name', eid)}")
        lines.append(f"  domain: {eid.split('.')[0]}")
        lines.append(f"  state: '{s['state']}'")
    return "\n".join(lines), len(ids)


def main():
    sys_prompt, n = build_prompt()
    print(f"系统提示词：{n} 个实体，{len(sys_prompt):,} 字符\n")

    ok = 0
    for text, want_fn, want_args in CASES:
        t0 = time.time()
        r = post(f"{OLLAMA}/api/chat", {
            "model": MODEL, "stream": False, "think": False,
            "messages": [{"role": "system", "content": sys_prompt},
                         {"role": "user", "content": text}],
            "tools": TOOLS,
        })
        dt = time.time() - t0
        calls = r.get("message", {}).get("tool_calls") or []
        pe = r.get("prompt_eval_count", 0)
        prefill = r.get("prompt_eval_duration", 0) / 1e9

        print(f"「{text}」")
        print(f"   prompt {pe} tok / prefill {prefill:.2f}s / 总 {dt:.2f}s")
        if not calls:
            print(f"   ❌ 没有 tool call，直接回了文本: "
                  f"{(r.get('message', {}).get('content') or '')[:50]}")
            continue
        fn = calls[0]["function"]["name"]
        args = calls[0]["function"].get("arguments", {})
        good = fn == want_fn and all(
            str(args.get(k, "")).find(str(v[0] if isinstance(v, list) else v)) >= 0
            for k, v in want_args.items())
        print(f"   {'✅' if good else '⚠️ '} {fn}({json.dumps(args, ensure_ascii=False)})")
        print(f"      期望 {want_fn} 含 {json.dumps(want_args, ensure_ascii=False)}")
        ok += bool(good)
    print(f"\n工具调用正确率：{ok}/{len(CASES)}")


main()
