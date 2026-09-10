#!/usr/bin/env python3
"""GUI 题跨系统跑题脚本:准备初始状态 → 跑 OpenClaw → 关 Chrome 落盘 → 独立评分器判分。

与代码题共用同一套 OpenClaw 配法(本地 gateway + `openclaw agent`),模型名可换。
Chrome 用远程调试端口 1337 起(三系统同一接口),初始状态(开标签/造历史/注入偏好/下载文件)
全部通过这个端口和本地文件完成,不依赖任何 Linux 专有工具。
用法: gui_run.py <task_id 或 序号> [--arm openclaw|null|list] [--model ...] [--timeout 900]
"""
import argparse, json, os, pathlib, platform, shutil, subprocess, sys, time, urllib.request
for _st in (sys.stdout, sys.stderr):
    try: _st.reconfigure(encoding="utf-8", errors="replace")
    except Exception: pass
HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import gui_grade as G

ap = argparse.ArgumentParser()
ap.add_argument("task"); ap.add_argument("--arm", default="openclaw", choices=["openclaw", "null", "list"])
ap.add_argument("--model", default="deepseek-v4-pro"); ap.add_argument("--base", default="https://api.llmgateway.io/v1")
ap.add_argument("--timeout", type=int, default=900); ap.add_argument("--port", type=int, default=1337)
a = ap.parse_args()
SYS = platform.system(); HOME = pathlib.Path.home()
TASKS = json.load(open(HERE / "gui_tasks.json", encoding="utf-8"))
if a.arm == "list":
    for i, t in enumerate(TASKS, 1): print(i, t["id"], t["app"], t["instruction"][:70])
    sys.exit(0)
if a.task.isdigit():                      # 纯数字 = 序号;别拿它去做 id 前缀匹配("3" 会撞上 "35253b65…")
    task = TASKS[int(a.task) - 1]
else:
    task = next(t for t in TASKS if t["id"] == a.task or t["id"].startswith(a.task))
log = lambda *x: print("[gui]", *x, flush=True)
OUTD = pathlib.Path("gui_out"); OUTD.mkdir(exist_ok=True)


# ── Chrome 生命周期(唯一按平台分支的地方) ─────────────────────────────────────
def chrome_bin():
    if SYS == "Darwin":
        return "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
    if SYS == "Windows":
        for c in (r"C:\Program Files\Google\Chrome\Application\chrome.exe", r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe"):
            if pathlib.Path(c).exists(): return c
        return "chrome.exe"
    return shutil.which("google-chrome") or shutil.which("google-chrome-stable") or "google-chrome"


def chrome_start(url="about:blank"):
    """按探路时验证过的方式起 Chrome(经 shell、后台),把它的 stdout/stderr 留到 gui_out/chrome.log 供排障。"""
    udd = G.chrome_user_data_dir(); udd.mkdir(parents=True, exist_ok=True)
    # Chrome 136+:默认数据目录不开放远程调试端口,必须显式给一个数据目录(放在系统约定根目录下,见 gui_grade)
    flags = f'--user-data-dir="{udd}" --remote-debugging-port={a.port} --remote-allow-origins=* --no-first-run --no-default-browser-check {url}'
    env = dict(os.environ); env.setdefault("DISPLAY", ":99")
    logf = open(OUTD / "chrome.log", "a")
    if SYS == "Windows":
        cmd = f'start "" "{chrome_bin()}" {flags}'
        subprocess.Popen(cmd, shell=True, env=env, stdout=logf, stderr=logf)
    elif SYS == "Darwin":
        subprocess.Popen(f'"{chrome_bin()}" {flags} &', shell=True, env=env, stdout=logf, stderr=logf)
    else:
        subprocess.Popen(f'{chrome_bin()} --no-sandbox --disable-gpu --disable-dev-shm-usage {flags} &', shell=True, env=env, stdout=logf, stderr=logf)
    for i in range(60):
        if G._cdp_tabs(a.port) is not None:
            log(f"Chrome 远程调试端口 {a.port} 就绪({i}s)"); return True
        time.sleep(1)
    try: log("chrome.log 尾部:", (OUTD / "chrome.log").read_text(errors="replace")[-600:].replace("\n", " ⏎ "))
    except Exception: pass
    return False


def chrome_stop():
    if SYS == "Windows": subprocess.run(["taskkill", "/F", "/IM", "chrome.exe"], capture_output=True)
    elif SYS == "Darwin": subprocess.run(["pkill", "-f", "Google Chrome"], capture_output=True)
    else: subprocess.run(["pkill", "-f", "chrome"], capture_output=True)
    time.sleep(3)   # 等偏好/书签落盘


def cdp(path, method="GET"):
    req = urllib.request.Request(f"http://127.0.0.1:{a.port}{path}", method=method)
    with urllib.request.urlopen(req, timeout=10) as r:
        body = r.read().decode("utf-8", "replace")
        return json.loads(body) if body.strip().startswith(("{", "[")) else body


# ── 初始状态 ────────────────────────────────────────────────────────────────
def setup(s):
    # 真实桌面系统都有 ~/Desktop(Linux 由 xdg-user-dirs 建);CI 的 Linux runner 没有,补上以对齐真实环境
    G.desktop_dir().mkdir(parents=True, exist_ok=True)
    if s.get("inject_prefs"):                        # 要在 Chrome 关着时改 Preferences
        chrome_start(); time.sleep(3); chrome_stop()
        p = G.chrome_profile() / "Preferences"; d = G._load_json(p) or {}
        for k, v in s["inject_prefs"].items(): d.setdefault(k, {}).update(v) if isinstance(v, dict) else d.__setitem__(k, v)
        p.parent.mkdir(parents=True, exist_ok=True); p.write_text(json.dumps(d), encoding="utf-8")
        log("已注入偏好:", s["inject_prefs"])
    for f in s.get("download_to_desktop", []):
        url = f.get("url") if isinstance(f, dict) else f
        if not url: continue
        dest = G.desktop_dir() / (f.get("path", url).split("/")[-1] if isinstance(f, dict) else url.split("/")[-1])
        dest.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(["curl", "-sL", "-o", str(dest), url], check=False); log("已下载到桌面:", dest.name)
    ok = chrome_start(); log("Chrome 起来了" if ok else "SETUP-FAIL Chrome 远程调试端口没起来")
    global SETUP_OK; SETUP_OK = ok
    for u in s.get("open_tabs", []):
        try: cdp("/json/new?" + u, "PUT"); time.sleep(2)
        except Exception as e: log("开标签失败", u, e)
    if s.get("wait"): time.sleep(s["wait"])
    if s.get("close_all") or s.get("close_last"):
        tabs = [t for t in (G._cdp_tabs(a.port) or []) if t.get("url") != "about:blank"]
        victims = tabs if s.get("close_all") else tabs[-1:]
        for t in victims:
            try: cdp("/json/close/" + t["id"])
            except Exception: pass
        time.sleep(2)
        if s.get("close_all"):
            try: cdp("/json/new?about:blank", "PUT")
            except Exception: pass
        log("关掉标签", len(victims), "个")


# ── agent ───────────────────────────────────────────────────────────────────
def run_agent(instruction):
    key = os.environ.get("ENVSHIFT_API_KEY", "")
    if not key: log("NO-API-KEY"); return {"rc": -1}
    st = HOME / "oc-state-gui"; oh = HOME / "oc-home-gui"; ws = HOME / "gui-workspace"; outd = OUTD
    for d in (st, oh, ws, outd): d.mkdir(parents=True, exist_ok=True)
    cfg = {"models": {"providers": {"llmgateway": {"baseUrl": a.base, "apiKey": key, "api": "openai-completions", "models": [{"id": a.model, "name": a.model}]}}},
           "agents": {"defaults": {"workspace": str(ws), "model": {"primary": f"llmgateway/{a.model}"}, "models": {f"llmgateway/{a.model}": {"alias": a.model}}}},
           "gateway": {"mode": "local", "bind": "loopback", "port": 18789, "auth": {"mode": "token"}},
           "tools": {"deny": ["web_search", "web_fetch", "browser"]}}
    (st / "openclaw.json").write_text(json.dumps(cfg), encoding="utf-8")
    env = dict(os.environ, HOME=str(oh), USERPROFILE=str(oh), OPENCLAW_STATE_DIR=str(st), OPENCLAW_CONFIG_PATH=str(st / "openclaw.json"),
               OPENCLAW_CONFIG=str(st / "openclaw.json"), OPENCLAW_WORKSPACE_DIR=str(ws), OPENCLAW_GATEWAY_TOKEN=os.urandom(24).hex(),
               OPENCLAW_EXEC_SHELL_SNAPSHOT="off", NO_PROXY="127.0.0.1,localhost", no_proxy="127.0.0.1,localhost")
    env.setdefault("DISPLAY", ":99")
    oc = shutil.which("openclaw") or shutil.which("openclaw.cmd") or "openclaw"
    # 题面 = 原指令 + 一句环境说明,三系统一字不差;不告诉它端口、路径、命令,让它自己发现
    prompt = ("You are operating a real desktop computer. Google Chrome is currently running on this machine, "
              "and Visual Studio Code is installed. Complete the following request for the user, then stop.\n\n" + instruction)
    gw = subprocess.Popen([oc, "gateway", "run", "--bind", "loopback", "--port", "18789", "--auth", "token"],
                          stdout=open(outd / "gateway.log", "w"), stderr=subprocess.STDOUT, env=env, cwd=str(ws))
    import socket
    for _ in range(90):
        if gw.poll() is not None: break
        try: socket.create_connection(("127.0.0.1", 18789), timeout=1).close(); break
        except OSError: time.sleep(1)
    t0 = time.time()
    ag = subprocess.run([oc, "agent", "--session-id", f"gui-{os.getpid()}", "--message", prompt, "--thinking", "off",
                         "--timeout", str(a.timeout), "--json"], capture_output=True, text=True, env=env, cwd=str(ws), timeout=a.timeout + 300)
    (outd / "agent.json").write_text(ag.stdout, encoding="utf-8"); (outd / "agent.stderr").write_text(ag.stderr, encoding="utf-8")
    gw.kill()
    if SYS == "Windows":
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(gw.pid)], capture_output=True)
    try: shutil.copytree(st, outd / "oc-state", dirs_exist_ok=True); (outd / "oc-state" / "openclaw.json").unlink(missing_ok=True)
    except Exception: pass
    return {"rc": ag.returncode, "agent_s": int(time.time() - t0)}


# ── 主流程 ──────────────────────────────────────────────────────────────────
t0 = time.time()
SETUP_OK = False
log("平台", platform.platform(), "| 题", task["id"][:8], task["app"], "|", task["instruction"][:70])
log("Chrome 数据目录:", G.chrome_user_data_dir())
setup(task.get("setup", {}))
res = {"rc": 0, "agent_s": 0}
if a.arm == "openclaw":
    res = run_agent(task["instruction"])
# 判分前:标签页类判据要在 Chrome 还开着时读;其余判据要先关 Chrome 让偏好落盘
g = task["grade"]
if g["func"] in ("is_expected_tabs",):
    ok, why = G.FUNCS[g["func"]](g["args"]); chrome_stop()
else:
    chrome_stop(); ok, why = G.FUNCS[g["func"]](g["args"])
out = {"instance": f"gui__{task['app']}-{task['id'][:8]}", "task_id": task["id"], "arm": a.arm, "platform": platform.platform(),
       "resolved": int(ok), "setup_ok": SETUP_OK, "grade": g["func"], "why": why, "agent_rc": res.get("rc"), "agent_s": res.get("agent_s"),
       "total_s": int(time.time() - t0), "model": a.model}
print(f"RESULT {out['instance']} arm={a.arm} platform={platform.system()}-{platform.machine()} resolved={out['resolved']} setup_ok={int(SETUP_OK)} grade={g['func']} | {why[:120]}")
pathlib.Path(f"gui_{platform.system()}_{platform.machine()}.json").write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
