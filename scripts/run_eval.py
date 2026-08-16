#!/usr/bin/env python3
"""语音助手评测：跑一遍用例集，报通过率与**回归**。

为什么需要：提示词调了七版，每版只手工验证三四条，看不见别处的破坏 ——
v7 修好执行类的同时把全屋概况弄退化了，纯粹是碰巧测到才发现的。
没有尺子的时候，你以为在优化，实际在随机游走。详见 docs/model-tuning.md。

⚠️ 走的是**真实语音管线**（`assist_pipeline/run`），不是直接调对话代理。
两者不等价：管线上 `prefer_local_intents=True`，高频指令先由 HA 内建意图匹配，
命中就 0.08s 返回、根本不进 LLM。直接调代理会绕过这一层，测出来的东西
和用户实际听到的不是一回事 —— 实测「打开书房的灯」走管线 0.08s 成功，
直接调 LLM 则幻觉出「书房灯带」失败。用 `--path agent` 可以切回去做对照。

⚠️ 这还是个**随机系统**：温度 0.7 下同一版提示词连跑三轮拿到 18/18、15/18、16/18。
所以每条默认跑三遍报通过率，而不是给一个二元的通过/失败。

用法：
    python scripts/run_eval.py                 跑全量（每条 3 遍），与上次基线对比
    python scripts/run_eval.py --repeat 5      要更稳的结论就多跑几遍
    python scripts/run_eval.py --save          把本次结果存为新基线
    python scripts/run_eval.py --only 安全      只跑某一类
    python scripts/run_eval.py --path agent    绕过内建层只测 LLM（做对照用）

安全性：带 `restore: true` 的用例会在执行前快照相关实体、跑完恢复原状，
所以「把所有的灯都关掉」这种破坏性用例可以放心跑。
⚠️ 用例里不许出现卧室设备（有人睡觉），不许拿空调做 setup（反复启停伤压缩机）。
"""
import argparse
import asyncio
import json
import os
import re
import sys
import time
import urllib.request
import uuid

import websockets
import yaml

HA = os.environ["HA_URL"].rstrip("/")
T = os.environ["HA_TOKEN"]
WS = HA.replace("https://", "wss://").replace("http://", "ws://") + "/api/websocket"
HERE = os.path.dirname(os.path.abspath(__file__))
CASES = os.path.join(HERE, "eval_cases.yaml")
BASELINE = os.path.join(HERE, "eval_baseline.json")

JARGON = ["上下文", "系统提示", "提示词", "工具调用", "entity", "实体ID",
          "静态上下文", "context", "domain", "attributes"]
MD = re.compile(r"\*\*|^\s*[-*+]\s|^\s*#+\s|`", re.M)
QUESTION = re.compile(r"[？?]")
# 会被破坏性用例改动的域，快照范围
VOLATILE = ("light.", "switch.", "fan.", "cover.", "climate.")
RUN = uuid.uuid4().hex[:8]
CPS = 4.54          # 曼波音色实测语速，字/秒。字数 ÷ 这个 = 播报秒数


def _open(req, timeout=300):
    return urllib.request.build_opener(urllib.request.ProxyHandler({})).open(req, timeout=timeout)


def rest(path):
    req = urllib.request.Request(HA + path, headers={"Authorization": f"Bearer {T}"})
    return json.loads(_open(req, 60).read())


def post(path, payload, timeout=300):
    req = urllib.request.Request(
        HA + path, data=json.dumps(payload).encode(),
        headers={"Authorization": f"Bearer {T}", "Content-Type": "application/json"})
    return json.loads(_open(req, timeout).read())


def snapshot():
    # 只快照 on/off 的可用实体 —— unavailable / unknown 的恢复不了，
    # 强行 turn_on 会让 HA 返回 500
    return {s["entity_id"]: s["state"] for s in rest("/api/states")
            if s["entity_id"].startswith(VOLATILE) and s["state"] in ("on", "off")}


def restore(before):
    """把状态被改动的实体恢复原样"""
    now = snapshot()
    fixed = 0
    for eid, old in before.items():
        cur = now.get(eid)
        if cur == old or old not in ("on", "off") or cur not in ("on", "off"):
            continue
        dom = eid.split(".")[0]
        try:
            post(f"/api/services/{dom}/turn_{old}", {"entity_id": eid}, timeout=30)
            fixed += 1
        except Exception as e:
            # 单个实体恢复失败不该让整轮评测挂掉（离线设备、只读实体等）
            print(f"    ⚠️ 恢复失败 {eid}: {type(e).__name__}")
    return fixed


def named_states():
    """友好名 → 状态。断言按名字匹配，真实 entity_id 不入库（脱敏基线）"""
    return {s["attributes"].get("friendly_name", s["entity_id"]): s["state"]
            for s in rest("/api/states")
            if s["entity_id"].startswith(VOLATILE)}


def await_state(want, timeout=6.0):
    """轮询到期望状态出现，或超时。返回最后一次读到的 友好名→状态"""
    deadline = time.time() + timeout
    while True:
        cur = named_states()
        if not want:
            return cur
        if any(want["name"] in n and st == want["state"] for n, st in cur.items()):
            return cur
        if time.time() >= deadline:
            return cur
        time.sleep(0.4)


def resolve(name):
    """友好名片段 → entity_id。用例里写名字不写 ID，真实 ID 不入库（脱敏基线）"""
    hit = [s["entity_id"] for s in rest("/api/states")
           if name in s["attributes"].get("friendly_name", "")]
    if not hit:
        raise RuntimeError(f"setup 找不到实体 ~{name}")
    return hit[0]


def setup(steps):
    """跑用例前把设备摆成指定状态 —— 「我有点冷」这类感受类断言依赖当前状态"""
    for st in steps or []:
        dom, svc = st["service"].split(".")
        post(f"/api/services/{dom}/{svc}",
             {"entity_id": resolve(st["name"]), **(st.get("data") or {})}, timeout=60)
    if steps:
        time.sleep(3)   # 设备回报有延迟，等状态进到 LLM 看得见的地方


def check(assertion, speech, before_states, after, layer):
    """返回失败原因列表，空列表 = 通过"""
    a = assertion or {}
    bad = []

    if "max_chars" in a and len(speech) > a["max_chars"]:
        bad.append(f"超长 {len(speech)}>{a['max_chars']}")
    if "min_chars" in a and len(speech) < a["min_chars"]:
        bad.append(f"过短 {len(speech)}<{a['min_chars']}")
    if "question" in a:
        has = bool(QUESTION.search(speech))
        if has != a["question"]:
            bad.append("不该有问句" if has else "应该有问句")
    if a.get("no_markdown") and MD.search(speech):
        bad.append("含 Markdown")
    if a.get("no_jargon"):
        hit = [j for j in JARGON if j in speech]
        if hit:
            bad.append(f"内部术语 {hit}")
    if "contains" in a and not any(k in speech for k in a["contains"]):
        bad.append(f"缺少关键词之一 {a['contains']}")
    if "not_contains" in a:
        hit = [k for k in a["not_contains"] if k in speech]
        if hit:
            bad.append(f"含禁用词 {hit}")
    # 锁住「这条该走快路」—— 高频指令掉进 LLM 就是 15 倍的延迟劣化
    if "handled_by" in a and layer != a["handled_by"]:
        bad.append(f"该由「{a['handled_by']}」处理，实际是「{layer}」")
    # ⚠️ LLM 对话代理返回的是纯文本，工具调用结果**不在** response.data.success 里
    #    （那是内建意图代理的格式）。所以正确性与安全断言一律看实体真实状态。
    if "expect_state" in a:
        want = a["expect_state"]
        hits = [(n, st) for n, st in after.items() if want["name"] in n]
        if not hits:
            bad.append(f"找不到实体 ~{want['name']}")
        elif not any(st == want["state"] for _, st in hits):
            bad.append(f"状态不符 {want['name']} 期望 {want['state']}，实际 {hits[:2]}")
    if "not_affects" in a:
        hit = [n for n, st in after.items()
               if any(k in n for k in a["not_affects"]) and before_states.get(n) != st]
        if hit:
            bad.append(f"★安全★ 状态被改变 {hit[:3]}")
    return bad


class Session:
    """一条长连接跑完整轮评测。管线路径只有 WebSocket 端点，REST 上没有。"""

    def __init__(self, w):
        self.w, self.n, self.pid, self.agent = w, 0, None, None

    async def cmd(self, **kw):
        self.n += 1
        kw["id"] = self.n
        await self.w.send(json.dumps(kw))
        while True:
            m = json.loads(await self.w.recv())
            if m.get("id") == self.n and m["type"] == "result":
                if not m.get("success"):
                    raise RuntimeError(m.get("error"))
                return m.get("result")

    async def via_pipeline(self, text, conv_id):
        """真实语音路径：含 prefer_local_intents，可能根本不进 LLM"""
        self.n += 1
        mid, t0, prog, speech = self.n, time.time(), 0, ""
        await self.w.send(json.dumps({
            "id": mid, "type": "assist_pipeline/run", "pipeline": self.pid,
            "start_stage": "intent", "end_stage": "intent",
            "input": {"text": text}, "conversation_id": conv_id}))
        while True:
            m = json.loads(await self.w.recv())
            if m.get("id") != mid:
                continue
            if m["type"] == "result" and not m.get("success"):
                raise RuntimeError(m.get("error"))
            if m["type"] != "event":
                continue
            e = m["event"]
            if e["type"] == "intent-progress":
                prog += 1
            elif e["type"] == "intent-end":
                speech = (e["data"]["intent_output"]["response"]["speech"]
                          .get("plain", {}).get("speech") or "").strip()
            elif e["type"] == "error":
                raise RuntimeError(e["data"].get("message"))
            elif e["type"] == "run-end":
                # 一个 intent-progress 都没有 = 内建意图代理接住了，没进 LLM
                return speech, ("内建" if prog == 0 else "LLM"), time.time() - t0

    async def via_agent(self, text, conv_id):
        """对照路径：直接打对话代理，绕过内建层"""
        t0 = time.time()
        r = await self.cmd(type="conversation/process", text=text, language="zh-cn",
                           agent_id=self.agent, conversation_id=conv_id)
        sp = (r["response"]["speech"].get("plain", {}).get("speech") or "").strip()
        return sp, "LLM", time.time() - t0


async def run(args):
    cases = yaml.safe_load(open(CASES, encoding="utf-8"))["cases"]
    if args.only:
        cases = [c for c in cases if c.get("category") == args.only]
    base = {}
    if os.path.exists(BASELINE):
        base = json.load(open(BASELINE, encoding="utf-8")).get("results", {})

    async with websockets.connect(WS, max_size=None) as w:
        await w.recv()
        await w.send(json.dumps({"type": "auth", "access_token": T}))
        await w.recv()
        s = Session(w)
        pls = (await s.cmd(type="assist_pipeline/pipeline/list"))["pipelines"]
        pl = next((p for p in pls if args.pipeline in (p["id"], p["name"])), None) \
            if args.pipeline else None
        pl = pl or next((p for p in pls if p.get("stt_engine")), pls[0])
        s.pid, s.agent = pl["id"], args.agent or pl["conversation_engine"]
        ask = s.via_pipeline if args.path == "pipeline" else s.via_agent

        where = (f"语音管线「{pl['name']}」 prefer_local_intents={pl.get('prefer_local_intents')}"
                 if args.path == "pipeline" else f"直连 {s.agent}（绕过内建层）")
        print(f"跑 {len(cases)} 条 × {args.repeat} 遍   路径={where}"
              f"{'   对比基线' if base else '   无基线'}\n")

        results, layers, t_all = {}, {}, time.time()
        for c in cases:
            turns = c.get("turns") or [{"say": c["say"], "assert": c.get("assert", {})}]
            hits, lines, reasons, lay = 0, [], [], []
            t0 = time.time()
            for rep in range(args.repeat):
                before = snapshot() if c.get("restore") else None
                setup(c.get("setup"))
                before_named = named_states()
                # ⚠️ conversation_id 每次都得换新。写死会让 HA 把上一次跑的问答留在会话
                #    历史里，同一个问题再问时模型直接复读上次的答案 —— 表现为改了提示词
                #    而回复字节完全不变，看上去像「提示词没生效」。
                conv = f"eval-{RUN}-{c['id']}-{rep}"
                bad = []
                for ti, tn in enumerate(turns):
                    try:
                        speech, who, el = await ask(tn["say"], conv)
                        lay.append(who)
                        # 状态回报有延迟（实测 MIoT 约 0.5s），单次读会误判成「没执行」
                        after = await_state(tn.get("assert", {}).get("expect_state"))
                        tbad = check(tn.get("assert"), speech, before_named or {}, after, who)
                    except Exception as e:
                        speech, who, el, tbad = "", "?", 0, [f"异常 {type(e).__name__}: {e}"]
                    if rep == 0 or tbad:
                        pre = "     " if len(turns) == 1 else f"     {ti + 1}) "
                        tag = "" if args.repeat == 1 else f"[{rep + 1}] "
                        lines.append(f"{pre}{tag}〔{who} {el:.2f}s〕「{tn['say']}」 → {speech[:58]}"
                                     f"  ({len(speech)}字≈{len(speech) / CPS:.0f}s播报)")
                    bad += [f"第{ti + 1}轮 {b}" for b in tbad] if len(turns) > 1 else tbad
                if before:
                    restore(before)
                hits += not bad
                reasons += bad
            el = time.time() - t0

            rate = hits / args.repeat
            results[c["id"]] = rate
            layers[c["id"]] = ("内建" if lay and all(x == "内建" for x in lay)
                               else ("混合" if "内建" in lay else "LLM"))
            was = base.get(c["id"])
            # ⚠️ 温度 0.7 的系统没有「通过/不通过」，只有通过率。
            mark = "✅" if rate == 1 else ("⚠️" if rate > 0 else "❌")
            flag = ""
            if was is not None and rate < was - 1e-9:
                flag = f"  ⚠️ 回归 {was:.0%}→{rate:.0%}"
            elif was is not None and rate > was + 1e-9:
                flag = f"  🔧 改善 {was:.0%}→{rate:.0%}"
            score = "" if args.repeat == 1 else f" {hits}/{args.repeat}"
            print(f"{mark} [{c.get('category','')}] {c['id']}{score}{flag}  ({el:.1f}s)")
            for ln in lines:
                print(ln)
            for b in dict.fromkeys(reasons):
                print(f"     ✗ {b}（{reasons.count(b)}/{args.repeat}）"
                      if args.repeat > 1 else f"     ✗ {b}")

        stable = sum(1 for v in results.values() if v == 1)
        flaky = sum(1 for v in results.values() if 0 < v < 1)
        total = len(results)
        regressed = [k for k, v in results.items() if base.get(k, -1) > v]
        fixed = [k for k, v in results.items() if 0 <= base.get(k, 2) < v]

        print(f"\n{'=' * 60}")
        print(f"稳定通过 {stable}/{total}   不稳定 {flaky}   全败 {total - stable - flaky}"
              f"   整体 {sum(results.values()) / total:.0%}   耗时 {time.time() - t_all:.0f}s")
        if args.path == "pipeline":
            byl = {}
            for v in layers.values():
                byl[v] = byl.get(v, 0) + 1
            print("处理层：" + "  ".join(f"{k} {n} 条" for k, n in sorted(byl.items()))
                  + "   ← 走内建的越多日常越快（内建 ~0.1s / LLM ~1.2s）")
        if args.repeat == 1:
            print("⚠️  只跑了一轮，结果含运气成分。判断提示词改动请用 --repeat 3 以上")
        if regressed:
            print(f"⚠️  回归 {len(regressed)}：{', '.join(regressed)}")
        if fixed:
            print(f"🔧 改善 {len(fixed)}：{', '.join(fixed)}")
        if base and not regressed and not fixed:
            print("与基线一致")

        if args.save:
            json.dump({"ts": time.strftime("%Y-%m-%d %H:%M:%S"), "repeat": args.repeat,
                       "path": args.path, "stable": stable, "total": total,
                       "results": results, "layers": layers},
                      open(BASELINE, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
            print(f"\n已存为基线：{BASELINE}")

        return 1 if regressed else 0


ap = argparse.ArgumentParser()
ap.add_argument("--save", action="store_true", help="把本次结果存为新基线")
ap.add_argument("--only", help="只跑某一类（执行/安全/查询/概况/感受/多轮/异常/稳定性）")
ap.add_argument("--agent", default=None, help="对话代理 entity_id，默认用管线配置的")
ap.add_argument("--pipeline", default=None, help="管线 id 或名称，默认取第一个带 STT 的")
ap.add_argument("--path", choices=["pipeline", "agent"], default="pipeline",
                help="pipeline=真实语音路径（默认）；agent=绕过内建层只测 LLM")
ap.add_argument("--repeat", type=int, default=3,
                help="每条跑几遍，报通过率（默认 3；温度 0.7 下跑一遍不可信）")
sys.exit(asyncio.run(run(ap.parse_args())))
