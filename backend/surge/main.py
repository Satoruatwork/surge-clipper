"""Entry point: engine + API on 127.0.0.1:8765, shown in a native Windows window (WebView2)."""
from __future__ import annotations

import argparse
import logging
import os
import sys
import threading
import time

import uvicorn

from . import db, distribute, live, tools

PORT = int(os.environ.get("SURGE_PORT") or 8765)  # another port for a second copy, or when 8765 is taken


def serve() -> uvicorn.Server:
    from .api import app
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=PORT, log_level="warning", ws="websockets"))
    threading.Thread(target=server.run, daemon=True, name="api").start()
    for _ in range(100):
        if server.started:
            break
        time.sleep(0.1)
    return server


def main() -> None:
    ap = argparse.ArgumentParser(prog="surge")
    ap.add_argument("--server", action="store_true", help="run the engine without opening a window")
    ap.add_argument("--selftest", action="store_true", help="check the bundled components, print a report and exit")
    args = ap.parse_args()
    if args.selftest:
        from . import selftest
        sys.exit(selftest.run())
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", datefmt="%H:%M:%S")
    for noisy in ("faster_whisper", "httpx", "googleapiclient.discovery_cache"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    db.init()
    from . import sites
    sites.seed()
    distribute.start()
    threading.Thread(target=tools.detect_encoder, daemon=True).start()
    from . import fetch_sounds, library, updates, watch
    threading.Thread(target=library.warm_drops, daemon=True).start()
    threading.Thread(target=fetch_sounds.ensure, daemon=True, name="sound-pack").start()
    watch.start()
    updates.start()
    server = serve()
    url = f"http://127.0.0.1:{PORT}"
    print(f"Surge engine running at {url}")
    try:
        if args.server:
            while True:
                time.sleep(3600)
        else:
            import webview
            from .config import DATA
            sites.set_gui(True)
            webview.create_window("Surge", url, width=1480, height=940, min_size=(1180, 740),
                                  background_color="#17121F", text_select=True)
            webview.start(private_mode=False, storage_path=str(DATA / "webview"))  # site sign-ins persist
    except KeyboardInterrupt:
        pass
    finally:
        live.stop_all()
        distribute.stop()
        server.should_exit = True
        sys.exit(0)


if __name__ == "__main__":
    main()
