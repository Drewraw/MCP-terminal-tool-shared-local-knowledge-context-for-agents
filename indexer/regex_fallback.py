"""
Regex-Based Fallback Indexer
==============================
For languages without tree-sitter packages (Dart, Kotlin, Swift, PHP, Ruby,
C#, C/C++, etc.), extract class/function/method definitions using regex.

Not as accurate as tree-sitter AST parsing, but good enough for skeletal
indexing — captures ~90% of symbols for pruning decisions.
"""

from __future__ import annotations

import hashlib
import re
from typing import Optional

from .models import SkeletonEntry, SymbolKind


# ── Language-specific regex patterns ─────────────────────────────────

# Each pattern group: (kind, compiled_regex)
# Regex must have named groups: 'name' (required), 'signature' (optional)

_DART_PATTERNS = [
    # class / abstract class / mixin / extension
    (SymbolKind.CLASS, re.compile(
        r'^(?:abstract\s+)?(?:class|mixin|extension)\s+(?P<name>\w+)',
        re.MULTILINE,
    )),
    # enum
    (SymbolKind.ENUM, re.compile(
        r'^enum\s+(?P<name>\w+)',
        re.MULTILINE,
    )),
    # top-level and method functions:
    # Future<void> foo(...) async {
    # static String bar(int x) {
    # void _private() {
    (SymbolKind.FUNCTION, re.compile(
        r'^[ \t]*(?:static\s+)?(?:Future<[^>]*>|Stream<[^>]*>|void|int|double|bool|String|List<[^>]*>|Map<[^>]*>|Set<[^>]*>|dynamic|\w+[\w<>,\s]*?)\s+(?P<name>\w+)\s*(?:<[^>]*>)?\s*\(',
        re.MULTILINE,
    )),
    # factory constructors: factory ClassName.named(...)
    (SymbolKind.METHOD, re.compile(
        r'^[ \t]*factory\s+(?P<name>\w+(?:\.\w+)?)\s*\(',
        re.MULTILINE,
    )),
]

_KOTLIN_PATTERNS = [
    (SymbolKind.CLASS, re.compile(
        r'^(?:(?:abstract|open|data|sealed|inner|annotation)\s+)*class\s+(?P<name>\w+)',
        re.MULTILINE,
    )),
    (SymbolKind.INTERFACE, re.compile(
        r'^(?:fun\s+)?interface\s+(?P<name>\w+)',
        re.MULTILINE,
    )),
    (SymbolKind.ENUM, re.compile(
        r'^enum\s+class\s+(?P<name>\w+)',
        re.MULTILINE,
    )),
    (SymbolKind.CLASS, re.compile(
        r'^object\s+(?P<name>\w+)',
        re.MULTILINE,
    )),
    (SymbolKind.FUNCTION, re.compile(
        r'^[ \t]*(?:(?:private|public|internal|protected|override|suspend|inline|operator)\s+)*fun\s+(?:<[^>]*>\s+)?(?P<name>\w+)\s*\(',
        re.MULTILINE,
    )),
]

_SWIFT_PATTERNS = [
    (SymbolKind.CLASS, re.compile(
        r'^(?:(?:public|private|internal|open|final)\s+)*(?:class|actor)\s+(?P<name>\w+)',
        re.MULTILINE,
    )),
    (SymbolKind.CLASS, re.compile(
        r'^(?:(?:public|private|internal)\s+)*struct\s+(?P<name>\w+)',
        re.MULTILINE,
    )),
    (SymbolKind.INTERFACE, re.compile(
        r'^(?:(?:public|private|internal)\s+)*protocol\s+(?P<name>\w+)',
        re.MULTILINE,
    )),
    (SymbolKind.ENUM, re.compile(
        r'^(?:(?:public|private|internal)\s+)*enum\s+(?P<name>\w+)',
        re.MULTILINE,
    )),
    (SymbolKind.FUNCTION, re.compile(
        r'^[ \t]*(?:(?:public|private|internal|open|override|static|class|@\w+)\s+)*func\s+(?P<name>\w+)\s*(?:<[^>]*>)?\s*\(',
        re.MULTILINE,
    )),
]

_CSHARP_PATTERNS = [
    (SymbolKind.CLASS, re.compile(
        r'^[ \t]*(?:(?:public|private|protected|internal|static|abstract|sealed|partial)\s+)*class\s+(?P<name>\w+)',
        re.MULTILINE,
    )),
    (SymbolKind.INTERFACE, re.compile(
        r'^[ \t]*(?:(?:public|private|protected|internal)\s+)*interface\s+(?P<name>\w+)',
        re.MULTILINE,
    )),
    (SymbolKind.ENUM, re.compile(
        r'^[ \t]*(?:(?:public|private|protected|internal)\s+)*enum\s+(?P<name>\w+)',
        re.MULTILINE,
    )),
    (SymbolKind.CLASS, re.compile(
        r'^[ \t]*(?:(?:public|private|protected|internal|static|abstract|sealed|partial)\s+)*struct\s+(?P<name>\w+)',
        re.MULTILINE,
    )),
    (SymbolKind.FUNCTION, re.compile(
        r'^[ \t]*(?:(?:public|private|protected|internal|static|virtual|override|abstract|async)\s+)*(?:[\w<>\[\],\s]+?)\s+(?P<name>\w+)\s*(?:<[^>]*>)?\s*\([^)]*\)',
        re.MULTILINE,
    )),
]

_PHP_PATTERNS = [
    (SymbolKind.CLASS, re.compile(
        r'^(?:(?:abstract|final)\s+)?class\s+(?P<name>\w+)',
        re.MULTILINE,
    )),
    (SymbolKind.INTERFACE, re.compile(
        r'^interface\s+(?P<name>\w+)',
        re.MULTILINE,
    )),
    (SymbolKind.INTERFACE, re.compile(
        r'^trait\s+(?P<name>\w+)',
        re.MULTILINE,
    )),
    (SymbolKind.ENUM, re.compile(
        r'^enum\s+(?P<name>\w+)',
        re.MULTILINE,
    )),
    (SymbolKind.FUNCTION, re.compile(
        r'^[ \t]*(?:(?:public|private|protected|static|abstract|final)\s+)*function\s+(?P<name>\w+)\s*\(',
        re.MULTILINE,
    )),
]

_RUBY_PATTERNS = [
    (SymbolKind.CLASS, re.compile(
        r'^[ \t]*class\s+(?P<name>[\w:]+)',
        re.MULTILINE,
    )),
    (SymbolKind.MODULE, re.compile(
        r'^[ \t]*module\s+(?P<name>[\w:]+)',
        re.MULTILINE,
    )),
    (SymbolKind.FUNCTION, re.compile(
        r'^[ \t]*def\s+(?:self\.)?(?P<name>\w+[?!=]?)',
        re.MULTILINE,
    )),
]

_C_CPP_PATTERNS = [
    (SymbolKind.CLASS, re.compile(
        r'^[ \t]*(?:class|struct)\s+(?P<name>\w+)',
        re.MULTILINE,
    )),
    (SymbolKind.ENUM, re.compile(
        r'^[ \t]*enum\s+(?:class\s+)?(?P<name>\w+)',
        re.MULTILINE,
    )),
    # function definitions (type name(...) {)
    (SymbolKind.FUNCTION, re.compile(
        r'^(?:(?:static|inline|virtual|extern|const|unsigned|signed)\s+)*(?:[\w:*&<>]+\s+)+(?P<name>\w+)\s*\([^;]*\)\s*(?:const\s*)?(?:override\s*)?(?:noexcept\s*)?\{',
        re.MULTILINE,
    )),
]


# ── Markdown patterns ──────────────────────────────────────────────
# Markdown doesn't have classes/functions. Instead we extract:
#   - Headings as HEADING symbols (searchable by topic)
#   - File path references as FILE_REF symbols (link docs to code)
#   - Sections with content summaries as SECTION symbols

_MD_HEADING_PATTERN = re.compile(
    r'^(?P<hashes>#{1,4})\s+(?P<name>.+?)(?:\s*#*\s*)?$',
    re.MULTILINE,
)

# Match file paths in docs: `path/to/file.ext`, path/to/file.ext, **path/to/file.ext**
# Covers: .dart, .ts, .js, .py, .go, .rs, .java, .kt, .swift, .yaml, .json, etc.
_MD_FILE_REF_PATTERN = re.compile(
    r'[`*]*(?P<name>(?:[\w./-]+/)?[\w.-]+\.(?:dart|ts|tsx|js|jsx|py|go|rs|java|kt|swift|cs|rb|php|c|cpp|h|yaml|yml|json|rules))\b[`*]*',
)

# Match directory references: `path/to/dir/`, functions/src/incident-engine/
_MD_DIR_REF_PATTERN = re.compile(
    r'[`*]*(?P<name>(?:[\w.-]+/){1,8}[\w.-]+/)[`*]*',
)

# ── Language registry ────────────────────────────────────────────────

REGEX_LANG_MAP: dict[str, str] = {
    ".dart": "dart",
    ".kt": "kotlin",
    ".kts": "kotlin",
    ".swift": "swift",
    ".cs": "csharp",
    ".php": "php",
    ".rb": "ruby",
    ".c": "c_cpp",
    ".cpp": "c_cpp",
    ".cc": "c_cpp",
    ".cxx": "c_cpp",
    ".h": "c_cpp",
    ".hpp": "c_cpp",
    ".m": "swift",      # Objective-C close enough to Swift patterns
    ".mm": "swift",
    ".scala": "kotlin",  # Scala close enough to Kotlin patterns
    ".r": "ruby",        # R is simple enough
    ".md": "markdown",
}

REGEX_PATTERNS: dict[str, list] = {
    "dart": _DART_PATTERNS,
    "kotlin": _KOTLIN_PATTERNS,
    "swift": _SWIFT_PATTERNS,
    "csharp": _CSHARP_PATTERNS,
    "php": _PHP_PATTERNS,
    "ruby": _RUBY_PATTERNS,
    "c_cpp": _C_CPP_PATTERNS,
    "markdown": [],  # Markdown uses a custom indexer, not the generic regex pipeline
}


# ── Core extraction ─────────────────────────────────────────────────

def _find_block_end(lines: list[str], start_line: int) -> int:
    """Find the end of a brace-delimited block starting near start_line."""
    depth = 0
    found_open = False
    for i in range(start_line, min(start_line + 500, len(lines))):
        for ch in lines[i]:
            if ch == '{':
                depth += 1
                found_open = True
            elif ch == '}':
                depth -= 1
                if found_open and depth == 0:
                    return i
    # Fallback: if no block found, estimate ~20 lines
    return min(start_line + 20, len(lines) - 1)


def _extract_signature_from_lines(lines: list[str], match_line: int) -> str:
    """Extract the signature (everything up to the first '{' or end of params)."""
    sig_lines = []
    for i in range(match_line, min(match_line + 8, len(lines))):
        line = lines[i]
        sig_lines.append(line)
        stripped = line.strip()
        if '{' in stripped or stripped.endswith(';'):
            # Cut at the brace
            last = sig_lines[-1]
            brace_pos = last.find('{')
            if brace_pos >= 0:
                sig_lines[-1] = last[:brace_pos].rstrip()
            break
    sig = '\n'.join(sig_lines).strip()
    if len(sig) > 500:
        sig = sig[:500] + '...'
    return sig


def _extract_comment_above(lines: list[str], match_line: int) -> Optional[str]:
    """Extract doc comment immediately above the definition."""
    doc_lines = []
    for i in range(match_line - 1, max(match_line - 15, -1), -1):
        stripped = lines[i].strip()
        if stripped.startswith('///') or stripped.startswith('//') or stripped.startswith('*') or stripped.startswith('/**') or stripped.startswith('*/'):
            doc_lines.insert(0, stripped)
        elif stripped == '':
            continue  # skip blank lines between doc and definition
        else:
            break
    if doc_lines:
        doc = '\n'.join(doc_lines)
        return doc[:300] + '...' if len(doc) > 300 else doc
    return None


def _extract_enum_values_regex(lines: list[str], start_line: int) -> Optional[str]:
    """
    Extract enum value names from brace-delimited enum body.
    Works for Dart, C#, Kotlin, Swift, and other regex-parsed languages.

    Returns pipe-separated names e.g. "road|sanitation|water|civicReport",
    or None if the enum body cannot be parsed.
    """
    # Collect lines until the block closes
    block_lines = []
    depth = 0
    found_open = False
    for i in range(start_line, min(start_line + 80, len(lines))):
        line = lines[i]
        for ch in line:
            if ch == '{':
                depth += 1
                found_open = True
            elif ch == '}':
                depth -= 1
        if found_open:
            block_lines.append(line)
        if found_open and depth == 0:
            break

    if not block_lines:
        return None

    # Strip single-line comments BEFORE joining so they don't bleed into next value
    cleaned_lines = [re.sub(r'//[^\n]*', '', line) for line in block_lines]
    block_text = ' '.join(cleaned_lines)
    # Strip block comments
    block_text = re.sub(r'/\*.*?\*/', '', block_text, flags=re.DOTALL)

    start = block_text.find('{')
    end = block_text.rfind('}')
    if start == -1 or end == -1:
        return None

    inner = block_text[start + 1:end]
    # Split on commas/semicolons (Dart 2.17+ enums use `;` to separate values from methods)
    values = []
    _SKIP_KW = {'final', 'const', 'static', 'late', 'void', 'dynamic', 'override',
                'abstract', 'required', 'external', 'get', 'set', 'async', 'await'}
    for segment in re.split(r'[,;]', inner):
        segment = segment.strip()
        segment = re.sub(r'@\w+(?:\([^)]*\))?', '', segment).strip()
        m = re.match(r'^([A-Za-z_]\w*)', segment)
        if m:
            name = m.group(1)
            if name not in _SKIP_KW and len(name) > 1:
                values.append(name)

    if values:
        return '|'.join(values[:20])
    return None


def _detect_parent_class(lines: list[str], match_line: int) -> Optional[str]:
    """Walk backwards to find enclosing class definition."""
    indent = len(lines[match_line]) - len(lines[match_line].lstrip())
    if indent < 2:
        return None  # top-level, no parent

    for i in range(match_line - 1, max(match_line - 200, -1), -1):
        line = lines[i]
        line_indent = len(line) - len(line.lstrip())
        if line_indent < indent:
            m = re.match(r'(?:abstract\s+)?(?:class|mixin|struct|extension|object)\s+(\w+)', line.strip())
            if m:
                return m.group(1)
            break
    return None


def index_file_regex(file_path: str, rel_path: str, language: str) -> list[SkeletonEntry]:
    """Extract skeleton entries from a file using regex patterns."""
    patterns = REGEX_PATTERNS.get(language)
    if not patterns:
        return []

    try:
        with open(file_path, 'r', encoding='utf-8', errors='replace') as f:
            content = f.read()
    except OSError:
        return []

    if len(content) > 1_048_576:  # skip >1MB files
        return []

    lines = content.split('\n')
    entries: list[SkeletonEntry] = []
    seen_names_at_line: set[tuple[str, int]] = set()  # dedup

    for kind, pattern in patterns:
        for match in pattern.finditer(content):
            name = match.group('name')
            # Calculate line number
            line_num = content[:match.start()].count('\n')

            # Dedup: same name at same line
            key = (name, line_num)
            if key in seen_names_at_line:
                continue
            seen_names_at_line.add(key)

            # Skip common false positives
            if name in ('if', 'for', 'while', 'switch', 'catch', 'return', 'else', 'try', 'new', 'throw', 'await', 'yield'):
                continue

            # Determine actual kind (method if inside a class)
            actual_kind = kind
            parent_class = None
            if kind == SymbolKind.FUNCTION:
                parent_class = _detect_parent_class(lines, line_num)
                if parent_class:
                    actual_kind = SymbolKind.METHOD

            # Find block end
            line_end = _find_block_end(lines, line_num)

            # Extract signature
            signature = _extract_signature_from_lines(lines, line_num)

            # Extract docstring
            docstring = _extract_comment_above(lines, line_num)

            # Body hash
            body_text = '\n'.join(lines[line_num:line_end + 1])
            body_hash = hashlib.md5(body_text.encode('utf-8')).hexdigest()[:12]

            # Extract enum values for ENUM symbols (no body reading needed)
            data_context = None
            if actual_kind == SymbolKind.ENUM:
                data_context = _extract_enum_values_regex(lines, line_num)

            entries.append(SkeletonEntry(
                file_path=rel_path,
                name=name,
                kind=actual_kind,
                signature=signature,
                line_start=line_num + 1,  # 1-indexed
                line_end=line_end + 1,
                parent=parent_class,
                docstring=docstring,
                body_hash=body_hash,
                data_context=data_context,
            ))

    return entries


# ── Markdown indexer ───────────────────────────────────────────────

# Common README filenames to prioritize
_README_NAMES = {"readme.md", "readme", "architecture.md", "contributing.md",
                 "design.md", "overview.md", "index.md"}


def index_markdown_file(file_path: str, rel_path: str) -> list[SkeletonEntry]:
    """
    Extract skeleton entries from a Markdown file.

    Extracts:
      - HEADING entries: each heading becomes a searchable symbol with
        the content underneath as its docstring (first 300 chars).
      - FILE_REF entries: file paths mentioned in the doc. These link
        documentation context back to code files so the pruner can
        discover code modules described in READMEs.
      - SECTION entries: top-level sections (h1/h2) with a content summary.

    This is what makes "scan project" aware of architecture docs —
    when a query mentions "incident detection", the pruner can now
    find README sections describing the incident engine AND the
    code files those sections reference.
    """
    try:
        with open(file_path, 'r', encoding='utf-8', errors='replace') as f:
            content = f.read()
    except OSError:
        return []

    # Skip very large markdown files (>500KB — likely generated)
    if len(content) > 500_000:
        return []

    lines = content.split('\n')
    entries: list[SkeletonEntry] = []
    seen_refs: set[str] = set()

    # ── Extract headings with section content ─────────────────────
    heading_positions = []
    for match in _MD_HEADING_PATTERN.finditer(content):
        level = len(match.group('hashes'))
        name = match.group('name').strip()
        line_num = content[:match.start()].count('\n')
        heading_positions.append((line_num, level, name))

    for idx, (line_num, level, name) in enumerate(heading_positions):
        # Determine section end (next heading of same or higher level, or EOF)
        if idx + 1 < len(heading_positions):
            section_end = heading_positions[idx + 1][0] - 1
        else:
            section_end = len(lines) - 1

        # Extract section content as docstring (first 300 chars)
        section_lines = lines[line_num + 1:min(line_num + 20, section_end + 1)]
        section_text = '\n'.join(l for l in section_lines if l.strip()).strip()
        docstring = section_text[:300] + '...' if len(section_text) > 300 else section_text

        # Clean name: remove markdown formatting like **bold**, `code`, [links](url)
        clean_name = re.sub(r'[*`\[\]]', '', name).strip()
        clean_name = re.sub(r'\(http[^)]*\)', '', clean_name).strip()

        if not clean_name or len(clean_name) < 2:
            continue

        kind = SymbolKind.SECTION if level <= 2 else SymbolKind.HEADING
        body_hash = hashlib.md5(section_text.encode('utf-8')).hexdigest()[:12]

        entries.append(SkeletonEntry(
            file_path=rel_path,
            name=clean_name,
            kind=kind,
            signature=f"{'#' * level} {clean_name}",
            line_start=line_num + 1,
            line_end=section_end + 1,
            parent=None,
            docstring=docstring if docstring else None,
            body_hash=body_hash,
        ))

    # ── Extract file path references ──────────────────────────────
    # These create links between docs and code files
    for match in _MD_FILE_REF_PATTERN.finditer(content):
        ref_path = match.group('name')
        # Normalize path separators
        ref_path_normalized = ref_path.replace('\\', '/')

        # Skip duplicates and very short refs
        if ref_path_normalized in seen_refs or len(ref_path_normalized) < 5:
            continue
        seen_refs.add(ref_path_normalized)

        line_num = content[:match.start()].count('\n')

        # Find the heading this reference lives under (for context)
        parent_heading = None
        for h_line, h_level, h_name in reversed(heading_positions):
            if h_line <= line_num:
                parent_heading = re.sub(r'[*`\[\]]', '', h_name).strip()
                break

        # Get surrounding context (the line containing the reference)
        context_line = lines[line_num].strip() if line_num < len(lines) else ""
        docstring = context_line[:300] if context_line else None

        entries.append(SkeletonEntry(
            file_path=rel_path,
            name=ref_path_normalized,
            kind=SymbolKind.FILE_REF,
            signature=f"ref: {ref_path_normalized}",
            line_start=line_num + 1,
            line_end=line_num + 1,
            parent=parent_heading,
            docstring=docstring,
            body_hash=hashlib.md5(ref_path_normalized.encode()).hexdigest()[:12],
        ))

    # ── Extract directory references ──────────────────────────────
    for match in _MD_DIR_REF_PATTERN.finditer(content):
        ref_path = match.group('name').rstrip('/')
        ref_path_normalized = ref_path.replace('\\', '/')

        if ref_path_normalized in seen_refs or len(ref_path_normalized) < 4:
            continue
        # Skip URLs
        if ref_path_normalized.startswith(('http', 'www', '//')):
            continue
        seen_refs.add(ref_path_normalized)

        line_num = content[:match.start()].count('\n')
        context_line = lines[line_num].strip() if line_num < len(lines) else ""

        parent_heading = None
        for h_line, h_level, h_name in reversed(heading_positions):
            if h_line <= line_num:
                parent_heading = re.sub(r'[*`\[\]]', '', h_name).strip()
                break

        entries.append(SkeletonEntry(
            file_path=rel_path,
            name=ref_path_normalized,
            kind=SymbolKind.FILE_REF,
            signature=f"dir: {ref_path_normalized}/",
            line_start=line_num + 1,
            line_end=line_num + 1,
            parent=parent_heading,
            docstring=context_line[:300] if context_line else None,
            body_hash=hashlib.md5(ref_path_normalized.encode()).hexdigest()[:12],
        ))

    return entries
