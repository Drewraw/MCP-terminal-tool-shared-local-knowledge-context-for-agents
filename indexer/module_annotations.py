"""
Module Annotations Manager
===========================
Allows users to add contextual notes to modules/folders.
These annotations are used during pruning to provide better context.

Example:
  "indexer/skeletal_indexer.py": "Tree-sitter based indexing. Performance issue with large files?"
  "server/gateway.py": "FastAPI server. Need to debug WebSocket reconnection logic"
"""

import json
import os
from pathlib import Path
from typing import Optional, Dict


class ModuleAnnotationsManager:
    """Manages user annotations for modules/files."""

    def __init__(self, storage_path: str):
        """
        Initialize with a storage path for annotations.
        
        Args:
            storage_path: Path to .prunetool/annotations.json
        """
        self.storage_path = storage_path
        self.annotations: Dict[str, str] = self._load()

    def _load(self) -> Dict[str, str]:
        """Load annotations from storage."""
        if not os.path.exists(self.storage_path):
            return {}

        try:
            with open(self.storage_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            return {}

    def _save(self):
        """Save annotations to storage."""
        os.makedirs(os.path.dirname(self.storage_path), exist_ok=True)
        with open(self.storage_path, "w", encoding="utf-8") as f:
            json.dump(self.annotations, f, indent=2, ensure_ascii=False)

    def set_annotation(self, file_path: str, annotation: str) -> bool:
        """
        Set annotation for a file/module.
        
        Args:
            file_path: Relative path to file (e.g., "indexer/skeletal_indexer.py")
            annotation: User's comment/note about what's in this file
            
        Returns:
            True if saved, False otherwise
        """
        if not annotation or not annotation.strip():
            # Remove annotation if empty
            if file_path in self.annotations:
                del self.annotations[file_path]
                self._save()
            return True

        self.annotations[file_path] = annotation.strip()
        self._save()
        return True

    def get_annotation(self, file_path: str) -> Optional[str]:
        """Get annotation for a file/module."""
        return self.annotations.get(file_path)

    def get_all_annotations(self) -> Dict[str, str]:
        """Get all annotations."""
        return dict(self.annotations)

    def get_context_for_query(self, file_paths: Optional[list[str]] = None) -> str:
        """
        Generate context string from relevant annotations for the pruner's
        keyword search (enhances goal_hint matching).

        Args:
            file_paths: Optional list of specific file paths to get annotations for.
                       If None, includes all files with annotations.

        Returns:
            A formatted string of relevant annotations for context.
        """
        if file_paths:
            relevant = {fp: self.annotations.get(fp) for fp in file_paths if fp in self.annotations}
        else:
            relevant = self.annotations

        if not relevant:
            return ""

        lines = []
        for file_path, annotation in relevant.items():
            if annotation:
                lines.append(f"  • {file_path}: {annotation}")

        if not lines:
            return ""

        context = "## Module Context\n" + "\n".join(lines)
        return context

    def get_llm_context(self, file_paths: Optional[list[str]] = None) -> str:
        """
        Generate a natural-language context block for the LLM prompt.

        Unlike get_context_for_query() which feeds keyword search, this
        method produces instructions that the LLM can semantically understand.
        The LLM can then reason about relationships between annotations and
        the user's query — e.g. knowing "billing" relates to "payment".

        Args:
            file_paths: Optional list of specific file paths.
                       If None, includes all annotated files.

        Returns:
            A formatted string for inclusion in the assembled prompt's
            extra_context block, or empty string if no annotations exist.
        """
        if file_paths:
            relevant = {fp: self.annotations.get(fp) for fp in file_paths if fp in self.annotations}
        else:
            relevant = self.annotations

        if not relevant:
            return ""

        lines = []
        for file_path, annotation in relevant.items():
            if annotation:
                lines.append(f"- **{file_path}**: {annotation}")

        if not lines:
            return ""

        return (
            "## Developer Notes on Modules\n"
            "The developer has annotated the following modules with context.\n"
            "Use these notes to understand which files are most relevant to "
            "the query — even if the terminology differs (e.g. 'billing' "
            "relates to 'payment', 'perf issue' relates to 'performance').\n\n"
            + "\n".join(lines)
        )

    def clear_all(self):
        """Clear all annotations."""
        self.annotations = {}
        self._save()

    def to_dict(self) -> Dict[str, str]:
        """Export all annotations as dict."""
        return dict(self.annotations)
