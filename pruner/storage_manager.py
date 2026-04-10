"""
Storage Manager — Single Source of Truth
==========================================
Centralizes all persistent state for the pruning pipeline:

  ┌─────────────────────────────────────────────────────────┐
  │                   Storage Manager                       │
  │                                                         │
  │  skeletal_index    ← rebuilt on rescan (volatile)       │
  │  project_metadata  ← rebuilt on rescan (volatile)       │
  │  readme_overview   ← re-read on rescan (volatile)      │
  │  user_annotations  ← PRESERVED across rescans          │
  │  scout             ← Llama 3.1-8B-Instant via Groq     │
  │                                                         │
  │  rescan_project()  → clears index+meta, keeps annos    │
  │  update_annotation()  → saves pin in real-time          │
  │  build_scout_context()  → layers 1+2+3 for the Scout   │
  └─────────────────────────────────────────────────────────┘

The Storage Manager owns the lifecycle of all data. The gateway
and pruning engine read from it; only the Storage Manager writes.
"""

from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Dict, List, Any

try:
    from indexer.folder_mapper import build_folder_map, save_folder_map, load_folder_map, format_folder_context
except ImportError:
    try:
        from ..indexer.folder_mapper import build_folder_map, save_folder_map, load_folder_map, format_folder_context
    except ImportError:
        build_folder_map = None
        save_folder_map = None
        load_folder_map = None
        format_folder_context = None


# ── Project Metadata ────────────────────────────────────────────────

@dataclass
class ProjectMetadata:
    """Volatile project-level metadata, rebuilt on every rescan."""
    root_path: str = ""
    readme_overview: str = ""              # Extracted from README.md
    readme_path: Optional[str] = None      # Which README was found
    file_count: int = 0
    total_symbols: int = 0
    language_breakdown: Dict[str, int] = field(default_factory=dict)  # {ext: count}
    directory_tree: Dict[str, int] = field(default_factory=dict)      # {dir: symbol_count}
    last_scanned_at: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "root_path": self.root_path,
            "readme_overview": self.readme_overview[:500],  # summary only
            "readme_path": self.readme_path,
            "file_count": self.file_count,
            "total_symbols": self.total_symbols,
            "language_breakdown": self.language_breakdown,
            "directory_tree": self.directory_tree,
            "last_scanned_at": self.last_scanned_at,
        }


# ── README Extraction removed ────────────────────────────────────────
# Project context now comes exclusively from prune library/*.md files,
# extracted at annotation time in pruning_engine._load_readme_summary().


# ── Storage Manager ─────────────────────────────────────────────────

class StorageManager:
    """
    Single source of truth for all pruning pipeline state.

    Owns:
      - skeletal_index:    Rebuilt on rescan. Code symbols from tree-sitter/regex.
      - project_metadata:  Rebuilt on rescan. README, file counts, dir tree.
      - user_annotations:  PRESERVED across rescans. Human-written context pins.

    The Scout (Llama 3.1-8B-Instant) reads all three layers to rank symbols.
    """

    def __init__(self, root_path: str, data_dir: Optional[str] = None):
        """
        Args:
            root_path: Codebase root (e.g., C:\\Newexpw\\new\\experiment)
            data_dir:  Where to store persistent files. Defaults to {root}/.prunetool/
        """
        self.root_path = os.path.abspath(root_path)
        self.data_dir = data_dir or os.path.join(self.root_path, ".prunetool")

        # Persistent file paths
        self._skeleton_path = os.path.join(self.data_dir, "skeleton.json")
        self._annotations_path = os.path.join(self.data_dir, "annotations.json")
        self._metadata_path = os.path.join(self.data_dir, "project_metadata.json")
        # In-memory state
        self.project_metadata = ProjectMetadata(root_path=self.root_path)
        self.user_annotations: Dict[str, str] = {}
        self.folder_map: Optional[Dict] = None

        # Load persisted annotations (these survive rescans)
        self._load_annotations()

        # Load folder map if it exists
        if load_folder_map:
            self.folder_map = load_folder_map(self.data_dir)

    # ── Annotations (Persistent Across Rescans) ─────────────────

    def _load_annotations(self):
        """Load user annotations from disk."""
        if not os.path.exists(self._annotations_path):
            self.user_annotations = {}
            return

        try:
            with open(self._annotations_path, "r", encoding="utf-8") as f:
                self.user_annotations = json.load(f)
            print(f"[storage] Loaded {len(self.user_annotations)} annotations")
        except (json.JSONDecodeError, OSError):
            self.user_annotations = {}

    def _save_annotations(self):
        """Persist annotations to disk."""
        os.makedirs(self.data_dir, exist_ok=True)
        with open(self._annotations_path, "w", encoding="utf-8") as f:
            json.dump(self.user_annotations, f, indent=2, ensure_ascii=False)

    def update_annotation(self, path: str, note: str) -> bool:
        """
        Save or remove a user-defined context pin in real-time.

        Args:
            path: File or folder path (e.g., "functions/src/incident-engine/")
            note: Human annotation. Empty string removes the pin.

        Returns:
            True if saved successfully.
        """
        path = path.strip()
        if not path:
            return False

        note = note.strip()
        if not note:
            # Remove annotation
            if path in self.user_annotations:
                del self.user_annotations[path]
                self._save_annotations()
                print(f"[storage] Removed annotation: {path}")
            return True

        self.user_annotations[path] = note
        self._save_annotations()
        print(f"[storage] Updated annotation: {path} = {note[:60]}...")
        return True

    def get_annotation(self, path: str) -> Optional[str]:
        """Get annotation for a path."""
        return self.user_annotations.get(path)

    def get_all_annotations(self) -> Dict[str, str]:
        """Get all annotations."""
        return dict(self.user_annotations)

    # ── Rescan (Clears Index + Metadata, Preserves Annotations) ──

    def rescan_project(self, indexer) -> Dict[str, Any]:
        """
        Full project rescan:
          1. CLEAR skeletal_index and project_metadata
          2. PRESERVE user_annotations (untouched)
          3. Re-read README.md immediately
          4. Rebuild skeletal index
          5. Rebuild project metadata

        Args:
            indexer: SkeletalIndexer instance to rebuild the index

        Returns:
            Dict with scan results
        """
        start = time.time()
        print(f"[storage] Rescanning project at {self.root_path}...")

        # Step 1: Clear volatile state
        self.project_metadata = ProjectMetadata(root_path=self.root_path)
        print(f"[storage] Cleared skeletal_index and project_metadata")
        print(f"[storage] Preserved {len(self.user_annotations)} user annotations")

        # Step 2: Rebuild skeletal index
        skeleton = indexer.index_and_save()
        # Files that were re-parsed (cache miss) — their auto-annotations are stale
        self.reparsed_files: set = indexer.last_reparsed_files

        # Step 4: Build project metadata from skeleton
        self.project_metadata.file_count = skeleton.file_count
        self.project_metadata.total_symbols = skeleton.total_symbols
        self.project_metadata.last_scanned_at = datetime.now(timezone.utc).isoformat()

        # Language breakdown
        lang_counts: Dict[str, int] = {}
        dir_tree: Dict[str, int] = {}
        for entry in skeleton.entries:
            ext = Path(entry.file_path).suffix
            lang_counts[ext] = lang_counts.get(ext, 0) + 1
            parts = entry.file_path.replace("\\", "/").split("/")
            top_dir = parts[0] if len(parts) > 1 else "(root)"
            dir_tree[top_dir] = dir_tree.get(top_dir, 0) + 1

        self.project_metadata.language_breakdown = lang_counts
        self.project_metadata.directory_tree = dir_tree

        # Step 5: Build folder dependency map (reads first 40 lines per file)
        if build_folder_map:
            self.folder_map = build_folder_map(self.root_path,
                                               existing_map=self.folder_map)
            save_folder_map(self.folder_map, self.data_dir)
            fm_stats = self.folder_map.get("stats", {})
            print(f"[storage] Folder map: {fm_stats.get('total_folders', 0)} folders, "
                  f"{fm_stats.get('total_edges', 0)} import edges")

        # Save all state to .prunetool/ (single source of truth)
        self._save_metadata()
        self._save_annotations()  # Always persist (even if empty)

        elapsed = time.time() - start
        print(f"[storage] Rescan complete: {skeleton.file_count} files, "
              f"{skeleton.total_symbols} symbols in {elapsed:.2f}s")

        return {
            "status": "scanned",
            "file_count": skeleton.file_count,
            "total_symbols": skeleton.total_symbols,
            "annotations_preserved": len(self.user_annotations),
            "elapsed_ms": round(elapsed * 1000, 1),
            "indexed_at": self.project_metadata.last_scanned_at,
        }

    def _save_metadata(self):
        """Persist project metadata."""
        os.makedirs(self.data_dir, exist_ok=True)
        with open(self._metadata_path, "w", encoding="utf-8") as f:
            json.dump(self.project_metadata.to_dict(), f, indent=2)

    # ── Scout Context Builder ───────────────────────────────────

    def build_scout_context(self, skeleton) -> str:
        """
        Build the multi-layered context string for the Scout LLM.

        Hierarchy (checked in order):
          1. README overview     — project architecture (first scan only)
          2. User annotations    — human-defined folder/file hints
          3. Folder dependencies — import graph from first 40 lines per file
          4. Directory structure  — folder-level symbol counts

        Returns ~2-4K tokens of context.
        """
        parts = []

        # Layer 1: README overview (project understanding)
        readme = self.project_metadata.readme_overview
        if readme:
            parts.append(f"## Project Overview\n{readme}")

        # Layer 2: User annotations (human-defined context pins)
        if self.user_annotations:
            anno_lines = ["Developer annotations (use these to navigate):"]
            for path, note in list(self.user_annotations.items())[:50]:
                short = note[:150] + "..." if len(note) > 150 else note
                anno_lines.append(f"  {path}: {short}")
            parts.append("## " + "\n".join(anno_lines))

        # Layer 3: Folder dependency map (import relationships)
        if self.folder_map and format_folder_context:
            folder_ctx = format_folder_context(self.folder_map, max_folders=40)
            if folder_ctx:
                parts.append(folder_ctx)

        # Layer 4: Directory structure (symbol counts per top-level dir)
        tree = self.project_metadata.directory_tree
        if tree:
            dir_lines = [f"Project: {self.project_metadata.file_count} files, "
                         f"{self.project_metadata.total_symbols} symbols"]
            for d, count in sorted(tree.items(), key=lambda x: -x[1]):
                dir_lines.append(f"  {d}/: {count} symbols")
            parts.append(f"## Codebase Structure\n" + "\n".join(dir_lines))

        return "\n\n".join(parts)

    def build_symbol_list(self, entries: list) -> str:
        """Build compact symbol list for the Scout to rank.

        Format: [kind] parent.name @ file_path:line | docstring_hint
        ~10 tokens per symbol.
        """
        lines = []
        for entry in entries:
            parent = f"{entry.parent}." if entry.parent else ""
            doc = f" | {entry.docstring[:60]}" if entry.docstring else ""
            lines.append(
                f"[{entry.kind.value}] {parent}{entry.name} "
                f"@ {entry.file_path}:{entry.line_start}{doc}"
            )
        return "\n".join(lines)

    # ── Info Endpoints ──────────────────────────────────────────

    def get_status(self) -> dict:
        """Get current storage state summary."""
        return {
            "root_path": self.root_path,
            "data_dir": self.data_dir,
            "metadata": self.project_metadata.to_dict(),
            "annotations_count": len(self.user_annotations),
            "annotations": dict(self.user_annotations),
        }
