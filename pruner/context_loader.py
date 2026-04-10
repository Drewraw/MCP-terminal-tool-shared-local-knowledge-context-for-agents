"""
Multi-Layered Context Loader
==============================
Builds a rich context snapshot for the Scout LLM by combining:

  Layer 1 (Mandatory):  README.md / docs → Project overview, architecture hints
  Layer 2 (Optional):   Folder annotations → Human-written notes per module
  Layer 3 (Always):     Skeletal index summary → File list, symbol counts

The output is a compact text block (~1-2K tokens) that the 1B Scout
can digest in a single pass to understand the project before ranking
symbols.

Flow:
  README → /src/auth (User Note) → session_logic.py (Final Selection)
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Optional, Dict, List


# README filenames to look for (priority order)
_README_NAMES = [
    "README.md", "readme.md", "Readme.md",
    "ARCHITECTURE.md", "architecture.md",
    "OVERVIEW.md", "overview.md",
    "DESIGN.md", "design.md",
    "INDEX.md", "index.md",
]

# Max tokens (~4 chars/token) to extract from README
_README_MAX_CHARS = 4000

# Max annotations to include
_MAX_ANNOTATIONS = 50


def _find_readme(root_path: str) -> Optional[str]:
    """Find the best README file in the project root."""
    for name in _README_NAMES:
        path = os.path.join(root_path, name)
        if os.path.isfile(path):
            return path
    return None


def _extract_project_overview(readme_path: str) -> str:
    """
    Extract the project overview from a README.

    Prioritizes:
      1. Content under '# Project' or first H1 heading
      2. Content under '## Overview' / '## Architecture' / '## About'
      3. First N paragraphs if no clear section found

    Returns a compact summary suitable for a 1B model context window.
    """
    try:
        with open(readme_path, "r", encoding="utf-8", errors="replace") as f:
            content = f.read()
    except OSError:
        return ""

    if not content.strip():
        return ""

    lines = content.split("\n")

    # Strategy 1: Find key sections
    target_headings = re.compile(
        r'^#{1,2}\s+(project|overview|architecture|about|introduction|getting\s*started|summary)',
        re.IGNORECASE,
    )

    best_section = None
    for i, line in enumerate(lines):
        if target_headings.match(line.strip()):
            # Collect content until next heading of same or higher level
            heading_level = len(line.strip().split()[0])  # count #'s
            section_lines = [line]
            for j in range(i + 1, len(lines)):
                next_line = lines[j].strip()
                if next_line.startswith("#"):
                    next_level = len(next_line.split()[0]) if next_line.split() else 0
                    if next_level <= heading_level:
                        break
                section_lines.append(lines[j])

            section_text = "\n".join(section_lines).strip()
            if len(section_text) > 50:  # meaningful content
                best_section = section_text
                break

    # Strategy 2: First H1 + content
    if not best_section:
        for i, line in enumerate(lines):
            if line.strip().startswith("# "):
                section_lines = [line]
                for j in range(i + 1, min(i + 40, len(lines))):
                    if lines[j].strip().startswith("# "):
                        break
                    section_lines.append(lines[j])
                best_section = "\n".join(section_lines).strip()
                break

    # Strategy 3: First N non-empty lines
    if not best_section:
        first_lines = []
        for line in lines:
            if line.strip():
                first_lines.append(line)
                if len(first_lines) >= 30:
                    break
        best_section = "\n".join(first_lines)

    # Truncate to budget
    if best_section and len(best_section) > _README_MAX_CHARS:
        best_section = best_section[:_README_MAX_CHARS] + "\n[...truncated]"

    return best_section or ""


def _format_skeleton_summary(
    file_count: int,
    total_symbols: int,
    files_by_dir: Dict[str, int],
) -> str:
    """Format a compact skeleton summary showing directory structure."""
    lines = [f"Project: {file_count} files, {total_symbols} symbols"]

    # Group by top-level directory
    dir_summary = {}
    for file_path, sym_count in files_by_dir.items():
        parts = file_path.replace("\\", "/").split("/")
        top_dir = parts[0] if len(parts) > 1 else "(root)"
        dir_summary.setdefault(top_dir, {"files": 0, "symbols": 0})
        dir_summary[top_dir]["files"] += 1
        dir_summary[top_dir]["symbols"] += sym_count

    # Sort by symbol count descending
    for d, info in sorted(dir_summary.items(), key=lambda x: -x[1]["symbols"]):
        lines.append(f"  {d}/: {info['files']} files, {info['symbols']} symbols")

    return "\n".join(lines)


def _format_annotations(annotations: Dict[str, str]) -> str:
    """Format annotations as a compact list for the scout."""
    if not annotations:
        return ""

    lines = ["Developer annotations:"]
    for path, note in list(annotations.items())[:_MAX_ANNOTATIONS]:
        # Truncate long notes
        short_note = note[:150] + "..." if len(note) > 150 else note
        lines.append(f"  {path}: {short_note}")

    return "\n".join(lines)


class ContextLoader:
    """
    Loads multi-layered project context for the Scout LLM.

    Usage:
        loader = ContextLoader(root_path="/path/to/project")
        context = loader.build_context(
            skeleton=skeleton_index,
            annotations={"src/auth/": "OAuth2 + session management"},
        )
        # Pass `context` to the Scout alongside the user query
    """

    def __init__(self, root_path: str):
        self.root_path = root_path
        self._readme_cache: Optional[str] = None

    def get_readme_overview(self) -> str:
        """Extract and cache the project overview from README."""
        if self._readme_cache is not None:
            return self._readme_cache

        readme_path = _find_readme(self.root_path)
        if readme_path:
            self._readme_cache = _extract_project_overview(readme_path)
        else:
            self._readme_cache = ""

        return self._readme_cache

    def build_context(
        self,
        skeleton_files: Dict[str, int],
        file_count: int,
        total_symbols: int,
        annotations: Optional[Dict[str, str]] = None,
        folder_context: str = "",
    ) -> str:
        """
        Build the full multi-layered context string for the Scout.

        Args:
            skeleton_files: Dict of {file_path: symbol_count} from skeleton
            file_count: Total number of indexed files
            total_symbols: Total number of symbols
            annotations: Optional user annotations {path: note}
            folder_context: Pre-formatted folder dependency map

        Returns:
            A compact context string (~2-4K tokens) combining all layers.
        """
        parts = []

        # Layer 1: README overview
        readme = self.get_readme_overview()
        if readme:
            parts.append(f"## Project Overview\n{readme}")

        # Layer 2: Developer annotations
        if annotations:
            anno_text = _format_annotations(annotations)
            if anno_text:
                parts.append(f"## {anno_text}")

        # Layer 3: Folder dependency map (import relationships)
        if folder_context:
            parts.append(folder_context)

        # Layer 4: Skeleton summary
        skel_text = _format_skeleton_summary(file_count, total_symbols, skeleton_files)
        parts.append(f"## Codebase Structure\n{skel_text}")

        return "\n\n".join(parts)

    def build_symbol_list(self, entries: list, file_annotations: dict | None = None) -> str:
        """
        Build a compact symbol list for the Scout to rank.

        Each line: [kind] parent.name @ file_path:line [| hint]

        Hint priority (first match wins, keeps lines compact):
          1. data_context  — enum values:  "| values: road|sanitation|civicReport"
          2. file annotation — auto-generated purpose shown once per file:
                               "| purpose: Handles civic thread categories and routing"
          3. docstring     — first 60 chars of inline doc comment

        This is what the Scout reads to pick relevant symbols.
        """
        file_annotations = file_annotations or {}
        # Track which files have already had their annotation appended
        # (show per-file annotation on first symbol only to avoid repetition)
        annotated_files: set[str] = set()

        lines = []
        for entry in entries:
            parent = f"{entry.parent}." if entry.parent else ""
            hint = ""

            if entry.data_context:
                # Enum/const values — most informative signal
                hint = f" | values: {entry.data_context}"
            elif entry.file_path not in annotated_files:
                ann = file_annotations.get(entry.file_path)
                if ann:
                    hint = f" | purpose: {ann[:100]}"
                    annotated_files.add(entry.file_path)
                elif entry.docstring:
                    hint = f" | {entry.docstring[:60]}"
            elif entry.docstring:
                hint = f" | {entry.docstring[:60]}"

            lines.append(
                f"[{entry.kind.value}] {parent}{entry.name} "
                f"@ {entry.file_path.replace(chr(92), '/')}:{entry.line_start}{hint}"
            )
        return "\n".join(lines)
