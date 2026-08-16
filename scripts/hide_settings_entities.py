#!/usr/bin/env python3
"""隐藏「设备内部设置项」类实体，清理 UI 噪音。

与「解除暴露」是两件独立的事：
  解除暴露 -> 语音助手/LLM 看不见（options.<assistant>.should_expose）
  隐藏     -> 仪表板和实体选择器里看不见（hidden_by）
HA 只在实体首次出现时用 hidden_by 推导默认暴露值，之后两者互不影响。

所以隐藏集合 ⊂ 解除暴露集合，需剔除「真设备但不给 LLM」的那些：
  - sensor / binary_sensor 查询类  —— 仪表板要用
  - 植物灯、P1S 风扇             —— 真设备，UI 里要能控制
  - 纯无线模式继电器              —— 真继电器，保留可见性由人判断

默认只打印，加 --apply 才执行。
"""
import asyncio
import collections
import json
import os
import sys

import websockets

HA = os.environ["HA_URL"].rstrip("/")
T = os.environ["HA_TOKEN"]
S = os.path.dirname(os.path.abspath(__file__))
APPLY = "--apply" in sys.argv

# 这些理由对应的实体是真设备，只是不给 LLM —— 不隐藏
KEEP_VISIBLE = (
    "查询类",
    "植物灯",
    "P1S 风扇",
    "纯无线模式",
)

# 逐个排除：名称命中即保持可见（真设备功能，非设置项）
KEEP_BY_NAME = (
    "猫厕所 MAX PRO 2 摄像头",
)


async def ws_calls(msgs):
    async with websockets.connect(
        HA.replace("http", "ws", 1) + "/api/websocket", max_size=128 * 1024 * 1024
    ) as w:
        await w.recv()
        await w.send(json.dumps({"type": "auth", "access_token": T}))
        await w.recv()
        out = []
        for i, m in enumerate(msgs, 1):
            await w.send(json.dumps({"id": i, **m}))
            out.append(json.loads(await w.recv()))
        return out


async def main():
    plan = json.load(open(f"{S}/exposure_plan.json"))
    reg = (await ws_calls([{"type": "config/entity_registry/list"}]))[0]["result"]
    already = {e["entity_id"] for e in reg if e.get("hidden_by")}

    hide, skip = [], collections.defaultdict(list)
    for r in plan["drop"]:
        if any(k in r["reason"] for k in KEEP_VISIBLE) or any(k in r["name"] for k in KEEP_BY_NAME):
            skip["真设备功能，保持可见" if any(k in r["name"] for k in KEEP_BY_NAME) else r["reason"]].append(r)
        elif r["entity_id"] in already:
            skip["已经是隐藏状态"].append(r)
        else:
            hide.append(r)

    print(f"解除暴露的 {len(plan['drop'])} 个中：隐藏 {len(hide)} / 保持可见 {sum(len(v) for v in skip.values())}\n")
    print("=" * 62 + "\n【保持可见】—— 真设备，只是不给 LLM\n" + "=" * 62)
    for why, rs in sorted(skip.items(), key=lambda x: -len(x[1])):
        print(f"  {len(rs):>4} × {why}")
    print("\n" + "=" * 62 + "\n【隐藏】按原因\n" + "=" * 62)
    for why, n in collections.Counter(r["reason"] for r in hide).most_common():
        print(f"  {n:>4} × {why}")
    print("\n按域：", dict(collections.Counter(r["domain"] for r in hide)))

    json.dump([r["entity_id"] for r in hide], open(f"{S}/hide_plan.json", "w"), indent=1)
    json.dump(sorted(already), open(f"{S}/hidden_backup.json", "w"), indent=1)

    if not APPLY:
        print("\n【未执行】加 --apply 生效")
        return

    print(f"\n隐藏 {len(hide)} 个实体…")
    msgs = [{"type": "config/entity_registry/update",
             "entity_id": r["entity_id"], "hidden_by": "user"} for r in hide]
    res = await ws_calls(msgs)
    ok = sum(1 for x in res if x.get("success"))
    print(f"  成功 {ok} / {len(res)}")
    for x in res:
        if not x.get("success"):
            print("   失败:", x.get("error"))
            break


asyncio.run(main())
