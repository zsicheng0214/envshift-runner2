#!/usr/bin/env python3
"""在 arm64 runner 上直接用 SWE-bench 官方 arm64 镜像跑 gold 补丁 + 官方 eval_script + 官方 parser。
   用法: swe_arm_gold.py <instance_id> [arm64|x86_64]
   数据从 HF 拉该实例的一行(不进仓库)。"""
import json, os, subprocess, sys, tempfile, pathlib, urllib.request
iid = sys.argv[1]; arch = sys.argv[2] if len(sys.argv) > 2 else "arm64"
def sh(c, **k): return subprocess.run(c, shell=True, capture_output=True, text=True, **k)
# 取该实例数据(HF datasets-server 行查询)
def fetch_row(iid):
    """HF 的 filter 接口对这种查询回 422;改为直接下载官方 parquet(6MB)本地筛。"""
    import pyarrow.parquet as pq
    pq_path = pathlib.Path(tempfile.gettempdir()) / "swe_verified.parquet"
    if not pq_path.exists():
        urllib.request.urlretrieve("https://huggingface.co/datasets/SWE-bench/SWE-bench_Verified/resolve/main/data/test-00000-of-00001.parquet", pq_path)
    _full = pq_path.parent / "swe_full.parquet"
    for r in pq.read_table(pq_path).to_pylist():
        if r["instance_id"] == iid: return r
    _full = pq_path.parent / "swe_full.parquet"   # ★兜底:完整版 2294 道
    if not _full.exists():
        import subprocess; subprocess.run(["curl", "-sL", "-o", str(_full), "https://huggingface.co/datasets/SWE-bench/SWE-bench/resolve/main/data/test-00000-of-00001.parquet"], check=True)
    for r in pq.read_table(_full).to_pylist():
        if r["instance_id"] == iid: return r
    raise SystemExit(f"没有这道题: {iid}")
row = fetch_row(iid)
img = f"swebench/sweb.eval.{arch}.{iid.replace('__', '_1776_')}:latest"
print("镜像:", img)
p = sh(f"docker pull -q {img}")
if p.returncode != 0: print("PULL-FAIL", p.stderr.strip()[:200]); sys.exit(3)
c = f"swe-{arch}-{os.getpid()}"
sh(f"docker run -d --name {c} {img} sleep 3600")
print("容器架构:", sh(f"docker exec {c} uname -m").stdout.strip())
t = pathlib.Path(tempfile.mkdtemp())
(t / "patch.diff").write_text(row["patch"], encoding="utf-8"); (t / "eval.sh").write_text(row["eval_script"], encoding="utf-8")
sh(f"docker cp {t}/patch.diff {c}:/tmp/patch.diff"); sh(f"docker cp {t}/eval.sh {c}:/eval.sh")
g = sh(f"docker exec {c} sh -c 'cd /testbed && git apply -v /tmp/patch.diff'")
if g.returncode != 0: print("GOLD-APPLY-FAIL", g.stderr[-200:]); sys.exit(4)
e = sh(f"docker exec {c} bash /eval.sh", timeout=3000); log = e.stdout + e.stderr
sh(f"docker rm -f {c}")
import importlib
LP = importlib.import_module("swebench.harness.log_parsers")
parser = getattr(LP, row["log_parser"])
try: status = parser(log, row)
except TypeError: status = parser(log)
L = lambda v: json.loads(v) if isinstance(v, str) else list(v)   # parquet 里已是列表,jsonl 里是字符串
f2p = L(row["FAIL_TO_PASS"]); p2p = L(row["PASS_TO_PASS"])
fo = sum(status.get(x) == "PASSED" for x in f2p); po = sum(status.get(x) == "PASSED" for x in p2p)
res = int(fo == len(f2p) and po == len(p2p) and len(f2p) > 0)
print(f"RESULT {iid} arch={arch} resolved={res} f2p={fo}/{len(f2p)} p2p={po}/{len(p2p)}")
json.dump({"instance_id": iid, "arch": arch, "resolved": res, "status": status}, open(f"result_{arch}.json", "w"))
