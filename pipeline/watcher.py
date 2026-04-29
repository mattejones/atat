"""
watcher.py — Monitors the /jds directory for new files and triggers the pipeline.

Uses PollingObserver rather than the default OS-native Observer to ensure
compatibility with OneDrive, network drives, and other virtual filesystems
that intercept or delay native filesystem events.

Polling interval defaults to 2 seconds — sufficient for a drop-folder use case.
"""

import logging
import time
from pathlib import Path
from watchdog.observers.polling import PollingObserver
from watchdog.events import FileSystemEventHandler

from pipeline.config import JDS_PATH, LOG_LEVEL
from pipeline.tailorer import process_jd

logging.basicConfig(level=LOG_LEVEL, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

VALID_SUFFIXES = {".txt", ".md"}
POLL_INTERVAL  = 2  # seconds


class JDHandler(FileSystemEventHandler):
    """Handles new files dropped into the JDs folder."""

    def __init__(self):
        super().__init__()
        self._seen = set()

    def _handle(self, event):
        if event.is_directory:
            return

        path = Path(event.src_path)

        if path.suffix not in VALID_SUFFIXES:
            return

        # Deduplicate — polling can fire multiple events per file
        if path in self._seen:
            return
        self._seen.add(path)

        log.info(f"New JD detected: {path.name}")
        try:
            process_jd(path)
        except Exception as e:
            log.error(f"Failed to process {path.name}: {e}", exc_info=True)
        finally:
            # Allow reprocessing if the user drops the same filename again
            self._seen.discard(path)

    def on_created(self, event):
        self._handle(event)

    def on_modified(self, event):
        self._handle(event)


def start():
    JDS_PATH.mkdir(parents=True, exist_ok=True)
    log.info(f"Watching for JDs in: {JDS_PATH}")
    log.info(f"Polling every {POLL_INTERVAL}s — drop a .txt or .md file to trigger.")

    handler  = JDHandler()
    observer = PollingObserver(timeout=POLL_INTERVAL)
    observer.schedule(handler, str(JDS_PATH), recursive=False)
    observer.start()

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        log.info("Stopping watcher...")
        observer.stop()

    observer.join()


if __name__ == "__main__":
    start()
