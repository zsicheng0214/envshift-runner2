#!/usr/bin/env python3
"""GUI 题跨系统跑题脚本:准备初始状态 → 跑 OpenClaw → 关 Chrome 落盘 → 独立评分器判分。

与代码题共用同一套 OpenClaw 配法(本地 gateway + `openclaw agent`),模型名可换。
Chrome 用远程调试端口 1337 起(三系统同一接口),初始状态(开标签/造历史/注入偏好/下载文件)
全部通过这个端口和本地文件完成,不依赖任何 Linux 专有工具。
用法: gui_run.py <task_id 或 序号> [--arm openclaw|null|list] [--model ...] [--timeout 900]
"""
import argparse, json, os, pathlib, platform, re, shutil, subprocess, sys, time, urllib.request
for _st in (sys.stdout, sys.stderr):
    try: _st.reconfigure(encoding="utf-8", errors="replace")
    except Exception: pass
HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import gui_grade as G

ap = argparse.ArgumentParser()
ap.add_argument("task"); ap.add_argument("--arm", default="openclaw", choices=["openclaw", "gui", "gui-selfcheck", "null", "list"])
ap.add_argument("--model", default="deepseek-v4-pro"); ap.add_argument("--base", default="https://api.llmgateway.io/v1")
ap.add_argument("--timeout", type=int, default=900); ap.add_argument("--port", type=int, default=1337)
ap.add_argument("--suite", default="desk", choices=["desk", "gui30"], help="desk=15 道应用状态;gui30=30 道(含 15 道办公文档,GUI 通道用)")
a = ap.parse_args()
SYS = platform.system(); HOME = pathlib.Path.home()
TASKS = json.load(open(HERE / ("gui30_tasks.json" if a.suite == "gui30" else "gui_tasks.json"), encoding="utf-8"))
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
    """三系统一律先温和退出(让 Chrome 把偏好/书签写盘),等不到再强杀。
    ★曾经 Windows 用 taskkill /F 直接强杀而 Unix 用 SIGTERM——不对称,会把 Windows 上 agent 在内存里改好的东西丢掉。"""
    if SYS == "Windows":
        subprocess.run(["taskkill", "/IM", "chrome.exe"], capture_output=True)          # WM_CLOSE,温和
        for _ in range(15):
            if subprocess.run(["tasklist", "/FI", "IMAGENAME eq chrome.exe"], capture_output=True, text=True).stdout.count("chrome.exe") == 0: break
            time.sleep(1)
        subprocess.run(["taskkill", "/F", "/IM", "chrome.exe"], capture_output=True)    # 兜底
    else:
        subprocess.run(["pkill", "-TERM", "-f", "Google Chrome" if SYS == "Darwin" else "chrome"], capture_output=True)
        for _ in range(15):
            if subprocess.run(["pgrep", "-f", "Google Chrome" if SYS == "Darwin" else "chrome"], capture_output=True).returncode != 0: break
            time.sleep(1)
        subprocess.run(["pkill", "-KILL", "-f", "Google Chrome" if SYS == "Darwin" else "chrome"], capture_output=True)
    time.sleep(3)   # 等偏好/书签落盘


def cdp(path, method="GET"):
    req = urllib.request.Request(f"http://127.0.0.1:{a.port}{path}", method=method)
    with urllib.request.urlopen(req, timeout=10) as r:
        body = r.read().decode("utf-8", "replace")
        return json.loads(body) if body.strip().startswith(("{", "[")) else body


# ── 初始状态 ────────────────────────────────────────────────────────────────
def soffice_bin():
    """LibreOffice 可执行文件:三系统名字与位置都不同,这本身就是接触面。"""
    if SYS == "Darwin":
        p = pathlib.Path("/Applications/LibreOffice.app/Contents/MacOS/soffice")
        return str(p) if p.exists() else (shutil.which("soffice") or "soffice")
    if SYS == "Windows":
        for c in (r"C:\Program Files\LibreOffice\program\soffice.exe", r"C:\Program Files (x86)\LibreOffice\program\soffice.exe"):
            if pathlib.Path(c).exists(): return c
        return "soffice.exe"
    return shutil.which("soffice") or shutil.which("libreoffice") or "soffice"


# LibreOffice 首次运行的「Did you know?」弹窗和「running for the first time」信息栏会挡住界面、抢焦点,
# 留存截图证实模型头 1~2 步都在关弹窗。用独立 profile 预置关掉,三系统同一做法(-env:UserInstallation 三系统都认)。
LO_XCU = """<?xml version="1.0" encoding="UTF-8"?>
<oor:items xmlns:oor="http://openoffice.org/2001/registry" xmlns:xs="http://www.w3.org/2001/XMLSchema" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">
<item oor:path="/org.openoffice.Office.Common/Misc"><prop oor:name="ShowTipOfTheDay" oor:op="fuse"><value>false</value></prop></item>
<item oor:path="/org.openoffice.Office.Common/Misc"><prop oor:name="FirstRun" oor:op="fuse"><value>false</value></prop></item>
<item oor:path="/org.openoffice.Setup/Product"><prop oor:name="ooSetupLastVersion" oor:op="fuse"><value>99.9</value></prop></item>
<item oor:path="/org.openoffice.Office.Common/Misc"><prop oor:name="ShowWhatsNew" oor:op="fuse"><value>false</value></prop></item>
<item oor:path="/org.openoffice.Office.Common/Misc"><prop oor:name="ShowWelcomeDialog" oor:op="fuse"><value>false</value></prop></item>
<item oor:path="/org.openoffice.Office.Common/Misc"><prop oor:name="ShowDonation" oor:op="fuse"><value>false</value></prop></item>
<item oor:path="/org.openoffice.Office.Common/Save/Document"><prop oor:name="WarnAlienFormat" oor:op="fuse"><value>false</value></prop></item>
<item oor:path="/org.openoffice.Office.Update/Update"><prop oor:name="Enabled" oor:op="fuse"><value>false</value></prop></item>
</oor:items>
"""
# ★最后一项 Update/Enabled=false 是 Windows 第四次自检坐实的:Windows 版 LibreOffice(choco 26.2.3)带 MAR 自动更新器,
#   首次启动就把自己更新掉、弹「LibreOffice Update — Please wait while we update your installation」、然后重启;
#   重启的命令行由 updater.cxx createCommandLine() 用 rtl_getAppCommandArg 重拼,**-env: 引导参数不在其中**,
#   于是更新后的实例用的是默认 profile(收工件:预置 xcu 里 UpdateRunning=true / OfficeRestartInProgress=true / OldBuildID=旧构建号,
#   默认 profile 141KB、LastCompatibilityCheckID 是另一个构建号),首启向导「Welcome to LibreOffice!」随之弹出。
#   app.cxx 里整段更新逻辑由 Office.Update/Update/Enabled 门控,关掉即不更新、不重启、不丢 profile。


# LibreOffice 默认 profile 的位置,三系统各不同。用来判别「它到底用没用我们预置的那份」,也顺手预置一份同样的种子。
LO_DEFAULT_PROFILE = {"Windows": pathlib.Path(os.environ.get("APPDATA") or (HOME / "AppData" / "Roaming")) / "LibreOffice" / "4" / "user",
                      "Darwin": HOME / "Library" / "Application Support" / "LibreOffice" / "4" / "user",
                      "Linux": HOME / ".config" / "libreoffice" / "4" / "user"}


def lo_profile_arg():
    prof = HOME / "lo_profile"; (prof / "user").mkdir(parents=True, exist_ok=True)
    xcu = prof / "user" / "registrymodifications.xcu"
    xcu.write_text(LO_XCU, encoding="utf-8", newline="\n")
    log(f"LibreOffice 预置 profile: {prof.as_uri()} (xcu {xcu.stat().st_size}B)")
    # 默认 profile 也放一份同样的种子(只在它还不存在时):万一哪条路径又绕开 -env:(Windows 更新器重启就是这么丢的),
    # 落到默认 profile 的实例也不弹向导、不弹格式警告。用没用它,收工时按 xcu 项数看得出来(种子只有 9 项)。
    d = LO_DEFAULT_PROFILE.get(SYS)
    try:
        if d and not (d / "registrymodifications.xcu").exists():
            d.mkdir(parents=True, exist_ok=True); (d / "registrymodifications.xcu").write_text(LO_XCU, encoding="utf-8", newline="\n")
            log(f"默认 profile 也预置了同样的种子: {d}")
    except Exception as e:
        log("默认 profile 预置失败", type(e).__name__)
    return f"-env:UserInstallation={prof.as_uri()}"


def lo_profile_evidence():
    """LibreOffice 关掉之后回答一个判别问题:它到底用没用我们预置的 profile?
    用了 → 退出时会把 user/registrymodifications.xcu 整个重写(从 1.3KB 涨到几十 KB、几十项),默认 profile 目录不会出现;
    没用 → 我们的 xcu 纹丝不动,默认 profile 目录(三系统位置不同)被它建出来。
    起因:Windows 自检截图里「Welcome to LibreOffice!」首启向导盖在文档上,A1 没写进去。按 26.2 源码(unotools VersionConfig.cxx)
    这个向导只在 ooSetupLastVersion 键不存在时才弹,而预置里写了 99.9——所以先判 profile 有没有被读,不猜。"""
    xcu = HOME / "lo_profile" / "user" / "registrymodifications.xcu"
    def val(txt, name):
        m = re.search(name + r'"[^>]*>\s*<value>([^<]*)</value>', txt); return m.group(1) if m else "缺"
    try:
        txt = xcu.read_text(encoding="utf-8", errors="replace"); items = txt.count("<item ")
        log(f"预置 profile 收工: xcu {xcu.stat().st_size}B / {items} 项 / ooSetupLastVersion={val(txt, 'ooSetupLastVersion')} → "
            + ("LibreOffice 读了并重写过" if items > 12 else "★纹丝不动,LibreOffice 没读这份 profile"))
        # 更新器有没有跑过:UpdateRunning / OldBuildID / OfficeRestartInProgress 三个键是它留下的脚印(第四次自检坐实的机制)
        log(f"更新器脚印: UpdateRunning={val(txt, 'UpdateRunning')} OldBuildID={val(txt, 'OldBuildID')[:12]} OfficeRestartInProgress={val(txt, 'OfficeRestartInProgress')}"
            + (" → ★更新器跑了并重启过" if val(txt, "UpdateRunning") == "true" or val(txt, "OfficeRestartInProgress") == "true" else " → 没更新没重启"))
    except Exception as e:
        log("预置 profile 收工: 读不到", type(e).__name__)
    d = LO_DEFAULT_PROFILE.get(SYS); dx = (d / "registrymodifications.xcu") if d else None
    if d:
        if dx.exists():
            n = dx.read_text(encoding="utf-8", errors="replace").count("<item ")
            log(f"默认 profile {d}: xcu {dx.stat().st_size}B / {n} 项 → " + ("只有我们预置的种子,LibreOffice 没用它" if n <= 9 else "★被 LibreOffice 重写过 = 有实例用了默认 profile"))
        else:
            log(f"默认 profile {d}: " + ("目录在但没有 xcu" if d.exists() else "不存在(没用默认 profile)"))
    try:
        keep = pathlib.Path(f"gui_state_{platform.system()}"); keep.mkdir(exist_ok=True)
        if xcu.exists(): shutil.copy(xcu, keep / "lo_registrymodifications.xcu")
        if dx and dx.exists(): shutil.copy(dx, keep / "lo_default_registrymodifications.xcu")
    except Exception as e:
        log("profile 留存失败", type(e).__name__)


def lo_wait_and_focus():
    """三系统各自确认 LibreOffice 文档窗口真的起来了、并且拿到了键盘焦点。自检截图证实的坑:
    Windows:choco 装的 LibreOffice 首次启动先跑一个「LibreOffice Update」进度条,主窗口延迟一两分钟才出;
            「画面非黑」判据被 runner 控制台窗口骗过——控制台本来就非黑。要按窗口标题等文档窗口,再 activate 前置。
            ★只按「标题含 LibreOffice」等,会把模态的「Welcome to LibreOffice!」首启向导当成主窗口前置,键盘输入全进向导
            (第三次自检截图证实)。文档窗口标题形如「x.xlsx — LibreOffice Calc」,弹窗单独识别、Esc 关掉、记日志。
    mac:    Popen 起的 LibreOffice 窗口可见但不是活动应用(菜单栏还是 Finder),键盘焦点不在它上——osascript activate。
    Linux:  Xvfb 上只有它一个窗口,非黑判据够用。"""
    if SYS == "Windows":
        try:
            import pyautogui
            DOC = ("LibreOffice Calc", "LibreOffice Impress", "LibreOffice Writer")
            POPUP = ("Welcome to LibreOffice", "Tip of the Day", "What's New", "Keep Current Format", "Keep current format")
            closed = 0
            for i in range(90):
                allw = [w for w in pyautogui.getAllWindows() if any(k in (w.title or "") for k in ("LibreOffice", "Calc", "Impress", "Writer")) and "Update" not in (w.title or "")]
                pop = [w for w in allw if any(k in (w.title or "") for k in POPUP)]
                doc = [w for w in allw if any(k in (w.title or "") for k in DOC)]
                if pop:
                    # 弹窗是模态的,盖在文档窗口上。预置 profile 本该关掉它,真弹出来 = profile 没生效——先关掉保住这一格,
                    # 原因由 lo_profile_evidence() 单独判别,不在这里猜。
                    closed += 1; log(f"★LibreOffice 弹窗挡在前面: {[w.title[:40] for w in pop]} → Esc 关闭(第 {closed} 次)")
                    try: pop[0].activate()
                    except Exception: pass
                    time.sleep(0.5); pyautogui.press("esc"); time.sleep(1.5)
                    if closed >= 3:                      # Esc 关不掉就点它右上角的 ×
                        try: w = pop[0]; pyautogui.click(w.left + w.width - 18, w.top + 16); time.sleep(1.5)
                        except Exception: pass
                    continue
                if doc:
                    try: doc[0].activate()
                    except Exception: pass
                    time.sleep(1); log(f"LibreOffice 文档窗口已出现并前置({i*2}s): {doc[0].title[:50]}" + (f",此前关掉 {closed} 个弹窗" if closed else "")); return True
                if allw and i % 10 == 9: log(f"等文档窗口中({i*2}s),现有 LibreOffice 窗口: {[w.title[:40] for w in allw][:4]}")
                time.sleep(2)
            log("★LibreOffice 文档窗口 180s 内没出现(首次启动的 Update 阶段可能更久)"); return False
        except Exception as e:
            log("窗口检测失败", type(e).__name__); return False
    if SYS == "Darwin":
        for _ in range(3):
            subprocess.run(["osascript", "-e", 'tell application "LibreOffice" to activate'], capture_output=True); time.sleep(2)
        front = subprocess.run(["osascript", "-e", 'tell application "System Events" to get name of first application process whose frontmost is true'], capture_output=True, text=True).stdout.strip()
        ok = any(k in front.lower() for k in ("soffice", "libreoffice"))
        log(f"mac 前台应用: {front or '?'} → {'焦点在 LibreOffice' if ok else '★焦点不在 LibreOffice'}"); return ok
    return True


def setup_docs(task):
    """办公文档题(GUI 通道):把文件下到工作目录,再用 LibreOffice 打开,让模型有界面可点。"""
    work = HOME / "office-work"; work.mkdir(parents=True, exist_ok=True)
    opened = []
    for f in task.get("files", []):
        dest = work / f["name"]
        for _ in range(3):
            r = subprocess.run(["curl", "-sL", "--fail", "-m", "180", "-o", str(dest), f["url"]])
            if r.returncode == 0 and dest.exists() and dest.stat().st_size > 0: break
            time.sleep(5)
        if not dest.exists(): log("SETUP-FAIL 文件下载失败", f["url"]); return False
        log("已就位", dest, dest.stat().st_size, "字节"); opened.append(dest)
    env = dict(os.environ); env.setdefault("DISPLAY", ":99")
    logf = open(OUTD / "soffice.log", "a")
    if SYS == "Windows":
        # ★用 start 启动才会前置。Popen 直接起的 LibreOffice 窗口留在 runner 控制台后面,pyautogui 的键全打到控制台上(自检截图证实)。
        cmd = f'start "" /MAX "{soffice_bin()}" {lo_profile_arg()} --norestore "{opened[0]}"'
        log("启动命令:", cmd)
        subprocess.Popen(cmd, shell=True, env=env, stdout=logf, stderr=logf)
    else:
        subprocess.Popen([soffice_bin(), lo_profile_arg(), "--norestore", str(opened[0])], env=env, stdout=logf, stderr=logf)
    if SYS == "Windows":
        return lo_wait_and_focus()           # Windows 不用非黑判据(会被 runner 控制台骗过),按窗口标题等
    for i in range(60):                      # 等窗口真的画出来:靠截图里的非黑像素判断,而不是靠 sleep
        time.sleep(2)
        try:
            import mss
            from PIL import Image
            with mss.mss() as sc:
                raw = sc.grab(sc.monitors[1]); im = Image.frombytes("RGB", raw.size, raw.rgb)
            nz = sum(1 for p in im.convert("L").getdata() if p > 8) / (im.size[0] * im.size[1])
            if nz > 0.05:
                log(f"LibreOffice 窗口已出现({(i+1)*2}s,画面非黑 {nz:.2f})")
                if SYS == "Darwin": lo_wait_and_focus()
                return True
        except Exception: pass
    log("SETUP-FAIL LibreOffice 窗口没出现"); return False


def soffice_stop():
    """先温和关闭 LibreOffice 让它把改动写盘,等不到再强杀(与 Chrome 同一条纪律)。"""
    if SYS == "Windows":
        subprocess.run(["taskkill", "/IM", "soffice.bin"], capture_output=True)
        subprocess.run(["taskkill", "/IM", "soffice.exe"], capture_output=True)
        time.sleep(8)
        subprocess.run(["taskkill", "/F", "/IM", "soffice.bin"], capture_output=True)
        subprocess.run(["taskkill", "/F", "/IM", "soffice.exe"], capture_output=True)
    else:
        subprocess.run(["pkill", "-TERM", "-f", "soffice"], capture_output=True)
        time.sleep(8)
        subprocess.run(["pkill", "-KILL", "-f", "soffice"], capture_output=True)
    time.sleep(3)


def grade_doc(task):
    """办公文档题的判分:调 OSWorld 官方 metrics,与终端通道完全同一套(不另写判据)。"""
    sys.path.insert(0, str(HERE / "osw_metrics_pkg"))
    fn = None; errs = []
    for mod in ("table", "slides", "general", "pdf", "libreoffice", "others"):
        try: m = __import__(f"desktop_env.evaluators.metrics.{mod}", fromlist=["x"])
        except Exception as e: errs.append(f"{mod}: {type(e).__name__}"); continue
        if hasattr(m, task["grade"]["func"]): fn = getattr(m, task["grade"]["func"]); break
    if fn is None: return False, f"官方判分函数找不到 {task['grade']['func']}({';'.join(errs)})"
    work = HOME / "office-work"
    ref = work / ("_ref_" + task["files"][0]["name"])
    for _ in range(3):
        r = subprocess.run(["curl", "-sL", "--fail", "-m", "180", "-o", str(ref), task["grade"]["expected_url"]])
        if r.returncode == 0 and ref.exists() and ref.stat().st_size > 0: break
        time.sleep(5)
    if not ref.exists(): return False, "参考文件下载失败"
    target = work / task["grade"].get("result_file", task["files"][0]["name"])
    try: score = fn(str(target), str(ref), **(task["grade"].get("options") or {}))
    except Exception as e: return False, f"判分抛异常 {type(e).__name__}: {str(e)[:110]}"
    return int(float(score) >= 1.0), f"官方判分 {task['grade']['func']} = {score}"


def setup(s):
    # 真实桌面系统都有 ~/Desktop(Linux 由 xdg-user-dirs 建);CI 的 Linux runner 没有,补上以对齐真实环境
    G.desktop_dir().mkdir(parents=True, exist_ok=True)
    if s.get("inject_prefs"):                        # 要在 Chrome 关着时改 Preferences
        chrome_start(); time.sleep(3); chrome_stop()
        p = G.chrome_profile() / "Preferences"; d = G._load_json(p) or {}
        for k, v in s["inject_prefs"].items(): d.setdefault(k, {}).update(v) if isinstance(v, dict) else d.__setitem__(k, v)
        p.parent.mkdir(parents=True, exist_ok=True); p.write_text(json.dumps(d), encoding="utf-8")
        back = G._load_json(p) or {}
        log("已注入偏好:", s["inject_prefs"], "| 回读:", {k: G._dig(back, k) for k in ("session.restore_on_startup", "session.startup_urls")})
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


def openclaw_cmd():
    """★Windows 上 `openclaw` 是 .cmd 批处理壳,参数经 cmd.exe 解析,题面在第一个换行处被截断(第一轮 15 道全中招)。
    统一改成 node 直接跑 openclaw.mjs,三系统同一调用路径。"""
    node = shutil.which("node")
    for cand in [pathlib.Path(p) for p in subprocess.run(["npm", "root", "-g"], capture_output=True, text=True, shell=(SYS == "Windows")).stdout.split()]:
        mjs = cand / "openclaw" / "openclaw.mjs"
        if node and mjs.exists(): return [node, str(mjs)]
    exe = shutil.which("openclaw") or shutil.which("openclaw.cmd") or "openclaw"
    log("⚠ 没找到 openclaw.mjs,退回", exe); return [exe]


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
    # ★不覆盖 HOME/USERPROFILE:agent 起的 code/Chrome 会照 HOME 落盘,覆盖了它们就写进假家目录、判分器看不到(第一轮 VS Code 两道就是这么挂的)
    env = dict(os.environ, OPENCLAW_STATE_DIR=str(st), OPENCLAW_CONFIG_PATH=str(st / "openclaw.json"),
               OPENCLAW_CONFIG=str(st / "openclaw.json"), OPENCLAW_WORKSPACE_DIR=str(ws), OPENCLAW_GATEWAY_TOKEN=os.urandom(24).hex(),
               OPENCLAW_EXEC_SHELL_SNAPSHOT="off", NO_PROXY="127.0.0.1,localhost", no_proxy="127.0.0.1,localhost")
    env.setdefault("DISPLAY", ":99")
    oc = openclaw_cmd()
    # 题面 = 原指令 + 一句环境说明,三系统一字不差;不告诉它端口、路径、命令,让它自己发现
    prompt = ("You are operating a real desktop computer. Google Chrome is currently running on this machine, "
              "and Visual Studio Code is installed. Complete the following request for the user, then stop.\n\n" + instruction)
    gw = subprocess.Popen(oc + ["gateway", "run", "--bind", "loopback", "--port", "18789", "--auth", "token"],
                          stdout=open(outd / "gateway.log", "w"), stderr=subprocess.STDOUT, env=env, cwd=str(ws))
    import socket
    for _ in range(90):
        if gw.poll() is not None: break
        try: socket.create_connection(("127.0.0.1", 18789), timeout=1).close(); break
        except OSError: time.sleep(1)
    t0 = time.time()
    ag = subprocess.run(oc + ["agent", "--session-id", f"gui-{os.getpid()}", "--message", prompt, "--thinking", "off",
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
if task.get("files"):                      # 办公文档题
    SETUP_OK = setup_docs(task)
    if not SETUP_OK: print(f"RESULT {task['id'][:8]} SETUP-FAIL"); sys.exit(4)
else:
    setup(task.get("setup", {}))
res = {"rc": 0, "agent_s": 0}
if a.arm == "openclaw":
    res = run_agent(task["instruction"])
elif a.arm == "gui-selfcheck":
    # 装置自检,不跑模型:装置自己在 A1 写一个标记,走和真 GUI 通道完全相同的「保存→关闭→判分」,
    # 判分后用 openpyxl 读回 A1。读得到标记 = 保存链路真写盘;读不到 = 装置问题,模型做对了也判 0。
    ta = time.time(); time.sleep(3)
    try:
        import pyautogui
        lo_wait_and_focus()
        # 不按 Ctrl+Home:mac 上 pyautogui 把它映射成别的组合、弹出系统 emoji 面板(自检截图证实);打开文件时活动格默认就是 A1
        pyautogui.typewrite("ENVSHIFT-SELFCHECK", interval=0.02); pyautogui.press("enter"); time.sleep(2)
        log("自检:已在 A1 写入标记")
    except Exception as e:
        log("自检写入失败", type(e).__name__)
    res = {"rc": 0, "agent_s": int(time.time() - ta)}
elif a.arm == "gui":
    # 真 GUI 通道:同一道题,agent 只有截图和鼠标键盘,没有 shell 与文件工具
    ta = time.time()
    r = subprocess.run([sys.executable, str(HERE / "gui_agent_loop.py"), "--task", str(a.task), "--model", a.model,
                        "--base", a.base, "--instruction", task["instruction"], "--outdir", str(OUTD / "gui_loop")],
                       env=dict(os.environ), timeout=a.timeout + 300)
    res = {"rc": r.returncode, "agent_s": int(time.time() - ta)}
    # ★几张截图和 loop.json 必须留在 gui_out 之外:打包那步会把 gui_out 整个加密,
    #   而「模型到底看到了什么」是判断 GUI 通道成不成立的唯一硬证据——只看「非黑像素比例」
    #   会把「一片均匀灰的空桌面」也算成画面正常,必须能直接把图调出来看。
    for want in (1, 2, 5, 12, 25):
        src = OUTD / "gui_loop" / f"step{want:02d}.png"
        if src.exists():
            try: shutil.copy(src, pathlib.Path(f"gui_shot{want:02d}_{platform.system()}.png"))
            except Exception as e: log("截图留存失败", want, type(e).__name__)
    lj = OUTD / "gui_loop" / "loop.json"
    if lj.exists():
        try: shutil.copy(lj, pathlib.Path(f"gui_loop_{platform.system()}.json"))
        except Exception as e: log("loop.json 留存失败", type(e).__name__)
# 判分前:标签页类判据要在 Chrome 还开着时读;其余判据要先关 Chrome 让偏好落盘
g = task["grade"]
if task.get("files"):
    # 办公文档题:判分走 OSWorld 官方 metrics(与终端通道同一套)。
    # ★真 GUI 通道判分前必须先保存:模型在 LibreOffice 界面里改的东西在内存里,不保存关窗口就丢。
    #   OSWorld 原题就是判分前用 postconfig 快捷键保存的;终端通道题面明确要求 agent 自己保存所以不需要。
    #   round A 真 GUI 办公文档 45 格全 0:Linux 15 道里 0 道按过 Ctrl+S,不保存是硬伤,先修这个再看模型能做几道。
    if a.arm in ("gui", "gui-selfcheck"):
        try:
            import pyautogui
            # 诊断:保存到底有没有写盘——记目标文件保存前后的大小和 mtime,保存后再截一张图留存
            lo_wait_and_focus()                  # 保存前再确认一次焦点在 LibreOffice 上
            _tgt = HOME / "office-work" / task["grade"].get("result_file", task["files"][0]["name"])
            def _stat():
                try: st = _tgt.stat(); return f"{st.st_size}B mtime={int(st.st_mtime)}"
                except Exception as e: return f"stat失败 {type(e).__name__}"
            log("保存前目标文件:", _stat())
            pyautogui.hotkey("command" if SYS == "Darwin" else "ctrl", "s"); time.sleep(3)
            pyautogui.press("enter"); time.sleep(3)      # 保存 xlsx/pptx 时 LibreOffice 会问「保持当前格式?」,回车 = 保持
            log("保存后目标文件:", _stat())
            try:
                import mss
                from PIL import Image
                with mss.mss() as sc:
                    raw = sc.grab(sc.monitors[1]); Image.frombytes("RGB", raw.size, raw.rgb).save(f"gui_shot_aftersave_{platform.system()}.png")
            except Exception as e:
                log("保存后截图失败", type(e).__name__)
            log("判分前已发保存快捷键(恢复 OSWorld postconfig 行为)")
        except Exception as e:
            log("判分前保存失败", type(e).__name__)
    soffice_stop()
    lo_profile_evidence()                   # 关掉之后才能判:它用的是预置 profile 还是默认 profile
    ok, why = grade_doc(task)
    if a.arm == "gui-selfcheck":
        try:
            import openpyxl
            _t = HOME / "office-work" / task["grade"].get("result_file", task["files"][0]["name"])
            _v = openpyxl.load_workbook(_t).active["A1"].value
            print(f"SELFCHECK A1={_v!r} → " + ("写盘成功" if _v == "ENVSHIFT-SELFCHECK" else "★没写盘,保存链路是装置问题"), flush=True)
        except Exception as e:
            print(f"SELFCHECK 读回失败 {type(e).__name__}: {e}", flush=True)
elif g["func"] in ("is_expected_tabs",):
    ok, why = G.FUNCS[g["func"]](g["args"]); chrome_stop()
else:
    chrome_stop(); ok, why = G.FUNCS[g["func"]](g["args"])
# 把 Chrome 的状态文件留一份在加密包之外:判据以后再改,能离线重判,不用重跑 agent。判分已结束、Chrome 已关,文件不会被锁。
try:
    _keep = pathlib.Path(f"gui_state_{platform.system()}"); _keep.mkdir(exist_ok=True)
    for _f in ("Preferences", "Secure Preferences", "Bookmarks", "History", "Cookies", "Network/Cookies"):
        _src = G.chrome_profile() / _f
        if _src.exists(): shutil.copy(_src, _keep / _f.replace("/", "_"))
except Exception as _e:
    log("状态文件留存失败", type(_e).__name__)
out = {"instance": f"gui__{task['app']}-{task['id'][:8]}", "task_id": task["id"], "arm": a.arm, "platform": platform.platform(),
       "resolved": int(ok), "setup_ok": SETUP_OK, "grade": g["func"], "why": why, "agent_rc": res.get("rc"), "agent_s": res.get("agent_s"),
       "total_s": int(time.time() - t0), "model": a.model}
print(f"RESULT {out['instance']} arm={a.arm} platform={platform.system()}-{platform.machine()} resolved={out['resolved']} setup_ok={int(SETUP_OK)} grade={g['func']} | {why[:120]}")
pathlib.Path(f"gui_{platform.system()}_{platform.machine()}.json").write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
