#!/usr/bin/env python3
"""真 GUI 通道的执行循环:截图 → 模型看图给动作 → 执行 → 再截图,直到模型说完成或到步数上限。

与「终端通道」的唯一区别是 agent 的工具面:这里只有看屏幕和操作鼠标键盘,没有 shell、没有文件读写。
同一批题、同一个模型、同一套判分,两条通道的结果差异 = 工具面带来的差异。

截图统一用 mss(三系统均已实测可用,且不依赖桌面环境),动作用 pyautogui。
坐标一律用「截图像素坐标」,执行前按 截图尺寸/屏幕尺寸 折算,避免 Retina/缩放错位。
用法: gui_agent_loop.py --task <序号> --model <模型> [--max-steps 25]
"""
import argparse, base64, io, json, os, pathlib, platform, re, sys, time, urllib.request
for _st in (sys.stdout, sys.stderr):
    try: _st.reconfigure(encoding="utf-8", errors="replace")
    except Exception: pass

ap = argparse.ArgumentParser()
ap.add_argument("--task", required=True)
ap.add_argument("--model", default="gemini-3.5-flash")
ap.add_argument("--base", default="https://api.llmgateway.io/v1")
ap.add_argument("--max-steps", type=int, default=25)
ap.add_argument("--instruction", default="")
ap.add_argument("--outdir", default="gui_loop_out")
a = ap.parse_args()
SYS = platform.system()
OUT = pathlib.Path(a.outdir); OUT.mkdir(parents=True, exist_ok=True)
KEY = os.environ.get("ENVSHIFT_API_KEY", "")
log = lambda *x: print("[loop]", *x, flush=True)
# 启动阶段逐段打点:Windows 上有过一格循环 20 分钟一行没打就被父进程超时杀掉(连下面「平台…任务」那句都没出),
# 卡点只能在导入或取屏幕尺寸这几步里;打点后再出问题能定位到哪一步。
log(f"启动 python {sys.version.split()[0]} 任务 {a.task} 模型 {a.model}")
import mss
from PIL import Image
log("mss / PIL 导入完成")
import pyautogui
pyautogui.FAILSAFE = False
log("pyautogui 导入完成")

SCREEN_W, SCREEN_H = pyautogui.size()
log(f"屏幕 {SCREEN_W}x{SCREEN_H}")


def shot(step):
    with mss.mss() as s:
        m = s.monitors[1]
        raw = s.grab(m)
        im = Image.frombytes("RGB", raw.size, raw.rgb)
    # 缩到长边 1280,省 token 又不丢定位精度;坐标按比例还原
    scale = min(1.0, 1280 / max(im.size))
    if scale < 1.0:
        im = im.resize((int(im.size[0] * scale), int(im.size[1] * scale)))
    p = OUT / f"step{step:02d}.png"; im.save(p)
    buf = io.BytesIO(); im.save(buf, format="PNG")
    return im.size, base64.b64encode(buf.getvalue()).decode(), p


ACTIONS = """你只能输出一个 JSON 对象,不要有其他文字。可用动作:
{"action":"click","x":<截图像素x>,"y":<截图像素y>}            单击
{"action":"double_click","x":..,"y":..}                        双击
{"action":"right_click","x":..,"y":..}                         右键
{"action":"type","text":"要输入的文字"}                        在当前焦点处打字
{"action":"key","keys":["ctrl","s"]}                           组合键(mac 上 ctrl 会自动换成 command)
{"action":"scroll","dx":0,"dy":-3}                             滚动
{"action":"wait","seconds":2}                                  等待
{"action":"done","reason":"为什么认为完成了"}                  任务完成
坐标以你看到的截图为准(左上角为原点)。每次只做一个动作。只输出 JSON,不要输出任何工具调用标记或其他文字。"""


def call_model(history, img_b64, img_size, instruction):
    sys_txt = (f"你在操作一台 {SYS} 电脑的图形界面。屏幕截图尺寸 {img_size[0]}x{img_size[1]}。\n"
               f"任务:{instruction}\n\n{ACTIONS}")
    msgs = [{"role": "user", "content": [{"type": "text", "text": sys_txt}]}]
    for h in history[-6:]:
        msgs.append({"role": "assistant", "content": json.dumps(h["action"], ensure_ascii=False)})
        msgs.append({"role": "user", "content": [{"type": "text", "text": f"上一步执行结果:{h['result']}。这是最新截图,请给下一个动作。"}]})
    msgs[-1]["content"].append({"type": "image_url", "image_url": {"url": "data:image/png;base64," + img_b64}})
    body = {"model": a.model, "messages": msgs, "max_completion_tokens": 300}
    req = urllib.request.Request(a.base.rstrip("/") + "/chat/completions", data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json", "Authorization": "Bearer " + KEY})
    # ★网关限流(429)和连接被掐断是**通道故障,不是模型的回答**。
    #   原来这里一次失败就把整轮 break 掉、记成 resolved=0,看起来和「模型做错了」一模一样——
    #   噪声底线实验里 6 格就是这么被毁的(第 1 步 429,agent 一张截图都没看到)。
    #   改成退避重试;重试仍失败才抛出,由调用方标成通道故障而不是模型失败。
    last = None
    for attempt in range(5):
        try:
            with urllib.request.urlopen(req, timeout=180) as r:
                return json.load(r)["choices"][0]["message"]["content"]
        except Exception as e:
            last = e
            code = getattr(e, "code", None)
            transient = code in (429, 500, 502, 503, 504) or isinstance(e, urllib.error.URLError) or "timed out" in str(e)
            if not transient or attempt == 4: raise
            wait = min(60, 5 * (2 ** attempt))
            log(f"  通道故障 {type(e).__name__} {code or ''},{wait}s 后重试(第 {attempt+1}/5 次)")
            time.sleep(wait)
    raise last


def parse_action(txt):
    # deepseek 有时吐工具调用标记(如 <｜｜DSML｜｜ calls>)而不是纯 JSON,round A 每道浪费 1~4 步;剥掉标记再找 JSON
    txt = re.sub(r"<｜｜[^>]*>|<\|\|[^>]*>", "", txt or "")
    m = re.search(r"\{[\s\S]*\}", txt)
    if not m: return None
    try: return json.loads(m.group(0))
    except Exception:
        try: return json.loads(m.group(0).replace("'", '"'))
        except Exception: return None


def do(act, img_size):
    """把截图坐标折算回屏幕坐标再执行。"""
    kx, ky = SCREEN_W / img_size[0], SCREEN_H / img_size[1]
    t = act.get("action")
    if t in ("click", "double_click", "right_click"):
        x, y = int(act["x"] * kx), int(act["y"] * ky)
        pyautogui.moveTo(x, y, duration=0.15)
        if t == "click": pyautogui.click()
        elif t == "double_click": pyautogui.doubleClick()
        else: pyautogui.rightClick()
        return f"在屏幕({x},{y})执行了{t}"
    if t == "type":
        pyautogui.typewrite(act.get("text", ""), interval=0.02); return f"输入了 {len(act.get('text',''))} 个字符"
    if t == "key":
        keys = [("command" if (SYS == "Darwin" and k.lower() in ("ctrl", "control")) else k) for k in act.get("keys", [])]
        pyautogui.hotkey(*keys); return f"按了 {'+'.join(keys)}"
    if t == "scroll":
        pyautogui.scroll(int(act.get("dy", 0)) * 100); return "滚动了"
    if t == "wait":
        time.sleep(min(10, float(act.get("seconds", 1)))); return "等待结束"
    if t == "done": return "DONE"
    return f"不认识的动作 {t}"


if not KEY: sys.exit("NO-API-KEY")
instruction = a.instruction or "(未提供任务文本)"
log(f"平台 {SYS} 屏幕 {SCREEN_W}x{SCREEN_H} 模型 {a.model} 任务:{instruction[:70]}")
history = []
channel_fail = None          # 非 None 表示这一轮是通道故障(网关限流/连接断),不是模型的成绩
for step in range(1, a.max_steps + 1):
    size, b64, path = shot(step)
    try:
        raw = call_model(history, b64, size, instruction)
    except Exception as e:
        # 重试完仍失败 = 通道故障。必须和「模型答错」分开标:这一轮根本没跑成,不能当成模型的成绩记 0 分。
        channel_fail = f"{type(e).__name__} {getattr(e, 'code', '') or ''}".strip()
        log(f"第{step}步 ★通道故障(已重试 5 次仍失败){channel_fail}: {str(e)[:140]}"); break
    act = parse_action(raw)
    _r = str(raw)[:300]; blind = bool(re.search(r"(?i)cannot (view|see) images|text-based UI|no image|can't see the image|unable to (view|see) (the )?image", _r))
    if blind: log(f"第{step}步 ★模型说看不到图: {_r[:100]}")
    if not act:
        log(f"第{step}步 模型没给出可解析的动作: {str(raw)[:100]}"); history.append({"action": {"action": "?"}, "result": "上次输出无法解析,请只输出 JSON", "raw": _r, "blind": blind}); continue
    if act.get("action") == "done":
        log(f"第{step}步 模型认为完成:{act.get('reason','')[:80]}"); history.append({"action": act, "result": "DONE"}); break
    try: r = do(act, size)
    except Exception as e: r = f"执行失败 {type(e).__name__}: {str(e)[:80]}"
    log(f"第{step}步 {json.dumps(act, ensure_ascii=False)[:90]} → {r}")
    history.append({"action": act, "result": r, "raw": _r, "blind": blind})
    time.sleep(0.8)
nblind = sum(1 for h in history if h.get("blind"))
json.dump({"platform": platform.platform(), "model": a.model, "steps": len(history), "blind_steps": nblind,
           "channel_fail": channel_fail, "history": history}, open(OUT / "loop.json", "w"), ensure_ascii=False, indent=1)
# ★channel_fail 一定要打进 LOOP-DONE:收割据此把「通道故障」和「模型没做出来」分开,
#   否则两者都长成 resolved=0,只能靠步数猜,而「跑到第 3 步才被限流」是猜不出来的。
print(f"LOOP-DONE steps={len(history)} platform={SYS} blind={nblind} channel_fail={channel_fail or 'none'}")
