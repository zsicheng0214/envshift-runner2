#!/usr/bin/env python3
"""模型通道探针:问网关有哪些模型,并逐个测「能不能看图」「能不能给出准确坐标」。
第二项才是 GUI 通道的生死线——很多模型能描述图片,但说不准该点哪里。
用法: model_probe.py [--base URL] [--models a,b,c]
"""
import argparse, base64, json, os, sys, urllib.request, zlib
from struct import pack
for _st in (sys.stdout, sys.stderr):
    try: _st.reconfigure(encoding="utf-8", errors="replace")
    except Exception: pass

ap = argparse.ArgumentParser()
ap.add_argument("--base", default="https://api.llmgateway.io/v1")
ap.add_argument("--models", default="")
a = ap.parse_args()
KEY = os.environ.get("ENVSHIFT_API_KEY", "")
if not KEY: sys.exit("NO-API-KEY")
H = {"Content-Type": "application/json", "Authorization": "Bearer " + KEY}


def png(w, h, px):
    raw = b"".join(b"\x00" + bytes(px[y * w * 3:(y + 1) * w * 3]) for y in range(h))
    def chunk(t, d):
        c = t + d; return pack(">I", len(d)) + c + pack(">I", zlib.crc32(c) & 0xffffffff)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)) + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b"")


def make_image():
    """800x600 白底:左上角蓝色方块、右下角红色方块。问红色方块中心坐标 → 约 (680, 480)。"""
    w, h = 800, 600
    px = bytearray([245] * (w * h * 3))
    def rect(x0, y0, x1, y1, c):
        for y in range(y0, y1):
            for x in range(x0, x1):
                i = (y * w + x) * 3; px[i], px[i + 1], px[i + 2] = c
    rect(60, 60, 180, 160, (30, 60, 220))       # 蓝色,中心约 (120,110)
    rect(620, 420, 740, 540, (220, 30, 30))     # 红色,中心约 (680,480)
    return png(w, h, px), (680, 480), (w, h)


IMG, TRUTH, SIZE = make_image()
B64 = base64.b64encode(IMG).decode()


def call(model, content, max_tok=120):
    body = {"model": model, "messages": [{"role": "user", "content": content}], "max_completion_tokens": max_tok}
    req = urllib.request.Request(a.base.rstrip("/") + "/chat/completions", data=json.dumps(body).encode(), headers=H)
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            return json.load(r)["choices"][0]["message"]["content"]
    except Exception as e:
        t = ""
        if hasattr(e, "read"):
            try: t = e.read().decode()[:200]
            except Exception: pass
        return f"ERR {type(e).__name__} {str(e)[:80]} {t}"


def list_models():
    req = urllib.request.Request(a.base.rstrip("/") + "/models", headers=H)
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return [m.get("id") for m in json.load(r).get("data", [])]
    except Exception as e:
        print("列模型失败:", type(e).__name__, str(e)[:120]); return []


models = [m.strip() for m in a.models.split(",") if m.strip()]
avail = list_models()
print(f"══ 网关 {a.base} 共 {len(avail)} 个模型")
if avail:
    import collections
    for k in ("glm", "claude", "gemini", "gpt", "grok", "qwen", "kimi", "deepseek", "hunyuan", "vl", "vision"):
        hit = [m for m in avail if k in str(m).lower()]
        if hit: print(f"  {k:9s} {len(hit):3d} 个: {' '.join(hit)}")
if not models: models = [m for m in avail if any(k in str(m).lower() for k in ("glm", "claude", "gemini", "gpt-5", "grok", "qwen", "vl"))][:12]

print(f"\n══ 逐个测视觉({len(models)} 个) | 图 {SIZE[0]}x{SIZE[1]},红块中心真值 {TRUTH}")
print(f"{'模型':34s} {'看图':6s} {'坐标':>12s} {'误差':>7s}  回答")
rows = []
for m in models:
    r1 = call(m, [{"type": "text", "text": "图里有两个方块,分别是什么颜色?只答两个颜色。"},
                  {"type": "image_url", "image_url": {"url": "data:image/png;base64," + B64}}])
    sees = ("红" in r1 or "red" in r1.lower()) and ("蓝" in r1 or "blue" in r1.lower())
    coord, err = "—", "—"
    if sees:
        r2 = call(m, [{"type": "text", "text": f"这是一张 {SIZE[0]}x{SIZE[1]} 的截图。红色方块中心的像素坐标是多少?只回答 x,y 两个数字,用逗号分隔,不要其他文字。"},
                      {"type": "image_url", "image_url": {"url": "data:image/png;base64," + B64}}])
        import re
        nums = re.findall(r"\d+", r2)
        if len(nums) >= 2:
            x, y = int(nums[0]), int(nums[1]); coord = f"({x},{y})"
            err = f"{((x-TRUTH[0])**2+(y-TRUTH[1])**2)**0.5:.0f}px"
    print(f"{m[:34]:34s} {'是' if sees else '否':6s} {coord:>12s} {err:>7s}  {str(r1)[:46]}")
    rows.append({"model": m, "sees": sees, "coord": coord, "err": err, "raw": str(r1)[:120]})
json.dump({"truth": TRUTH, "size": SIZE, "available": avail, "rows": rows}, open("model_probe.json", "w"), ensure_ascii=False, indent=1)
good = [r for r in rows if r["sees"] and r["err"] != "—" and float(str(r["err"]).rstrip("px")) < 60]
print(f"\n能看图且坐标误差 <60px 的:{len(good)} 个 → {' '.join(r['model'] for r in good)}")
