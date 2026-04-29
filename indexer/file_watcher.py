"""
File Watcher — Live Skeleton Sync
===================================
Monitors the codebase for file changes and incrementally updates
the local JSON skeleton index. Uses watchfiles (built on Rust's
notify crate) for efficient cross-platform file watching.

The watcher keeps the skeleton index always up-to-date so the
Pruner can search against a fresh map without full re-indexing.
"""

from __future__ import annotations

import asyncio
import os
import time
from pathlib import Path
from typing import Callable, Optional

try:
    from .models import SkeletalIndex
    from .skeletal_indexer import LANG_MAP, SKIP_DIRS, SkeletalIndexer
except ImportError:
    from indexer.models import SkeletalIndex
    from indexer.skeletal_indexer import LANG_MAP, SKIP_DIRS, SkeletalIndexer


class SkeletonFileWatcher:
    """
    Watches the codebase and incrementally patches the skeleton
    index when source files are created, modified, or deleted.

    Uses debouncing: batches rapid changes into a single re-index
    to avoid thrashing on save-all or branch-switch operations.
    """

    def __init__(
        self,
        indexer: SkeletalIndexer,
        skeleton: SkeletalIndex,
        debounce_ms: int = 500,
        on_update: Optional[Callable[[SkeletalIndex], None]] = None,
    ):
        self.indexer = indexer
        self.skeleton = skeleton
        self.debounce_ms = debounce_ms
        self.on_update = on_update
        self._running = False
        self._pending_changes: dict[str, str] = {}  # path -> change_type
        self._debounce_task: Optional[asyncio.Task] = None

    async def start(self):
        """Start watching for file changes."""
        self._running = True
        print(f"[file_watcher] Watching {self.indexer.root_path} for changes...")

        try:
            from watchfiles import awatch, Change

            async for changes in awatch(
                self.indexer.root_path,
                stop_event=self._make_stop_event(),
                watch_filter=self._should_watch,
                recursive=True,
            ):
                for change_type, path in changes:
                    rel_path = os.path.relpath(path, self.indexer.root_path)

                    # Skip non-source files
                    ext = Path(path).suffix
                    if ext not in LANG_MAP:
                        continue

                    # Skip files in ignored directories
                    if any(part in SKIP_DIRS for part in Path(rel_path).parts):
                        continue

                    change_name = {
                        Change.added: "added",
                        Change.modified: "modified",
                        Change.deleted: "deleted",
                    }.get(change_type, "unknown")

                    self._pending_changes[rel_path] = change_name

                # Debounce: wait before processing
                if self._debounce_task and not self._debounce_task.done():
                    self._debounce_task.cancel()
                self._debounce_task = asyncio.create_task(
                    self._process_after_debounce()
                )

        except ImportError:
            print("[file_watcher] watchfiles not installed — using polling fallback")
            await self._poll_fallback()

    async def _process_after_debounce(self):
        """Wait for debounce period then process all pending changes."""
        await asyncio.sleep(self.debounce_ms / 1000.0)

        if not self._pending_changes:
            return

        changes = dict(self._pending_changes)
        self._pending_changes.clear()

        start = time.time()
        updated = 0
        removed = 0

        for rel_path, change_type in changes.items():
            if change_type == "deleted":
                # Remove all entries for this file
                before = len(self.skeleton.entries)
                self.skeleton.entries = [
                    e for e in self.skeleton.entries if e.file_path != rel_path
                ]
                removed += before - len(self.skeleton.entries)
            else:
                # Re-index this single file (added or modified)
                abs_path = os.path.join(self.indexer.root_path, rel_path)
                ext = Path(rel_path).suffix
                language = LANG_MAP.get(ext)
                if not language:
                    continue

                # Remove old entries for this file
                self.skeleton.entries = [
                    e for e in self.skeleton.entries if e.file_path != rel_path
                ]

                # Re-index
                new_entries = self.indexer._index_file(abs_path, rel_path, language)
                self.skeleton.entries.extend(new_entries)
                updated += len(new_entries)

        # Update counts
        self.skeleton.total_symbols = len(self.skeleton.entries)

        elapsed = time.time() - start
        print(
            f"[file_watcher] Incremental update: {len(changes)} files, "
            f"+{updated} symbols, -{removed} removed in {elapsed:.3f}s"
        )

        # Persist to disk
        self.indexer.save(self.skeleton)

        # Notify callback with changed file list for targeted re-annotation
        if self.on_update:
            self.on_update(self.skeleton, list(changes.keys()))

    async def _poll_fallback(self):
        """Polling fallback when watchfiles is not available."""
        last_mtimes: dict[str, float] = {}

        # Initial scan
        for entry in self.skeleton.entries:
            abs_path = os.path.join(self.indexer.root_path, entry.file_path)
            try:
                last_mtimes[entry.file_path] = os.path.getmtime(abs_path)
            except OSError:
                pass

        while self._running:
            await asyncio.sleep(2.0)  # Poll every 2 seconds
            changed = False

            for dirpath, dirnames, filenames in os.walk(self.indexer.root_path):
                dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]

                for filename in filenames:
                    ext = Path(filename).suffix
                    if ext not in LANG_MAP:
                        continue

                    abs_path = os.path.join(dirpath, filename)
                    rel_path = os.path.relpath(abs_path, self.indexer.root_path)

                    try:
                        mtime = os.path.getmtime(abs_path)
                    except OSError:
                        continue

                    if rel_path not in last_mtimes or mtime > last_mtimes[rel_path]:
                        last_mtimes[rel_path] = mtime
                        self._pending_changes[rel_path] = "modified"
                        changed = True

            if changed and not self._debounce_task:
                self._debounce_task = asyncio.create_task(
                    self._process_after_debounce()
                )

    def stop(self):
        """Stop watching."""
        self._running = False
        if self._debounce_task and not self._debounce_task.done():
            self._debounce_task.cancel()

    def _make_stop_event(self):
        """Create an event that's set when _running becomes False."""
        import threading
        event = threading.Event()
        # We'll set it in stop()
        original_stop = self.stop

        def patched_stop():
            original_stop()
            event.set()

        self.stop = patched_stop
        return event

    @staticmethod
    def _should_watch(change, path: str) -> bool:
        """Filter for watchfiles — only watch source code files."""
        ext = Path(path).suffix
        if ext not in LANG_MAP:
            return False
        # Skip ignored directories
        parts = Path(path).parts
        return not any(part in SKIP_DIRS for part in parts)
