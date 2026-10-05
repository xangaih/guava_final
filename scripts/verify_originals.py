#!/usr/bin/env python3
"""Record or verify SHA-256 hashes of originals/.

  python scripts/verify_originals.py --record   (done once by bootstrap)
  python scripts/verify_originals.py            (run before every commit)
"""
import hashlib
import pathlib
import sys

root = pathlib.Path(__file__).resolve().parent.parent / "originals"
sums = root / "CHECKSUMS.txt"


def digest(path: pathlib.Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


current = {str(p.relative_to(root)): digest(p) for p in sorted(root.rglob("__main__.py"))}

if "--record" in sys.argv:
    sums.write_text("".join(f"{h}  {n}\n" for n, h in current.items()))
    print(f"recorded {len(current)} file(s)")
    sys.exit(0)

recorded = {}
for line in sums.read_text().splitlines():
    if line.strip():
        h, name = line.split("  ", 1)
        recorded[name.strip()] = h

changed = sorted(n for n in set(recorded) | set(current) if recorded.get(n) != current.get(n))
if changed:
    print("ORIGINALS CHANGED:", ", ".join(changed))
    sys.exit(1)
print("originals unchanged")
