import os, platform, subprocess, time, pathlib, json, tempfile
r = pathlib.Path(tempfile.mkdtemp()); p = {"runner": os.environ.get("RUNNER_OS"), "platform": platform.platform(), "machine": platform.machine(), "python": platform.python_version()}
(r/".Probe").write_text("A"); (r/".probe").write_text("B"); p["fs_case_sensitive"] = (r/".Probe").read_text() == "A"
t = r/".p2"; t.write_text("x"); os.utime(t, (1700000001.5,)*2); p["mtime_read_back"] = os.stat(t).st_mtime
try: os.chmod(t, 0o600); p["chmod_600_gives"] = oct(os.stat(t).st_mode & 0o777)
except Exception as e: p["chmod_600_gives"] = f"ERR {type(e).__name__}"
t2 = r/".p3"; t2.write_text("x"); p["clock_skew_s"] = int(time.time() - os.stat(t2).st_mtime)
try: p["umask"] = "%03o" % (lambda m: (os.umask(m), m)[1])(os.umask(0))
except Exception as e: p["umask"] = f"N/A {type(e).__name__}"
def sh(c):
    try: return subprocess.run(c, shell=True, capture_output=True, text=True, timeout=15).stdout.strip()[:70]
    except Exception as e: return f"ERR {e}"
p["ls_flavor"] = sh("ls --version 2>&1 | head -1"); p["sed_i"] = sh("printf 'a\\n' > $TMPDIR/s 2>/dev/null || printf 'a\\n' > /tmp/s; f=$TMPDIR/s; [ -f \"$f\" ] || f=/tmp/s; sed -i 's/a/b/' \"$f\" 2>&1 | head -1; cat \"$f\"")
p["date_d"] = sh("date -d 2020-01-01 +%F 2>&1 | head -1"); p["node"] = sh("node -v 2>&1"); p["shell_bash"] = sh("bash --version | head -1")
p["locale_env"] = os.environ.get("LC_ALL") or os.environ.get("LANG"); p["tr_upper_I"] = sh("echo I | tr '[:upper:]' '[:lower:]'")
home = os.environ.get("HOME") or os.environ.get("USERPROFILE") or ""
try: p["home_dir_mode"] = oct(os.stat(home).st_mode & 0o777)
except Exception as e: p["home_dir_mode"] = f"ERR {type(e).__name__}"
p["path_sep"] = os.sep; p["newline"] = repr(os.linesep)
try:
    import multiprocessing, shutil
    p["cpu_count"] = multiprocessing.cpu_count()
    p["mem_gb"] = round(os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES") / 1e9, 1) if hasattr(os, "sysconf") and "SC_PHYS_PAGES" in os.sysconf_names else None
    p["disk_free_gb"] = round(shutil.disk_usage("/").free / 1e9, 1)
    p["processor"] = platform.processor() or "?"
except Exception as _e:
    p["hw_probe_error"] = str(_e)
print("PROFILE " + json.dumps(p, ensure_ascii=False))
