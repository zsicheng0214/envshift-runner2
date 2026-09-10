#!/usr/bin/env python3
"""用 SWE-bench 官方 v4.0.4 构建器重建一道题的环境(可改 ubuntu 版本;架构随宿主),然后 gold 补丁 + 官方 eval_script + 官方 parser。
   用法: swe_rebuild_gold.py <instance_id> <ubuntu_version> <tag> [--jsonl path]
   目的:实测"在别的 OS/架构上重建"到底难不难 —— 官方解在重建环境里过不过,就是闸门。"""
import argparse, inspect, json, os, platform, subprocess, sys, tempfile, pathlib, time, urllib.request
ap = argparse.ArgumentParser(); ap.add_argument("instance"); ap.add_argument("ubuntu"); ap.add_argument("tag"); ap.add_argument("--jsonl", default="")
ap.add_argument("--arm", default="gold", choices=["gold", "agent", "openclaw", "null"])
ap.add_argument("--official", action="store_true", help="不重建,直接用 SWE-bench 官方预制镜像(基准格);其余流程完全不变")
ap.add_argument("--sanitize", action="store_true", help="起跑前修剪 git 历史+屏蔽 github/pypi+禁 web 工具(oc_agent2.sh)"); ap.add_argument("--ockit", default=os.path.expanduser("~/ockit"))
ap.add_argument("--kit", default=os.path.expanduser("~/tbkit"), help="含 bridge.mjs/cordis.yaml/drive_dsh.py/node_modules/node/bin/node 的目录")
ap.add_argument("--model", default="deepseek-v4-pro"); ap.add_argument("--base", default="https://api.llmgateway.io/v1"); ap.add_argument("--timeout", type=int, default=1800)
a = ap.parse_args()
OCS = "oc_agent2.sh" if a.sanitize else "oc_agent.sh"
def sh(c, **k): return subprocess.run(c, shell=True, capture_output=True, text=True, **k)
# ---- 取实例 ----
if a.jsonl:
    row = next(json.loads(l) for l in open(a.jsonl, encoding="utf-8") if json.loads(l)["instance_id"] == a.instance)
else:
    import pyarrow.parquet as pq
    pq_path = pathlib.Path(tempfile.gettempdir()) / "swe_verified.parquet"
    if not pq_path.exists():
        urllib.request.urlretrieve("https://huggingface.co/datasets/SWE-bench/SWE-bench_Verified/resolve/main/data/test-00000-of-00001.parquet", pq_path)
    row = next((r for r in pq.read_table(pq_path).to_pylist() if r["instance_id"] == a.instance), None)
    if row is None:   # ★接触面达标的题多数只在完整版里,只读 Verified 会全部报「没有这道题」
        _full = pq_path.parent / "swe_full.parquet"
        if not _full.exists():
            import subprocess; subprocess.run(["curl", "-sL", "-o", str(_full), "https://huggingface.co/datasets/SWE-bench/SWE-bench/resolve/main/data/test-00000-of-00001.parquet"], check=True)
        row = next((r for r in pq.read_table(_full).to_pylist() if r["instance_id"] == a.instance), None)
    if row is None: raise SystemExit(f"没有这道题: {a.instance}")
# ---- 官方构建器:改 ubuntu 版本(这是官方模板自带的参数),架构随宿主 ----
import swebench.harness.constants as C
for name in dir(C):
    v = getattr(C, name)
    if isinstance(v, dict) and v.get("ubuntu_version"):
        v["ubuntu_version"] = a.ubuntu; print(f"官方模板参数 {name}.ubuntu_version -> {a.ubuntu}")
from spec_fixes import apply_spec_fixes
FIXES = apply_spec_fixes("linux")   # 与 mac/Windows 同一份 A 类改动,保证四平台配方一致
print("适配层:", FIXES or "(本题无需适配)")
from swebench.harness.test_spec.test_spec import make_test_spec
import docker
client = docker.from_env()
# 官方一体化构建:base → env → instance。★三层镜像 tag 都带本次 tag(=ubuntu 版本):官方 base/env 镜像名不含 ubuntu 版本,
# 不这样做的话换版本会复用/覆盖同名底座(2026-09-08 亲验:标 22.04 的格 13/21 实跑 24.04)。
# 做法:改 make_test_spec 的默认参数(namespace, base_image_tag, env_image_tag, instance_image_tag),让构建器内部所有调用一致生效。
_d = list(make_test_spec.__defaults__); _d[1] = a.tag; _d[2] = a.tag; make_test_spec.__defaults__ = tuple(_d)
ts = make_test_spec(row, instance_image_tag=a.tag)
print("宿主架构:", platform.machine(), "| 官方 spec 架构:", ts.arch, ts.platform)
print("镜像名:", ts.base_image_key, "|", ts.env_image_key, "|", ts.instance_image_key)
from swebench.harness import docker_build as DB
import logging; logging.basicConfig(level=logging.WARNING)
t0 = time.time()
if a.official:
    # 基准格:官方预制镜像(冻结于 2023-24 年),全世界报分数的口径。名字里 __ 要换成 _1776_
    img_off = f"swebench/sweb.eval.{ts.arch}.{a.instance.replace('__', '_1776_')}:latest"
    print("官方预制镜像:", img_off)
    if sh(f"docker pull {img_off}").returncode != 0:
        print(f"BUILD-FAIL {a.instance}: 官方镜像拉不下来 {img_off}"); sys.exit(6)
    sh(f"docker tag {img_off} {ts.instance_image_key}")
else:
    DB.build_instance_images(client=client, dataset=[row], force_rebuild=os.environ.get("FORCE_REBUILD", "0") == "1", max_workers=2, namespace=None, tag=a.tag)
print("构建耗时 %ds" % (time.time() - t0))
img = ts.instance_image_key
if not client.images.list(name=img):   # 构建失败就明说,别让后面的 OS 断言误报
    import glob as _g
    # 环境镜像(env)先于实例镜像构建;env 挂了就没有实例日志,两处都要找
    bl = sorted(_g.glob("logs/build_images/**/build_image.log", recursive=True), key=os.path.getmtime)
    tail = open(bl[-1], errors="replace").read()[-600:].replace("\n", " ") if bl else ""
    key = [l for l in tail.split(" ") if any(k in l for k in ("PackagesNotFound","Unsatisfiable","nothing", "conflict"))]
    print(f"BUILD-FAIL {a.instance} ubuntu={a.ubuntu} log={bl[-1] if bl else 'none'}: {tail[-500:]}"); sys.exit(6)
# ---- gold + 官方 eval ----
c = f"swe-rebuild-{os.getpid()}"
sh(f"docker rm -f {c}"); sh(f"docker run -d --name {c} " + (f"-v {a.ockit}:/opt/ockit:ro " if a.arm == "openclaw" else "") + f"{img} sleep {a.timeout + 3600}")
print("容器内:", sh(f"docker exec {c} sh -c 'uname -m; . /etc/os-release; echo $PRETTY_NAME'").stdout.strip().replace("\n", " | "))
os_actual = sh(f"docker exec {c} sh -c '. /etc/os-release; echo $VERSION_ID'").stdout.strip()
if os_actual != a.ubuntu and not a.official:   # ★硬断言:环境=X 必须从容器里读回来,标签不算数(官方镜像的版本由官方定,放行但记录)
    print(f"OS-MISMATCH requested={a.ubuntu} actual={os_actual}"); sh(f"docker rm -f {c}"); sys.exit(7)
t = pathlib.Path(tempfile.mkdtemp())
(t / "patch.diff").write_text(row["patch"], encoding="utf-8"); (t / "eval.sh").write_text(row["eval_script"], encoding="utf-8")
sh(f"docker cp {t}/patch.diff {c}:/tmp/patch.diff"); sh(f"docker cp {t}/eval.sh {c}:/eval.sh")
agent_s = 0
if a.arm == "gold":
    g = sh(f"docker exec {c} sh -c 'cd /testbed && git apply -v /tmp/patch.diff'")
    if g.returncode != 0: print("GOLD-APPLY-FAIL", g.stderr[-300:]); sh(f"docker rm -f {c}"); sys.exit(4)
elif a.arm == "openclaw":
    key = os.environ.get("ENVSHIFT_API_KEY", "")
    if not key: print("NO-API-KEY"); sh(f"docker rm -f {c}"); sys.exit(5)
    (t / "prompt.md").write_text("下面是一个真实仓库里的 issue。仓库已经在 /testbed,请直接修改源码解决它。\n只改实现代码,不要改测试文件。完成后不需要提交,把文件改好即可。\n\n" + (row["problem_statement"] or ""), encoding="utf-8")
    (t / ".k").write_text(key, encoding="utf-8")
    sh(f"docker cp {t}/prompt.md {c}:/tmp/.prompt.md"); sh(f"docker cp {t}/.k {c}:/tmp/.k")
    ta = time.time()
    r2 = sh(f"docker exec {c} bash -c 'bash /opt/ockit/{OCS} /testbed llmgateway {a.base} /tmp/.k {a.model} /tmp/.prompt.md /rout/oc {a.timeout}; rm -f /tmp/.k /tmp/.prompt.md'", timeout=a.timeout + 900)
    agent_s = int(time.time() - ta)
    od = pathlib.Path(f"agent_{a.instance}_{a.tag}_openclaw"); od.mkdir(exist_ok=True)
    (od / "driver.log").write_text(r2.stdout + r2.stderr, encoding="utf-8"); sh(f"docker cp {c}:/rout/oc {od}/oc")
    print("openclaw rc", r2.returncode, "|", (r2.stdout + r2.stderr)[-200:].replace(chr(10), " "))
elif a.arm == "agent":
    # 与容器版执行器同一套:kit 拷进容器,DSH 在 /testbed 里干活;key 走文件不走命令行(㊴)
    key = os.environ.get("ENVSHIFT_API_KEY", "")
    if not key: print("NO-API-KEY"); sh(f"docker rm -f {c}"); sys.exit(5)
    (t / "prompt.md").write_text("下面是一个真实仓库里的 issue。仓库已经在 /testbed,请直接修改源码解决它。\n只改实现代码,不要改测试文件。完成后不需要提交,把文件改好即可。\n\n" + (row["problem_statement"] or ""), encoding="utf-8")
    (t / ".k").write_text(key, encoding="utf-8")
    sh(f"docker exec {c} sh -c 'mkdir -p /opt/tbkit /tmp/dshrt /tmp/dsh-home /rout'")
    sh(f"docker cp {a.kit}/. {c}:/opt/tbkit"); sh(f"docker cp {t}/prompt.md {c}:/tmp/.prompt.md"); sh(f"docker cp {t}/.k {c}:/tmp/.k")
    sh(f"docker exec {c} sh -c 'cp /opt/tbkit/bridge.mjs /opt/tbkit/cordis.yaml /opt/tbkit/drive_dsh.py /tmp/dshrt/ && ln -sfn /opt/tbkit/node_modules /tmp/dshrt/node_modules && chmod +x /opt/tbkit/node/bin/node'")
    ta = time.time()
    cmd = (f"docker exec -e DSH_NM=/opt/tbkit/node_modules -e DSH_BRIDGE=/tmp/dshrt/bridge.mjs -e DSH_CONFIG=/tmp/dshrt/cordis.yaml "
           f"-e DSH_HOME_DIR=/tmp/dsh-home -e DSH_NODE_BIN=/opt/tbkit/node/bin/node -e DSH_RUN_TIMEOUT={a.timeout} -e DSH_SESSION_ROOT=/tmp/dsh-sessions "
           f"-e DSH_MAX_TOKENS=131072 {c} sh -c \"cd /testbed && PATH=/opt/tbkit/node/bin:\\$PATH timeout {a.timeout + 180} "
           f"python3 /tmp/dshrt/drive_dsh.py /testbed '{a.model}' '{a.base}' \\\"\\$(cat /tmp/.k)\\\" /rout /tmp/.prompt.md > /rout/driver.log 2>&1; rm -f /tmp/.k /tmp/.prompt.md; echo \\$?\"")
    rc = sh(cmd, timeout=a.timeout + 600).stdout.strip(); agent_s = int(time.time() - ta)
    sh(f"docker cp {c}:/rout {t}/rout"); (pathlib.Path(".") / f"agent_{a.instance}_{a.tag}").mkdir(exist_ok=True)
    sh(f"cp -r {t}/rout/. agent_{a.instance}_{a.tag}/")
    dl = pathlib.Path(f"agent_{a.instance}_{a.tag}/driver.log"); print("agent rc", rc, "|", (dl.read_text(errors="replace")[:160].replace(chr(10), " ") if dl.exists() else "无 driver.log"))
    # 断粮/通道空跑标记(与主线 triage 同口径)
    txt = dl.read_text(errors="replace") if dl.exists() else ""
    if any(k in txt for k in ("Insufficient Balance", "RATE_LIMIT", "Too many requests", "TRANSPORT", "MISSING_CREDENTIAL")):
        print("DEAD-RUN", [k for k in ("Insufficient Balance", "RATE_LIMIT", "TRANSPORT", "MISSING_CREDENTIAL") if k in txt])
e = sh(f"docker exec {c} bash /eval.sh", timeout=3000); log = e.stdout + e.stderr
pathlib.Path(f"eval_{a.instance}_{a.arm}_{ts.arch}_u{a.ubuntu}.log").write_text(log, encoding="utf-8")
sh(f"docker rm -f {c}")
import importlib
LP = importlib.import_module("swebench.harness.log_parsers"); parser = getattr(LP, row["log_parser"], None)
if parser is None:
    from swebench.harness.log_parsers import MAP_REPO_TO_PARSER
    parser = MAP_REPO_TO_PARSER[row["repo"]]
try: status = parser(log, row)
except TypeError: status = parser(log)
L = lambda v: json.loads(v) if isinstance(v, str) else list(v)   # parquet 里已是列表,jsonl 里是字符串
f2p = L(row["FAIL_TO_PASS"]); p2p = L(row["PASS_TO_PASS"])
fo = sum(status.get(x) == "PASSED" for x in f2p); po = sum(status.get(x) == "PASSED" for x in p2p)
res = int(fo == len(f2p) and po == len(p2p) and len(f2p) > 0)
print(f"RESULT {a.instance} arm={a.arm} arch={ts.arch} ubuntu={a.ubuntu} resolved={res} f2p={fo}/{len(f2p)} p2p={po}/{len(p2p)} agent_s={agent_s} os_actual={os_actual}")
pathlib.Path(f"rebuild_{a.instance}_{a.arm}_{ts.arch}_u{a.ubuntu}.json").write_text(json.dumps({"fixes": FIXES, "os_actual": os_actual, "instance": a.instance, "arm": a.arm, "arch": ts.arch, "ubuntu": a.ubuntu, "resolved": res, "f2p": f"{fo}/{len(f2p)}", "p2p": f"{po}/{len(p2p)}", "status": status, "build_s": int(time.time()-t0)}), encoding="utf-8")
