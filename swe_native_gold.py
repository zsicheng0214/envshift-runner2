#!/usr/bin/env python3
"""macOS / Windows 上原生跑 SWE-bench 实例(无 Docker):
   用官方 v4.0.4 的 make_test_spec 拿到官方三段脚本(setup_env / install_repo / eval),只把路径从
   /testbed、/opt/miniconda3 换成本机路径,其余一字不改;gold 补丁 + 官方 parser 判分。
   用法: swe_native_gold.py <instance_id> [--arm gold|null]"""
import argparse, json, os, pathlib, platform, re, shutil, subprocess, sys, tempfile, time, urllib.request
for _st in (sys.stdout, sys.stderr):
    try: _st.reconfigure(encoding="utf-8", errors="replace")   # Windows 控制台 cp1252 印中文会崩(老坑)
    except Exception: pass
ap = argparse.ArgumentParser(); ap.add_argument("instance"); ap.add_argument("--arm", default="gold", choices=["gold", "openclaw", "null"])
ap.add_argument("--model", default="deepseek-v4-pro"); ap.add_argument("--base", default="https://api.llmgateway.io/v1"); ap.add_argument("--timeout", type=int, default=1800)
a = ap.parse_args()
HOME = pathlib.Path.home(); TB = HOME / "testbed"; CONDA = pathlib.Path(os.environ.get("CONDA", "")) if os.environ.get("CONDA") else HOME / "miniconda3"
def _bash():
    # Windows 上裸 `bash` 会解析到 WSL 启动器(没装发行版就报错);必须显式用 Git Bash(跨 OS 那轮的老坑)
    if os.name == "nt":
        for c in (r"C:\Program Files\Git\bin\bash.exe", r"C:\Program Files\Git\usr\bin\bash.exe"):
            if pathlib.Path(c).exists(): return c
    return "bash"
def sh(c, **k): return subprocess.run([_bash(), "-lc", c], capture_output=True, text=True, **k)
import pyarrow.parquet as pq
def _find_row(iid):
    """先在 Verified(500 道,6MB)里找;找不到再拉完整版(2294 道,33MB)。
    ★接触面达标的题多数只存在于完整版里,只读 Verified 会让它们全部报「没有这道题」。"""
    import subprocess
    for name, url in (("swe_verified.parquet", "https://huggingface.co/datasets/SWE-bench/SWE-bench_Verified/resolve/main/data/test-00000-of-00001.parquet"), ("swe_full.parquet", "https://huggingface.co/datasets/SWE-bench/SWE-bench/resolve/main/data/test-00000-of-00001.parquet")):
        f = pathlib.Path(tempfile.gettempdir()) / name
        if not f.exists():
            subprocess.run(["curl", "-sL", "-o", str(f), url], check=True)
        for r in pq.read_table(f).to_pylist():
            if r["instance_id"] == iid: return r
    return None
row = _find_row(a.instance)
if row is None: raise SystemExit("没有这道题: " + a.instance)
if os.name == "nt":
    # 官方 swebench 包自己 import resource(Unix 专有),Windows 上连导入都过不去;塞一个空垫片,不改任何任务/判分逻辑
    import types; _r = types.ModuleType("resource"); _r.getrlimit = lambda *a, **k: (0, 0); _r.setrlimit = lambda *a, **k: None; _r.RLIMIT_NOFILE = 7; sys.modules["resource"] = _r
from spec_fixes import apply_spec_fixes, retry_variant
PLAT = "win" if os.name == "nt" else ("mac" if platform.system() == "Darwin" else "linux")
FIXES = apply_spec_fixes(PLAT)
print("适配层:", FIXES or "(本题无需适配)")
from swebench.harness.test_spec.test_spec import make_test_spec
ts = make_test_spec(row)
def adapt(script):
    s = script.replace("/testbed", str(TB).replace("\\", "/")).replace("/opt/miniconda3", str(CONDA).replace("\\", "/"))
    gnu = os.environ.get("ENVSHIFT_GNUBIN")
    if gnu:
        # macOS 的登录 shell(bash -lc)会跑 path_helper 重排 PATH,把 /usr/bin 顶到最前,
        # GITHUB_PATH 里加的 GNU 工具目录会被冲掉 —— 必须在脚本内部再置一次。
        s = f'export PATH="{gnu}:$PATH"\n' + s
    s = s.replace("source /root/.bashrc", ":")
    if os.name == "nt":
        # Windows 的 miniconda 没有 bin/activate;Git Bash 下的官方入口是 etc/profile.d/conda.sh(路径适配,不动逻辑)
        c = str(CONDA).replace("\\", "/")
        s = s.replace(f"source {c}/bin/activate", f"source {c}/etc/profile.d/conda.sh")
    return s
print("平台:", platform.platform(), platform.machine(), "| conda:", CONDA, "| python 规格:", re.search(r"python=([\d.]+)", ts.setup_env_script or "").group(1) if re.search(r"python=([\d.]+)", ts.setup_env_script or "") else "?")
t0 = time.time()
# 1) 官方 env 脚本(conda create + 依赖)
r = sh(adapt(ts.setup_env_script)); (pathlib.Path("native_setup_env.log")).write_text(r.stdout + r.stderr, encoding="utf-8")
if r.returncode != 0 and PLAT == "mac" and re.search(r"PackagesNotFound|Unsatisfiable|does not exist", r.stdout + r.stderr):
    # B 类适配:苹果芯片的软件仓库没有老版本包(python<=3.7 / scipy<1.7 等),换成 x86 包走 Rosetta 转译,
    # 依赖版本与 Linux 完全一致,只是指令集不同;记进结果 json 备查。
    print("B:osx-arm64 缺老版本包,改用 osx-64 包(Rosetta 转译)重试")
    sh("conda config --env --set subdir osx-64 2>/dev/null; true")
    r = sh("export CONDA_SUBDIR=osx-64\n" + adapt(ts.setup_env_script))
    (pathlib.Path("native_setup_env.log")).write_text(r.stdout + r.stderr, encoding="utf-8")
    if r.returncode == 0: FIXES.append("B:苹果芯片改用 osx-64 包经 Rosetta 转译(依赖版本与 Linux 一致)")
if r.returncode != 0:
    print("ENV-FAIL rc", r.returncode, "|", (r.stdout + r.stderr)[-400:].replace("\n", " ")); sys.exit(4)
print("env 建好 %ds" % (time.time() - t0))
# 2) 官方 repo 脚本(clone + checkout base_commit + install)
if TB.exists(): shutil.rmtree(TB, ignore_errors=True)
r = sh(adapt(ts.install_repo_script)); (pathlib.Path("native_install_repo.log")).write_text(r.stdout + r.stderr, encoding="utf-8")
if r.returncode != 0:
    alt, why = retry_variant(adapt(ts.install_repo_script), r.stdout + r.stderr)
    if alt:
        print("适配重试:", why)
        if TB.exists(): shutil.rmtree(TB, ignore_errors=True)
        r = sh(alt); (pathlib.Path("native_install_repo.log")).write_text(r.stdout + r.stderr, encoding="utf-8")
        if r.returncode == 0: FIXES.append(why)
if r.returncode != 0:
    print("REPO-FAIL rc", r.returncode, "|", (r.stdout + r.stderr)[-400:].replace("\n", " ")); sys.exit(4)
print("repo 装好 %ds" % (time.time() - t0))
# 3) 臂
if a.arm == "gold":
    (TB / ".gold.diff").write_text(row["patch"], encoding="utf-8")
    g = sh(f"cd '{TB}' && git apply -v .gold.diff")
    if g.returncode != 0: print("GOLD-APPLY-FAIL", g.stderr[-300:]); sys.exit(4)
elif a.arm == "openclaw":
    # 原生 OpenClaw(npm -g 装在 runner 上):与容器版 oc_agent.sh 同一套配法,workspace=本机 testbed
    key = os.environ.get("ENVSHIFT_API_KEY", "")
    if not key: print("NO-API-KEY"); sys.exit(5)
    st = HOME / "oc-state"; oh = HOME / "oc-home"; outd = pathlib.Path("oc_out"); [d.mkdir(parents=True, exist_ok=True) for d in (st, oh, outd)]
    ws = str(TB).replace("\\", "/")
    cfg = {"models": {"providers": {"llmgateway": {"baseUrl": a.base, "apiKey": key, "api": "openai-completions", "models": [{"id": a.model, "name": a.model}]}}},
           "agents": {"defaults": {"workspace": ws, "model": {"primary": f"llmgateway/{a.model}"}, "models": {f"llmgateway/{a.model}": {"alias": a.model}}}},
           "gateway": {"mode": "local", "bind": "loopback", "port": 18789, "auth": {"mode": "token"}},
           "tools": {"deny": ["web_search", "web_fetch", "browser"]}}
    (st / "openclaw.json").write_text(json.dumps(cfg), encoding="utf-8")
    # 净化(与容器版 oc_agent2.sh 一致):修剪 git 历史(去掉未来提交/tags)+ agent 阶段屏蔽 github/pypi;判分前恢复。ENVSHIFT_SANITIZE=0 可关(仅调试)
    SAN = os.environ.get("ENVSHIFT_SANITIZE", "1") != "0"
    HERE = pathlib.Path(__file__).resolve().parent
    HOSTS = "/c/Windows/System32/drivers/etc/hosts" if os.name == "nt" else "/etc/hosts"
    SUDO = "" if os.name == "nt" else "sudo -n "
    if SAN:
        z = sh(f"bash '{HERE / 'sanitize_testbed.sh'}' '{ws}'"); (outd / "sanitize.log").write_text(z.stdout + z.stderr, encoding="utf-8")
        print((z.stdout + z.stderr).strip()[-300:])
        if z.returncode != 0: print("SANITIZE-FAIL"); sys.exit(9)
        z = sh(f"{SUDO}bash '{HERE / 'netblock.sh'}' on '{HOSTS}'; curl -sS -m 6 -o /dev/null -w 'selfcheck raw=%{{http_code}}' https://raw.githubusercontent.com/ 2>&1 | tail -c 60; echo; curl -sS -m 6 -o /dev/null -w 'selfcheck pypi=%{{http_code}}' https://pypi.org/simple/ 2>&1 | tail -c 60")
        (outd / "sanitize.log").open("a", encoding="utf-8").write(z.stdout + z.stderr); print((z.stdout + z.stderr).strip()[-300:])
    token = os.urandom(24).hex()
    env = dict(os.environ, HOME=str(oh), USERPROFILE=str(oh), OPENCLAW_STATE_DIR=str(st), OPENCLAW_CONFIG_PATH=str(st / "openclaw.json"), OPENCLAW_CONFIG=str(st / "openclaw.json"),
               OPENCLAW_WORKSPACE_DIR=ws, OPENCLAW_GATEWAY_TOKEN=token, OPENCLAW_EXEC_SHELL_SNAPSHOT="off", NO_PROXY="127.0.0.1,localhost", no_proxy="127.0.0.1,localhost")
    # ★Windows 上 `openclaw` 是 .cmd 壳,多行题面在第一个换行被 cmd.exe 截断 → 改用 node 直接跑 openclaw.mjs(三系统同一路径)
    oc = None
    _node = shutil.which("node")
    for _r in subprocess.run(["npm", "root", "-g"], capture_output=True, text=True, shell=(os.name == "nt")).stdout.split():
        _m = pathlib.Path(_r) / "openclaw" / "openclaw.mjs"
        if _node and _m.exists(): oc = [_node, str(_m)]; break
    if oc is None: oc = [shutil.which("openclaw") or shutil.which("openclaw.cmd") or "openclaw"]; print("⚠ 没找到 openclaw.mjs,退回", oc)
    prompt = "下面是一个真实仓库里的 issue。仓库已经在 " + ws + ",请直接修改源码解决它。\n只改实现代码,不要改测试文件。完成后不需要提交,把文件改好即可。\n\n" + (row["problem_statement"] or "")
    gw = subprocess.Popen(oc + ["gateway", "run", "--bind", "loopback", "--port", "18789", "--auth", "token"], stdout=open(outd / "gateway.log", "w"), stderr=subprocess.STDOUT, env=env, cwd=str(TB))
    import socket
    ok = False
    for _ in range(90):
        if gw.poll() is not None: break
        try: socket.create_connection(("127.0.0.1", 18789), timeout=1).close(); ok = True; break
        except OSError: time.sleep(1)
    if not ok: print("GATEWAY-NOT-READY", open(outd / "gateway.log", errors="replace").read()[-400:].replace("\n", " ")); gw.kill(); sys.exit(3)
    ta = time.time()
    ag = subprocess.run(oc + ["agent", "--session-id", f"envshift-{os.getpid()}", "--message", prompt, "--thinking", "off", "--timeout", str(a.timeout), "--json"],
                        capture_output=True, text=True, env=env, cwd=str(TB), timeout=a.timeout + 300)
    (outd / "agent.json").write_text(ag.stdout, encoding="utf-8"); (outd / "agent.stderr").write_text(ag.stderr, encoding="utf-8")
    print("openclaw agent rc", ag.returncode, "elapsed", int(time.time() - ta), "s |", ag.stdout[:200].replace("\n", " "))
    gw.kill()
    if os.name == "nt":   # Windows 上 Popen.kill 只杀 openclaw.cmd 壳,node 子进程还攥着 gateway.log(收尾 rm 报 Device or resource busy)
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(gw.pid)], capture_output=True)
        subprocess.run(["taskkill", "/F", "/IM", "node.exe"], capture_output=True)
    if SAN:
        z = sh(f"{SUDO}bash '{HERE / 'netblock.sh'}' off '{HOSTS}'"); (outd / "sanitize.log").open("a", encoding="utf-8").write(z.stdout + z.stderr); print((z.stdout + z.stderr).strip()[-120:])
    try: shutil.copytree(st, outd / "oc-state", dirs_exist_ok=True); (outd / "oc-state" / "openclaw.json").unlink(missing_ok=True)
    except Exception: pass
    try: shutil.copy(outd / "sanitize.log", "native_sanitize.log")
    except Exception: pass
# 4) 官方 eval 脚本(改路径不改逻辑)
e = sh(adapt(row["eval_script"]), timeout=3000); log = e.stdout + e.stderr
pathlib.Path("native_eval.log").write_text(log, encoding="utf-8")
import importlib; LP = importlib.import_module("swebench.harness.log_parsers")
parser = getattr(LP, row["log_parser"], None) or LP.MAP_REPO_TO_PARSER[row["repo"]]
try: status = parser(log, row)
except TypeError: status = parser(log)
L = lambda v: json.loads(v) if isinstance(v, str) else list(v)
f2p, p2p = L(row["FAIL_TO_PASS"]), L(row["PASS_TO_PASS"])
fo = sum(status.get(x) == "PASSED" for x in f2p); po = sum(status.get(x) == "PASSED" for x in p2p)
res = int(fo == len(f2p) and po == len(p2p) and len(f2p) > 0)
print(f"RESULT {a.instance} arm={a.arm} platform={platform.system()}-{platform.machine()} resolved={res} f2p={fo}/{len(f2p)} p2p={po}/{len(p2p)} total_s={int(time.time()-t0)}")
pathlib.Path(f"native_{platform.system()}_{platform.machine()}.json").write_text(json.dumps({"fixes": FIXES, "instance": a.instance, "arm": a.arm, "platform": platform.platform(), "resolved": res, "f2p": f"{fo}/{len(f2p)}", "p2p": f"{po}/{len(p2p)}", "status": status}), encoding="utf-8")
