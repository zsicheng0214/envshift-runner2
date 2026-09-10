#!/usr/bin/env python3
"""GUI 题 × 三系统 结果表。读若干产物根目录下的 gui_<System>_<machine>.json。
   setup_ok=0 的运行记「装置未就绪」,不计入 agent 归因(与代码题的「装不起来」同一档)。
用法: gui_table.py <产物根目录>..."""
import json, glob, os, sys, collections

roots = sys.argv[1:] or ["/tmp"]
tasks = json.load(open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "gui_tasks.json"), encoding="utf-8"))
order = [f"gui__{t['app']}-{t['id'][:8]}" for t in tasks]
title = {f"gui__{t['app']}-{t['id'][:8]}": t["instruction"][:52] for t in tasks}
res = collections.defaultdict(lambda: collections.defaultdict(list))
for root in roots:
    for f in glob.glob(os.path.join(root, "**", "gui_*.json"), recursive=True):
        try: j = json.load(open(f, encoding="utf-8"))
        except Exception: continue
        if not isinstance(j, dict) or "resolved" not in j or not j.get("instance", "").startswith("gui__"): continue
        p = j.get("platform", "")
        plat = "mac" if "macOS" in p or "Darwin" in p else "win" if "Windows" in p else "linux"
        res[j["instance"]][plat].append((j["resolved"], j.get("setup_ok", True), j.get("why", "")[:60], j.get("agent_s")))
plats = ["linux", "mac", "win"]
print(f"{'题':26s} {'Linux':>7s} {'macOS':>7s} {'Win':>7s}   说明(× = 装置未就绪)")
flips = []
for i in order:
    r = res.get(i, {})
    cells = []
    for p in plats:
        v = r.get(p, [])
        cells.append("".join(("×" if not s else str(ok)) for ok, s, _, _ in v) or "·")
    got = {p: [ok for ok, s, _, _ in r.get(p, []) if s] for p in plats}
    vals = [x for p in plats for x in got[p]]
    mark = ""
    if vals and len(set(vals)) > 1:
        mark = "  ← 不一致"; flips.append(i)
    print(f"{i:26s} " + " ".join(f"{c:>7s}" for c in cells) + f"   {title.get(i,'')}{mark}")
print()
for p in plats:
    v = [ok for i in order for ok, s, _, _ in res.get(i, {}).get(p, []) if s]
    bad = sum(1 for i in order for ok, s, _, _ in res.get(i, {}).get(p, []) if not s)
    print(f"  {p:6s} 通过 {sum(v):2d}/{len(v):2d} ({100*sum(v)//max(1,len(v))}%)   装置未就绪 {bad}")
print(f"\n跨系统不一致 {len(flips)} 道")
