#!/usr/bin/env python3
"""量一下「暴露给 Assist 的实体」在 LLM prompt 里到底占多大。

复刻 HA AssistAPI 生成实体清单的格式，统计字符数并估算 token。
只输出统计量，不打印实体内容（避免刷屏 + 避免真实实体 ID 外泄）。
"""
import asyncio, json, os, collections
import websockets
import urllib.request

HA = os.environ["HA_URL"].rstrip("/")
TOKEN = os.environ["HA_TOKEN"]


def rest(path):
    req = urllib.request.Request(HA + path, headers={"Authorization": f"Bearer {TOKEN}"})
    op = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    return json.loads(op.open(req, timeout=30).read())


async def exposed_ids():
    url = HA.replace("http", "ws", 1) + "/api/websocket"
    async with websockets.connect(url, max_size=64 * 1024 * 1024) as ws:
        await ws.recv()
        await ws.send(json.dumps({"type": "auth", "access_token": TOKEN}))
        await ws.recv()
        await ws.send(json.dumps({"id": 1, "type": "homeassistant/expose_entity/list"}))
        r = json.loads(await ws.recv())
        out = []
        for eid, cfg in (r.get("result", {}).get("exposed_entities", {}) or {}).items():
            if cfg.get("conversation"):
                out.append(eid)
        return out


def entry(st, areas):
    """复刻 AssistAPI 给 LLM 的单条实体描述"""
    a = st.get("attributes", {})
    name = a.get("friendly_name", st["entity_id"])
    dom = st["entity_id"].split(".")[0]
    lines = [f"- names: {name}", f"  domain: {dom}"]
    ar = areas.get(st["entity_id"])
    if ar:
        lines.append(f"  areas: {ar}")
    lines.append(f"  state: '{st['state']}'")
    # 常见会被带上的属性
    for k in ("device_class", "unit_of_measurement", "supported_features"):
        if k in a:
            lines.append(f"  {k}: {a[k]}")
    return "\n".join(lines)


async def main():
    ids = set(await exposed_ids())
    states = {s["entity_id"]: s for s in rest("/api/states")}
    blocks, by_domain = [], collections.Counter()
    for eid in ids:
        st = states.get(eid)
        if not st:
            continue
        blocks.append(entry(st, {}))
        by_domain[eid.split(".")[0]] += 1

    text = "\n".join(blocks)
    chars = len(text)
    # Qwen 系分词器: 中文约 1~1.5 字/token, ASCII 约 4 字符/token
    cjk = sum(1 for c in text if "一" <= c <= "鿿")
    ascii_n = chars - cjk
    est = int(cjk / 1.2 + ascii_n / 3.5)

    print(f"暴露给 conversation 的实体: {len(ids)} 个（其中 {len(blocks)} 个有状态）")
    print(f"实体清单字符数: {chars:,}  （中文 {cjk:,} / 其他 {ascii_n:,}）")
    print(f"估算 token:     ~{est:,}")
    print()
    print("按域分布 top10:")
    for d, n in by_domain.most_common(10):
        print(f"  {d:<20} {n}")
    print()
    print(f"平均每实体: {chars // max(len(blocks),1)} 字符 / ~{est // max(len(blocks),1)} token")


asyncio.run(main())
