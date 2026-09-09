#!/bin/bash
# agent 阶段屏蔽「答案所在」域名(github / pypi),判分前恢复。hosts 文件在容器里是 bind mount,只能整文件覆写不能 sed -i。
# 用法: netblock.sh on|off [hosts文件路径]   (Linux/mac 默认 /etc/hosts;Windows 传 /c/Windows/System32/drivers/etc/hosts)
set -u
MODE="$1"; HF="${2:-/etc/hosts}"; BAK="${HF}.envshift.bak"
HOSTS="github.com raw.githubusercontent.com api.github.com codeload.github.com objects.githubusercontent.com gist.githubusercontent.com gist.github.com pypi.org files.pythonhosted.org test.pypi.org conda.anaconda.org repo.anaconda.com"
if [ "$MODE" = "on" ]; then
  [ -f "$BAK" ] || cp "$HF" "$BAK"
  { cat "$BAK"; echo "# envshift-netblock"; for h in $HOSTS; do printf "127.0.0.1 %s\n::1 %s\n" "$h" "$h"; done; } > "$HF"
  echo "NETBLOCK on ($(grep -c envshift-netblock "$HF"))"
elif [ "$MODE" = "off" ]; then
  [ -f "$BAK" ] && cat "$BAK" > "$HF" && rm -f "$BAK"; echo "NETBLOCK off ($(grep -c envshift-netblock "$HF"))"
fi
