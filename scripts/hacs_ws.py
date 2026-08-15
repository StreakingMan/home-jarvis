#!/usr/bin/env python3
"""HACS WebSocket 操作工具。
用法:
  python hacs_ws.py list                 # 只读：列出已下载的仓库
  python hacs_ws.py find <关键词>         # 只读：在 HACS 全量索引里搜
  python hacs_ws.py remove <repo_id>     # 移除（删文件）
  python hacs_ws.py download <repo_id>   # 下载
令牌从环境变量取，不落盘、不打印。
"""
import asyncio, json, os, sys
import websockets

HA_URL = os.environ["HA_URL"].rstrip("/")
TOKEN = os.environ["HA_TOKEN"]
WS_URL = HA_URL.replace("http", "ws", 1) + "/api/websocket"

_id = 0
def nid():
    global _id
    _id += 1
    return _id

async def call(ws, msg):
    msg["id"] = nid()
    await ws.send(json.dumps(msg))
    while True:
        r = json.loads(await ws.recv())
        if r.get("id") == msg["id"]:
            return r

async def main():
    action = sys.argv[1] if len(sys.argv) > 1 else "list"
    arg = sys.argv[2] if len(sys.argv) > 2 else None

    async with websockets.connect(WS_URL, max_size=32 * 1024 * 1024) as ws:
        await ws.recv()  # auth_required
        await ws.send(json.dumps({"type": "auth", "access_token": TOKEN}))
        auth = json.loads(await ws.recv())
        if auth.get("type") != "auth_ok":
            print("认证失败:", auth.get("type")); return
        print(f"已连接 HA {auth.get('ha_version')}")

        if action in ("list", "find"):
            # HACS 不同版本命令名不同，逐个试
            for cmd in ("hacs/repositories/list", "hacs/repositories", "hacs/repository/list"):
                r = await call(ws, {"type": cmd})
                if r.get("success"):
                    repos = r.get("result") or []
                    print(f"命令 {cmd} 可用，返回 {len(repos)} 条\n")
                    for x in repos:
                        if not isinstance(x, dict):
                            continue
                        name = x.get("full_name") or x.get("name", "")
                        if action == "find" and arg and arg.lower() not in name.lower():
                            continue
                        if action == "list" and not x.get("installed"):
                            continue
                        print(f"  id={x.get('id'):<12} {name:<52} "
                              f"cat={x.get('category',''):<12} 已装={x.get('installed')} "
                              f"版本={x.get('installed_version') or x.get('available_version') or '-'}")
                    return
                else:
                    print(f"  {cmd} -> {r.get('error',{}).get('message','不可用')}")
            print("没有可用的 HACS 列表命令")

        elif action == "add":
            # 加自定义仓库：不同 HACS 版本命令名不同，逐个试
            for cmd, payload in (
                ("hacs/repositories/add", {"repository": arg, "category": "integration"}),
                ("hacs/repository/add",   {"repository": arg, "category": "integration"}),
                ("hacs/repositories/custom/add", {"repository": arg, "category": "integration"}),
            ):
                r = await call(ws, {"type": cmd, **payload})
                ok = r.get("success")
                print(f"  {cmd} -> {'OK' if ok else r.get('error',{}).get('message','失败')}")
                if ok:
                    return

        elif action in ("remove", "download"):
            for cmd in (f"hacs/repository/{action}", f"hacs/repositories/{action}"):
                r = await call(ws, {"type": cmd, "repository": arg})
                ok = r.get("success")
                print(f"  {cmd} -> {'OK' if ok else r.get('error',{}).get('message','失败')}")
                if ok:
                    return

asyncio.run(main())
