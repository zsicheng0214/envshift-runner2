#!/bin/bash
# 在任务容器里跑一轮 OpenClaw(与 HarnessBench 同一套配法:自写 openclaw.json + 本地 gateway + `openclaw agent`)。
# 用法(容器内): oc_agent.sh <workspace> <provider-name> <base_url> <keyfile> <model> <prompt文件> <out目录> [timeout秒]
# 依赖:/opt/ockit/node/bin/node 与 /opt/ockit/lib/openclaw(从 harnessbench/openclaw:2026.6.9 镜像抽出的 kit)
set -u
WS="$1"; PROV="$2"; BASE="$3"; KEYF="$4"; MODEL="$5"; PROMPT="$6"; OUT="$7"; TMO="${8:-1800}"
OC="/opt/ockit/node/bin/node /opt/ockit/lib/openclaw/openclaw.mjs"
export PATH=/opt/ockit/node/bin:$PATH
ST=/tmp/oc-state; HOME_DIR=/tmp/oc-home; mkdir -p "$ST" "$HOME_DIR" "$OUT"; chmod 700 "$ST" "$HOME_DIR"
TOKEN=$(head -c 24 /dev/urandom | od -An -tx1 | tr -d ' \n')
KEY=$(cat "$KEYF")
python3 - "$ST/openclaw.json" "$PROV" "$BASE" "$KEY" "$MODEL" "$WS" <<'PY'
import json, sys
p, prov, base, key, model, ws = sys.argv[1:7]
cfg = {
  "models": {"providers": {prov: {"baseUrl": base, "apiKey": key, "api": "openai-completions", "models": [{"id": model, "name": model}]}}},
  "agents": {"defaults": {"workspace": ws, "model": {"primary": f"{prov}/{model}"}, "models": {f"{prov}/{model}": {"alias": model}}}},
  "gateway": {"mode": "local", "bind": "loopback", "port": 18789, "auth": {"mode": "token"}},
}
open(p, "w").write(json.dumps(cfg, indent=1)); import os; os.chmod(p, 0o600)
PY
export HOME="$HOME_DIR" OPENCLAW_STATE_DIR="$ST" OPENCLAW_CONFIG_PATH="$ST/openclaw.json" OPENCLAW_CONFIG="$ST/openclaw.json" \
       OPENCLAW_WORKSPACE_DIR="$WS" OPENCLAW_GATEWAY_TOKEN="$TOKEN" OPENCLAW_EXEC_SHELL_SNAPSHOT=off NO_PROXY=127.0.0.1,localhost no_proxy=127.0.0.1,localhost
cd "$WS"
$OC gateway run --bind loopback --port 18789 --auth token > "$OUT/gateway.log" 2>&1 &
GW=$!
for i in $(seq 1 60); do (echo > /dev/tcp/127.0.0.1/18789) 2>/dev/null && break; kill -0 $GW 2>/dev/null || { echo "GATEWAY-DIED"; tail -20 "$OUT/gateway.log"; exit 3; }; sleep 1; done
(echo > /dev/tcp/127.0.0.1/18789) 2>/dev/null || { echo "GATEWAY-NOT-READY"; tail -20 "$OUT/gateway.log"; kill $GW 2>/dev/null; exit 3; }
echo "gateway up (pid $GW)"
T0=$(date +%s)
timeout $((TMO+120)) $OC agent --session-id "envshift-$$" --message "$(cat "$PROMPT")" --thinking off --timeout "$TMO" --json > "$OUT/agent.json" 2> "$OUT/agent.stderr" 
RC=$?
echo "agent rc=$RC elapsed=$(( $(date +%s) - T0 ))s"
# 会话记录(轨迹)一并留下
cp -r "$ST" "$OUT/oc-state" 2>/dev/null; rm -f "$OUT/oc-state/openclaw.json"
kill $GW 2>/dev/null; sleep 1; kill -9 $GW 2>/dev/null
exit $RC
