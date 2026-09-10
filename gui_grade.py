#!/usr/bin/env python3
"""GUI 任务的跨系统独立评分器。

OSWorld 官方评分器绑在 Linux 虚拟机上(pkill chrome / google-chrome / 写死 /home/user)。
这里只保留它的**判据本身**——读 Chrome 与 VS Code 的本地状态文件比对——把"去哪读"
按平台重新解析。判据一字不改,只改路径与进程名。

支持的判据(与 OSWorld 同名):
  is_expected_bookmarks   书签栏文件夹/条目
  exact_match             Preferences 里的某个偏好项
  match_in_list           默认搜索引擎之类的列表匹配
  check_font_size         字体大小设置
  is_shortcut_on_desktop  桌面快捷方式(三系统格式不同:.desktop / .lnk / .webloc)
  is_extension_installed  VS Code 扩展目录
  check_json_settings     VS Code 用户设置项
  is_cookie_deleted       Cookie 库里某域名是否已清
  check_history_deleted   历史库里某模式是否已清

用法: gui_grade.py <判据名> <参数 json>   → 打印 PASS/FAIL 并返回退出码 0/1
"""
import json, os, pathlib, platform, sqlite3, sys, shutil, tempfile
for _st in (sys.stdout, sys.stderr):   # Windows 控制台默认 cp1252,打中文会崩(老坑)
    try: _st.reconfigure(encoding="utf-8", errors="replace")
    except Exception: pass

SYS = platform.system()          # Linux / Darwin / Windows
HOME = pathlib.Path.home()


PROFILE_NAME = os.environ.get("ENVSHIFT_CHROME_PROFILE", "EnvShift")


def chrome_user_data_root():
    """Chrome 用户数据的系统约定根目录:三个系统位置完全不同,这正是环境敏感面本身。"""
    if SYS == "Darwin":
        return HOME / "Library/Application Support/Google/Chrome"
    if SYS == "Windows":
        return pathlib.Path(os.environ.get("LOCALAPPDATA", HOME / "AppData/Local")) / "Google/Chrome/User Data"
    return HOME / ".config/google-chrome"


def chrome_user_data_dir():
    """Chrome 136 起,默认数据目录不再开放远程调试端口,必须显式指定一个命名的数据目录。
    我们把它放在系统约定根目录之下(根目录随系统不同,目录名固定),环境差异保留在「根在哪」上。"""
    return chrome_user_data_root() / PROFILE_NAME


def chrome_profile():
    return chrome_user_data_dir() / "Default"


def desktop_dir():
    if SYS == "Windows":
        return pathlib.Path(os.environ.get("USERPROFILE", HOME)) / "Desktop"
    return HOME / "Desktop"


def vscode_ext_dir():
    return HOME / ".vscode/extensions"        # 三系统一致,但 VS Code 的 user settings 不一致


def vscode_settings():
    """VS Code 用户设置文件:三系统位置不同;Linux 上还随安装方式不同——snap 装的 VS Code 被沙箱隔离,
    设置在 ~/snap/code/current/.config/Code/User/,deb 装的在 ~/.config/Code/User/。哪个存在用哪个。"""
    if SYS == "Darwin":
        return HOME / "Library/Application Support/Code/User/settings.json"
    if SYS == "Windows":
        return pathlib.Path(os.environ.get("APPDATA", HOME / "AppData/Roaming")) / "Code/User/settings.json"
    cands = [HOME / ".config/Code/User/settings.json", HOME / "snap/code/current/.config/Code/User/settings.json"]
    for c in cands:
        if c.exists(): return c
    return cands[0]


def _load_json(p):
    try:
        return json.loads(pathlib.Path(p).read_text(encoding="utf-8", errors="replace"))
    except Exception:
        return None


def _prefs():
    return _load_json(chrome_profile() / "Preferences") or {}


def _dig(d, path, default=None):
    for k in path.split("."):
        if not isinstance(d, dict) or k not in d:
            return default
        d = d[k]
    return d


# ── 各判据 ──────────────────────────────────────────────────────────────────
def is_expected_bookmarks(args):
    """args: {"names": [...]} 书签栏里应存在的文件夹名 / {"urls": [...]} 应存在的链接"""
    bm = _load_json(chrome_profile() / "Bookmarks")
    if bm is None:
        return False, "读不到 Bookmarks 文件"
    bar = _dig(bm, "roots.bookmark_bar.children", []) or []
    if args.get("names"):
        got = [c.get("name") for c in bar if c.get("type") == "folder"]
        miss = [n for n in args["names"] if n not in got]
        return (not miss), f"书签栏文件夹={got} 缺={miss}"
    if args.get("urls"):
        got = [c.get("url") for c in bar if c.get("type") == "url"]
        miss = [u for u in args["urls"] if not any(u.rstrip("/") in (g or "") for g in got)]
        return (not miss), f"书签栏链接={got} 缺={miss}"
    return False, "判据缺少 names/urls"


def exact_match(args):
    """args: {"pref": "点分路径", "expected": 值}"""
    got = _dig(_prefs(), args["pref"])
    return (str(got).lower() == str(args["expected"]).lower()), f"{args['pref']}={got} 期望={args['expected']}"


def match_in_list(args):
    got = _dig(_prefs(), args["pref"])
    return (str(got) in [str(x) for x in args["expected"]]), f"{args['pref']}={got} 应属于 {args['expected']}"


def check_font_size(args):
    got = _dig(_prefs(), "webkit.webprefs.default_font_size")
    lo, hi = args.get("min", 0), args.get("max", 10 ** 9)
    return (got is not None and lo <= got <= hi), f"字号={got} 期望区间 [{lo},{hi}]"


def is_shortcut_on_desktop(args):
    """三系统的快捷方式格式不同:Linux .desktop 文本 / Windows .lnk 二进制 / macOS .webloc XML"""
    name = args["name"]
    d = desktop_dir()
    if not d.exists():
        return False, f"桌面目录不存在 {d}"
    exts = {"Linux": (".desktop",), "Darwin": (".webloc", ".url", ".desktop"), "Windows": (".lnk", ".url")}[SYS]
    hits = [f.name for f in d.iterdir() if f.suffix in exts and name.lower() in f.stem.lower()]
    return bool(hits), f"桌面({d})匹配 {name} 的快捷方式={hits}"


def is_extension_installed(args):
    """先问 VS Code 自己(与 OSWorld 原判据一致:`code --list-extensions`),问不到再看扩展目录。"""
    want = args["id"].lower()
    import subprocess
    code = shutil.which("code") or shutil.which("code.cmd")
    if code:
        try:
            out = subprocess.run([code, "--list-extensions"], capture_output=True, text=True, timeout=60).stdout.lower()
            if out.strip():
                return (want in out), f"code --list-extensions: {out.split()} 匹配 {want}={want in out}"
        except Exception:
            pass
    d = vscode_ext_dir()
    if not d.exists():
        return False, f"扩展目录不存在 {d}(且 code 命令不可用)"
    got = [x.name for x in d.iterdir() if x.is_dir()]
    return any(want in g.lower() for g in got), f"已装扩展 {len(got)} 个,匹配 {want}={[g for g in got if want in g.lower()]}"


def check_json_settings(args):
    s = _load_json(vscode_settings())
    if s is None:
        return False, f"读不到 {vscode_settings()}"
    got = s.get(args["key"])
    return (str(got) == str(args["expected"])), f"{args['key']}={got} 期望={args['expected']}"


def _sqlite_rows(db, sql):
    """Chrome 运行时会锁库,复制一份再读。"""
    if not pathlib.Path(db).exists():
        return None
    t = pathlib.Path(tempfile.mkdtemp()) / "copy.db"
    shutil.copy(db, t)
    try:
        con = sqlite3.connect(str(t))
        rows = con.execute(sql).fetchall()
        con.close()
        return rows
    except Exception:
        return None


def is_cookie_deleted(args):
    rows = _sqlite_rows(chrome_profile() / "Cookies",
                        "select host_key from cookies where host_key like '%%%s%%'" % args["domain"].replace("'", ""))
    if rows is None:
        return False, "读不到 Cookies 库"
    return (len(rows) == 0), f"{args['domain']} 残留 cookie {len(rows)} 条"


def check_history_deleted(args):
    rows = _sqlite_rows(chrome_profile() / "History",
                        "select url from urls where url like '%%%s%%'" % args["pattern"].replace("'", ""))
    if rows is None:
        return False, "读不到 History 库"
    return (len(rows) == 0), f"匹配 {args['pattern']} 的历史残留 {len(rows)} 条"


def _cdp_tabs(port=1337):
    """通过 Chrome 远程调试端口列出打开的标签页(三系统同一接口)。"""
    import urllib.request
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/json", timeout=5) as r:
            return [t for t in json.load(r) if t.get("type") == "page"]
    except Exception:
        return None


def is_expected_tabs(args):
    """args: {"urls": [片段...]} 每个片段都得在某个打开的标签页 URL 里出现"""
    tabs = _cdp_tabs(args.get("port", 1337))
    if tabs is None:
        return False, "Chrome 远程调试端口不可用(Chrome 没在跑?)"
    got = [t.get("url", "") for t in tabs]
    miss = [u for u in args["urls"] if not any(u in g for g in got)]
    return (not miss), f"打开的标签={got} 缺={miss}"


def startup_page_cleared(args):
    """启动页里不再包含 args['bad'](或启动模式已不是「打开指定页」)"""
    p = _prefs()
    mode = _dig(p, "session.restore_on_startup")
    urls = _dig(p, "session.startup_urls", []) or []
    bad = [u for u in urls if args["bad"] in u]
    ok = (mode != 4) or (not bad)
    return ok, f"restore_on_startup={mode} startup_urls={urls}"


def unpacked_extension_loaded(args):
    """Preferences 的 extensions.settings 里有某个从本地目录加载的扩展,其路径含 args['name']"""
    p = _prefs()
    ext = _dig(p, "extensions.settings", {}) or {}
    hits = [k for k, v in ext.items() if isinstance(v, dict) and args["name"].lower() in str(v.get("path", "")).lower()]
    return bool(hits), f"从本地加载且路径含 {args['name']} 的扩展={hits}"


FUNCS = {k: v for k, v in list(globals().items()) if callable(v) and not k.startswith("_") and k[0].islower()
         and k not in ("chrome_profile", "desktop_dir", "vscode_ext_dir", "vscode_settings")}

if __name__ == "__main__":
    if len(sys.argv) < 2 or sys.argv[1] in ("-h", "--help", "list"):
        print("平台:", SYS)
        print("Chrome 配置目录:", chrome_profile(), "存在" if chrome_profile().exists() else "不存在")
        print("桌面目录:", desktop_dir(), "存在" if desktop_dir().exists() else "不存在")
        print("VS Code 设置:", vscode_settings(), "存在" if vscode_settings().exists() else "不存在")
        print("VS Code 扩展:", vscode_ext_dir(), "存在" if vscode_ext_dir().exists() else "不存在")
        print("可用判据:", " ".join(sorted(FUNCS)))
        sys.exit(0)
    fn = FUNCS[sys.argv[1]]
    args = json.loads(sys.argv[2]) if len(sys.argv) > 2 else {}
    ok, why = fn(args)
    print(("PASS " if ok else "FAIL ") + why)
    sys.exit(0 if ok else 1)
