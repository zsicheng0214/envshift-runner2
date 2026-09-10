#!/usr/bin/env python3
"""题面送达核对:每次运行的会话里,agent 收到的第一条用户消息长度,应当 ≈ 我们发出的题面长度。
   短于一半就判「题面未送达」,该次运行作废(装置缺陷,不是 agent 的失败)。
   2026-09-10 教训:Windows 的 .cmd 壳把题面在第一个换行截断,44 次运行 agent 只看到 25~64 字符,
   却被当成「Windows 通过率低」报了两天。
用法: delivery_check.py <产物根目录>... [--swe] [--gui]   → 每次运行一行 + 汇总;--json 输出到文件供表脚本用
"""
import json, glob, os, sys, pathlib, tempfile, subprocess

PRE_SWE = len("下面是一个真实仓库里的 issue。仓库已经在 /testbed,请直接修改源码解决它。\n只改实现代码,不要改测试文件。完成后不需要提交,把文件改好即可。\n\n")
PRE_GUI = len("You are operating a real desktop computer. Google Chrome is currently running on this machine, "
              "and Visual Studio Code is installed. Complete the following request for the user, then stop.\n\n")


def expected_lengths():
    exp = {}
    try:
        import pyarrow.parquet as pq
        for name in ("swe_verified.parquet", "swe_full.parquet"):
            f = pathlib.Path(tempfile.gettempdir()) / name
            if f.exists():
                for r in pq.read_table(f, columns=["instance_id", "problem_statement"]).to_pylist():
                    exp.setdefault(r["instance_id"], PRE_SWE + len(r["problem_statement"] or ""))
    except Exception:
        pass
    here = pathlib.Path(__file__).resolve().parent
    gt = here / "gui_tasks.json"
    if gt.exists():
        for t in json.load(open(gt, encoding="utf-8")):
            exp[f"gui__{t['app']}-{t['id'][:8]}"] = PRE_GUI + len(t["instruction"])
    return exp


def first_user_len(run):
    for f in glob.glob(os.path.join(run, "**", "sessions", "*.jsonl"), recursive=True):
        if "trajectory" in f: continue
        for line in open(f, encoding="utf-8", errors="replace"):
            try: m = json.loads(line).get("message") or {}
            except Exception: continue
            if m.get("role") == "user":
                c = m.get("content")
                return len(c) if isinstance(c, str) else len(" ".join(x.get("text", "") for x in (c or []) if isinstance(x, dict)))
    return None


def instance_of(run):
    for j in sorted(glob.glob(os.path.join(run, "*.json")) + glob.glob(os.path.join(run, "**", "*.json"), recursive=True), key=len):
        if "/oc-state/" in j: continue
        try: d = json.load(open(j, encoding="utf-8"))
        except Exception: continue
        if isinstance(d, dict) and "resolved" in d and d.get("instance"): return d["instance"], d.get("platform") or d.get("arch", "")
    return None, None


if __name__ == "__main__":
    args = [x for x in sys.argv[1:] if not x.startswith("--")]
    out_json = sys.argv[sys.argv.index("--json") + 1] if "--json" in sys.argv else None
    exp = expected_lengths(); rows = []
    for root in args:
        runs = {os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(f)))) for f in glob.glob(os.path.join(root, "**", "sessions", "*.jsonl"), recursive=True) if "trajectory" not in f}
        for run in sorted(runs):
            # 运行根:往上找到含结果 json 的目录
            r = run
            for _ in range(4):
                inst, plat = instance_of(r)
                if inst: break
                r = os.path.dirname(r)
            got = first_user_len(run); want = exp.get(inst or "")
            ok = (got is not None and want is not None and got >= 0.5 * want)
            rows.append((inst, plat, got, want, ok, r))
    bad = [x for x in rows if not x[4]]
    print(f"{'题':34s} {'平台':16s} {'收到':>7s} {'应为':>7s}  判定")
    for inst, plat, got, want, ok, r in rows:
        print(f"{str(inst)[:34]:34s} {str(plat)[:16]:16s} {str(got):>7s} {str(want):>7s}  {'送达' if ok else '★未送达/存疑'}")
    print(f"\n共 {len(rows)} 次运行,题面未送达(或存疑) {len(bad)} 次")
    if out_json:
        json.dump({r: {"instance": i, "platform": p, "got": g, "want": w, "delivered": ok} for i, p, g, w, ok, r in rows}, open(out_json, "w"), ensure_ascii=False, indent=1)
        print("→", out_json)
