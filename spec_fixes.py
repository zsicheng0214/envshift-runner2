#!/usr/bin/env python3
"""让 SWE-bench 官方安装配方在四个平台上都能跑起来的最小适配层。

铁律:
  1) 只动**安装/建环境**这一步,绝不碰题面、补丁、测试命令、判分脚本;
  2) A 类(配方本身过时)对**所有平台一视同仁**地改,不给任何平台开小灶;
  3) B 类(平台差异)只在该平台生效,且是把 Linux 写法翻译成本平台等价写法,不改语义;
  4) 改完必须过 gold 闸门(官方标准补丁打上去,官方判分全绿),过不了这道题在该平台仍然作废。
每次改动都记进 applied 列表,写进结果 json,可审计。
"""
import re

APPLIED = []          # 本次运行实际生效的改动(去重、保序);yml/requirements 是延迟读取的,所以必须共享同一个列表


def _note(kind, why):
    e = f"{kind}:{why}"
    if e not in APPLIED: APPLIED.append(e)

# ── A 类:配方过时,所有平台都改 ──────────────────────────────────────────────
DROP_PKGS = {
    # 只在 linux-64 有构建的包(气候数据库),xarray 的测试遇不到它会自动跳过
    "cdms2": "只有 Linux 版,mac/Windows 的软件仓库里没有",
    # 纯 GUI 后端,测试是无头跑的用不到;在 osx-arm64 上它拉的 wxwidgets 解不开
    "wxpython": "纯图形界面后端,无头测试用不到,且在苹果芯片上依赖冲突",
}
FLAG_FIXES = [
    (r"\s--no-use-pep517\b", "", "今天的 pip 已删掉 --no-use-pep517 这个参数"),
]


def _fix_text(s, plat, applied=None):
    """plat: 'linux' | 'mac' | 'win'"""
    if not isinstance(s, str) or not s:
        return s
    out = s
    for pat, rep, why in FLAG_FIXES:
        if re.search(pat, out):
            out = re.sub(pat, rep, out); _note("A", why)
    for pkg, why in DROP_PKGS.items():
        # 先删 environment.yml 里的整行("  - pkg"),否则下面的 token 规则会把包名抹成空行
        n = re.subn(r"(?m)^[ \t]*-[ \t]*%s(?:[=<>!\[][^\n]*)?[ \t]*\n" % re.escape(pkg), "", out)
        if n[1]: out = n[0]; _note("A", f"去掉 {pkg}:{why}")
        # requirements.txt:整行就是包名
        n = re.subn(r"(?m)^[ \t]*%s(?:[=<>!~][^\n]*)?[ \t]*\n" % re.escape(pkg), "", out)
        if n[1]: out = n[0]; _note("A", f"去掉 {pkg}:{why}")
        # 再删 conda create / pip install 命令行里的裸 token(连同版本号与前面多余空格)
        n = re.subn(r"[ \t]+(?<![\w-])%s(?:[=<>!][^\s]*)?(?![\w-])" % re.escape(pkg), "", out)
        if n[1]: out = n[0]; _note("A", f"去掉 {pkg}:{why}")
    # ── B 类:平台差异,把 Linux 写法翻成本平台等价写法 ──────────────────────
    if plat == "win":
        n = re.subn(r"(?m)^(\s*)(sudo\s+)?apt-get\b[^\n]*", r"\1true  # envshift: Windows 无 apt-get", out)
        if n[1]: out = n[0]; _note("B", "Windows 没有 apt-get,跳过装系统包这步")
    return out


def _walk(o, plat, applied=None):
    if isinstance(o, str): return _fix_text(o, plat, applied)
    if isinstance(o, list): return [_walk(x, plat, applied) for x in o]
    if isinstance(o, tuple): return tuple(_walk(x, plat, applied) for x in o)
    if isinstance(o, dict): return {k: _walk(v, plat, applied) for k, v in o.items()}
    return o


def apply_spec_fixes(plat):
    """就地改写官方安装配方,返回 applied 记录。必须在 make_test_spec 之前调用。
    两个入口都要改:① constants 里写死的 conda/pip 命令;② 从仓库现抓的 environment.yml / requirements.txt
    (cdms2、wxpython 就写在仓库自己的 environment.yml 里,只改 constants 是改不到的)。"""
    import swebench.harness.constants as C
    # ② 从仓库现抓的依赖文件:包一层,在出口处做同样的文本改写
    try:
        import swebench.harness.test_spec.python as P
        for fn in ("get_environment_yml", "get_requirements"):
            orig = getattr(P, fn, None)
            if orig is None or getattr(orig, "_envshift", False): continue
            def wrap(orig=orig):
                def inner(*a, **k): return _fix_text(orig(*a, **k), plat)
                inner._envshift = True
                return inner
            setattr(P, fn, wrap())
    except Exception as e:
        _note("!", f"包装依赖文件读取失败:{e}")
    for name in dir(C):
        if not name.startswith("MAP_"): continue
        v = getattr(C, name)
        if not isinstance(v, dict): continue
        try: setattr(C, name, _walk(v, plat, applied))
        except Exception: pass
    return APPLIED   # 返回活列表:make_test_spec 时才读的 yml/requirements 改动也会出现在里面


# ── 安装失败后的等价重试(只在原方式已挂时启用,记进 applied) ────────────────
def _to_develop(m):
    """把 pip 的可编辑安装翻成等价的 setup.py 写法;extras(.[test])要翻成 easy_install 的写法而不是硬拼在 develop 后面。"""
    extras = m.group("extras") or ""
    return "python setup.py develop" + (f" && python -m pip install -e .{extras} --no-deps --no-build-isolation" if extras else "")


RETRIES = [
    ("build_editable",
     [(r"python -m pip install (?:-v |--verbose )?(?:--no-build-isolation )?-e \.(?P<extras>\[[^\]]*\])?", _to_develop)],
     "A", "老式打包后端不支持新版 pip 的可编辑安装,改用同义的 setup.py develop"),
]


def retry_variant(script, log):
    """给定失败的脚本和日志,若命中已知模式则返回 (新脚本, 说明);否则 (None, None)。"""
    for needle, subs, kind, why in RETRIES:
        if needle in log:
            s = script
            for pat, rep in subs: s = re.sub(pat, rep, s)
            if s != script: return s, f"{kind}:{why}"
    return None, None


if __name__ == "__main__":   # 自测:纯文本改写不依赖 swebench 包
    demo = {"MAP_X": {"repo": {"1.0": {
        "packages": "numpy==1.19.2 scipy==1.5.2 cdms2 wxpython pytest",
        "install": "python -m pip install -v --no-use-pep517 --no-build-isolation -e .",
        "pre_install": ["apt-get -y update && apt-get -y install gcc", "echo ok"],
        "env_yml": "dependencies:\n  - numpy\n  - cdms2\n  - wxpython\n  - pandas\n"}}}}
    for plat in ("linux", "mac", "win"):
        APPLIED.clear()
        out = _walk(demo, plat)["MAP_X"]["repo"]["1.0"]
        print(f"\n── {plat}")
        for k, v in out.items(): print("  ", k, "=", v)
        print("   改动:", APPLIED)
