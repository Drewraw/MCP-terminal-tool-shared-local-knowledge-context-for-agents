"""
Mindmap Generator — Project Architecture Visualization
=======================================================
Analyzes the codebase structure and generates a hierarchical
mindmap showing modules, classes, functions, and dependencies.

The mindmap helps developers understand:
- What modules exist and what they contain
- How modules depend on each other
- High-level architecture at a glance
"""

import os
import re
from pathlib import Path
from typing import Optional
from collections import defaultdict

try:
    from .models import SkeletalIndex, SkeletonEntry, SymbolKind
except ImportError:
    from indexer.models import SkeletalIndex, SkeletonEntry, SymbolKind


class MindmapNode:
    """A node in the mindmap tree."""

    def __init__(self, name: str, node_type: str, file_path: str = "", line_number: int = 0):
        self.name = name
        self.node_type = node_type  # "module", "class", "function", "interface", etc.
        self.file_path = file_path
        self.line_number = line_number
        self.children: list[MindmapNode] = []
        self.imports: list[str] = []  # Other modules/classes this depends on

    def to_dict(self):
        """Convert to JSON-serializable dict."""
        return {
            "name": self.name,
            "type": self.node_type,
            "file_path": self.file_path,
            "line": self.line_number,
            "children": [child.to_dict() for child in self.children],
            "imports": self.imports,
        }

    def add_child(self, child: "MindmapNode"):
        """Add a child node."""
        self.children.append(child)


class MindmapGenerator:
    """Generates a hierarchical mindmap from a skeletal index."""

    def __init__(self, skeleton: SkeletalIndex, root_path: str):
        self.skeleton = skeleton
        self.root_path = root_path

    def generate(self) -> MindmapNode:
        """
        Generate the complete mindmap for the project.
        
        Returns a tree structure:
        PROJECT_ROOT
        ├── Module 1
        │   ├── Class A
        │   │   └── method1(), method2()
        │   └── Function B
        ├── Module 2
        │   └── Function C
        └── ...
        """
        root = MindmapNode("Project", "project", self.root_path)

        # Group entries by file/module
        modules: dict[str, dict] = defaultdict(lambda: {"classes": [], "functions": [], "interfaces": []})

        for entry in self.skeleton.entries:
            module_key = entry.file_path
            if entry.kind == SymbolKind.CLASS:
                modules[module_key]["classes"].append(entry)
            elif entry.kind == SymbolKind.FUNCTION:
                modules[module_key]["functions"].append(entry)
            elif entry.kind == SymbolKind.INTERFACE:
                modules[module_key]["interfaces"].append(entry)

        # Build module nodes
        for file_path in sorted(modules.keys()):
            module_node = self._create_module_node(file_path, modules[file_path])
            root.add_child(module_node)

        # Extract and add dependency information
        self._extract_dependencies(root)

        return root

    def _create_module_node(self, file_path: str, symbols: dict) -> MindmapNode:
        """Create a module node with its classes and functions as children."""
        # Extract readable module name
        module_name = Path(file_path).stem
        abs_path = os.path.join(self.root_path, file_path)
        
        module_node = MindmapNode(
            name=module_name,
            node_type="module",
            file_path=file_path,
        )

        # Add classes with their methods
        for class_entry in symbols["classes"]:
            class_node = MindmapNode(
                name=class_entry.name,
                node_type="class",
                file_path=file_path,
                line_number=class_entry.line_start,
            )

            # Find methods for this class
            for entry in self.skeleton.entries:
                if (entry.file_path == file_path and
                    entry.kind == SymbolKind.METHOD and
                    entry.parent == class_entry.name):
                    method_node = MindmapNode(
                        name=entry.name + "()",
                        node_type="method",
                        file_path=file_path,
                        line_number=entry.line_start,
                    )
                    class_node.add_child(method_node)

            module_node.add_child(class_node)

        # Add standalone functions
        for func_entry in symbols["functions"]:
            func_node = MindmapNode(
                name=func_entry.name + "()",
                node_type="function",
                file_path=file_path,
                line_number=func_entry.line_start,
            )
            module_node.add_child(func_node)

        # Add interfaces
        for intf_entry in symbols["interfaces"]:
            intf_node = MindmapNode(
                name=intf_entry.name,
                node_type="interface",
                file_path=file_path,
                line_number=intf_entry.line_start,
            )
            module_node.add_child(intf_node)

        return module_node

    def _extract_dependencies(self, root: MindmapNode):
        """Extract import dependencies and add them to module nodes."""
        for child in root.children:  # For each module
            if child.node_type == "module":
                imports = self._parse_imports_from_file(child.file_path)
                child.imports = imports

    def _parse_imports_from_file(self, file_path: str) -> list[str]:
        """Extract all imports from a file."""
        abs_path = os.path.join(self.root_path, file_path)
        imports = []

        if not os.path.isfile(abs_path):
            return imports

        try:
            with open(abs_path, "r", encoding="utf-8", errors="replace") as f:
                lines = f.readlines()

            # Look for import statements
            for line in lines[:50]:  # Check first 50 lines (where imports usually are)
                # Python imports
                if re.match(r"^\s*(from|import)\s+", line):
                    match = re.match(r"^\s*(?:from|import)\s+([a-zA-Z0-9._-]+)", line)
                    if match:
                        module_name = match.group(1).split(".")[0]
                        if module_name not in imports:
                            imports.append(module_name)

                # JavaScript/TypeScript imports
                elif re.match(r"^\s*(?:import|require)\s*", line):
                    match = re.search(r"(?:from|require)\s+['\"]([^'\"]+)['\"]", line)
                    if match:
                        import_path = match.group(1)
                        # Clean up relative paths
                        if import_path.startswith("."):
                            module_name = Path(import_path).stem
                        else:
                            module_name = import_path.split("/")[0]
                        if module_name not in imports:
                            imports.append(module_name)

        except Exception:
            pass

        return imports


def generate_mindmap_summary(root: MindmapNode) -> dict:
    """
    Generate a text summary of the mindmap structure.
    
    Example output:
    ```
    PROJECT STRUCTURE
    ├── indexer/
    │   ├── SkeletalIndexer (class)
    │   │   ├── index_and_save()
    │   │   └── load()
    │   ├── SkeletonFileWatcher (class)
    │   └── Models
    ├── pruner/
    │   ├── PruningEngine (class)
    │   │   ├── prune()
    │   │   └── _prune_file()
    │   └── TokenCounter
    ...
    ```
    """
    summary = {
        "total_modules": len([c for c in root.children if c.node_type == "module"]),
        "total_classes": sum(
            len([gc for gc in c.children if gc.node_type == "class"])
            for c in root.children
            if c.node_type == "module"
        ),
        "total_functions": sum(
            len([gc for gc in c.children if gc.node_type == "function"])
            for c in root.children
            if c.node_type == "module"
        ),
        "modules": [
            {
                "name": child.name,
                "file_path": child.file_path,
                "classes": [c.name for c in child.children if c.node_type == "class"],
                "functions": [f.name for f in child.children if f.node_type == "function"],
                "interfaces": [i.name for i in child.children if i.node_type == "interface"],
                "imports": child.imports,
            }
            for child in root.children
            if child.node_type == "module"
        ],
    }
    return summary
