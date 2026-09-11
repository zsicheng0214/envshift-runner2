#!/usr/bin/env python3
"""真 GUI 通道可行性探针:三个系统上能不能截屏、移动鼠标、点击、打字。
这是「给 agent 截图+鼠标键盘」这条路的生死问题——mac 的 CI 机器通常需要屏幕录制权限,
Linux 无头要虚拟显示,Windows 一般可行。全部用跨平台库,不做任何平台专有调用。
输出每项能力的 OK/FAIL 与证据(截图尺寸、非空像素比例),不做任何吞异常的假绿。
"""
import json, os, platform, sys, time, traceback
for _st in (sys.stdout, sys.stderr):
    try: _st.reconfigure(encoding="utf-8", errors="replace")
    except Exception: pass
SYS = platform.system()
R = {"platform": platform.platform(), "system": SYS}


def step(name, fn):
    try:
        v = fn(); R[name] = {"ok": True, "info": v}; print(f"  OK   {name}: {v}")
    except Exception as e:
        R[name] = {"ok": False, "err": f"{type(e).__name__}: {e}"}
        print(f"  FAIL {name}: {type(e).__name__}: {str(e)[:160]}")
        if os.environ.get("PROBE_TRACE"): traceback.print_exc()


def cap_pyautogui():
    import pyautogui
    im = pyautogui.screenshot()
    px = im.convert("L").getdata()
    nonblack = sum(1 for p in px if p > 8) / len(px)
    im.save("probe_pyautogui.png")
    return {"size": im.size, "非黑像素比例": round(nonblack, 3)}


def cap_mss():
    import mss
    from PIL import Image
    with mss.mss() as s:
        m = s.monitors[1]; raw = s.grab(m)
        im = Image.frombytes("RGB", raw.size, raw.rgb)
        im.save("probe_mss.png")
        nonblack = sum(1 for p in im.convert("L").getdata() if p > 8) / (im.size[0] * im.size[1])
    return {"size": im.size, "非黑像素比例": round(nonblack, 3)}


def mouse():
    import pyautogui
    pyautogui.FAILSAFE = False
    w, h = pyautogui.size()
    pyautogui.moveTo(w // 3, h // 3, duration=0.2)
    p1 = pyautogui.position()
    pyautogui.moveTo(w // 2, h // 2, duration=0.2)
    p2 = pyautogui.position()
    moved = (p1 != p2)
    return {"屏幕": (w, h), "第一次": tuple(p1), "第二次": tuple(p2), "鼠标真的动了": moved}


def keyboard_into_file():
    """打开一个文本编辑器太重;用 pyautogui 打字到当前焦点,只验证 typewrite 不报错。
    真正的证据在 mouse/screenshot;键盘这里只验 API 可用。"""
    import pyautogui
    pyautogui.FAILSAFE = False
    pyautogui.typewrite("", interval=0)
    return "typewrite 可调用"


print(f"══ 真 GUI 通道探针 | {platform.platform()} | DISPLAY={os.environ.get('DISPLAY','(无)')}")
step("截屏_pyautogui", cap_pyautogui)
step("截屏_mss", cap_mss)
step("鼠标", mouse)
step("键盘", keyboard_into_file)
ok = all(v.get("ok") for k, v in R.items() if isinstance(v, dict))
shots = [k for k, v in R.items() if isinstance(v, dict) and v.get("ok") and "非黑像素比例" in str(v.get("info"))]
usable = any(isinstance(R.get(k, {}).get("info"), dict) and R[k]["info"].get("非黑像素比例", 0) > 0.01 for k in ("截屏_pyautogui", "截屏_mss"))
R["verdict"] = {"全部能力可用": ok, "截到的是真画面(非全黑)": usable}
print(f"\nVERDICT platform={SYS} all_ok={int(ok)} real_screen={int(usable)}")
open(f"probe_gui_{SYS}.json", "w", encoding="utf-8").write(json.dumps(R, ensure_ascii=False, indent=1, default=str))
