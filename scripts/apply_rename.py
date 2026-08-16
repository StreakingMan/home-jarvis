#!/usr/bin/env python3
"""按 rename_plan.yaml 批量改实体名（可回滚）。

为什么值得批量做：名字不是好看问题。实测 `风扇␠␠风扇` 的开启成功率 0/6，
改成「卧室风扇」后 4/4 —— LLM 调 HassTurnOn 填的是 `name`，对不上就整条挂掉。
详见 docs/voice-tuning.md「命名不是优化，是功能正确性」。

用法：
    python scripts/apply_rename.py --dry-run     只打印会改什么
    python scripts/apply_rename.py               执行，同时写回滚文件
    python scripts/apply_rename.py --undo        按回滚文件还原
    python scripts/apply_rename.py --unexpose    顺带处理 expose:false 的条目

计划里按**友好名**定位实体（真实 entity_id 不入库，脱敏基线）。
友好名重复时用 `area:` 字段消歧；仍然重复就跳过并报出来，不猜。
"""
import argparse
import asyncio
import json
import os
import sys

import urllib.request
import websockets
import yaml

HA = os.environ["HA_URL"].rstrip("/")
T = os.environ["HA_TOKEN"]
HERE = os.path.dirname(os.path.abspath(__file__))
PLAN = os.path.join(HERE, "rename_plan.yaml")
UNDO = os.path.join(HERE, "rename_undo.json")
WS = HA.replace("https://", "wss://").replace("http://", "ws://") + "/api/websocket"


def states():
    req = urllib.request.Request(HA + "/api/states", headers={"Authorization": f"Bearer {T}"})
    op = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    return json.loads(op.open(req, timeout=60).read())


async def main(args):
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
                    return m.get("result")

        devs = {d["id"]: d for d in await cmd(type="config/device_registry/list")}
        areas = {a["area_id"]: a["name"] for a in await cmd(type="config/area_registry/list")}
        ents = await cmd(type="config/entity_registry/list")
        fname = {s["entity_id"]: s["attributes"].get("friendly_name", "")
                 for s in states()}

        def area_of(e):
            aid = e.get("area_id") or (devs.get(e.get("device_id")) or {}).get("area_id")
            return areas.get(aid, "")

        if args.undo:
            if not os.path.exists(UNDO):
                sys.exit("没有回滚文件")
            for eid, old in json.load(open(UNDO, encoding="utf-8")).items():
                await cmd(type="config/entity_registry/update", entity_id=eid, name=old)
                print(f"↩ {eid} → {old!r}")
            print("已回滚。别忘了把 rename_undo.json 删掉")
            return

        plan = yaml.safe_load(open(PLAN, encoding="utf-8"))["renames"]
        undo, done, skipped = {}, 0, []
        for item in plan:
            cands = [e for e in ents if fname.get(e["entity_id"], "") == item["old"]]
            if item.get("area"):
                cands = [e for e in cands if area_of(e) == item["area"]]
            if len(cands) != 1:
                skipped.append((item["old"], f"匹配到 {len(cands)} 个"))
                continue
            e = cands[0]
            act = []
            if args.dry_run:
                print(f"  {item['old']!r}\n    → {item['new']!r}  [{area_of(e)}]")
            else:
                undo[e["entity_id"]] = e.get("name")  # None 表示原本没覆盖过
                await cmd(type="config/entity_registry/update",
                          entity_id=e["entity_id"], name=item["new"])
                act.append("改名")
                if args.unexpose and item.get("expose") is False:
                    await cmd(type="homeassistant/expose_entity",
                              entity_ids=[e["entity_id"]],
                              assistants=["conversation"], should_expose=False)
                    act.append("撤出暴露集合")
                print(f"✓ {item['old']!r} → {item['new']!r}  ({'+'.join(act)})")
            done += 1

        if skipped:
            print(f"\n⚠️ 跳过 {len(skipped)} 条（不猜，请手工确认）：")
            for o, why in skipped:
                print(f"   {o!r}  {why}")
        if not args.dry_run:
            json.dump(undo, open(UNDO, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
            print(f"\n改了 {done} 个。回滚文件：{UNDO}")
            print("⚠️ 改完记得跑一遍评测：python scripts/run_eval.py --repeat 3")
        else:
            print(f"\n共 {done} 条待改（--dry-run，什么都没动）")


ap = argparse.ArgumentParser()
ap.add_argument("--dry-run", action="store_true")
ap.add_argument("--undo", action="store_true")
ap.add_argument("--unexpose", action="store_true",
                help="同时把 expose:false 的条目撤出暴露集合")
asyncio.run(main(ap.parse_args()))
