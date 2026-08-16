#!/usr/bin/env python3
"""语音助手评测：跑一遍用例集，报通过率与**回归**。

为什么需要：提示词调了七版，每版只手工验证三四条，看不见别处的破坏 ——
v7 修好执行类的同时把全屋概况弄退化了，纯粹是碰巧测到才发现的。
没有尺子的时候，你以为在优化，实际在随机游走。详见 docs/model-tuning.md。

⚠️ 这是个**随机系统**：温度 0.7 下同一版提示词连跑三轮拿到 18/18、15/18、16/18。
所以每条默认跑三遍报通过率，而不是给一个二元的通过/失败。判断某次改动有没有用，
看的是通过率的变化，不是某一次的跑分。

用法：
    python scripts/run_eval.py                 跑全量（每条 3 遍），与上次基线对比
    python scripts/run_eval.py --repeat 5      要更稳的结论就多跑几遍
    python scripts/run_eval.py --save          把本次结果存为新基线
    python scripts/run_eval.py --only 安全      只跑某一类
    python scripts/run_eval.py --agent conversation.xxx   指定对话代理

安全性：带 `restore: true` 的用例会在执行前快照相关实体、跑完恢复原状，
所以「把所有的灯都关掉」这种破坏性用例可以放心跑。
"""
import argparse
import json
import os
import re
import sys
import time
import urllib.request
import uuid

import yaml

HA = os.environ["HA_URL"].rstrip("/")
T = os.environ["HA_TOKEN"]
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


def converse(text, agent, conv_id):
    body = {"text": text, "language": "zh-cn", "conversation_id": conv_id}
    if agent:
        body["agent_id"] = agent
    return post("/api/conversation/process", body)


def check(case, resp, speech, before_states, after):
    """返回失败原因列表，空列表 = 通过"""
    a = case.get("assert", {})
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
    if "response_type" in a:
        rt = resp.get("response", {}).get("response_type")
        if rt != a["response_type"]:
            bad.append(f"类型 {rt}≠{a['response_type']}")
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


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--save", action="store_true", help="把本次结果存为新基线")
    ap.add_argument("--only", help="只跑某一类（执行/安全/查询/概况/异常/稳定性）")
    ap.add_argument("--agent", default=None, help="对话代理 entity_id，默认用管线首选")
    ap.add_argument("--repeat", type=int, default=3,
                    help="每条跑几遍，报通过率（默认 3；温度 0.7 下跑一遍不可信）")
    args = ap.parse_args()

    cases = yaml.safe_load(open(CASES, encoding="utf-8"))["cases"]
    if args.only:
        cases = [c for c in cases if c.get("category") == args.only]
    base = {}
    if os.path.exists(BASELINE):
        base = json.load(open(BASELINE, encoding="utf-8")).get("results", {})

    results, t_all = {}, time.time()
    print(f"跑 {len(cases)} 条用例"
          f"{'（对比基线）' if base else '（无基线，本次将作为首个基线）'}\n")

    for c in cases:
        # 单轮写 say/assert，多轮写 turns[]；多轮共用一个 conversation_id
        turns = c.get("turns") or [{"say": c["say"], "assert": c.get("assert", {})}]
        hits, lines, reasons = 0, [], []
        t0 = time.time()
        for rep in range(args.repeat):
            before = snapshot() if c.get("restore") else None
            setup(c.get("setup"))
            before_named = named_states()
            # ⚠️ conversation_id 每次都得换新。写死成 eval-<id> 会让 HA 把上一次跑的问答
            #    留在会话历史里，同一个问题再问时模型直接复读上次的答案 —— 表现为改了
            #    提示词而回复字节完全不变，看上去像「提示词没生效」。
            conv = f"eval-{RUN}-{c['id']}-{rep}"
            bad = []
            for ti, tn in enumerate(turns):
                try:
                    resp = converse(tn["say"], args.agent, conv)
                    r = resp.get("response", {})
                    speech = (r.get("speech", {}).get("plain", {}).get("speech") or "").strip()
                    # 设备状态回报有延迟（实测 MIoT 约 0.5s），单次读会误判成「没执行」
                    after = await_state(tn.get("assert", {}).get("expect_state"))
                    tbad = check({"assert": tn.get("assert", {})}, resp, speech,
                                 before_named or {}, after)
                except Exception as e:
                    speech, tbad = "", [f"异常 {type(e).__name__}: {e}"]
                if rep == 0 or tbad:
                    pre = "     " if len(turns) == 1 else f"     {ti + 1}) "
                    tag = "" if args.repeat == 1 else f"[{rep + 1}] "
                    lines.append(f"{pre}{tag}「{tn['say']}」 → {speech[:60]}  ({len(speech)}字)")
                bad += [f"第{ti + 1}轮 {b}" for b in tbad] if len(turns) > 1 else tbad
            if before:
                restore(before)
            hits += not bad
            reasons += bad
        el = time.time() - t0

        rate = hits / args.repeat
        results[c["id"]] = rate
        was = base.get(c["id"])
        # ⚠️ 温度 0.7 的系统没有「通过/不通过」，只有通过率。实测同一版提示词
        #    连跑三轮拿到 18/18、15/18、16/18 —— 单次跑分会把运气当成结论。
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

    print(f"\n{'=' * 56}")
    print(f"稳定通过 {stable}/{total}   不稳定 {flaky}   全败 {total - stable - flaky}"
          f"   整体 {sum(results.values()) / total:.0%}   耗时 {time.time() - t_all:.0f}s")
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
                   "stable": stable, "total": total, "results": results},
                  open(BASELINE, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        print(f"\n已存为基线：{BASELINE}")

    return 1 if regressed else 0


sys.exit(main())
