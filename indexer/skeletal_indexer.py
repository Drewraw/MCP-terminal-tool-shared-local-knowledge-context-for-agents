"""
Tree-sitter Skeletal Indexer
============================
Performs a Skeletal Scan of a codebase: maps file paths, class names,
and function signatures WITHOUT reading function bodies.

Inspired by SWE-Pruner's insight that agents spend 76% of tokens on
read operations — index the skeleton first, read bodies on-demand.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from .models import SkeletalIndex, SkeletonEntry, SymbolKind

try:
    from .regex_fallback import index_file_regex, index_markdown_file, REGEX_LANG_MAP, REGEX_PATTERNS
except ImportError:
    REGEX_PATTERNS = {}
    REGEX_LANG_MAP = {}
    index_markdown_file = None

# Tree-sitter language registry — lazy loaded
_PARSERS: dict[str, object] = {}

# File extension to tree-sitter language mapping
LANG_MAP: dict[str, str] = {
    ".py": "python",
    ".js": "javascript",
    ".jsx": "javascript",
    ".ts": "typescript",
    ".tsx": "tsx",
    ".go": "go",
    ".rs": "rust",
    ".java": "java",
}

# Extended language support via regex fallback
# (for languages without tree-sitter packages)
LANG_MAP.update(REGEX_LANG_MAP)

# Directories to always skip
SKIP_DIRS = {
    "node_modules", ".git", "__pycache__", ".venv", "venv",
    "dist", "build", ".next", ".cache", "coverage",
    ".tox", "egg-info", ".eggs", "target",
}

# Tree-sitter node types that represent symbols we want to extract
SYMBOL_NODE_TYPES = {
    "python": {
        "class_definition": SymbolKind.CLASS,
        "function_definition": SymbolKind.FUNCTION,
    },
    "javascript": {
        "class_declaration": SymbolKind.CLASS,
        "function_declaration": SymbolKind.FUNCTION,
        "method_definition": SymbolKind.METHOD,
        "arrow_function": SymbolKind.FUNCTION,
    },
    "typescript": {
        "class_declaration": SymbolKind.CLASS,
        "function_declaration": SymbolKind.FUNCTION,
        "method_definition": SymbolKind.METHOD,
        "interface_declaration": SymbolKind.INTERFACE,
        "enum_declaration": SymbolKind.ENUM,
        "arrow_function": SymbolKind.FUNCTION,
    },
    "tsx": {
        "class_declaration": SymbolKind.CLASS,
        "function_declaration": SymbolKind.FUNCTION,
        "method_definition": SymbolKind.METHOD,
        "interface_declaration": SymbolKind.INTERFACE,
        "enum_declaration": SymbolKind.ENUM,
        "arrow_function": SymbolKind.FUNCTION,
    },
    "go": {
        "type_declaration": SymbolKind.CLASS,
        "function_declaration": SymbolKind.FUNCTION,
        "method_declaration": SymbolKind.METHOD,
    },
    "rust": {
        "struct_item": SymbolKind.CLASS,
        "enum_item": SymbolKind.ENUM,
        "function_item": SymbolKind.FUNCTION,
        "impl_item": SymbolKind.CLASS,
        "trait_item": SymbolKind.INTERFACE,
    },
    "java": {
        "class_declaration": SymbolKind.CLASS,
        "interface_declaration": SymbolKind.INTERFACE,
        "method_declaration": SymbolKind.METHOD,
        "enum_declaration": SymbolKind.ENUM,
    },
}


def _get_parser(language: str):
    """Lazy-load a tree-sitter parser for the given language.

    The tree-sitter-typescript package is special: it exposes two grammars
    via language_typescript() and language_tsx() instead of a single
    language() entry-point.  We handle that here so .ts and .tsx files
    each get the correct grammar.
    """
    if language in _PARSERS:
        return _PARSERS[language]

    try:
        import tree_sitter

        # tree-sitter-typescript ships two grammars in one package
        if language in ("typescript", "tsx"):
            import tree_sitter_typescript
            lang_fn = (
                tree_sitter_typescript.language_typescript
                if language == "typescript"
                else tree_sitter_typescript.language_tsx
            )
            lang = tree_sitter.Language(lang_fn())
        else:
            lang_module = __import__(f"tree_sitter_{language.replace('-', '_')}")
            lang = tree_sitter.Language(lang_module.language())

        parser = tree_sitter.Parser(lang)
        _PARSERS[language] = parser
        return parser
    except (ImportError, Exception) as e:
        print(f"[skeletal_indexer] Could not load tree-sitter for {language}: {e}")
        _PARSERS[language] = None
        return None


def _extract_name(node, source_bytes: bytes, language: str) -> Optional[str]:
    """Extract the name of a symbol from its tree-sitter node."""
    # Try common child field names
    for field_name in ("name", "declarator"):
        child = node.child_by_field_name(field_name)
        if child:
            return source_bytes[child.start_byte:child.end_byte].decode("utf-8", errors="replace")

    # For arrow functions assigned to variables: const foo = () => {}
    if node.type == "arrow_function" and node.parent:
        parent = node.parent
        if parent.type in ("variable_declarator", "pair"):
            name_node = parent.child_by_field_name("name") or parent.child_by_field_name("key")
            if name_node:
                return source_bytes[name_node.start_byte:name_node.end_byte].decode("utf-8", errors="replace")

    return None


def _extract_signature(node, source_bytes: bytes) -> str:
    """Extract just the signature line (up to the body opener)."""
    text = source_bytes[node.start_byte:node.end_byte].decode("utf-8", errors="replace")
    lines = text.split("\n")

    # For most languages, the signature is everything before '{' or ':'
    sig_lines = []
    for line in lines:
        sig_lines.append(line)
        stripped = line.strip()
        if stripped.endswith(("{", ":", "=>")) or stripped == "{":
            break
        # Python: stop at the colon after def/class
        if stripped.endswith(":") and not stripped.startswith("#"):
            break

    signature = "\n".join(sig_lines).strip()
    # Truncate very long signatures
    if len(signature) > 500:
        signature = signature[:500] + "..."
    return signature


def _extract_docstring(node, source_bytes: bytes, language: str) -> Optional[str]:
    """Extract docstring/comment immediately inside a function/class body."""
    body = node.child_by_field_name("body")
    if not body or body.child_count == 0:
        return None

    first_child = body.children[0]

    if language == "python" and first_child.type == "expression_statement":
        inner = first_child.children[0] if first_child.child_count > 0 else None
        if inner and inner.type == "string":
            doc = source_bytes[inner.start_byte:inner.end_byte].decode("utf-8", errors="replace")
            if len(doc) > 300:
                doc = doc[:300] + "..."
            return doc

    if first_child.type == "comment":
        doc = source_bytes[first_child.start_byte:first_child.end_byte].decode("utf-8", errors="replace")
        if len(doc) > 300:
            doc = doc[:300] + "..."
        return doc

    return None


def _body_hash(node, source_bytes: bytes) -> str:
    """Hash the function body for change detection without storing it."""
    body = node.child_by_field_name("body")
    if body:
        body_bytes = source_bytes[body.start_byte:body.end_byte]
    else:
        body_bytes = source_bytes[node.start_byte:node.end_byte]
    return hashlib.md5(body_bytes).hexdigest()[:12]


def _extract_data_context(node, source_bytes: bytes, language: str, kind: SymbolKind) -> Optional[str]:
    """
    Extract compact semantic context for enums (values) without reading bodies.

    Returns a pipe-separated string of enum member names, capped at 20 values.
    e.g. "road|sanitation|water|power|civicReport" for a ThreadType enum.
    Only fires for ENUM kind — functions/classes return None.
    """
    if kind != SymbolKind.ENUM:
        return None

    values: list[str] = []

    if language in ("typescript", "tsx"):
        # enum_declaration -> body: enum_body -> enum_member (name field)
        body = node.child_by_field_name("body")
        if body:
            for child in body.children:
                if child.type == "enum_member":
                    name_node = child.child_by_field_name("name")
                    if name_node:
                        values.append(
                            source_bytes[name_node.start_byte:name_node.end_byte]
                            .decode("utf-8", errors="replace")
                        )

    elif language == "rust":
        # enum_item -> enum_variant_list -> enum_variant (name field)
        for child in node.children:
            if child.type == "enum_variant_list":
                for variant in child.children:
                    if variant.type == "enum_variant":
                        name_node = variant.child_by_field_name("name")
                        if name_node:
                            values.append(
                                source_bytes[name_node.start_byte:name_node.end_byte]
                                .decode("utf-8", errors="replace")
                            )

    elif language == "java":
        # enum_declaration -> enum_body -> enum_constant (name field)
        for child in node.children:
            if child.type == "enum_body":
                for constant in child.children:
                    if constant.type == "enum_constant":
                        name_node = constant.child_by_field_name("name")
                        if name_node:
                            values.append(
                                source_bytes[name_node.start_byte:name_node.end_byte]
                                .decode("utf-8", errors="replace")
                            )

    if values:
        return "|".join(values[:20])
    return None


def _find_parent_class(node) -> Optional[str]:
    """Walk up the tree to find the enclosing class name."""
    current = node.parent
    while current:
        if current.type in ("class_definition", "class_declaration", "impl_item"):
            name_node = current.child_by_field_name("name")
            if name_node:
                return name_node.text.decode("utf-8", errors="replace") if hasattr(name_node, 'text') else None
        current = current.parent
    return None


class SkeletalIndexer:
    """
    Indexes a codebase into a skeletal map using Tree-sitter.
    Only extracts structure (paths, classes, function signatures).
    Function bodies are NOT read — they're loaded on-demand by the Pruner.
    """

    def __init__(self, root_path: str, index_path: Optional[str] = None):
        self.root_path = os.path.abspath(root_path)
        _default_data = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "skeleton.json")
        self.index_path = index_path or _default_data
        # Populated after index() — set of rel_paths that were re-parsed (not cache hits)
        self.last_reparsed_files: set = set()

    def index(self) -> SkeletalIndex:
        """Perform a skeletal scan of the codebase.

        Uses mtime + size cache: if a file hasn't changed since the last scan
        its previous entries are reused directly — no re-parse needed.
        Only new or modified files go through Tree-sitter / regex.
        """
        start = time.time()

        # Load previous skeleton for cache lookup
        prev = self.load()
        prev_stats: dict = prev.file_stats if prev else {}
        # Build quick lookup: rel_path → list of entries from previous scan
        prev_entries: dict[str, list] = {}
        if prev:
            for entry in prev.entries:
                prev_entries.setdefault(entry.file_path, []).append(entry)

        skeleton = SkeletalIndex(root_path=self.root_path)
        self.last_reparsed_files = set()
        file_count = 0
        cache_hits = 0
        reparsed = 0

        for dirpath, dirnames, filenames in os.walk(self.root_path):
            dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]

            for filename in filenames:
                ext = Path(filename).suffix
                language = LANG_MAP.get(ext)
                if not language:
                    continue

                file_path = os.path.join(dirpath, filename)
                rel_path = os.path.relpath(file_path, self.root_path)
                file_count += 1

                # Check mtime + size against cache
                try:
                    st = os.stat(file_path)
                    mtime = st.st_mtime
                    size  = st.st_size
                except OSError:
                    mtime = size = 0

                cached = prev_stats.get(rel_path, {})
                if (cached.get("mtime") == mtime and
                        cached.get("size") == size and
                        rel_path in prev_entries):
                    # File unchanged — reuse previous entries
                    skeleton.entries.extend(prev_entries[rel_path])
                    skeleton.file_stats[rel_path] = cached
                    cache_hits += 1
                    continue

                # File is new or changed — re-parse
                entries = self._index_file(file_path, rel_path, language)
                skeleton.entries.extend(entries)
                skeleton.file_stats[rel_path] = {"mtime": mtime, "size": size}
                self.last_reparsed_files.add(rel_path)
                reparsed += 1

        skeleton.file_count = file_count
        skeleton.total_symbols = len(skeleton.entries)
        skeleton.indexed_at = datetime.now(timezone.utc).isoformat()

        elapsed = time.time() - start
        print(f"[skeletal_indexer] {file_count} files: "
              f"{cache_hits} cached, {reparsed} re-parsed, "
              f"{skeleton.total_symbols} symbols in {elapsed:.2f}s")

        return skeleton

    def _index_file(self, file_path: str, rel_path: str, language: str) -> list[SkeletonEntry]:
        """Extract skeleton entries from a single file.

        Tries tree-sitter first, then falls back to regex if tree-sitter is unavailable.
        Markdown files use a dedicated indexer that extracts headings, sections,
        and file path references.
        """
        # Markdown files use the dedicated markdown indexer
        if language == "markdown":
            if index_markdown_file:
                try:
                    return index_markdown_file(file_path, rel_path)
                except Exception as e:
                    print(f"[skeletal_indexer] Markdown index failed for {rel_path}: {e}")
                    return []
            return []

        parser = _get_parser(language)

        # Try tree-sitter parsing first
        if parser:
            try:
                with open(file_path, "rb") as f:
                    source_bytes = f.read()
            except (OSError, IOError):
                return []

            # Skip very large files (>1MB) — they're usually generated
            if len(source_bytes) > 1_048_576:
                return []

            try:
                tree = parser.parse(source_bytes)
                entries: list[SkeletonEntry] = []
                symbol_types = SYMBOL_NODE_TYPES.get(language, {})
                self._walk_tree(tree.root_node, source_bytes, rel_path, language, symbol_types, entries)
                return entries
            except Exception as e:
                print(f"[skeletal_indexer] Tree-sitter parse failed for {rel_path}: {e}")
                # Fall through to regex fallback
        
        # Fallback: use regex-based indexing for unsupported languages
        if language in REGEX_PATTERNS and REGEX_PATTERNS[language]:
            try:
                with open(file_path, "r", encoding="utf-8", errors="replace") as f:
                    source_text = f.read()
                
                # Skip very large files
                if len(source_text) > 1_048_576:
                    return []
                
                try:
                    from .regex_fallback import index_file_regex
                    entries = index_file_regex(file_path, rel_path, language)
                    return entries
                except ImportError:
                    return []
            except (OSError, IOError):
                return []
        
        return []

    def _walk_tree(
        self,
        node,
        source_bytes: bytes,
        rel_path: str,
        language: str,
        symbol_types: dict,
        entries: list[SkeletonEntry],
    ):
        """Recursively walk the AST and extract symbol skeletons."""
        if node.type in symbol_types:
            kind = symbol_types[node.type]
            name = _extract_name(node, source_bytes, language)

            if name:
                # Determine if this is a method (inside a class)
                parent_class = _find_parent_class(node)
                if parent_class and kind == SymbolKind.FUNCTION:
                    kind = SymbolKind.METHOD

                entry = SkeletonEntry(
                    file_path=rel_path,
                    name=name,
                    kind=kind,
                    signature=_extract_signature(node, source_bytes),
                    line_start=node.start_point[0] + 1,
                    line_end=node.end_point[0] + 1,
                    parent=parent_class,
                    docstring=_extract_docstring(node, source_bytes, language),
                    body_hash=_body_hash(node, source_bytes),
                    data_context=_extract_data_context(node, source_bytes, language, kind),
                )
                entries.append(entry)

        # Recurse into children
        for child in node.children:
            self._walk_tree(child, source_bytes, rel_path, language, symbol_types, entries)

    def save(self, skeleton: SkeletalIndex):
        """Persist the skeleton index to disk as JSON."""
        os.makedirs(os.path.dirname(self.index_path), exist_ok=True)
        with open(self.index_path, "w", encoding="utf-8") as f:
            json.dump(skeleton.to_dict(), f, indent=2)
        print(f"[skeletal_indexer] Saved index to {self.index_path}")

    def load(self) -> Optional[SkeletalIndex]:
        """Load a previously saved skeleton index."""
        if not os.path.exists(self.index_path):
            return None
        try:
            with open(self.index_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            return SkeletalIndex.from_dict(data)
        except (json.JSONDecodeError, KeyError):
            return None

    def index_and_save(self) -> SkeletalIndex:
        """Index the codebase and persist the result."""
        skeleton = self.index()
        self.save(skeleton)
        return skeleton


if __name__ == "__main__":
    import sys
    root = sys.argv[1] if len(sys.argv) > 1 else "."
    indexer = SkeletalIndexer(root)
    skeleton = indexer.index_and_save()
    print(f"\nSkeleton: {skeleton.file_count} files, {skeleton.total_symbols} symbols")
    for entry in skeleton.entries[:10]:
        print(f"  [{entry.kind.value}] {entry.parent + '.' if entry.parent else ''}{entry.name} @ {entry.file_path}:{entry.line_start}")
