#!/usr/bin/env python3
"""从 secret 还原题目材料(分片 base64 → tar.gz → tasks/)。题目不进 git。"""
import base64, io, os, sys, tarfile, pathlib
parts = []
for i in range(1, 9):
    v = os.environ.get(f"TASKS_B64_{i}", "")
    if v: parts.append(v.strip())
if not parts:
    print("没有 TASKS_B64_* secret,无法还原题目"); sys.exit(2)
raw = base64.b64decode("".join(parts))
here = pathlib.Path(__file__).resolve().parent
with tarfile.open(fileobj=io.BytesIO(raw), mode="r:gz") as tf:
    tf.extractall(here)
n = len(list((here / "tasks").glob("*")))
print(f"已还原 {n} 个任务目录")
