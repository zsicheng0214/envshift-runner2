#!/bin/bash
# 起跑前把「环境里带着的答案」修掉:只删【不是 HEAD 祖先】的 refs(未来的 tag/分支),保留历史 tag(setuptools_scm 等要靠它算版本,
# 官方镜像正是这个状态:tag 全是 HEAD 祖先);再 reflog expire + gc 把未来对象清掉。
# 用法: sanitize_testbed.sh <repo目录>   ;断言失败 exit 9(装置自检:可达对象=HEAD祖先 且 无 unreachable)
# v2(2026-09-08):v1 把祖先 tag 一并删了,pytest 版本变 0.1.dev 触发 minversion 全套报错——装置自伤,已改。
set -u
R="$1"; cd "$R" || exit 9
cur=$(git rev-parse --abbrev-ref HEAD 2>/dev/null) || exit 9
before_all=$(git rev-list --all 2>/dev/null | wc -l); head_n=$(git rev-list HEAD | wc -l)
kept=0; dropped=0
while read -r ref; do
  [ -z "$ref" ] && continue
  [ "$cur" != "HEAD" ] && [ "$ref" = "refs/heads/$cur" ] && continue
  if git merge-base --is-ancestor "$ref" HEAD 2>/dev/null; then kept=$((kept+1)); else git update-ref -d "$ref" 2>/dev/null && dropped=$((dropped+1)); fi
done < <(git for-each-ref --format='%(refname)')
git stash clear 2>/dev/null; git reflog expire --expire=now --all 2>/dev/null; git gc --prune=now -q 2>/dev/null
after_all=$(git rev-list --all | wc -l); unreach=$(git fsck --unreachable --no-reflogs 2>/dev/null | grep -c unreachable)
echo "SANITIZE v2 repo=$R branch=$cur commits all:$before_all->$after_all head:$head_n unreachable:$unreach refs kept(ancestor):$kept dropped(future):$dropped"
[ "$after_all" = "$head_n" ] && [ "$unreach" = "0" ] || { echo "SANITIZE-ASSERT-FAIL"; exit 9; }
