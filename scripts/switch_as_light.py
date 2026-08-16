#!/usr/bin/env python3
"""把「控制灯的继电器」从 switch 域转成 light 域（HA 内置 switch_as_x）。

**为什么需要**：米家里可以把开关的某一路「标记为灯」，小爱据此让「关书房所有灯」
包含它。但那是米家云端/App 层的属性，**不在 MIoT spec 里**，xiaomi_home 集成
读不到 —— 它只看到一个 `switch:on` 属性，如实映射成 `switch` 实体
（实测 `device_class: switch`）。

后果：HA 的「关某区域所有的灯」不包含这些继电器，LLM 拿到的语义也是 switch。

解法是 HA 内置的 `switch_as_x` 助手，把 switch 重新包装成 light。
转换后 HA 会自动把原 switch 置为 `hidden_by=integration`，
避免同一负载出现两个可控实体（符合暴露策略 R5）。

⚠️ **植物灯不转**：转成 light 后会被「把灯都关了」误伤，见暴露策略 R6。
⚠️ **只转真继电器**：纯无线模式的分路早已排除在暴露集合之外，这里从当前
   暴露集合出发，天然不会碰到。

默认只打印，加 --apply 才执行。
"""
import asyncio
import json
import os
import re
import sys
import urllib.request

import websockets

HA = os.environ["HA_URL"].rstrip("/")
T = os.environ["HA_TOKEN"]
APPLY = "--apply" in sys.argv

# 负载是灯 -> 转 light
# SKIP_PAT 与 FAN_PAT 先判，所以这里可以放宽到「名字里有灯」
LIGHT_PAT = re.compile(r"灯|照明")
# 负载是风扇 -> 转 fan
FAN_PAT = re.compile(r"风扇|排气扇")
# 明确不转：植物/生态缸（R6）、设备电源、非照明负载
SKIP_PAT = re.compile(r"雨林灯|猪笼草|盆栽|水泵|雾化器|二氧化碳|洗衣机|净化器|摄像|电源|猫砂盆")


def rest(path):
    req = urllib.request.Request(HA + path, headers={"Authorization": f"Bearer {T}"})
    return json.loads(
        urllib.request.build_opener(urllib.request.ProxyHandler({})).open(req, timeout=30).read()
    )


def post(path, payload, timeout=60):
    req = urllib.request.Request(
        HA + path, data=json.dumps(payload).encode(),
        headers={"Authorization": f"Bearer {T}", "Content-Type": "application/json"})
    return json.loads(
        urllib.request.build_opener(urllib.request.ProxyHandler({})).open(req, timeout=timeout).read()
    )


async def exposed_switches():
    url = HA.replace("http", "ws", 1) + "/api/websocket"
    async with websockets.connect(url, max_size=64 * 1024 * 1024) as w:
        await w.recv()
        await w.send(json.dumps({"type": "auth", "access_token": T}))
        await w.recv()
        await w.send(json.dumps({"id": 1, "type": "homeassistant/expose_entity/list"}))
        r = json.loads(await w.recv())
        return {k for k, v in (r["result"]["exposed_entities"] or {}).items()
                if v.get("conversation") and k.startswith("switch.")}


def convert(entity_id, target):
    flow = post("/api/config/config_entries/flow", {"handler": "switch_as_x"})
    return post(f"/api/config/config_entries/flow/{flow['flow_id']}",
                {"entity_id": entity_id, "target_domain": target, "invert": False})


async def main():
    ids = await exposed_switches()
    states = {s["entity_id"]: s for s in rest("/api/states")}

    plan = {"light": [], "fan": [], "skip": []}
    for eid in sorted(ids):
        nm = states.get(eid, {}).get("attributes", {}).get("friendly_name", eid)
        if SKIP_PAT.search(nm):
            plan["skip"].append((eid, nm, "生态缸/设备电源/非照明负载"))
        elif FAN_PAT.search(nm):
            plan["fan"].append((eid, nm))
        elif LIGHT_PAT.search(nm):
            plan["light"].append((eid, nm))
        else:
            plan["skip"].append((eid, nm, "负载类型不明，保持 switch"))

    for tgt in ("light", "fan"):
        print(f"\n{'=' * 60}\n转成 {tgt}：{len(plan[tgt])} 个\n{'=' * 60}")
        for eid, nm in plan[tgt]:
            print(f"  {nm}")
    print(f"\n{'=' * 60}\n保持 switch：{len(plan['skip'])} 个\n{'=' * 60}")
    for eid, nm, why in plan["skip"]:
        print(f"  {nm}\n      └ {why}")

    if not APPLY:
        print("\n【未执行】加 --apply 生效")
        return

    print()
    for tgt in ("light", "fan"):
        for eid, nm in plan[tgt]:
            try:
                r = convert(eid, tgt)
                ok = r.get("type") == "create_entry"
                print(f"  {'✅' if ok else '❌'} {tgt:<6} {nm}")
            except Exception as e:
                print(f"  ❌ {tgt:<6} {nm}  -> {e}")


asyncio.run(main())
