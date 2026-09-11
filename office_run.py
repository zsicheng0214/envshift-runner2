#!/usr/bin/env python3
"""办公题(LibreOffice 文档类)跨系统跑题脚本。

题源:OSWorld 的 libreoffice_calc / impress / writer。
判分:**直接用 OSWorld 官方的 metrics 函数**(compare_table / compare_pptx_files / compare_docx_files …),
     它们是纯文件比对(openpyxl / python-pptx / python-docx),不绑虚拟机。我们只负责把文件放到位。
移植:官方定义里唯一的平台依赖是 `/home/user/X.xlsx` 这类路径,以及判分前用 GUI 快捷键保存的 postconfig。
     这里把路径换成本机家目录(三系统各异,这正是接触面),并去掉 GUI 保存——agent 直接编辑文件本身。

用法: office_run.py <序号或 task_id> [--arm openclaw|null|list] [--model ...] [--timeout 1200]
"""
import argparse, json, os, pathlib, platform, shutil, subprocess, sys, time, urllib.request
for _st in (sys.stdout, sys.stderr):
    try: _st.reconfigure(encoding="utf-8", errors="replace")
    except Exception: pass
HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

ap = argparse.ArgumentParser()
ap.add_argument("task"); ap.add_argument("--arm", default="openclaw", choices=["openclaw", "null", "gold", "list"])
ap.add_argument("--model", default="deepseek-v4-pro"); ap.add_argument("--base", default="https://api.llmgateway.io/v1")
ap.add_argument("--timeout", type=int, default=1200)
a = ap.parse_args()
SYS = platform.system(); HOME = pathlib.Path.home()
TASKS = json.load(open(HERE / "office_tasks.json", encoding="utf-8"))
if a.arm == "list":
    for i, t in enumerate(TASKS, 1): print(i, t["id"][:8], t["app"], t["grade"]["func"], t["instruction"][:60])
    sys.exit(0)
task = TASKS[int(a.task) - 1] if a.task.isdigit() else next(t for t in TASKS if t["id"].startswith(a.task))
log = lambda *x: print("[office]", *x, flush=True)
OUTD = pathlib.Path("office_out"); OUTD.mkdir(exist_ok=True)
WORK = HOME / "office-work"; WORK.mkdir(parents=True, exist_ok=True)


def fetch(url, dest):
    dest.parent.mkdir(parents=True, exist_ok=True)
    for _ in range(3):
        r = subprocess.run(["curl", "-sL", "--fail", "-m", "180", "-o", str(dest), url])
        if r.returncode == 0 and dest.exists() and dest.stat().st_size > 0: return True
        time.sleep(5)
    return False


def official_metric(name):
    """按名字取 OSWorld 官方判分函数(osw_metrics_pkg/ 是原样 vendored 的官方模块)。
    ★别吞导入异常:找不到函数时必须说清是哪个模块、因为什么没导进来(缺依赖 vs 真没这个函数)。"""
    sys.path.insert(0, str(HERE / "osw_metrics_pkg"))
    errs = []
    for mod in ("table", "slides", "general", "pdf", "libreoffice", "others", "basic_os", "docs"):
        try:
            m = __import__(f"desktop_env.evaluators.metrics.{mod}", fromlist=["x"])
        except Exception as e:
            errs.append(f"{mod}: {type(e).__name__}: {str(e)[:120]}"); continue
        if hasattr(m, name): return getattr(m, name)
        errs.append(f"{mod}: 导入成功但没有 {name}")
    log("判分模块导入情况:")
    for e in errs: log("   ", e)
    raise SystemExit(f"官方判分函数找不到: {name}")


# ── 初始状态:把题目文件放进工作目录(路径随系统) ────────────────────────────────
t0 = time.time()
log("平台", platform.platform(), "| 题", task["id"][:8], task["app"], "|", task["instruction"][:70])
files = []
for f in task["files"]:
    dest = WORK / f["name"]
    ok = fetch(f["url"], dest)
    log(("已就位 " if ok else "★下载失败 ") + str(dest), dest.stat().st_size if dest.exists() else 0, "字节")
    if not ok: print(f"SETUP-FAIL 题目文件下载失败 {f['url']}"); sys.exit(4)
    files.append(dest)
SETUP_OK = True

# ── agent ───────────────────────────────────────────────────────────────────
def openclaw_cmd():
    node = shutil.which("node")
    for r in subprocess.run(["npm", "root", "-g"], capture_output=True, text=True, shell=(SYS == "Windows")).stdout.split():
        mjs = pathlib.Path(r) / "openclaw" / "openclaw.mjs"
        if node and mjs.exists(): return [node, str(mjs)]
    return [shutil.which("openclaw") or shutil.which("openclaw.cmd") or "openclaw"]


res = {"rc": 0, "agent_s": 0}
if a.arm == "gold":
    # 量具自检:把官方参考文件当成 agent 的产出,判分应当满分。不满分说明判分链路本身有问题,
    # 那样再跑 agent 是白跑(「agent 做对了也判 0」这种坑必须先排掉)。
    ref0 = WORK / "_goldcopy"
    if not fetch(task["grade"]["expected_url"], ref0): print("SETUP-FAIL 参考文件下载失败"); sys.exit(4)
    shutil.copy(ref0, WORK / task["grade"].get("result_file", task["files"][0]["name"]))
    log("gold 自检:已用官方参考文件覆盖结果文件")
elif a.arm == "openclaw":
    key = os.environ.get("ENVSHIFT_API_KEY", "")
    if not key: print("NO-API-KEY"); sys.exit(5)
    st = HOME / "oc-state-office"; st.mkdir(parents=True, exist_ok=True)
    cfg = {"models": {"providers": {"llmgateway": {"baseUrl": a.base, "apiKey": key, "api": "openai-completions", "models": [{"id": a.model, "name": a.model}]}}},
           "agents": {"defaults": {"workspace": str(WORK), "model": {"primary": f"llmgateway/{a.model}"}, "models": {f"llmgateway/{a.model}": {"alias": a.model}}}},
           "gateway": {"mode": "local", "bind": "loopback", "port": 18789, "auth": {"mode": "token"}},
           "tools": {"deny": ["web_search", "web_fetch", "browser"]}}
    (st / "openclaw.json").write_text(json.dumps(cfg), encoding="utf-8")
    env = dict(os.environ, OPENCLAW_STATE_DIR=str(st), OPENCLAW_CONFIG_PATH=str(st / "openclaw.json"), OPENCLAW_CONFIG=str(st / "openclaw.json"),
               OPENCLAW_WORKSPACE_DIR=str(WORK), OPENCLAW_GATEWAY_TOKEN=os.urandom(24).hex(), OPENCLAW_EXEC_SHELL_SNAPSHOT="off",
               NO_PROXY="127.0.0.1,localhost", no_proxy="127.0.0.1,localhost")
    oc = openclaw_cmd()
    names = "、".join(f["name"] for f in task["files"])
    prompt = (f"There is an office document to edit on this computer: {names}, in the folder {WORK}. "
              f"Edit the file in place so that it satisfies the request below, and save it in the same format at the same path.\n\n"
              + task["instruction"])
    gw = subprocess.Popen(oc + ["gateway", "run", "--bind", "loopback", "--port", "18789", "--auth", "token"],
                          stdout=open(OUTD / "gateway.log", "w"), stderr=subprocess.STDOUT, env=env, cwd=str(WORK))
    import socket
    for _ in range(90):
        if gw.poll() is not None: break
        try: socket.create_connection(("127.0.0.1", 18789), timeout=1).close(); break
        except OSError: time.sleep(1)
    ta = time.time()
    ag = subprocess.run(oc + ["agent", "--session-id", f"office-{os.getpid()}", "--message", prompt, "--thinking", "off",
                              "--timeout", str(a.timeout), "--json"], capture_output=True, text=True, env=env, cwd=str(WORK), timeout=a.timeout + 300)
    (OUTD / "agent.json").write_text(ag.stdout, encoding="utf-8"); (OUTD / "agent.stderr").write_text(ag.stderr, encoding="utf-8")
    gw.kill()
    if SYS == "Windows":
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(gw.pid)], capture_output=True)
    res = {"rc": ag.returncode, "agent_s": int(time.time() - ta)}
    log("agent rc", ag.returncode, "用时", res["agent_s"], "s")
    try: shutil.copytree(st, OUTD / "oc-state", dirs_exist_ok=True); (OUTD / "oc-state" / "openclaw.json").unlink(missing_ok=True)
    except Exception: pass

# ── 判分:官方函数 ────────────────────────────────────────────────────────────
ref = None
if task["grade"].get("expected_url"):
    ref = WORK / ("_ref_" + task["files"][0]["name"])
    if not fetch(task["grade"]["expected_url"], ref): print("GRADE-FAIL 参考文件下载失败"); sys.exit(4)
fn = official_metric(task["grade"]["func"])
target = WORK / task["grade"].get("result_file", task["files"][0]["name"])
try:
    score = fn(str(target), str(ref), **(task["grade"].get("options") or {})) if ref else fn(str(target), **(task["grade"].get("options") or {}))
except Exception as e:
    score = 0.0; log("判分抛异常:", type(e).__name__, str(e)[:160])
ok = int(float(score) >= 1.0)
out = {"instance": f"office__{task['app']}-{task['id'][:8]}", "task_id": task["id"], "arm": a.arm, "platform": platform.platform(),
       "resolved": ok, "score": float(score), "setup_ok": SETUP_OK, "grade": task["grade"]["func"],
       "agent_rc": res.get("rc"), "agent_s": res.get("agent_s"), "total_s": int(time.time() - t0), "model": a.model}
print(f"RESULT {out['instance']} arm={a.arm} platform={platform.system()}-{platform.machine()} resolved={ok} score={score} setup_ok=1 grade={task['grade']['func']}")
pathlib.Path(f"office_{platform.system()}_{platform.machine()}.json").write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
