#!/usr/bin/env python3
"""Block commits that contain secrets.

  python scripts/check_secrets.py          scan what is staged (this is what the pre-commit hook runs)
  python scripts/check_secrets.py --all    scan every tracked file

It reports file and line, never the secret itself.
"""
import pathlib
import re
import subprocess
import sys

PATTERNS = [
    ("Supabase secret key", re.compile(r"sb_secret_[A-Za-z0-9_\-]{8,}")),
    ("JWT (legacy Supabase keys are JWTs)", re.compile(r"eyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}")),
    ("connection string with a password", re.compile(r"postgres(?:ql)?://[^\s:@/]+:[^\s@/]+@")),
    ("credential assigned a value", re.compile(
        r"(?i)\b(GUAVA_API_KEY|SUPABASE_SECRET_KEY|SUPABASE_SERVICE_ROLE_KEY|SUPABASE_DB_PASSWORD|DATABASE_PASSWORD|DB_PASSWORD)\b"
        r"\s*[:=]\s*(?!os\.environ|os\.getenv)[\"']?[^\s\"'<>]{6,}")),
]
SELF = "scripts/check_secrets.py"


def git(*args):
    return subprocess.run(["git", *args], capture_output=True, text=True, check=True).stdout


def scan(path, line_no, text, findings):
    for label, pat in PATTERNS:
        if pat.search(text):
            findings.append((path, line_no, label))


def main():
    findings = []
    if "--all" in sys.argv:
        for path in git("ls-files").splitlines():
            if path == SELF:
                continue
            p = pathlib.Path(path)
            if not p.is_file() or p.stat().st_size > 1_000_000:
                continue
            try:
                text = p.read_text()
            except (UnicodeDecodeError, OSError):
                continue
            for n, line in enumerate(text.splitlines(), 1):
                scan(path, n, line, findings)
    else:
        for path in git("diff", "--cached", "--name-only").splitlines():
            if pathlib.PurePath(path).name == ".env":
                findings.append((path, 0, "a .env file is staged"))
        path, line_no = None, 0
        for line in git("diff", "--cached", "--no-color", "-U0").splitlines():
            if line.startswith("+++ b/"):
                path = line[6:]
            elif line.startswith("@@"):
                m = re.search(r"\+(\d+)", line)
                line_no = int(m.group(1)) - 1 if m else 0
            elif line.startswith("+") and not line.startswith("+++"):
                line_no += 1
                if path and path != SELF:
                    scan(path, line_no, line[1:], findings)
    if findings:
        print("BLOCKED: possible secrets found. Move them to .env (git-ignored) and rotate anything already exposed.")
        for path, n, label in findings:
            print(f"  {path}:{n}  {label}")
        sys.exit(1)
    print("no secrets found")


main()
