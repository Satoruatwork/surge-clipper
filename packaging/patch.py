"""Small updates: a manifest of every file in the app folder, and a patch zip with only the files that changed since
the previous release, so an update downloads megabytes instead of the whole installer.

  python packaging/patch.py manifest <app folder> <manifest.json> --version 0.4.4
  python packaging/patch.py patch <app folder> <base manifest.json> <out dir> --from 0.4.3 --to 0.4.4

The patch zip holds patch.json and the changed files under files/. patch.json lists every file of the new version
(path -> sha256 and size) so the app can check its install before and after applying, plus what changed and what to
delete. The installer's own uninstaller files never count."""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import zipfile
from pathlib import Path

SKIP = ("unins",)  # Inno Setup's uninstaller files live in the install folder but are not part of the app


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def manifest(app: Path, version: str) -> dict:
    files = {}
    for p in sorted(app.rglob("*")):
        rel = p.relative_to(app).as_posix()
        if p.is_file() and not (p.parent == app and p.name.lower().startswith(SKIP)):
            files[rel] = {"sha256": sha256(p), "size": p.stat().st_size}
    return {"version": version, "files": files}


def patch(app: Path, base: dict, out_dir: Path, from_v: str, to_v: str) -> Path:
    new = manifest(app, to_v)
    old = base["files"]
    changed = sorted(k for k, v in new["files"].items() if old.get(k, {}).get("sha256") != v["sha256"])
    removed = sorted(k for k in old if k not in new["files"])
    out = out_dir / f"Surge-Patch-{from_v}-to-{to_v}.zip"
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        z.writestr("patch.json", json.dumps({"from": from_v, "to": to_v, "changed": changed, "removed": removed,
                                             "files": new["files"]}, indent=1))
        for rel in changed:
            z.write(app / rel, f"files/{rel}")
    size = sum(new["files"][k]["size"] for k in changed)
    print(f"{out.name}: {len(changed)} changed files ({size / 1e6:.1f} MB before compression), {len(removed)} removed, "
          f"{out.stat().st_size / 1e6:.1f} MB to download")
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    m = sub.add_parser("manifest")
    m.add_argument("app", type=Path)
    m.add_argument("out", type=Path)
    m.add_argument("--version", required=True)
    p = sub.add_parser("patch")
    p.add_argument("app", type=Path)
    p.add_argument("base", type=Path)
    p.add_argument("out_dir", type=Path)
    p.add_argument("--from", dest="from_v", required=True)
    p.add_argument("--to", dest="to_v", required=True)
    a = ap.parse_args()
    if a.cmd == "manifest":
        a.out.parent.mkdir(parents=True, exist_ok=True)
        a.out.write_text(json.dumps(manifest(a.app, a.version.lstrip("v")), indent=1), encoding="utf-8")
        print(f"{a.out}: {len(json.loads(a.out.read_text(encoding='utf-8'))['files'])} files")
    else:
        patch(a.app, json.loads(a.base.read_text(encoding="utf-8")), a.out_dir, a.from_v.lstrip("v"), a.to_v.lstrip("v"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
