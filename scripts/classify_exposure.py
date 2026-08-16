#!/usr/bin/env python3
"""把当前暴露给 Assist 的实体分成 保留 / 待定 / 移除 三档。

原则:
  - 拿不准的一律进「待定」，不替用户做决定
  - 植物灯强制进「移除」——硬约束是「不让 LLM 碰」，不是「让它小心」
  - 明细（含 entity_id）写 JSON 到 scratchpad，终端只打友好名，避免刷屏
"""
import asyncio, json, os, re, collections
import websockets
import urllib.request

HA = os.environ["HA_URL"].rstrip("/")
T = os.environ["HA_TOKEN"]
OUT = os.path.dirname(os.path.abspath(__file__)) + "/exposure_plan.json"

# 设备内部设置项的特征词 —— 命中即建议移除
SETTING_PAT = re.compile(
    "|".join(map(re.escape, [
        "防闪烁", "左键", "右键", "中键", "物理控制锁", "提示音", "延时",
        "睡眠模式", "通电默认状态", "唤醒自定义", "助眠自定义", "勿扰",
        "开灯默认状态", "静音", "遥控器", "凌动开关", "指示灯", "童锁",
        "状态，true", "自定义开关", "蜂鸣", "屏幕显示", "夜灯开关",
        "记忆", "校准", "复位", "重置", "固件", "OTA", "灵敏度",
    ]))
)
# 植物灯 —— 硬约束，强制移除
PLANT_PAT = re.compile("猪笼草|盆栽")
# 直接保留的域
KEEP_DOMAINS = {"light", "climate", "fan", "cover", "media_player", "vacuum", "todo"}
# 直接移除的域（查询类走 GetLiveContext 工具即可，不必常驻 prompt）
DROP_DOMAINS = {"sensor", "binary_sensor"}


async def ws_call(ws, i, typ, **kw):
    await ws.send(json.dumps({"id": i, "type": typ, **kw}))
    return json.loads(await ws.recv()).get("result")


async def main():
    async with websockets.connect(
        HA.replace("http", "ws", 1) + "/api/websocket", max_size=128 * 1024 * 1024
    ) as ws:
        await ws.recv()
        await ws.send(json.dumps({"type": "auth", "access_token": T}))
        await ws.recv()
        exp = await ws_call(ws, 1, "homeassistant/expose_entity/list")
        reg = await ws_call(ws, 2, "config/entity_registry/list")
        devs = await ws_call(ws, 3, "config/device_registry/list")
        areas = await ws_call(ws, 4, "config/area_registry/list")

    _req = urllib.request.Request(HA + "/api/states", headers={"Authorization": f"Bearer {T}"})
    _op = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    states = {x["entity_id"]: x for x in json.loads(_op.open(_req, timeout=30).read())}

    exposed = {k for k, v in (exp.get("exposed_entities") or {}).items() if v.get("conversation")}
    area_name = {a["area_id"]: a["name"] for a in areas}
    dev_area = {d["id"]: d.get("area_id") for d in devs}
    ent = {e["entity_id"]: e for e in reg}

    def area_of(eid):
        e = ent.get(eid, {})
        aid = e.get("area_id") or dev_area.get(e.get("device_id"))
        return area_name.get(aid, "未分配")

    def name_of(eid):
        fn = states.get(eid, {}).get("attributes", {}).get("friendly_name")
        if fn: return fn
        e = ent.get(eid, {})
        return e.get("name") or e.get("original_name") or eid

    buckets = {"keep": [], "review": [], "drop": []}
    for eid in sorted(exposed):
        dom = eid.split(".")[0]
        nm, ar = name_of(eid), area_of(eid)
        rec = {"entity_id": eid, "name": nm, "domain": dom, "area": ar}
        if PLANT_PAT.search(nm):
            rec["reason"] = "植物灯，硬约束：不进 LLM 视野"
            buckets["drop"].append(rec)
        elif dom in DROP_DOMAINS:
            rec["reason"] = f"{dom} 查询类，走 GetLiveContext 按需查即可"
            buckets["drop"].append(rec)
        elif SETTING_PAT.search(nm):
            rec["reason"] = "设备内部设置项"
            buckets["drop"].append(rec)
        elif dom in KEEP_DOMAINS:
            rec["reason"] = f"{dom} 主控设备"
            buckets["keep"].append(rec)
        elif dom == "switch":
            rec["reason"] = "switch 且名称未命中设置项特征 —— 需人工判断"
            buckets["review"].append(rec)
        else:
            rec["reason"] = f"未归类的域 {dom}"
            buckets["review"].append(rec)

    json.dump(buckets, open(OUT, "w"), ensure_ascii=False, indent=1)

    print(f"总计 {len(exposed)} 个暴露实体\n")
    print(f"  ✅ 建议保留  {len(buckets['keep']):>3}")
    print(f"  ❓ 待你定    {len(buckets['review']):>3}")
    print(f"  ❌ 建议移除  {len(buckets['drop']):>3}")
    print(f"\n明细已写入 {OUT}\n")

    print("=" * 62)
    print("【建议保留】按区域")
    print("=" * 62)
    byarea = collections.defaultdict(list)
    for r in buckets["keep"]:
        byarea[r["area"]].append(r)
    for ar in sorted(byarea, key=lambda x: (x == "未分配", x)):
        print(f"\n■ {ar}（{len(byarea[ar])}）")
        for r in sorted(byarea[ar], key=lambda x: x["domain"]):
            print(f"    {r['domain']:<14} {r['name']}")

    print("\n" + "=" * 62)
    print("【待你定】switch 里名称不像设置项的")
    print("=" * 62)
    for r in sorted(buckets["review"], key=lambda x: (x["area"], x["name"])):
        print(f"  [{r['area']}] {r['name']}")

    print("\n" + "=" * 62)
    print("【建议移除】按原因聚合")
    print("=" * 62)
    c = collections.Counter(r["reason"] for r in buckets["drop"])
    for reason, n in c.most_common():
        print(f"  {n:>4} × {reason}")


asyncio.run(main())
