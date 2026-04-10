"""Data models for the skeletal index."""

from __future__ import annotations
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class SymbolKind(str, Enum):
    CLASS = "class"
    FUNCTION = "function"
    METHOD = "method"
    INTERFACE = "interface"
    ENUM = "enum"
    MODULE = "module"
    VARIABLE = "variable"
    HEADING = "heading"      # Markdown heading (# Title)
    SECTION = "section"      # Markdown section with content summary
    FILE_REF = "file_ref"    # File path reference found in docs


@dataclass
class SkeletonEntry:
    """A single symbol extracted from source code — the 'bone' of the skeleton."""
    file_path: str
    name: str
    kind: SymbolKind
    signature: str
    line_start: int
    line_end: int
    parent: Optional[str] = None
    docstring: Optional[str] = None
    # Body is NOT stored during indexing — only loaded on-demand during pruning
    body_hash: Optional[str] = None
    # Compact semantic context extracted at index time (enum values, const values)
    # e.g. "road|sanitation|water|civicReport" for a ThreadType enum
    data_context: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "file_path": self.file_path,
            "name": self.name,
            "kind": self.kind.value,
            "signature": self.signature,
            "line_start": self.line_start,
            "line_end": self.line_end,
            "parent": self.parent,
            "docstring": self.docstring,
            "body_hash": self.body_hash,
            "data_context": self.data_context,
        }

    @classmethod
    def from_dict(cls, d: dict) -> SkeletonEntry:
        return cls(
            file_path=d["file_path"],
            name=d["name"],
            kind=SymbolKind(d["kind"]),
            signature=d["signature"],
            line_start=d["line_start"],
            line_end=d["line_end"],
            parent=d.get("parent"),
            docstring=d.get("docstring"),
            body_hash=d.get("body_hash"),
            data_context=d.get("data_context"),
        )


@dataclass
class SkeletalIndex:
    """The full skeletal map of a codebase — paths, classes, function signatures."""
    root_path: str
    entries: list[SkeletonEntry] = field(default_factory=list)
    file_count: int = 0
    total_symbols: int = 0
    indexed_at: Optional[str] = None
    # mtime+size cache: {rel_path: {"mtime": float, "size": int}}
    # Populated during index(), used to skip unchanged files on next rescan.
    file_stats: dict = field(default_factory=dict)

    def search(self, query: str, top_k: int = 20) -> list[SkeletonEntry]:
        """Keyword search across symbol names, signatures, and doc content.

        Markdown entries (headings, sections, file_refs) participate in search
        so that README/doc descriptions of modules boost the right code files.
        """
        query_lower = query.lower()
        terms = query_lower.split()
        scored: list[tuple[float, SkeletonEntry]] = []

        # First pass: score markdown FILE_REF entries to discover which
        # code files are described by documentation that matches the query.
        doc_boosted_files: dict[str, float] = {}
        for entry in self.entries:
            if entry.kind != SymbolKind.FILE_REF:
                continue
            ref_score = 0.0
            searchable = f"{entry.name} {entry.signature} {entry.docstring or ''}".lower()
            for term in terms:
                if term in searchable:
                    ref_score += 1.0
            if ref_score > 0:
                # entry.name holds the referenced file path
                ref_path = entry.name.lower()
                doc_boosted_files[ref_path] = max(
                    doc_boosted_files.get(ref_path, 0), ref_score
                )

        for entry in self.entries:
            score = 0.0

            for term in terms:
                if term in entry.name.lower():
                    score += 3.0  # Name match is strongest
                if term in entry.signature.lower():
                    score += 1.5
                if entry.parent and term in entry.parent.lower():
                    score += 1.0
                if entry.docstring and term in entry.docstring.lower():
                    score += 0.5

            # Boost classes/interfaces (structural anchors)
            if entry.kind in (SymbolKind.CLASS, SymbolKind.INTERFACE):
                score *= 1.2

            # Boost markdown headings/sections (architecture context)
            if entry.kind in (SymbolKind.HEADING, SymbolKind.SECTION):
                score *= 1.1

            # Boost code entries whose file is referenced by matching docs
            if entry.kind not in (SymbolKind.HEADING, SymbolKind.SECTION, SymbolKind.FILE_REF):
                entry_path_lower = entry.file_path.lower().replace("\\", "/")
                for ref_path, ref_score in doc_boosted_files.items():
                    # Check if the doc reference matches this code file
                    if ref_path in entry_path_lower or entry_path_lower.endswith(ref_path):
                        score += ref_score * 2.0  # Strong boost from doc context
                        break

            if score > 0:
                scored.append((score, entry))

        scored.sort(key=lambda x: x[0], reverse=True)
        return [entry for _, entry in scored[:top_k]]

    def get_entries_for_file(self, file_path: str) -> list[SkeletonEntry]:
        return [e for e in self.entries if e.file_path == file_path]

    def to_dict(self) -> dict:
        return {
            "root_path": self.root_path,
            "entries": [e.to_dict() for e in self.entries],
            "file_count": self.file_count,
            "total_symbols": self.total_symbols,
            "indexed_at": self.indexed_at,
            "file_stats": self.file_stats,
        }

    @classmethod
    def from_dict(cls, d: dict) -> SkeletalIndex:
        idx = cls(
            root_path=d["root_path"],
            file_count=d.get("file_count", 0),
            total_symbols=d.get("total_symbols", 0),
            indexed_at=d.get("indexed_at"),
            file_stats=d.get("file_stats", {}),
        )
        idx.entries = [SkeletonEntry.from_dict(e) for e in d.get("entries", [])]
        return idx
