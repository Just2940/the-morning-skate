#!/usr/bin/env python3
"""Bootstrap update-content.py: decompress staging files, validate, commit, push, exit.

One-shot replacement. On first workflow run it unpacks staging/index.html.gz and the
staging/update-content.py.gz.part_* chunks into place (replacing itself with the real
update-content.py), validates, commits, and pushes. Subsequent runs are no-ops.
"""
import gzip
import subprocess
import sys
from pathlib import Path

TARGETS = [
    ("index.html", ["staging/index.html.gz"]),
    ("scripts/update-content.py", [
        "staging/update-content.py.gz.part_aa",
        "staging/update-content.py.gz.part_ab",
        "staging/update-content.py.gz.part_ac",
        "staging/update-content.py.gz.part_ad",
    ]),
]

changes = False
used = []
for target, parts in TARGETS:
    pp = [Path(p) for p in parts]
    if not all(p.exists() for p in pp):
        missing = [str(p) for p in pp if not p.exists()]
        print(f"[boot] skip {target}: missing {missing}")
        continue
    gz = b"".join(p.read_bytes() for p in pp)
    try:
        data = gzip.decompress(gz)
    except Exception as e:
        print(f"[boot] FAIL decompressing {target}: {e}", file=sys.stderr)
        sys.exit(1)
    Path(target).parent.mkdir(parents=True, exist_ok=True)
    Path(target).write_bytes(data)
    print(f"[boot] {len(pp)} part(s) -> {target} ({len(gz)} gz -> {len(data)} bytes)")
    used.extend(pp)
    changes = True

if not changes:
    print("[boot] no staging files; already bootstrapped. no-op.")
    sys.exit(0)

for p in used:
    try: p.unlink()
    except OSError: pass
try: Path("staging").rmdir()
except OSError: pass

rc = subprocess.run(
    [sys.executable, "scripts/validate_content.py", "--data", "data.json", "--html", "index.html"]
).returncode
if rc != 0:
    print("[boot] validation FAILED; aborting without commit.", file=sys.stderr)
    sys.exit(rc)

subprocess.run(["git", "config", "user.name", "Morning Skate Bootstrap"], check=True)
subprocess.run(["git", "config", "user.email", "bootstrap@themorningskate.com"], check=True)
subprocess.run(["git", "add", "-A"], check=True)
if subprocess.run(["git", "diff", "--staged", "--quiet"]).returncode == 0:
    print("[boot] nothing staged; exiting.")
    sys.exit(0)
subprocess.run(["git", "commit", "-m", "Bootstrap: decompress staging files into place"], check=True)
subprocess.run(["git", "push"], check=True)
print("[boot] pushed decompressed files. exiting 0.")
sys.exit(0)