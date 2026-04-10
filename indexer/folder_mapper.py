"""
Folder Dependency Mapper
=========================
Builds a lightweight folder-level dependency graph by reading
only the first 40 lines (import zone) of every file.

  ┌──────────────────────────────────────────────────────┐
  │  Folder Map — Architecture at a Glance               │
  │                                                      │
  │  For each folder:                                    │
  │    - files[]           (what's inside)               │
  │    - imports_from[]    (which folders it depends on)  │
  │    - imported_by[]     (which folders depend on it)   │
  │    - description       (from README or annotation)   │
  │                                                      │
  │  Built by reading ONLY first 40 lines per file.      │
  │  Cost: ~50ms for 400 files. No full reads needed.    │
  └──────────────────────────────────────────────────────┘

This gives the Scout real architectural knowledge:
  "functions/src/incident-engine/ imports from functions/src/index.ts"
  "lib/core/services/ is imported by lib/features/alerts/"
"""

from __future__ import annotations

import json
import os
import re
from collections import defaultdict
from pathlib import Path
from typing import Dict, List, Optional, Set

# ── Import Pattern Detection ─────────────────────────────────────

# Matches common import/require patterns across languages
_IMPORT_PATTERNS = [
    # Python: import X / from X import Y
    re.compile(r"""^\s*(?:from\s+([.\w]+)\s+import|import\s+([.\w]+))"""),
    # JS/TS: import ... from 'path' / require('path')
    re.compile(r"""(?:import\s+.*?\s+from\s+|require\s*\(\s*)['"]([^'"]+)['"]"""),
    # Dart: import 'package:X/Y.dart' / import 'relative/path.dart'
    re.compile(r"""^\s*import\s+['"](?:package:\w+/)?([^'"]+)['"]"""),
    # Go: import "path/to/pkg"
    re.compile(r"""^\s*"([^"]+)"\s*$"""),
    # Rust: use crate::module / mod module
    re.compile(r"""^\s*(?:use\s+(?:crate::)?|mod\s+)(\w[\w:]*)\s*"""),
    # Java/Kotlin: import com.example.Class
    re.compile(r"""^\s*import\s+([\w.]+)"""),
    # Swift: import Module
    re.compile(r"""^\s*import\s+(\w+)"""),
    # C/C++: #include "path" / #include <path>
    re.compile(r"""^\s*#include\s+["<]([^">]+)[">]"""),
]

# Directories to skip
_SKIP_DIRS = {
    "node_modules", ".git", "__pycache__", ".venv", "venv",
    "dist", "build", ".next", ".cache", "coverage",
    ".tox", "egg-info", ".eggs", "target", ".dart_tool",
    ".prunetool",
}

# File extensions we care about
_CODE_EXTENSIONS = {
    ".py", ".js", ".jsx", ".ts", ".tsx", ".dart",
    ".go", ".rs", ".java", ".kt", ".swift",
    ".c", ".cpp", ".h", ".hpp", ".cc",
}

# Lines to read from the top of each file (import zone)
# 40 lines covers multi-line imports in large files (e.g., monolith index.ts)
_IMPORT_ZONE_LINES = 40


# ── Core Functions ────────────────────────────────────────────────

def _extract_imports_from_lines(lines: List[str], file_ext: str) -> List[str]:
    """Extract raw import paths from the first N lines of a file.

    Handles both single-line and multi-line imports:
      import { foo } from './bar'           ← single line
      import {                               ← multi-line
        foo,
        bar,
      } from './baz'
    """
    raw_imports = []

    # Also match multi-line imports: } from 'path' on its own line
    _MULTILINE_FROM = re.compile(r"""}\s*from\s+['"]([^'"]+)['"]""")

    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("//") or stripped.startswith("#!"):
            continue

        # Check for multi-line import close: } from './path'
        multi_match = _MULTILINE_FROM.search(stripped)
        if multi_match:
            raw_imports.append(multi_match.group(1))
            continue

        for pattern in _IMPORT_PATTERNS:
            match = pattern.search(stripped)
            if match:
                # Get the first non-None group
                for g in match.groups():
                    if g:
                        raw_imports.append(g)
                        break
                break

    return raw_imports


def _resolve_import_to_folder(
    raw_import: str,
    source_file: str,
    root_path: str,
    all_folders: Set[str],
) -> Optional[str]:
    """
    Resolve a raw import string to a folder path relative to root.

    Handles:
      - Relative paths: './utils' → same folder
      - Parent paths: '../models' → parent folder
      - Package paths: 'package:citizen/core/services' → lib/core/services
      - Node-style: './lib/incident-engine' → functions/lib/incident-engine
      - Direct file refs: 'auth_service.dart' → folder containing that file
    """
    source_dir = os.path.dirname(source_file)

    # Skip external packages / stdlib
    if raw_import.startswith(("@", "react", "next", "dart:", "package:flutter",
                               "java.", "javax.", "kotlin.", "swift.",
                               "std::", "core::")):
        return None

    # Handle Dart package imports: package:citizen/X/Y → lib/X/Y
    if raw_import.startswith("package:"):
        parts = raw_import.split("/", 1)
        if len(parts) > 1:
            candidate = "lib/" + parts[1].rsplit(".", 1)[0]  # strip .dart
            candidate_dir = os.path.dirname(candidate)
            if candidate_dir and candidate_dir in all_folders:
                return candidate_dir

    # Handle relative paths: ./X or ../X
    if raw_import.startswith("."):
        resolved = os.path.normpath(os.path.join(source_dir, raw_import))
        resolved = resolved.replace("\\", "/")
        # Could be a file or folder
        folder = os.path.dirname(resolved) if "." in os.path.basename(resolved) else resolved
        if folder in all_folders:
            return folder
        # Try without extension
        for ext in _CODE_EXTENSIONS:
            test = resolved + ext
            test_dir = os.path.dirname(test)
            if test_dir in all_folders:
                return test_dir
        return folder if folder else None

    # Handle node-style relative (no dot prefix but has slash)
    if "/" in raw_import:
        # Could be: lib/incident-engine/groq-processor
        candidate_dir = os.path.dirname(raw_import) if "." in raw_import.split("/")[-1] else raw_import
        if candidate_dir in all_folders:
            return candidate_dir
        # Try from source directory
        resolved = os.path.normpath(os.path.join(source_dir, raw_import))
        resolved = resolved.replace("\\", "/")
        resolved_dir = os.path.dirname(resolved) if "." in os.path.basename(resolved) else resolved
        if resolved_dir in all_folders:
            return resolved_dir

    # Single module name — check if it matches a known folder
    for folder in all_folders:
        folder_name = folder.split("/")[-1] if "/" in folder else folder
        if folder_name == raw_import or folder_name == raw_import.replace(".", "/").split("/")[-1]:
            return folder

    return None


def build_folder_map(root_path: str, existing_map: Optional[Dict] = None) -> Dict:
    """
    Build a folder-level dependency map by reading first 40 lines (import zone) of every file.

    Returns:
        {
            "folders": {
                "lib/core/services": {
                    "files": ["auth_service.dart", "firebase_service.dart", ...],
                    "imports_from": ["lib/core/models", "lib/core/config"],
                    "imported_by": ["lib/features/auth", "lib/features/alerts"],
                    "file_count": 25,
                    "extensions": {".dart": 25}
                },
                ...
            },
            "edges": [
                {"from": "lib/core/services", "to": "lib/core/models", "weight": 12},
                ...
            ],
            "stats": {"total_folders": 30, "total_edges": 85, "total_files": 444}
        }
    """
    root_path = os.path.abspath(root_path)

    # Load per-file mtime cache from existing map (if provided)
    prev_file_stats: Dict[str, dict] = {}
    prev_file_imports: Dict[str, List[str]] = {}  # rel_path → list of target folders
    if existing_map:
        prev_file_stats   = existing_map.get("file_stats", {})
        prev_file_imports = existing_map.get("file_imports", {})

    # Phase 1: Collect all folders and their files
    folder_files: Dict[str, List[str]] = defaultdict(list)
    folder_extensions: Dict[str, Dict[str, int]] = defaultdict(lambda: defaultdict(int))

    for dirpath, dirnames, filenames in os.walk(root_path):
        dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS]

        for filename in filenames:
            ext = Path(filename).suffix
            if ext not in _CODE_EXTENSIONS:
                continue

            file_path = os.path.join(dirpath, filename)
            rel_path = os.path.relpath(file_path, root_path).replace("\\", "/")
            rel_dir = os.path.dirname(rel_path) if "/" in rel_path else "(root)"

            folder_files[rel_dir].append(filename)
            folder_extensions[rel_dir][ext] += 1

    all_folders = set(folder_files.keys())
    print(f"[folder_mapper] Found {len(all_folders)} folders with code files")

    # Phase 2: Read first N lines of each file, extract imports
    # Use mtime cache — skip files that haven't changed
    folder_imports: Dict[str, Set[str]] = defaultdict(set)
    edge_weights: Dict[tuple, int] = defaultdict(int)
    new_file_stats: Dict[str, dict] = {}
    new_file_imports: Dict[str, List[str]] = {}

    files_processed = 0
    cache_hits = 0

    for dirpath, dirnames, filenames in os.walk(root_path):
        dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS]

        for filename in filenames:
            ext = Path(filename).suffix
            if ext not in _CODE_EXTENSIONS:
                continue

            file_path = os.path.join(dirpath, filename)
            rel_path = os.path.relpath(file_path, root_path).replace("\\", "/")
            rel_dir = os.path.dirname(rel_path) if "/" in rel_path else "(root)"

            # Check mtime cache
            try:
                mtime = os.stat(file_path).st_mtime
            except OSError:
                mtime = 0

            cached = prev_file_stats.get(rel_path, {})
            if cached.get("mtime") == mtime and rel_path in prev_file_imports:
                # File unchanged — reuse cached imports
                target_folders = prev_file_imports[rel_path]
                cache_hits += 1
            else:
                # Read import zone and extract imports
                try:
                    with open(file_path, "r", encoding="utf-8", errors="replace") as f:
                        lines = [next(f) for _ in range(_IMPORT_ZONE_LINES) if True]
                except (OSError, StopIteration):
                    lines = []

                raw_imports = _extract_imports_from_lines(lines, ext)
                target_folders = []
                for raw_imp in raw_imports:
                    target = _resolve_import_to_folder(raw_imp, rel_path, root_path, all_folders)
                    if target and target != rel_dir:
                        target_folders.append(target)
                files_processed += 1

            # Record results
            new_file_stats[rel_path]   = {"mtime": mtime}
            new_file_imports[rel_path] = target_folders

            for target in target_folders:
                folder_imports[rel_dir].add(target)
                edge_weights[(rel_dir, target)] += 1

    print(f"[folder_mapper] {files_processed} files re-read, "
          f"{cache_hits} cached, "
          f"found {sum(len(v) for v in folder_imports.values())} import edges")

    # Phase 3: Build reverse map (imported_by)
    imported_by: Dict[str, Set[str]] = defaultdict(set)
    for source, targets in folder_imports.items():
        for target in targets:
            imported_by[target].add(source)

    # Phase 4: Assemble the folder map
    folders_data = {}
    for folder in sorted(all_folders):
        folders_data[folder] = {
            "files": sorted(folder_files[folder]),
            "file_count": len(folder_files[folder]),
            "extensions": dict(folder_extensions[folder]),
            "imports_from": sorted(folder_imports.get(folder, set())),
            "imported_by": sorted(imported_by.get(folder, set())),
        }

    # Build edges list (sorted by weight for readability)
    edges = [
        {"from": src, "to": dst, "weight": weight}
        for (src, dst), weight in sorted(edge_weights.items(), key=lambda x: -x[1])
    ]

    result = {
        "folders": folders_data,
        "edges": edges,
        "stats": {
            "total_folders": len(all_folders),
            "total_edges":   len(edges),
            "total_files":   files_processed + cache_hits,
        },
        # Cache — persisted so next rescan can skip unchanged files
        "file_stats":   new_file_stats,
        "file_imports": new_file_imports,
    }

    return result


def save_folder_map(folder_map: Dict, data_dir: str) -> str:
    """Save folder map to .prunetool/folder_map.json."""
    os.makedirs(data_dir, exist_ok=True)
    path = os.path.join(data_dir, "folder_map.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(folder_map, f, indent=2, ensure_ascii=False)
    print(f"[folder_mapper] Saved folder map to {path}")
    return path


def load_folder_map(data_dir: str) -> Optional[Dict]:
    """Load folder map from disk."""
    path = os.path.join(data_dir, "folder_map.json")
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return None


def format_folder_context(folder_map: Dict, max_folders: int = 60) -> str:
    """
    Format the folder map as a compact text block for the Scout LLM.

    Output looks like:
      ## Folder Architecture
      functions/src/incident-engine/ (7 files: .ts)
        → imports: functions/src/index.ts folder
        ← used by: functions/lib/incident-engine
      lib/core/services/ (25 files: .dart)
        → imports: lib/core/models, lib/core/config
        ← used by: lib/features/alerts, lib/features/auth

    ~3-5 tokens per folder line. Fits in Scout's context budget.
    """
    folders = folder_map.get("folders", {})
    if not folders:
        return ""

    # Sort folders by connectivity (most connected first — most architecturally important)
    def connectivity(folder_name):
        f = folders[folder_name]
        return len(f.get("imports_from", [])) + len(f.get("imported_by", []))

    sorted_folders = sorted(folders.keys(), key=connectivity, reverse=True)

    lines = ["## Folder Architecture (import dependencies)"]

    for folder_name in sorted_folders[:max_folders]:
        f = folders[folder_name]
        file_count = f.get("file_count", 0)
        exts = ", ".join(f.get("extensions", {}).keys())
        imports_from = f.get("imports_from", [])
        imported_by = f.get("imported_by", [])

        # Folder header
        lines.append(f"{folder_name}/ ({file_count} files: {exts})")
        lines.append(f"  files: {', '.join(f.get('files', [])[:10])}"
                     + (f" +{file_count - 10} more" if file_count > 10 else ""))

        if imports_from:
            lines.append(f"  → imports from: {', '.join(imports_from[:8])}")
        if imported_by:
            lines.append(f"  ← used by: {', '.join(imported_by[:8])}")

        if not imports_from and not imported_by:
            lines.append(f"  (standalone — no detected dependencies)")

    return "\n".join(lines)


# ── CLI for testing ────────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    root = sys.argv[1] if len(sys.argv) > 1 else "."
    folder_map = build_folder_map(root)

    print(f"\n{'='*60}")
    print(f"Folder Map: {folder_map['stats']['total_folders']} folders, "
          f"{folder_map['stats']['total_edges']} edges, "
          f"{folder_map['stats']['total_files']} files")
    print(f"{'='*60}\n")

    # Print top connected folders
    context = format_folder_context(folder_map, max_folders=20)
    print(context)
