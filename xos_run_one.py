#!/usr/bin/env python3
"""EnvShift 裸机执行器(不依赖 Docker;给 GitHub 的 macOS/Windows/Linux runner 用)。
三阶段流程,与容器版执行器一致。"""
import argparse, json, os, pathlib, shutil, stat, subprocess, sys, tempfile, time
for _st in (sys.stdout, sys.stderr):
    try: _st.reconfigure(encoding="utf-8", errors="replace")
    except Exception: pass
BIN = {"E3-archive-audit": ("audit", "E3"), "E4-vault-compliance": ("vault-archive", "E4"),
       "E5-retention-plan": ("retain", "E5"), "E6-contact-merge": ("merge.sh", "E6"), "E7-publish-shared": ("publish", "E7"),
       "E8-report-extend": ("report.py", "E8"), "E9-latest-extend": ("latest.py", "E9"), "E10-dupes-extend": ("dupes.py", "E10"),
       "E11-export-extend": ("export.sh", "E11"), "E12-publish-extend": ("publish.sh", "E12")}
ap = argparse.ArgumentParser()
ap.add_argument("--task", required=True); ap.add_argument("--arm", default="oracle")
ap.add_argument("--data", default="/data"); ap.add_argument("--app", default="/app")
ap.add_argument("--out", default="out"); ap.add_argument("--model", default="deepseek-v4-pro")
ap.add_argument("--base", default="https://api.llmgateway.io/v1"); ap.add_argument("--cell-env", default="{}")
ap.add_argument("--timeout", type=int, default=3600); ap.add_argument("--sudo-verify", action="store_true")
a = ap.parse_args()
here = pathlib.Path(__file__).resolve().parent; td = here / "tasks" / a.task
binname, venv = BIN[a.task]; data, app = pathlib.Path(a.data), pathlib.Path(a.app)
if os.name == "nt" and a.task in ("E7-publish-shared", "E12-publish-extend"):
    print(f"{a.task} cell={os.environ.get('XOS_CELL','?')} arm={a.arm} reward=NA 诊断=NA (Windows 无 POSIX 权限模型:坐标无定义)"); sys.exit(0)
out = pathlib.Path(a.out).resolve(); out.mkdir(parents=True, exist_ok=True)
cell_env = json.loads(a.cell_env); t0 = time.time()
def wipe(p):
    p.mkdir(parents=True, exist_ok=True)
    for c in p.iterdir(): shutil.rmtree(c, ignore_errors=True) if c.is_dir() else c.unlink(missing_ok=True)
wipe(data); wipe(app)
# 运行材料只存在于内存,agent 阶段不落盘。
import io, tarfile
_buf = io.BytesIO()
with tarfile.open(fileobj=_buf, mode="w") as _tf: _tf.add(str(td / "verifier"), arcname="verifier")
VERIFIER_TAR = _buf.getvalue()
def _rm(p):
    # 只读文件要先去只读位再删
    def _onerr(fn, path, exc):
        os.chmod(path, stat.S_IWRITE); fn(path)
    if p.exists(): shutil.rmtree(p, onerror=_onerr)
MKFIX_SRC = (td / "mkfixture.py").read_text(encoding="utf-8")
PROMPT_SRC = (td / "prompt.md").read_text(encoding="utf-8")
ARM_DIR_TAR = None
if a.arm not in ("agent", "null"):
    _b2 = io.BytesIO()
    with tarfile.open(fileobj=_b2, mode="w") as _tf: _tf.add(str(td / "arms" / a.arm), arcname="arm")
    ARM_DIR_TAR = _b2.getvalue()
if a.arm == "agent":
    _rm(here / "tasks"); _rm(here / ".git")
    for _f in here.glob("*.md"): _f.unlink(missing_ok=True)
    for _f in here.glob("*.py"):
        if _f.resolve() != pathlib.Path(__file__).resolve():
            try: _f.unlink()
            except OSError: pass
    assert not (here / "tasks").exists() and not (here / ".git").exists(), "materials not cleared"
# 1) fixture:不施加格子环境
_mk = pathlib.Path(tempfile.mkdtemp(prefix="mkfix-")) / "mkfixture.py"; _mk.write_text(MKFIX_SRC, encoding="utf-8")
r = subprocess.run([sys.executable, str(_mk), str(data)], capture_output=True, text=True,
                   env={**os.environ, f"{venv}_APP": str(app)})   # 从内存写回临时目录跑;E8/E9 的现成工具由 fixture 写进 app 根
shutil.rmtree(_mk.parent, ignore_errors=True)
(out / "fixture.log").write_text(r.stdout + r.stderr)
if r.returncode != 0: print(f"{a.task} arm={a.arm} FIXTURE-FAIL {r.stderr[-200:]}"); sys.exit(4)
# 2) 臂
agent_rc = 0; env_cell = dict(os.environ, **cell_env)
if a.arm == "agent":
    dsh = here / "dsh"; nm = dsh / "node_modules"
    prompt = out / ".prompt.md"
    # ★题面里写死的 /data /app 按本 OS 的真实根目录渲染(mac 根目录只读、Windows 无根目录)——这本身是 OS 坐标的一部分
    def _p(pth): return str(pathlib.Path(pth).resolve()).replace("\\", "/")
    txt = PROMPT_SRC.replace("/data", _p(data)).replace("/app", _p(app))
    prompt.write_text(txt, encoding="utf-8"); (out / "prompt.rendered.md").write_text(txt, encoding="utf-8")
    env = dict(env_cell, DSH_NM=str(nm), DSH_BRIDGE=str(dsh / "bridge.mjs"), DSH_CONFIG=str(dsh / "cordis.yaml"),
               DSH_HOME_DIR=str(out / "dsh-home"), DSH_SESSION_ROOT=str(out / "dsh-sessions"),
               DSH_RUN_TIMEOUT=str(a.timeout), DSH_MAX_TOKENS=os.environ.get("DSH_MAX_TOKENS", "131072"))
    key = os.environ.get("ENVSHIFT_API_KEY", "")
    r = subprocess.run([sys.executable, str(dsh / "drive_dsh.py"), str(app), a.model, a.base, key, str(out), str(prompt)],
                       cwd=str(app), env=env, capture_output=True, text=True, timeout=a.timeout + 300)
    (out / "driver.log").write_text(r.stdout + r.stderr); agent_rc = r.returncode; prompt.unlink(missing_ok=True)
elif a.arm != "null":
    if ARM_DIR_TAR is None: print(f"{a.task} arm={a.arm} NO-SUCH-ARM"); sys.exit(2)
    _at = pathlib.Path(tempfile.mkdtemp(prefix="arm-"))
    with tarfile.open(fileobj=io.BytesIO(ARM_DIR_TAR), mode="r") as _tf: _tf.extractall(_at)
    shutil.copytree(_at / "arm", app, dirs_exist_ok=True); shutil.rmtree(_at, ignore_errors=True)
    b = app / binname; b.chmod(b.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
t1 = time.time()
# 3) 评分阶段
tests = pathlib.Path(tempfile.mkdtemp(prefix="tests-"))
with tarfile.open(fileobj=io.BytesIO(VERIFIER_TAR), mode="r") as _tf: _tf.extractall(tests)
tests = tests / "verifier"
logdir = out / "verifier-log"; logdir.mkdir(exist_ok=True)
venv_env = dict(env_cell, **{f"{venv}_LOG": str(logdir), f"{venv}_BIN": str(app / binname), f"{venv}_ROOT": str(data)})
cmd = [sys.executable, str(tests / "check.py")]
if a.sudo_verify and os.name == "posix" and shutil.which("sudo"): cmd = ["sudo", "-E", "--preserve-env=PATH"] + cmd
r = subprocess.run(cmd, env=venv_env, capture_output=True, text=True, timeout=1800)
(out / "verifier.log").write_text(r.stdout + r.stderr)
reward = (logdir / "reward.txt").read_text().strip() if (logdir / "reward.txt").exists() else "?"
tr = json.loads((logdir / "trace_results.json").read_text()) if (logdir / "trace_results.json").exists() else {}
line = f"{a.task} cell={os.environ.get('XOS_CELL','?')} arm={a.arm} reward={reward} 诊断={tr.get('points','?')}/{tr.get('total','?')} agent_rc={agent_rc} vrc={r.returncode} agent_s={int(t1-t0)} verify_s={int(time.time()-t1)}"
print(line); (out / "meta.json").write_text(json.dumps({"line": line, "reward": reward, "trace": tr, "cell_env": cell_env}, ensure_ascii=False, indent=2))
