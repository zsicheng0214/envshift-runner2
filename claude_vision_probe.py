#!/usr/bin/env python3
"""Claude 在网关上收不了图,到底是模型不行还是网关转换不对?
试三种发法:① OpenAI 的 image_url(data URL) ② Anthropic 原生 image/source ③ 走网关的 /v1/messages 端点。
把完整错误打出来,不截断。"""
import base64, json, os, sys, urllib.request, zlib
from struct import pack
for _st in (sys.stdout, sys.stderr):
    try: _st.reconfigure(encoding="utf-8", errors="replace")
    except Exception: pass
KEY=os.environ["ENVSHIFT_API_KEY"]; BASE="https://api.llmgateway.io/v1"
def png(w,h,px):
    raw=b"".join(b"\x00"+bytes(px[y*w*3:(y+1)*w*3]) for y in range(h))
    def ch(t,d):
        c=t+d; return pack(">I",len(d))+c+pack(">I",zlib.crc32(c)&0xffffffff)
    return b"\x89PNG\r\n\x1a\n"+ch(b"IHDR",pack(">IIBBBBB",w,h,8,2,0,0,0))+ch(b"IDAT",zlib.compress(raw))+ch(b"IEND",b"")
w,h=400,300; px=bytearray([245]*(w*h*3))
for y in range(200,260):
    for x in range(300,360):
        i=(y*w+x)*3; px[i],px[i+1],px[i+2]=220,30,30
B64=base64.b64encode(png(w,h,px)).decode()
Q="图里有一个彩色方块,是什么颜色?只答颜色。"
def post(url, body, hdr):
    req=urllib.request.Request(url, data=json.dumps(body).encode(), headers=hdr)
    try:
        with urllib.request.urlopen(req, timeout=120) as r: return 200, json.load(r)
    except Exception as e:
        t=""
        if hasattr(e,"read"):
            try: t=e.read().decode()
            except Exception: pass
        return getattr(e,"code",0), t
def show(tag, code, resp):
    if code==200:
        try:
            c=resp["choices"][0]["message"]["content"] if "choices" in resp else resp.get("content")
            print(f"   {tag}: OK → {str(c)[:90]}")
        except Exception: print(f"   {tag}: OK 但结构意外 {str(resp)[:120]}")
    else: print(f"   {tag}: HTTP {code} → {str(resp)[:340]}")
H={"Content-Type":"application/json","Authorization":"Bearer "+KEY}
for m in ("claude-sonnet-5","claude-opus-4-7","claude-fable-5-1","claude-haiku-4-5"):
    print(f"══ {m}")
    show("① OpenAI image_url", *post(BASE+"/chat/completions",
        {"model":m,"messages":[{"role":"user","content":[{"type":"text","text":Q},{"type":"image_url","image_url":{"url":"data:image/png;base64,"+B64}}]}],"max_completion_tokens":40}, H))
    show("② Anthropic source", *post(BASE+"/chat/completions",
        {"model":m,"messages":[{"role":"user","content":[{"type":"text","text":Q},{"type":"image","source":{"type":"base64","media_type":"image/png","data":B64}}]}],"max_completion_tokens":40}, H))
    show("③ /v1/messages", *post(BASE+"/messages",
        {"model":m,"max_tokens":40,"messages":[{"role":"user","content":[{"type":"text","text":Q},{"type":"image","source":{"type":"base64","media_type":"image/png","data":B64}}]}]},
        {**H,"anthropic-version":"2023-06-01"}))
    show("④ 纯文本(对照)", *post(BASE+"/chat/completions",
        {"model":m,"messages":[{"role":"user","content":"回答:1+1=?"}],"max_completion_tokens":20}, H))
