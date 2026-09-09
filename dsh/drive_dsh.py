#!/usr/bin/env python3
"""EnvShift 的 DSH 薄驱动：node bridge.mjs 起 SDK，initialize→run→close，轨迹落盘。"""
import json, os, pathlib, subprocess, sys, threading, time

def main():
    for _st in (sys.stdout, sys.stderr):
        try: _st.reconfigure(encoding="utf-8", errors="replace")
        except Exception: pass
    ws, model, base_url, api_key, out_dir, prompt_file = sys.argv[1:7]
    ws = str(pathlib.Path(ws).resolve())
    out = pathlib.Path(out_dir); out.mkdir(parents=True, exist_ok=True)
    here = pathlib.Path(__file__).parent.resolve()
    nm = os.environ.get("DSH_NM", str(pathlib.Path.home() / "dsh-probe/node_modules"))
    env = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "HOME": os.environ.get("DSH_HOME_DIR", str(pathlib.Path.home() / ".envshift-dsh-home")),
        "HARNESSBENCH_DEEPSEEK_SDK_MODULE": f"{nm}/@deepseek-ai/dsh-sdk-client/lib/index.js",
        "DSH_CORDIS_CONFIG": os.environ.get("DSH_CONFIG", str(pathlib.Path.home() / "dsh-probe/cordis.yaml")),
        "DSH_CWD": ws,
        "DSH_HOME": os.environ.get("DSH_HOME_DIR", str(pathlib.Path.home() / ".envshift-dsh-home")),
        "DSH_TELEMETRY_DISABLED": "1",
        # cordis.yaml 的 sessions 插件默认 root='./.sessions'，会写进工作目录：
        # ① 只读工作目录（如 TB3 的 USER nobody + root-owned /app）直接 EACCES 起不来
        # ② 工作目录本身是 artifact 时（如 /app 整目录）会污染交付物
        "DSH_SESSION_ROOT": os.environ.get("DSH_SESSION_ROOT", "/tmp/dsh-sessions"),
        "DSH_PERMISSION_MODE": "danger-full-access",
        "DEEPSEEK_BASE_URL": base_url,
        "DEEPSEEK_API_KEY": api_key,
    }
    for _k in os.environ.get("ENVSHIFT_PASSTHROUGH", "").split(","):
        _k = _k.strip()
        if _k and os.environ.get(_k):
            env[_k] = os.environ[_k]
    for _k in ("LANG", "LC_ALL", "LANGUAGE", "TZ"):
        if os.environ.get(_k):
            env[_k] = os.environ[_k]
    if os.name == "nt":
        # ★Windows:子进程缺 SystemRoot/TEMP/USERPROFILE 等系统变量时,Node 的网络与加密会静默失败
        #   (initialize 不联网所以之前没暴露;chat 请求报 TRANSPORT)。以完整环境为底,再叠加我们的变量。
        env = {**os.environ, **env}
    pathlib.Path(env["HOME"]).mkdir(exist_ok=True)
    proc = subprocess.Popen([os.environ.get("DSH_NODE_BIN","node"), os.environ.get("DSH_BRIDGE", str(pathlib.Path.home() / "dsh-probe/bridge.mjs"))], cwd=ws, env=env,
                            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="replace", bufsize=1)
    trace = (out / "trace.jsonl").open("w", encoding="utf-8")
    stderr_buf = []
    threading.Thread(target=lambda: stderr_buf.extend(proc.stderr), daemon=True).start()

    def send(obj):
        proc.stdin.write(json.dumps(obj) + "\n"); proc.stdin.flush()

    def wait_result(rid, deadline):
        while time.time() < deadline:
            line = proc.stdout.readline()
            if not line:
                break
            try:
                msg = json.loads(line)
            except ValueError:
                continue
            trace.write(line); trace.flush()
            if msg.get("id") == rid and "result" in msg:
                return msg["result"]
            if msg.get("id") == rid and "error" in msg:
                raise RuntimeError(f"rpc error: {msg['error']}")
        raise TimeoutError(f"rpc id={rid} no result")

    send({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
        "runtimeCommand": os.environ.get("DSH_NODE_BIN", "node"),
        "runtimeArgs": [f"{nm}/@deepseek-ai/dsh-sdk-jsonrpc-demo/lib/bin.js", env["DSH_CORDIS_CONFIG"]],
        "cwd": ws, "env": env,
        "provider": "deepseek-official", "model": model, "maxTokens": int(os.environ.get("DSH_MAX_TOKENS", "64000")),
    }})
    print("init:", wait_result(1, time.time() + 120), flush=True)
    if os.environ.get("DSH_INIT_ONLY"):          # 只验证 harness 能在本平台启动,不调用模型
        send({"jsonrpc": "2.0", "id": 3, "method": "close", "params": {}})
        time.sleep(1); proc.terminate()
        (out / "stderr.txt").write_text("".join(stderr_buf[-200:]), encoding="utf-8")
        print("INIT-ONLY-OK", flush=True); return
    prompt = pathlib.Path(prompt_file).read_text(encoding="utf-8")
    send({"jsonrpc": "2.0", "id": 2, "method": "run",
          "params": {"input": prompt, "sessionId": "envshift-run"}})
    res = wait_result(2, time.time() + int(os.environ.get("DSH_RUN_TIMEOUT", "1500")))
    (out / "final.json").write_text(json.dumps(
        {"finalResponse": res.get("finalResponse"), "finishReason": res.get("finishReason")},
        ensure_ascii=False, indent=2), encoding="utf-8")
    print("finishReason:", res.get("finishReason"), flush=True)
    print("finalResponse:", (res.get("finalResponse") or "")[:200], flush=True)
    send({"jsonrpc": "2.0", "id": 3, "method": "close", "params": {}})
    time.sleep(1); proc.terminate()
    (out / "stderr.txt").write_text("".join(stderr_buf[-400:]), encoding="utf-8")

if __name__ == "__main__":
    main()
