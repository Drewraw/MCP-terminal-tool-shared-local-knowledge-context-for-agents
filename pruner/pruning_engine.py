"""
Pruning Engine — The "Brain"
=============================
Implements SWE-Pruner-style self-adaptive context-aware pruning:

1. Context Loading:    README + annotations + skeleton → project understanding
2. Scout (Llama 1B):  Ranks symbols by semantic relevance to the query
3. On-Demand Extract:  Reads only the Scout-selected files
4. Precision Pruning:  Strips noise keeping only goal-relevant logic

The key insight from the SWE-Pruner paper: 76% of agent tokens are
spent on read operations. By indexing structure first and reading
bodies on-demand, we eliminate most of that waste.

The Scout (Llama 3.2-1B) adds semantic understanding:
  README → /src/auth (User Annotation) → session_logic.py (Final Selection)
"""

from __future__ import annotations

import os
import re
from typing import Optional, Dict

try:
    from ..indexer.models import SkeletalIndex, SkeletonEntry, SymbolKind
    from .models import PruneRequest, PruneResult, PrunedFile, PruneStats
    from .token_counter import count_tokens
    from .context_loader import ContextLoader
    from .scout import Scout, ScoutResult
    from .auto_annotator import AutoAnnotator
except ImportError:
    from indexer.models import SkeletalIndex, SkeletonEntry, SymbolKind
    from pruner.models import PruneRequest, PruneResult, PrunedFile, PruneStats
    from pruner.token_counter import count_tokens
    from pruner.context_loader import ContextLoader
    from pruner.scout import Scout, ScoutResult
    try:
        from pruner.auto_annotator import AutoAnnotator
    except ImportError:
        AutoAnnotator = None


# Patterns for noise detection
DOCSTRING_PATTERNS = [
    re.compile(r'^\s*"""[\s\S]*?"""\s*$', re.MULTILINE),
    re.compile(r"^\s*'''[\s\S]*?'''\s*$", re.MULTILINE),
    re.compile(r'^\s*/\*\*[\s\S]*?\*/\s*$', re.MULTILINE),
]

IMPORT_PATTERN = re.compile(
    r"^\s*(import\s+|from\s+\S+\s+import\s+|const\s+.*=\s*require\(|"
    r"use\s+|#include\s+|package\s+)",
    re.MULTILINE,
)

BLANK_OR_COMMENT = re.compile(r"^\s*(#.*|//.*|/\*.*\*/|\*.*|--.*)?$")

# File extensions treated as TSX/React for component-aware pruning
_TSX_EXTENSIONS = {".tsx", ".jsx"}

# Patterns that indicate a Props/type definition name (kept in full)
_PROPS_NAME_PATTERN = re.compile(r"(?i)props|state|context|config|options")


class PruningEngine:
    """
    Context-aware pruning engine that combines:
      - Multi-layered context (README + annotations + skeleton)
      - Scout LLM (Llama 3.2-1B) for semantic symbol ranking
      - On-demand body extraction and noise removal

    When the Scout is available (Ollama or Groq), it replaces keyword
    search with semantic understanding. When unavailable, falls back
    to the original keyword-based skeleton search.
    """

    def __init__(
        self,
        skeleton: SkeletalIndex,
        root_path: str,
        annotations: Optional[Dict[str, str]] = None,
        scout: Optional[Scout] = None,
        folder_map: Optional[Dict] = None,
        auto_annotations_path: Optional[str] = None,
    ):
        self.skeleton = skeleton
        self.root_path = root_path
        self.annotations = annotations or {}
        self.scout = scout or Scout()
        self.context_loader = ContextLoader(root_path)
        self.folder_map = folder_map

        # Auto-annotator: lazy file-level semantic annotations
        _cache_path = auto_annotations_path or os.path.join(
            root_path, ".prunetool", "auto_annotations.json"
        )
        _groq_key = getattr(self.scout, "groq_api_key", "")
        self.auto_annotator = AutoAnnotator(_cache_path, groq_api_key=_groq_key) if AutoAnnotator else None

        # README summary for annotation context (first 200 chars)
        self._readme_summary = self._load_readme_summary()

    def _load_readme_summary(self) -> str:
        """
        Build project context for AutoAnnotator from prune library/ .md files only.

        Scans ALL .md files in <project>/prune library/, extracts:
          - Every heading line (# ## ###) — structural map
          - First non-empty sentence/line under each heading — key fact
        Uses mtime cache so re-reads only changed files between scans.
        Result is capped at ~1500 chars to stay within Llama's context budget.
        """
        lib_dir = os.path.join(self.root_path, "prune library")
        if not os.path.isdir(lib_dir):
            return ""

        parts = []
        try:
            md_files = sorted([
                f for f in os.listdir(lib_dir)
                if f.lower().endswith(".md")
            ])
        except OSError:
            return ""

        for fname in md_files:
            fpath = os.path.join(lib_dir, fname)
            try:
                with open(fpath, "r", encoding="utf-8", errors="replace") as f:
                    lines = f.readlines()
            except OSError:
                continue

            # Extract headings + first content line under each heading
            file_parts = [f"=== {fname} ==="]
            pending_heading = None
            for line in lines:
                stripped = line.strip()
                if stripped.startswith("#"):
                    pending_heading = stripped
                elif pending_heading is not None and stripped:
                    # First non-empty line under heading
                    file_parts.append(f"{pending_heading}")
                    file_parts.append(f"  {stripped[:120]}")
                    pending_heading = None
                # skip blank lines and lines under already-consumed headings

            if len(file_parts) > 1:  # has content beyond filename
                parts.append("\n".join(file_parts))

        summary = "\n\n".join(parts)
        return summary[:1500]

    def prune(self, request: PruneRequest) -> PruneResult:
        """
        Execute the full pruning pipeline:
        1. Load multi-layered context (README + annotations + skeleton)
        2. Scout ranks symbols (Llama 1B) OR keyword search fallback
        3. Read only the Scout-selected file sections
        4. Strip noise from extracted code
        5. Return pruned result with stats
        """
        goal = request.goal_hint or self._infer_goal(request.user_query)
        result = PruneResult(goal_hint_used=goal)

        # Step 1: Try Scout-based ranking (semantic understanding)
        matched_entries = self._scout_rank(request.user_query, goal)

        # Step 2: Fall back to keyword search if Scout returned nothing
        if not matched_entries:
            matched_entries = self.skeleton.search(goal, top_k=30)

        # If specific files were requested, filter to those + matches
        if request.file_paths:
            file_set = set(request.file_paths)
            file_entries = [e for e in matched_entries if e.file_path in file_set]
            for fp in request.file_paths:
                if not any(e.file_path == fp for e in file_entries):
                    file_entries.extend(self.skeleton.get_entries_for_file(fp))
            matched_entries = file_entries

        if not matched_entries:
            matched_entries = self._fallback_file_search(goal)

        # Step 2: Group by file and extract
        files_to_entries: dict[str, list[SkeletonEntry]] = {}
        for entry in matched_entries:
            files_to_entries.setdefault(entry.file_path, []).append(entry)

        # Step 3: On-demand extraction + pruning per file
        total_budget = request.max_tokens
        tokens_used = 0

        for file_path, entries in files_to_entries.items():
            if tokens_used >= total_budget:
                break

            pruned_file = self._prune_file(file_path, entries, goal)
            if pruned_file and pruned_file.pruned_tokens > 0:
                tokens_used += pruned_file.pruned_tokens
                result.pruned_files.append(pruned_file)

        # Step 4: Compute stats
        result.stats = self._compute_stats(result.pruned_files)
        return result

    def _infer_goal(self, user_query: str) -> str:
        """
        Infer a goal hint from the user's query.
        Simple keyword extraction — used as fallback when Scout is unavailable.
        """
        goal = user_query.strip()
        for prefix in ["how do i", "how does", "what is", "where is", "why does",
                        "can you", "please", "help me", "i need to", "fix the"]:
            if goal.lower().startswith(prefix):
                goal = goal[len(prefix):].strip()

        return f"Focus on: {goal}" if goal else "General code understanding"

    def _scout_rank(self, query: str, goal: str) -> list[SkeletonEntry]:
        """
        Use the Scout LLM to semantically rank symbols.

        Flow:
          1. Build multi-layered context (README + annotations + skeleton summary)
          2. Build compact symbol list from skeletal index
          3. Ask Scout to pick relevant symbols
          4. Map Scout's picks back to SkeletonEntry objects

        Returns empty list if Scout is unavailable or fails.
        """
        if not self.scout:
            return []

        try:
            # Build skeleton file summary for context loader
            skeleton_files: Dict[str, int] = {}
            for entry in self.skeleton.entries:
                skeleton_files[entry.file_path] = skeleton_files.get(entry.file_path, 0) + 1

            # Layer 1 + 2 + 3 + 4: Multi-layered context
            # Build folder dependency context if available
            folder_ctx = ""
            if self.folder_map:
                try:
                    from indexer.folder_mapper import format_folder_context
                    folder_ctx = format_folder_context(self.folder_map, max_folders=40)
                except ImportError:
                    pass

            context = self.context_loader.build_context(
                skeleton_files=skeleton_files,
                file_count=self.skeleton.file_count,
                total_symbols=self.skeleton.total_symbols,
                annotations=self.annotations,
                folder_context=folder_ctx,
            )

            # Build symbol list (exclude markdown entries — Scout sees code only)
            code_entries = [
                e for e in self.skeleton.entries
                if e.kind not in (SymbolKind.HEADING, SymbolKind.SECTION, SymbolKind.FILE_REF)
            ]

            # Pre-filter to stay within Groq's token budget (~30K tokens for symbol list)
            # Strategy: score by keyword overlap, cap at 5 symbols per file for diversity
            MAX_SCOUT_SYMBOLS = 1500
            MAX_PER_FILE = 5
            if len(code_entries) > MAX_SCOUT_SYMBOLS:
                _sw = {'the','a','an','in','on','of','to','for','how','what','where','why','is',
                       'are','does','do','can','and','or','with','from','that','this','it','be',
                       'dart','ts','js','py','md','json','yaml','txt','html','css'}
                q_words = set(re.sub(r'[^a-z0-9]', ' ', query.lower()).split()) - _sw
                def _score(e):
                    text = f"{e.file_path} {e.name} {e.parent or ''} {e.docstring or ''}".lower()
                    return sum(1 for w in q_words if w in text)
                scored = sorted(code_entries, key=_score, reverse=True)
                # Cap per file: ensures diverse file coverage rather than
                # one large file dominating all 1500 slots
                file_counts: dict[str, int] = {}
                top = []
                for e in scored:
                    if len(top) >= MAX_SCOUT_SYMBOLS:
                        break
                    fc = file_counts.get(e.file_path, 0)
                    if fc < MAX_PER_FILE:
                        top.append(e)
                        file_counts[e.file_path] = fc + 1
                code_entries = top

            # Load cached auto-annotations to enrich symbol hints
            file_annotations = self.auto_annotator.all_annotations() if self.auto_annotator else {}
            symbol_list = self.context_loader.build_symbol_list(code_entries, file_annotations)
            print(f"[pruner] Symbol list: {len(code_entries)} entries, {len(symbol_list):,} chars (~{len(symbol_list)//4:,} tokens)")

            # Ask the Scout
            scout_result = self.scout.rank_symbols(
                query=query,
                context=context,
                symbol_list=symbol_list,
                symbol_count=len(code_entries),
            )

            if scout_result.backend == "fallback" or not scout_result.ranked_symbols:
                return []

            print(f"[pruner] Scout symbols: {scout_result.ranked_symbols[:10]}")

            # Map Scout's symbol IDs back to SkeletonEntry objects
            # Scout returns "file_path::symbol_name" format
            # Strategy: try name+path match first; fall back to path-only match
            # (handles hallucinated symbol names — still captures the right file)
            matched = []
            seen = set()
            path_matched: set[str] = set()  # files already added via path fallback

            for symbol_id in scout_result.ranked_symbols:
                parts = symbol_id.split("::")
                if len(parts) != 2:
                    continue
                file_path = parts[0].replace("\\", "/")
                name = parts[1]

                found_by_name = False
                for entry in self.skeleton.entries:
                    entry_fp = entry.file_path.replace("\\", "/")
                    if entry.name == name and (
                        file_path in entry_fp or entry_fp.endswith(file_path)
                    ):
                        key = f"{entry.file_path}:{entry.name}:{entry.line_start}"
                        if key not in seen:
                            seen.add(key)
                            matched.append(entry)
                        found_by_name = True
                        break

                if not found_by_name:
                    # Name didn't match (hallucinated) — still include the file
                    for entry in self.skeleton.entries:
                        entry_fp = entry.file_path.replace("\\", "/")
                        if (file_path in entry_fp or entry_fp.endswith(file_path)) \
                                and entry.file_path not in path_matched:
                            path_matched.add(entry.file_path)
                            break

            # Expand: include all entries from every matched/path-matched file
            all_matched_files = {e.file_path for e in matched} | path_matched
            for entry in self.skeleton.entries:
                if entry.file_path in all_matched_files:
                    key = f"{entry.file_path}:{entry.name}:{entry.line_start}"
                    if key not in seen:
                        seen.add(key)
                        matched.append(entry)

            print(f"[pruner] Scout selected {len(scout_result.ranked_symbols)} symbols "
                  f"-> {len(matched)} entries from {len({e.file_path for e in matched})} files "
                  f"(via {scout_result.backend}, {scout_result.elapsed_ms}ms)")

            # Trigger lazy annotation for newly-seen files (benefits next call)
            if self.auto_annotator:
                self._trigger_lazy_annotations(matched)

            return matched

        except Exception as e:
            print(f"[pruner] Scout error: {e}, falling back to keyword search")
            return []

    def _trigger_lazy_annotations(self, matched_entries: list) -> None:
        """
        After Scout selection, trigger lazy annotation for files not yet annotated.
        Runs synchronously but only calls Groq for NEW files (cached ones are instant).
        Results are saved to disk so the NEXT Scout call benefits.
        """
        if not self.auto_annotator:
            return

        # Group entries by file, build data_context per file
        from collections import defaultdict
        by_file: dict[str, list] = defaultdict(list)
        for entry in matched_entries:
            by_file[entry.file_path].append(entry)

        file_specs = []
        for file_path, entries in by_file.items():
            try:
                from pruner.auto_annotator import AutoAnnotator as _AA
                symbols = _AA.build_file_data_context(file_path, entries)
            except ImportError:
                from auto_annotator import AutoAnnotator as _AA
                symbols = _AA.build_file_data_context(file_path, entries)

            file_specs.append({
                "file_path": file_path,
                "symbols": symbols,
            })

        if file_specs:
            self.auto_annotator.lazy_annotate_batch(file_specs, self.folder_map)

    def _is_jsx_file(self, rel_path: str) -> bool:
        """Check if file is a TSX/JSX file that needs component-aware pruning."""
        _, ext = os.path.splitext(rel_path)
        return ext.lower() in _TSX_EXTENSIONS

    def _is_props_definition(self, entry: SkeletonEntry) -> bool:
        """Check if entry is a Props/type definition that should be kept in full."""
        return _PROPS_NAME_PATTERN.search(entry.name) is not None

    def _extract_signature_lines(
        self,
        lines: list[str],
        line_start: int,
        line_end: int,
    ) -> set[int]:
        """
        For a component/function, extract only the signature lines up to the opening brace.
        Returns 0-indexed line numbers.
        """
        sig_lines: set[int] = set()
        brace_found = False

        for ln in range(line_start - 1, min(line_end, len(lines))):
            sig_lines.add(ln)
            line = lines[ln]
            # Look for opening brace or return type (JSX)
            if "{" in line or "return" in line and not brace_found:
                brace_found = True
                break

        return sig_lines if sig_lines else {line_start - 1}

    def _extract_skeleton_view(self, lines: list[str]) -> set[int]:
        """
        Extract a high-level skeleton of the file:
        - All import/require statements
        - All class, interface, enum, function definitions (just signature)
        - Docstrings and comments at top level
        
        Used when pruning results in too little content (safety fallback).
        """
        skeleton_lines: set[int] = set()
        pattern_class_or_interface = re.compile(
            r"^\s*(export\s+)?(class|interface|enum|type|function|const\s+.*=\s*\(|async\s+function)"
        )
        pattern_import = IMPORT_PATTERN
        pattern_docstring = re.compile(r'^\s*("""|\'\'\' |/\*\*|#####|===)')

        for i, line in enumerate(lines):
            stripped = line.strip()

            # Always keep imports
            if pattern_import.match(line):
                skeleton_lines.add(i)
                continue

            # Keep top-level definitions (class, interface, function, const arrow)
            if pattern_class_or_interface.match(line):
                skeleton_lines.add(i)
                # For multi-line signatures, keep up to opening brace
                for j in range(i, min(i + 10, len(lines))):
                    skeleton_lines.add(j)
                    if "{" in lines[j] or "=>" in lines[j]:
                        break
                continue

            # Keep docstrings and block comments at file level
            if pattern_docstring.match(line) and i < len(lines) // 2:
                skeleton_lines.add(i)
                continue

        return skeleton_lines

    def _prune_file(
        self,
        rel_path: str,
        entries: list[SkeletonEntry],
        goal: str,
    ) -> Optional[PrunedFile]:
        """Read a file and extract only goal-relevant sections."""
        abs_path = os.path.join(self.root_path, rel_path)
        if not os.path.isfile(abs_path):
            return None

        try:
            with open(abs_path, "r", encoding="utf-8", errors="replace") as f:
                raw_content = f.read()
        except OSError:
            return None

        raw_lines = raw_content.split("\n")
        raw_line_count = len(raw_lines)
        raw_tokens = count_tokens(raw_content)

        # Determine which lines to keep based on matched symbols
        keep_lines: set[int] = set()
        kept_symbols: list[str] = []
        removed_sections: list[str] = []

        is_jsx = self._is_jsx_file(rel_path)

        for entry in entries:
            # For TSX/JSX files, use component-aware pruning
            if is_jsx:
                if self._is_props_definition(entry):
                    # Keep Props/type definitions in full
                    for ln in range(entry.line_start - 1, min(entry.line_end, raw_line_count)):
                        keep_lines.add(ln)
                else:
                    # For component functions, keep only signature lines
                    sig_lines = self._extract_signature_lines(raw_lines, entry.line_start, entry.line_end)
                    keep_lines.update(sig_lines)
            else:
                # For non-JSX files, keep all lines as before
                for ln in range(entry.line_start - 1, min(entry.line_end, raw_line_count)):
                    keep_lines.add(ln)

            symbol_name = f"{entry.parent + '.' if entry.parent else ''}{entry.name}"
            kept_symbols.append(symbol_name)

        # Also keep structural context: lines referenced by kept symbols
        keep_lines = self._expand_context(raw_lines, keep_lines, goal)

        # SAFETY FALLBACK: If pruning resulted in almost no content, use skeleton view
        if len(keep_lines) < max(3, raw_line_count // 20):  # Less than 5% or 3 lines
            skeleton_keep = self._extract_skeleton_view(raw_lines)
            if skeleton_keep and len(skeleton_keep) > len(keep_lines):
                keep_lines = skeleton_keep
                # Mark that we used fallback
                kept_symbols = ["[High-Level Skeleton View - No exact matches found]"]

        # Build pruned content
        pruned_parts: list[str] = []
        in_gap = False
        gap_start = -1

        for i, line in enumerate(raw_lines):
            if i in keep_lines:
                if in_gap:
                    gap_size = i - gap_start
                    if gap_size <= 3:
                        # Small gap — keep it for readability
                        for j in range(gap_start, i):
                            pruned_parts.append(raw_lines[j])
                    else:
                        pruned_parts.append(f"    # ... [{gap_size} lines pruned] ...")
                        removed_sections.append(f"lines {gap_start + 1}-{i}")
                    in_gap = False
                pruned_parts.append(line)
            else:
                if not in_gap:
                    in_gap = True
                    gap_start = i

        # Handle trailing gap
        if in_gap:
            gap_size = raw_line_count - gap_start
            if gap_size > 3:
                pruned_parts.append(f"    # ... [{gap_size} lines pruned] ...")

        pruned_content = "\n".join(pruned_parts)
        pruned_tokens = count_tokens(pruned_content)

        return PrunedFile(
            file_path=rel_path,
            raw_content=raw_content,
            pruned_content=pruned_content,
            raw_lines=raw_line_count,
            pruned_lines=len(pruned_parts),
            raw_tokens=raw_tokens,
            pruned_tokens=pruned_tokens,
            kept_symbols=kept_symbols,
            removed_sections=removed_sections,
        )

    def _expand_context(
        self,
        lines: list[str],
        keep_lines: set[int],
        goal: str,
    ) -> set[int]:
        """
        Expand the kept line set with structural context:
        - Import lines that are actually used by kept code
        - Class/function definition lines above kept methods
        - Lines containing goal-related keywords
        """
        expanded = set(keep_lines)
        goal_terms = set(goal.lower().split())
        goal_terms.discard("focus")
        goal_terms.discard("on:")

        for i, line in enumerate(lines):
            # Keep lines with goal-relevant keywords
            line_lower = line.lower().strip()
            if any(term in line_lower for term in goal_terms if len(term) > 3):
                expanded.add(i)
                # Keep 2 lines of surrounding context
                for offset in range(-2, 3):
                    if 0 <= i + offset < len(lines):
                        expanded.add(i + offset)

        # Keep import lines that reference symbols we're keeping
        kept_names: set[str] = set()
        for i in keep_lines:
            if i < len(lines):
                for word in re.findall(r'\b[A-Za-z_]\w+\b', lines[i]):
                    kept_names.add(word)

        for i, line in enumerate(lines):
            if IMPORT_PATTERN.match(line):
                # Keep import if it references any kept symbol
                import_names = set(re.findall(r'\b[A-Za-z_]\w+\b', line))
                if import_names & kept_names:
                    expanded.add(i)

        return expanded

    def _fallback_file_search(self, goal: str) -> list[SkeletonEntry]:
        """If skeleton search returns nothing, do a broader search."""
        # Search with individual words instead of the full phrase
        terms = goal.lower().split()
        all_matches: list[SkeletonEntry] = []
        seen: set[str] = set()

        for term in terms:
            if len(term) < 3:
                continue
            matches = self.skeleton.search(term, top_k=5)
            for m in matches:
                key = f"{m.file_path}:{m.name}"
                if key not in seen:
                    seen.add(key)
                    all_matches.append(m)

        return all_matches[:20]

    def _compute_stats(self, pruned_files: list[PrunedFile]) -> PruneStats:
        """Aggregate statistics across all pruned files."""
        stats = PruneStats()
        for pf in pruned_files:
            stats.total_raw_tokens += pf.raw_tokens
            stats.total_pruned_tokens += pf.pruned_tokens
            stats.total_raw_lines += pf.raw_lines
            stats.total_pruned_lines += pf.pruned_lines
            stats.files_processed += 1
            stats.symbols_matched += len(pf.kept_symbols)

        if stats.total_raw_tokens > 0:
            stats.compression_ratio = stats.total_raw_tokens / max(stats.total_pruned_tokens, 1)
            stats.token_savings_pct = (
                (stats.total_raw_tokens - stats.total_pruned_tokens) / stats.total_raw_tokens
            ) * 100

        return stats
