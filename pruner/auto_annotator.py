"""
Auto Annotator — Dual-Layer File Semantic Annotations
======================================================
Generates 1-sentence file descriptions from:
  - Skeletal symbols (classes, functions, enums)
  - Folder-level dependency context (imports_from, imported_by)

Two prompting scenarios:
  - Scenario A: Symbol-rich files → describe internal logic + architectural role
  - Scenario B: Zero-symbol files → infer role from filename + folder position

Batching strategy:
  - Groups files into batches of 8
  - Sends all 8 in ONE Groq call → gets JSON with 8 annotations
  - If batch fails (bad JSON / truncation) → retries each file individually
  - ~54 calls for 432 files instead of 432 calls → ~10x faster

Uses file-level hash invalidation — only regenerates files whose
symbols or folder dependencies actually changed.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from typing import Optional, Dict, List

BATCH_SIZE = 8  # files per Groq call

# ── System Prompt ────────────────────────────────────────────────────

_SYSTEM_PROMPT = (
    "You are a senior software architect. Your job is to annotate source files "
    "with exactly one sentence each. Focus on internal logic and dependency graph role. "
    "Do not use filler words like 'This file is' or 'It seems to'. "
    "Start directly with the action or purpose. "
    "Always respond with valid JSON only — no markdown, no code fences, no explanation."
)

# ── Batch prompt (8 files → JSON response) ───────────────────────────

_BATCH_USER = """Annotate each of the following files with exactly one sentence.
Respond ONLY with a JSON object where each key is the file path and the value is the annotation.

{file_blocks}

Return JSON only. Example format:
{{
  "path/to/file.dart": "One sentence annotation here",
  "path/to/other.js": "One sentence annotation here"
}}"""

# ── Single-file fallback prompts ─────────────────────────────────────

_SCENARIO_A_USER = """File: {file_path}
Internal APIs: {symbols}
Imports from: {imports_from}
Imported by: {imported_by}

One sentence describing what this file does internally and its architectural role:"""

_SCENARIO_B_USER = """File: {file_path}
Filename: {filename}
Folder: {folder}
Imports from: {imports_from}
Imported by: {imported_by}

This file has no internal symbols (likely config, setup, or glue code).
Based on the filename and its location, infer its purpose. One sentence:"""


class AutoAnnotator:
    """
    Batch-optimised dual-scenario file annotator.

    Batches 8 files per Groq call. Falls back to individual calls if
    batch JSON parsing fails. File-level hash invalidation avoids
    re-annotating unchanged files on subsequent scans.
    """

    def __init__(
        self,
        cache_path: str,
        groq_api_key: str = "",
        groq_model: str = "llama-3.1-8b-instant",
    ):
        self.cache_path = cache_path
        self.groq_api_key = groq_api_key or os.environ.get("GROQ_API_KEY", "") or self._load_env_key()
        self.groq_model = groq_model
        self._cache: Dict[str, str] = {}
        self._file_hashes: Dict[str, str] = {}
        self._dirty = False
        self._load_cache()

    @staticmethod
    def _load_env_key() -> str:
        for env_path in [".env", "../.env", os.path.join(os.path.dirname(__file__), "..", ".env")]:
            try:
                with open(env_path, "r") as f:
                    for line in f:
                        line = line.strip()
                        if line.startswith("GROQ_API_KEY="):
                            return line.split("=", 1)[1].strip().strip('"').strip("'")
            except OSError:
                continue
        return ""

    # ── Cache ────────────────────────────────────────────────────────

    def _load_cache(self):
        """Load annotations + hashes. Handles old flat format gracefully."""
        if os.path.exists(self.cache_path):
            try:
                with open(self.cache_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if "annotations" in data:
                    self._cache = data.get("annotations", {})
                    self._file_hashes = data.get("file_hashes", {})
                else:
                    # Migrate old flat format
                    self._cache = data
                    self._file_hashes = {}
                    print("[auto_annotator] Migrated old flat cache — hashes recomputed on next scan")
            except (json.JSONDecodeError, OSError):
                self._cache = {}
                self._file_hashes = {}

    def _save_cache(self):
        if not self._dirty:
            return
        try:
            os.makedirs(os.path.dirname(self.cache_path), exist_ok=True)
            with open(self.cache_path, "w", encoding="utf-8") as f:
                json.dump({"annotations": self._cache, "file_hashes": self._file_hashes},
                          f, indent=2, ensure_ascii=False)
            self._dirty = False
        except OSError as e:
            print(f"[auto_annotator] Could not save cache: {e}")

    def _compute_file_hash(self, symbols: str, imports_from: List[str], imported_by: List[str]) -> str:
        content = f"{symbols}|{','.join(sorted(imports_from))}|{','.join(sorted(imported_by))}"
        return hashlib.md5(content.encode()).hexdigest()[:12]

    def _should_regenerate(self, file_path: str, symbols: str,
                           imports_from: List[str], imported_by: List[str]) -> bool:
        new_hash = self._compute_file_hash(symbols, imports_from, imported_by)
        needs = (file_path not in self._cache) or (self._file_hashes.get(file_path) != new_hash)
        if needs:
            self._file_hashes[file_path] = new_hash
        return needs

    def get_annotation(self, file_path: str) -> Optional[str]:
        return self._cache.get(file_path)

    def all_annotations(self) -> Dict[str, str]:
        return dict(self._cache)

    # ── Barrel export guard ──────────────────────────────────────────

    def _is_generic_export(self, annotation: str, folder: str) -> Optional[str]:
        """Return static replacement if annotation is a generic barrel export."""
        patterns = [r"exports? .* from", r"aggregates? exports?",
                    r"re-exports?", r"barrel export", r"index file"]
        if any(re.search(p, annotation.lower()) for p in patterns):
            return f"Aggregator for {folder} exports"
        return None

    # ── Batch prompting ──────────────────────────────────────────────

    def _build_file_block(self, spec: Dict, folders_data: Dict) -> str:
        """Build a single file entry for the batch prompt."""
        file_path = spec["file_path"]
        symbols = spec.get("symbols", "")
        filename = os.path.basename(file_path)
        folder = os.path.dirname(file_path) if "/" in file_path or "\\" in file_path else "(root)"
        folder = folder.replace("\\", "/")

        folder_entry = folders_data.get(folder, {})
        imports_from = ", ".join(folder_entry.get("imports_from", [])[:6]) or "(none)"
        imported_by  = ", ".join(folder_entry.get("imported_by",  [])[:6]) or "(none)"

        if symbols.strip():
            return (f'File: {file_path}\n'
                    f'Internal APIs: {symbols[:300]}\n'
                    f'Imports from: {imports_from}\n'
                    f'Imported by: {imported_by}')
        else:
            return (f'File: {file_path}\n'
                    f'Filename: {filename} (no internal symbols — likely config/glue)\n'
                    f'Folder: {folder}\n'
                    f'Imports from: {imports_from}\n'
                    f'Imported by: {imported_by}')

    def _extract_json(self, raw: str) -> Optional[Dict[str, str]]:
        """Extract JSON dict from Groq response, stripping markdown fences."""
        raw = raw.strip()
        # Strip markdown code fences
        raw = re.sub(r'^```(?:json)?\s*', '', raw, flags=re.MULTILINE)
        raw = re.sub(r'\s*```\s*$', '', raw, flags=re.MULTILINE)
        raw = raw.strip()

        # Try direct parse
        try:
            data = json.loads(raw)
            if isinstance(data, dict):
                return data
        except json.JSONDecodeError:
            pass

        # Try to find JSON object in response
        match = re.search(r'\{[\s\S]*\}', raw)
        if match:
            try:
                data = json.loads(match.group())
                if isinstance(data, dict):
                    return data
            except json.JSONDecodeError:
                pass

        return None

    def _call_groq_batch(self, file_blocks: str, expected_keys: List[str]) -> Optional[Dict[str, str]]:
        """Send batch of 8 files in one Groq call. Returns dict or None."""
        prompt = _BATCH_USER.format(file_blocks=file_blocks)
        try:
            import requests
            resp = requests.post(
                "https://api.groq.com/openai/v1/chat/completions",
                json={
                    "model": self.groq_model,
                    "messages": [
                        {"role": "system", "content": _SYSTEM_PROMPT},
                        {"role": "user",   "content": prompt},
                    ],
                    "temperature": 0.1,
                    "max_tokens": BATCH_SIZE * 80,  # ~80 tokens per annotation
                },
                headers={
                    "Authorization": f"Bearer {self.groq_api_key}",
                    "Content-Type": "application/json",
                },
                timeout=30,
            )
            resp.raise_for_status()
            raw = resp.json()["choices"][0]["message"]["content"]
            return self._extract_json(raw)
        except Exception as e:
            print(f"[auto_annotator] Batch Groq error: {e}")
            return None

    def _call_groq_single(self, spec: Dict, folders_data: Dict) -> Optional[str]:
        """Fallback: annotate one file individually."""
        file_path = spec["file_path"]
        symbols   = spec.get("symbols", "")
        filename  = os.path.basename(file_path)
        folder    = os.path.dirname(file_path).replace("\\", "/") if "/" in file_path or "\\" in file_path else "(root)"

        folder_entry = folders_data.get(folder, {})
        imports_from = ", ".join(folder_entry.get("imports_from", [])[:8]) or "(none)"
        imported_by  = ", ".join(folder_entry.get("imported_by",  [])[:8]) or "(none)"

        if symbols.strip():
            prompt = _SCENARIO_A_USER.format(
                file_path=file_path, symbols=symbols[:500],
                imports_from=imports_from, imported_by=imported_by,
            )
        else:
            prompt = _SCENARIO_B_USER.format(
                file_path=file_path, filename=filename, folder=folder,
                imports_from=imports_from, imported_by=imported_by,
            )

        try:
            import requests
            resp = requests.post(
                "https://api.groq.com/openai/v1/chat/completions",
                json={
                    "model": self.groq_model,
                    "messages": [
                        {"role": "system", "content": _SYSTEM_PROMPT},
                        {"role": "user",   "content": prompt},
                    ],
                    "temperature": 0.1,
                    "max_tokens": 120,
                },
                headers={
                    "Authorization": f"Bearer {self.groq_api_key}",
                    "Content-Type": "application/json",
                },
                timeout=15,
            )
            resp.raise_for_status()
            return resp.json()["choices"][0]["message"]["content"].strip()
        except Exception as e:
            print(f"[auto_annotator] Single Groq error for {file_path}: {e}")
            return None

    # ── Main entry point ─────────────────────────────────────────────

    def lazy_annotate_batch(
        self,
        file_specs: List[Dict],
        folder_map: Optional[Dict] = None,
    ) -> Dict[str, str]:
        """
        Annotate files using batch-of-8 Groq calls.

        Flow:
          1. Filter out files that don't need regeneration (hash match)
          2. Group remaining into batches of 8
          3. For each batch: one Groq call → JSON with 8 annotations
          4. If JSON parse fails → retry each of the 8 individually
          5. Save after every batch (partial failure safe)
        """
        folder_map   = folder_map or {}
        folders_data = folder_map.get("folders", {})
        new_annotations: Dict[str, str] = {}

        # Step 1: filter to only files needing regeneration
        to_annotate = []
        for spec in file_specs:
            fp = spec["file_path"]
            symbols = spec.get("symbols", "")
            folder  = os.path.dirname(fp).replace("\\", "/") if "/" in fp or "\\" in fp else "(root)"
            fe      = folders_data.get(folder, {})
            if self._should_regenerate(fp, symbols, fe.get("imports_from", []), fe.get("imported_by", [])):
                to_annotate.append(spec)

        if not to_annotate:
            return {}

        total   = len(to_annotate)
        batches = [to_annotate[i:i + BATCH_SIZE] for i in range(0, total, BATCH_SIZE)]
        print(f"[auto_annotator] {total} files to annotate → {len(batches)} batches of {BATCH_SIZE}")

        for batch_num, batch in enumerate(batches, 1):
            # Build combined prompt block
            blocks = "\n\n".join(
                self._build_file_block(spec, folders_data) for spec in batch
            )
            expected_keys = [s["file_path"] for s in batch]

            # Try batch call
            result = self._call_groq_batch(blocks, expected_keys)

            if result and isinstance(result, dict) and len(result) > 0:
                # Normalise all keys to forward slashes once for fast lookup
                normalised_result = {k.replace("\\", "/"): v for k, v in result.items()}

                # Batch succeeded
                saved = 0
                for spec in batch:
                    fp     = spec["file_path"]
                    folder = os.path.dirname(fp).replace("\\", "/") if "/" in fp or "\\" in fp else "(root)"
                    fp_norm = fp.replace("\\", "/")
                    filename = os.path.basename(fp_norm)

                    # 1. Exact match (normalised)
                    annotation = normalised_result.get(fp_norm)

                    # 2. Suffix match — catches lib/ prefix variations
                    if not annotation:
                        annotation = next(
                            (v for k, v in normalised_result.items()
                             if k.endswith(fp_norm) or fp_norm.endswith(k)),
                            None
                        )

                    # 3. Filename-only match — last resort
                    if not annotation:
                        annotation = next(
                            (v for k, v in normalised_result.items()
                             if os.path.basename(k) == filename),
                            None
                        )

                    if annotation and annotation.strip():
                        annotation = annotation.strip()
                        replacement = self._is_generic_export(annotation, folder)
                        if replacement:
                            annotation = replacement
                        self._cache[fp] = annotation
                        new_annotations[fp] = annotation
                        self._dirty = True
                        saved += 1

                self._save_cache()
                print(f"[auto_annotator] Batch {batch_num}/{len(batches)} ✓ — {saved}/{len(batch)} saved")

            else:
                # Batch failed — retry individually
                print(f"[auto_annotator] Batch {batch_num} failed (bad JSON/truncation) — retrying {len(batch)} files individually")
                for spec in batch:
                    fp     = spec["file_path"]
                    folder = os.path.dirname(fp).replace("\\", "/") if "/" in fp or "\\" in fp else "(root)"
                    annotation = self._call_groq_single(spec, folders_data)
                    if annotation:
                        replacement = self._is_generic_export(annotation, folder)
                        if replacement:
                            annotation = replacement
                        self._cache[fp] = annotation
                        new_annotations[fp] = annotation
                        self._dirty = True
                        print(f"[auto_annotator]   ✓ {fp}: {annotation[:70]}")
                    else:
                        print(f"[auto_annotator]   ✗ {fp}: skipped")

                self._save_cache()

        return new_annotations

    # ── Utility ─────────────────────────────────────────────────────

    @staticmethod
    def build_file_data_context(file_path: str, entries: list) -> str:
        """Build compact symbol string from SkeletonEntry list."""
        parts = []
        for entry in entries:
            if entry.data_context:
                parts.append(f"{entry.name}({entry.data_context})")
            elif entry.kind.value in ("class", "enum", "interface"):
                parts.append(entry.name)
        funcs = [e.name for e in entries if e.kind.value in ("function", "method")][:5]
        parts.extend(funcs)
        return ", ".join(parts[:20])
