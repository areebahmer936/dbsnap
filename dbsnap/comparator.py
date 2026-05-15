"""Comparison engine for dbsnap snapshots."""

import difflib
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class DiffItem:
    """A single item in a comparison result."""
    name: str
    status: str
    left_hash: Optional[str] = None
    right_hash: Optional[str] = None
    left_def: Optional[str] = None
    right_def: Optional[str] = None
    unified_diff: list = field(default_factory=list)
    left_only_fields: list = field(default_factory=list)
    right_only_fields: list = field(default_factory=list)
    modified_fields: list = field(default_factory=list)


def compare_snapshots(left: dict, right: dict, category: str) -> list:
    """Compare a category between two snapshots using hash-first strategy.
    
    Args:
        left: Left snapshot dict
        right: Right snapshot dict
        category: Category to compare ('tables', 'procedures', 'functions', 'triggers')
        
    Returns:
        List of DiffItem objects
    """
    left_items = left.get(category, {})
    right_items = right.get(category, {})
    
    all_names = set(left_items.keys()) | set(right_items.keys())
    items = []
    
    for name in sorted(all_names):
        left_item = left_items.get(name)
        right_item = right_items.get(name)
        
        if left_item and not right_item:
            status = "only_in_left"
            left_hash = _get_hash(left_item)
            right_hash = None
            left_def = _get_definition(left_item, name)
            right_def = None
        elif right_item and not left_item:
            status = "only_in_right"
            left_hash = None
            right_hash = _get_hash(right_item)
            left_def = None
            right_def = _get_definition(right_item, name)
        else:
            left_hash = _get_hash(left_item)
            right_hash = _get_hash(right_item)
            left_def = _get_definition(left_item, name)
            right_def = _get_definition(right_item, name)
            
            if left_hash == right_hash:
                status = "identical"
            else:
                status = "modified"
        
        item = DiffItem(
            name=name,
            status=status,
            left_hash=left_hash,
            right_hash=right_hash,
            left_def=left_def,
            right_def=right_def,
        )
        items.append(item)
    
    return items


def compute_diffs(items: list) -> list:
    """Compute unified diffs for all modified items.
    
    Args:
        items: List of DiffItem objects
        
    Returns:
        Updated list with unified_diff populated
    """
    for item in items:
        if item.status == "modified" and item.left_def and item.right_def:
            left_lines = item.left_def.splitlines(keepends=True)
            right_lines = item.right_def.splitlines(keepends=True)
            
            diff = list(difflib.unified_diff(
                left_lines,
                right_lines,
                fromfile="left",
                tofile="right",
                lineterm="",
            ))
            item.unified_diff = diff
        elif item.status == "only_in_left":
            left_lines = item.left_def.splitlines(keepends=True) if item.left_def else []
            diff = [f"-{line}" for line in left_lines]
            item.unified_diff = diff
        elif item.status == "only_in_right":
            right_lines = item.right_def.splitlines(keepends=True) if item.right_def else []
            diff = [f"+{line}" for line in right_lines]
            item.unified_diff = diff
    
    return items


def compare_all_categories(left: dict, right: dict, categories: list = None) -> dict:
    """Compare all categories between two snapshots.
    
    Args:
        left: Left snapshot dict
        right: Right snapshot dict
        categories: List of categories to compare (None for all)
        
    Returns:
        Dict mapping category to list of DiffItem objects
    """
    if categories is None:
        categories = ["tables", "procedures", "functions", "triggers"]
    
    result = {}
    
    for category in categories:
        items = compare_snapshots(left, right, category)
        items = compute_diffs(items)
        result[category] = items
    
    return result


def get_summary(comparison: dict) -> dict:
    """Get summary counts from a comparison result.
    
    Args:
        comparison: Dict from compare_all_categories
        
    Returns:
        Dict with counts per category per status
    """
    summary = {}
    
    for category, items in comparison.items():
        counts = {
            "modified": 0,
            "only_in_left": 0,
            "only_in_right": 0,
            "identical": 0,
        }
        
        for item in items:
            if item.status in counts:
                counts[item.status] += 1
        
        summary[category] = counts
    
    return summary


def filter_items(items: list, exclude_identical: bool = False) -> list:
    """Filter comparison items based on options.
    
    Args:
        items: List of DiffItem objects
        exclude_identical: Whether to exclude identical items
        
    Returns:
        Filtered list
    """
    if exclude_identical:
        return [item for item in items if item.status != "identical"]
    return items


def _get_hash(item: dict) -> str:
    """Get the hash from a snapshot item."""
    return item.get("hash") or item.get("schema_hash") or ""


def _get_definition(item: dict, name: str) -> str:
    """Get a human-readable definition from a snapshot item."""
    if "definition" in item:
        return item["definition"]
    
    if "columns" in item:
        lines = [f"TABLE [{name}]"]
        lines.append("")
        lines.append("COLUMNS:")
        for col in item.get("columns", []):
            nullable = "NULL" if col.get("nullable") else "NOT NULL"
            identity = " IDENTITY" if col.get("identity") else ""
            default = f" DEFAULT {col['default']}" if col.get("default") else ""
            lines.append(f"  [{col['name']}] {col['type']}{identity} {nullable}{default}")
        
        indexes = item.get("indexes", [])
        if indexes:
            lines.append("")
            lines.append("INDEXES:")
            for idx in indexes:
                unique = "UNIQUE " if idx.get("unique") else ""
                keys = ", ".join(idx.get("keys", []))
                lines.append(f"  {unique}{idx['type']}: {idx['name']} ({keys})")
                if idx.get("included"):
                    lines.append(f"    INCLUDES: {', '.join(idx['included'])}")
        
        fks = item.get("foreign_keys", [])
        if fks:
            lines.append("")
            lines.append("FOREIGN KEYS:")
            for fk in fks:
                lines.append(f"  {fk['name']}: [{fk['from']}] -> {fk['to_table']}.[{fk['to_column']}]")
        
        return "\n".join(lines)
    
    return ""
