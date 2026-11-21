"""PyInstaller entry point for Surge.exe.

The installed app is a windowed program: it has no console, so stdout and stderr go to a log file in the data folder
(handy for bug reports). Everything else is the normal `surge.main` entry."""
import multiprocessing
import sys

multiprocessing.freeze_support()

from surge import config  # noqa: E402

if sys.stdout is None or sys.stderr is None:
    _log = config.DATA / "surge.log"
    try:
        if _log.exists() and _log.stat().st_size > 5_000_000:
            _log.replace(_log.with_suffix(".old.log"))
    except OSError:
        pass
    _fh = open(_log, "a", encoding="utf-8", buffering=1)
    sys.stdout = sys.stdout or _fh
    sys.stderr = sys.stderr or _fh

from surge.main import main  # noqa: E402

main()
