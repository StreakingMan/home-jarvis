#!/usr/bin/env python3
"""把暴露给 Assist 的实体分成 保留 / 移除，并解释每条判定依据。

**判定顺序至关重要**（2026-08-16 重写，v1 的顺序是错的）：

    1. 按 MIoT siid 分路   —— entity_id 尾部 `_p_<siid>_<piid>`，同一路共享 siid
    2. 读该路的模式实体值
         无线开关        -> 该路继电器绝不暴露（下游接智能灯，动它会断电）
         有线/有线和无线  -> 该路继电器就是真控制手段，暴露
         无模式实体      -> 落到第 3 步
    3. 再用域 / 型号 / 名称词表判断

v1 先用名称词表过滤，在多路开关上**既误删又漏判**：
  - 误删：把「筒灯 左键」这类真继电器当成按键配置删掉（本宅 13 个）
  - 漏判：把「大门筒灯 开关」当普通继电器保留，实际是纯无线（本宅 29 个）

默认只打印，加 --apply 才实际解除暴露。
"""
import asyncio
import collections
import json
import os
import re
import sys
import urllib.request

import websockets

HA = os.environ["HA_URL"].rstrip("/")
T = os.environ["HA_TOKEN"]
OUT = os.path.dirname(os.path.abspath(__file__)) + "/exposure_plan.json"
APPLY = "--apply" in sys.argv

SIID = re.compile(r"_p_(\d+)_\d+$")
MODES = {"有线开关", "无线开关", "有线和无线开关"}

# 一路之内，命中这些的是设置项而非继电器
GANG_SETTING = re.compile(r"防闪烁|指示灯|模式$|通电默认|默认上电|状态$")

# 通用设置项词表（第 3 步才用）
SETTING = re.compile(
    "防闪烁|物理控制锁|提示音|延时|睡眠模式|通电默认|唤醒自定义|助眠自定义|勿扰|"
    "开灯默认|静音|遥控器|凌动|指示灯|童锁|自定义|蜂鸣|屏幕|记忆|校准|复位|重置|"
    "固件|灵敏度|夜灯|保温|除氯|风冷|识别|清洁剂|拖布|集尘|上下水|地毯|割毛发|甩尾|断点|弹窗|降噪|"
    "开发者模式|凝水|ECO|喜好|干燥功能|柔风|辅热|UV杀菌|洗衣凝珠|摆风|电机反向|息屏|禁麦|"
    "雷达|移动侦测|移动追踪|时间水印|微光|宽动态|收合|自动关闭|状态，|true为|"
    "亮度增|亮度减|色温增|色温减|亮度切换|色温切换|开或切换|主从灯|跟随主光|"
    "起夜|默认灯光|恢复断电|电量信息|房屋面积|导风板|标准睡眠|MAX风挡|关机下|"
    "左右摆风|diy|加餐提醒|进食量低|计划进度|音箱模式|充电保护|倒计时|最大功率|过度用电"
)

KEEP_DOMAINS = {"light", "climate", "fan", "cover", "media_player", "vacuum", "todo"}
DROP_DOMAINS = {"sensor", "binary_sensor"}

# —— 用户决策（2026-08-16）——
USER_DROP = [
    (re.compile(r"P1S.*(辅助风扇|打印仓风扇|冷却风扇)"), "用户决定：P1S 风扇不需要语音控制"),
    (re.compile(r"猪笼草缸\s+灯|盆栽射灯\s+灯"), "植物灯在 light 域，会被「把灯都关了」误伤（R6）"),
]


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


def rest(path):
    req = urllib.request.Request(HA + path, headers={"Authorization": f"Bearer {T}"})
    return json.loads(
        urllib.request.build_opener(urllib.request.ProxyHandler({})).open(req, timeout=30).read()
    )


async def main():
    r = await ws_calls([
        {"type": "homeassistant/expose_entity/list"},
        {"type": "config/entity_registry/list"},
        {"type": "config/device_registry/list"},
        {"type": "config/area_registry/list"},
    ])
    exp, reg, devs, areas = (x.get("result") for x in r)
    states = {s["entity_id"]: s for s in rest("/api/states")}

    exposed = {k for k, v in (exp.get("exposed_entities") or {}).items() if v.get("conversation")}
    ent = {e["entity_id"]: e for e in reg}
    dev = {d["id"]: d for d in devs}
    an = {a["area_id"]: a["name"] for a in areas}

    def name_of(eid):
        return states.get(eid, {}).get("attributes", {}).get("friendly_name") or eid

    def area_of(eid):
        e = ent.get(eid, {})
        return an.get(e.get("area_id") or dev.get(e.get("device_id"), {}).get("area_id"), "未分配")

    # ---- 第 1 步：按 siid 分路 ----
    gang = collections.defaultdict(list)
    for e in reg:
        mo = SIID.search(e["entity_id"])
        if mo and e.get("device_id"):
            gang[(e["device_id"], mo.group(1))].append(e["entity_id"])

    # ---- 第 2 步：每一路的模式 ----
    gang_mode = {}
    for key, eids in gang.items():
        for eid in eids:
            if eid.startswith("select."):
                st = states.get(eid, {}).get("state")
                if st in MODES:
                    gang_mode[key] = st
    ent_gang = {eid: key for key, eids in gang.items() for eid in eids}

    keep, drop = {}, {}
    for eid in sorted(exposed):
        dom = eid.split(".")[0]
        nm = name_of(eid)
        rec = {"entity_id": eid, "name": nm, "domain": dom, "area": area_of(eid)}

        hit = next((why for pat, why in USER_DROP if pat.search(nm)), None)
        if hit:
            rec["reason"] = hit
            drop[eid] = rec
            continue

        if dom in DROP_DOMAINS:
            rec["reason"] = f"{dom} 查询类，走 GetLiveContext 按需查"
            drop[eid] = rec
        elif dom in KEEP_DOMAINS:
            rec["reason"] = f"{dom} 主控设备"
            keep[eid] = rec
        elif dom == "switch":
            mode = gang_mode.get(ent_gang.get(eid))
            if mode == "无线开关":
                rec["reason"] = "★ 该路为纯无线模式，动继电器会让下游智能灯断电"
                drop[eid] = rec
            elif mode in ("有线开关", "有线和无线开关"):
                if GANG_SETTING.search(nm):
                    rec["reason"] = "有线路内的设置项"
                    drop[eid] = rec
                else:
                    rec["reason"] = f"★ 有线路继电器（模式={mode}）"
                    keep[eid] = rec
            elif SETTING.search(nm):
                rec["reason"] = "设备内部设置项"
                drop[eid] = rec
            elif " * " in nm:
                rec["reason"] = "厂商自定义服务（名称含 *）"
                drop[eid] = rec
            elif re.search(r"(开关|电源)\s*$", nm):
                rec["reason"] = "真继电器（无模式实体，按名称判定）"
                keep[eid] = rec
            else:
                rec["reason"] = "switch 未命中任何保留条件"
                drop[eid] = rec
        else:
            rec["reason"] = f"未归类的域 {dom}"
            drop[eid] = rec

    json.dump({"keep": list(keep.values()), "drop": list(drop.values())},
              open(OUT, "w"), ensure_ascii=False, indent=1)
    json.dump(sorted(exposed), open(OUT.replace("plan", "backup"), "w"), indent=1)

    print(f"暴露 {len(exposed)} → 保留 {len(keep)} / 解除 {len(drop)}\n")
    print("=" * 66 + "\n【保留】按区域\n" + "=" * 66)
    ba = collections.defaultdict(list)
    for v in keep.values():
        ba[v["area"]].append(v)
    for a in sorted(ba, key=lambda x: (x == "未分配", x)):
        print(f"\n■ {a}（{len(ba[a])}）")
        for v in sorted(ba[a], key=lambda x: (x["domain"], x["name"])):
            star = "★" if v["reason"].startswith("★") else " "
            print(f"   {star}{v['domain']:<12} {v['name']}")
    print("\n" + "=" * 66 + "\n【解除】按原因\n" + "=" * 66)
    for why, n in collections.Counter(v["reason"] for v in drop.values()).most_common():
        print(f"  {n:>4} × {why}")

    if not APPLY:
        print("\n【未执行】加 --apply 生效")
        return
    # 命令名是 homeassistant/expose_entity（无 /set），assistants 是复数列表
    res = await ws_calls([{"type": "homeassistant/expose_entity",
                           "assistants": ["conversation"],
                           "entity_ids": list(drop), "should_expose": False}])
    print("\n执行结果:", "OK" if res[0].get("success") else res[0].get("error"))


asyncio.run(main())
