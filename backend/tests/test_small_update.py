"""Small updates: the release side (manifest, patch zip) and the app side (find the patch, check it, swap the files in
with the real batch script, restart), on a throwaway app folder."""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "packaging"))

import patch as packager  # noqa: E402  (packaging/patch.py)
from surge import updates  # noqa: E402

BASE = "https://github.com/Satoruatwork/surge-clipper/"


def app_folder(root: Path, files: dict[str, bytes]) -> Path:
    for rel, data in files.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_bytes(data)
    return root


OLD = {"Surge.exe": b"old exe", "_internal/lib.dll": b"same library" * 100, "_internal/ui/index-old.js": b"old ui",
       "ffmpeg.exe": b"big unchanged binary" * 1000, "unins000.exe": b"uninstaller"}
NEW = {"Surge.exe": b"new exe!", "_internal/lib.dll": b"same library" * 100, "_internal/ui/index-new.js": b"new ui",
       "ffmpeg.exe": b"big unchanged binary" * 1000}


def build_patch(tmp: Path) -> tuple[Path, Path]:
    old = app_folder(tmp / "old", OLD)
    new = app_folder(tmp / "new", NEW)
    base = packager.manifest(old, "1.0.0")
    assert "unins000.exe" not in base["files"]  # the installer's own files never count
    zpath = packager.patch(new, base, tmp, "1.0.0", "1.0.1")
    return old, zpath


def test_patch_holds_only_what_changed(tmp_path):
    _, zpath = build_patch(tmp_path)
    with zipfile.ZipFile(zpath) as z:
        plan = json.loads(z.read("patch.json"))
        assert sorted(n for n in z.namelist() if n.startswith("files/")) == \
            ["files/Surge.exe", "files/_internal/ui/index-new.js"]
    assert plan["removed"] == ["_internal/ui/index-old.js"] and plan["from"] == "1.0.0" and plan["to"] == "1.0.1"
    assert set(plan["files"]) == set(NEW)


def test_the_release_names_the_patch_for_this_version():
    rel = {"tag_name": "v1.0.1", "html_url": BASE + "releases/tag/v1.0.1", "body": "", "assets": [
        {"name": "Surge-Setup-1.0.1.exe", "size": 300_000_000, "browser_download_url": BASE + "releases/download/v1.0.1/Surge-Setup-1.0.1.exe"},
        {"name": "Surge-Patch-1.0.0-to-1.0.1.zip", "size": 9_000_000, "digest": "sha256:ab12",
         "browser_download_url": BASE + "releases/download/v1.0.1/Surge-Patch-1.0.0-to-1.0.1.zip"},
        {"name": "Surge-Patch-0.9.0-to-1.0.1.zip", "size": 9_000_000,
         "browser_download_url": BASE + "releases/download/v1.0.1/Surge-Patch-0.9.0-to-1.0.1.zip"}]}
    got = updates._read(rel, "1.0.0")
    assert got["patch"] == {"url": BASE + "releases/download/v1.0.1/Surge-Patch-1.0.0-to-1.0.1.zip", "size": 9_000_000,
                            "sha256": "ab12"}
    assert updates._read(rel, "0.8.0")["patch"] is None  # no patch from that version: the installer it is


def test_a_tampered_or_unsafe_patch_is_refused(tmp_path):
    _, zpath = build_patch(tmp_path)
    bad = tmp_path / "bad.zip"
    with zipfile.ZipFile(zpath) as z, zipfile.ZipFile(bad, "w") as out:
        for item in z.infolist():
            data = z.read(item)
            out.writestr(item, b"evil" if item.filename == "files/Surge.exe" else data)
    with pytest.raises(updates.UpdateError, match="checksum"):
        updates.stage(bad, tmp_path / "stage1")
    for unsafe in ("../outside.exe", "C:/Windows/x.dll", "a\\b.dll", "/etc/x"):
        assert not updates._safe(unsafe)
    assert updates._safe("_internal/ui/index.js")


def test_an_install_that_differs_falls_back_to_the_installer(tmp_path):
    old, zpath = build_patch(tmp_path)
    plan = updates.stage(zpath, tmp_path / "stage")
    assert updates.differences(old, plan) == []
    (old / "ffmpeg.exe").write_bytes(b"someone replaced it")
    assert updates.differences(old, plan) == ["ffmpeg.exe"]


@pytest.mark.skipif(sys.platform != "win32", reason="the swap script is a Windows batch file")
def test_the_swap_script_updates_the_folder_and_restarts(tmp_path, monkeypatch):
    # both versions of Surge.exe are a harmless real program (hostname.exe plus a marker), so the restart runs it
    exe = Path(shutil.which("hostname") or r"C:\Windows\System32\HOSTNAME.EXE").read_bytes()
    monkeypatch.setitem(OLD, "Surge.exe", exe + b"v1")
    monkeypatch.setitem(NEW, "Surge.exe", exe + b"v2")
    old, zpath = build_patch(tmp_path)
    work = tmp_path / "work"
    plan = updates.stage(zpath, work / "files")
    updates._backup(old, plan, work / "backup")
    dead = subprocess.Popen(["cmd.exe", "/c", "exit 0"])
    dead.wait()  # a process id that has already exited: the script doesn't wait
    bat = work / "apply.cmd"
    bat.write_text(updates.script(old, work, plan, "1.0.1", dead.pid), encoding="utf-8")
    subprocess.run(["cmd.exe", "/c", str(bat)], timeout=120, check=False, stdin=subprocess.DEVNULL,
                   capture_output=True)  # no console, like the real run
    assert (old / "Surge.exe").read_bytes() == NEW["Surge.exe"]
    assert (old / "_internal/ui/index-new.js").read_bytes() == NEW["_internal/ui/index-new.js"]
    assert not (old / "_internal/ui/index-old.js").exists()
    assert (old / "unins000.exe").exists()  # the uninstaller stays
    assert (work / "backup/Surge.exe").exists() and "done" in (work / "apply.log").read_text(encoding="utf-8")


@pytest.mark.skipif(sys.platform != "win32", reason="the swap script is a Windows batch file")
def test_the_swap_script_waits_for_surge_to_close(tmp_path):
    """The wait must really wait without a console (`timeout` returns at once there, and the copy hit a running
    Surge.exe)."""
    import time
    old, zpath = build_patch(tmp_path)
    work = tmp_path / "work"
    plan = updates.stage(zpath, work / "files")
    sleeper = subprocess.Popen(["cmd.exe", "/c", "ping -n 4 127.0.0.1 >nul"])  # "Surge", closing in ~3 s
    bat = work / "apply.cmd"
    bat.write_text(updates.script(old, work, plan, "1.0.1", sleeper.pid), encoding="utf-8")
    t0 = time.time()
    subprocess.run(["cmd.exe", "/c", str(bat)], timeout=120, check=False, stdin=subprocess.DEVNULL,
                   capture_output=True)
    assert time.time() - t0 >= 2.0 and sleeper.poll() is not None


@pytest.mark.skipif(sys.platform != "win32", reason="Apps & features lives in the Windows registry")
def test_the_app_keeps_its_apps_and_features_version_in_step():
    import winreg
    key = r"Software\SurgeTest\UninstallEntry"
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, key) as k:
        winreg.SetValueEx(k, "DisplayVersion", 0, winreg.REG_SZ, "0.0.1")
    try:
        assert updates.sync_uninstall_entry(key) is True
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key) as k:
            assert winreg.QueryValueEx(k, "DisplayVersion")[0] == updates.__version__
            assert winreg.QueryValueEx(k, "DisplayName")[0] == f"Surge {updates.__version__}"
        assert updates.sync_uninstall_entry(key) is False  # already in step
        assert updates.sync_uninstall_entry(r"Software\SurgeTest\NoSuchEntry") is False
    finally:
        winreg.DeleteKey(winreg.HKEY_CURRENT_USER, key)
        winreg.DeleteKey(winreg.HKEY_CURRENT_USER, r"Software\SurgeTest")
