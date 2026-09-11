#!/usr/bin/env python3
"""绕开 OpenClaw,直接按 OpenAI 兼容格式做「两轮工具调用」:
第一轮给工具让模型调用,第二轮把工具结果发回去看模型能不能继续。
这能把「模型/网关处理不了 tool 结果」与「OpenClaw 发的格式不对」分开。"""
import json, os, sys, urllib.request
for _st in (sys.stdout, sys.stderr):
    try: _st.reconfigure(encoding="utf-8", errors="replace")
    except Exception: pass
KEY=os.environ["ENVSHIFT_API_KEY"]; BASE="https://api.llmgateway.io/v1/chat/completions"
H={"Content-Type":"application/json","Authorization":"Bearer "+KEY}
TOOLS=[{"type":"function","function":{"name":"exec","description":"在机器上执行一条 shell 命令",
        "parameters":{"type":"object","properties":{"command":{"type":"string"}},"required":["command"]}}}]
def post(body):
    req=urllib.request.Request(BASE, data=json.dumps(body).encode(), headers=H)
    try:
        with urllib.request.urlopen(req, timeout=120) as r: return 200, json.load(r)
    except Exception as e:
        t=""
        if hasattr(e,"read"):
            try: t=e.read().decode()
            except Exception: pass
        return getattr(e,"code",0), t
RESULT_TEXT = ("grep: /testbed/requests/__pycache__/auth.cpython-39.pyc: binary file matches\n"
               "/testbed/requests/auth.py:72:        qop = self.chal.get('qop')\n"
               "/testbed/requests/auth.py:123:        noncebit = \"%s:%s:%s:%s:%s\"\n")
for model in sys.argv[1:] or ["gemini-3.5-flash","gemini-3.1-pro-preview","gemini-2.5-pro","deepseek-v4-pro"]:
    print(f"══ {model}")
    msgs=[{"role":"user","content":"用 exec 工具在 /testbed 里搜索 qop 这个词。"}]
    c1,r1=post({"model":model,"messages":msgs,"tools":TOOLS,"max_completion_tokens":200})
    if c1!=200: print(f"   第一轮失败 HTTP {c1}: {str(r1)[:200]}"); continue
    msg=r1["choices"][0]["message"]; tc=msg.get("tool_calls") or []
    print(f"   第一轮: {'调了工具 '+tc[0]['function']['name'] if tc else '没调工具,直接回文字'}")
    if not tc: continue
    msgs.append({"role":"assistant","content":msg.get("content"),"tool_calls":tc})
    msgs.append({"role":"tool","tool_call_id":tc[0]["id"],"content":RESULT_TEXT})
    c2,r2=post({"model":model,"messages":msgs,"tools":TOOLS,"max_completion_tokens":200})
    if c2==200:
        m2=r2["choices"][0]["message"]
        print(f"   第二轮: OK → {(m2.get('content') or '(调了下一个工具)')[:110]}")
    else:
        print(f"   ★第二轮失败 HTTP {c2}: {str(r2)[:320]}")
