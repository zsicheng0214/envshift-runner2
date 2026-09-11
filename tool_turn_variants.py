#!/usr/bin/env python3
"""复现 OpenClaw 下 gemini 的 thought_signature 报错:标准两轮已知 OK,那到底哪个请求形态触发?
逐个变体测,只改一个因素。"""
import json, os, sys, urllib.request
for _st in (sys.stdout, sys.stderr):
    try: _st.reconfigure(encoding="utf-8", errors="replace")
    except Exception: pass
KEY=os.environ["ENVSHIFT_API_KEY"]; BASE="https://api.llmgateway.io/v1/chat/completions"
H={"Content-Type":"application/json","Authorization":"Bearer "+KEY}
TOOLS=[{"type":"function","function":{"name":"exec","description":"执行 shell 命令",
        "parameters":{"type":"object","properties":{"command":{"type":"string"}},"required":["command"]}}}]
RES="/testbed/requests/auth.py:72:        qop = self.chal.get('qop')\n"
def post(body):
    req=urllib.request.Request(BASE, data=json.dumps(body).encode(), headers=H)
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            raw=r.read().decode()
            return 200, raw[:400] if body.get("stream") else json.loads(raw)
    except Exception as e:
        t=""
        if hasattr(e,"read"):
            try: t=e.read().decode()
            except Exception: pass
        return getattr(e,"code",0), t
MODEL=sys.argv[1] if len(sys.argv)>1 else "gemini-3.5-flash"
def two_turn(tag, **extra):
    msgs=[{"role":"user","content":"用 exec 在 /testbed 搜索 qop。"}]
    b1={"model":MODEL,"messages":msgs,"tools":TOOLS,"max_completion_tokens":200}
    c1,r1=post(b1)
    if c1!=200: print(f"   {tag:28s} 第一轮 HTTP {c1}: {str(r1)[:140]}"); return
    msg=r1["choices"][0]["message"]; tc=msg.get("tool_calls") or []
    if not tc: print(f"   {tag:28s} 第一轮没调工具"); return
    asst={"role":"assistant","content":msg.get("content"),"tool_calls":tc}
    if extra.pop("drop_content", False): asst.pop("content", None)
    if extra.pop("null_content", False): asst["content"]=None
    msgs += [asst, {"role":"tool","tool_call_id":tc[0]["id"],"content":RES}]
    b2={"model":MODEL,"messages":msgs,"tools":TOOLS,"max_completion_tokens":200, **extra}
    c2,r2=post(b2)
    if c2==200: print(f"   {tag:28s} 第二轮 OK")
    else:
        s=str(r2); hit="★thought_signature" if "thought_signature" in s else ""
        print(f"   {tag:28s} 第二轮 HTTP {c2} {hit}: {s[:190]}")
print(f"══ {MODEL} 的请求形态变体")
two_turn("① 标准")
two_turn("② content=None", null_content=True)
two_turn("③ 无 content 字段", drop_content=True)
two_turn("④ stream=True", stream=True)
two_turn("⑤ 带 tool_choice=auto", tool_choice="auto")
two_turn("⑥ 带 reasoning_effort", reasoning_effort="low")
two_turn("⑦ 带 parallel_tool_calls", parallel_tool_calls=False)
two_turn("⑧ 带 temperature+top_p", temperature=0.0, top_p=1.0)
