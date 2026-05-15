"""HTML report generation for dbsnap."""

import os
from jinja2 import Environment, FileSystemLoader, PackageLoader

from .comparator import get_summary


def generate_report(
    comparison: dict,
    output_path: str,
    left_name: str,
    right_name: str,
    left_meta: dict = None,
    right_meta: dict = None,
) -> str:
    """Generate a self-contained HTML diff report.
    
    Args:
        comparison: Dict from compare_all_categories
        output_path: Path to write the HTML file
        left_name: Display name for left snapshot
        right_name: Display name for right snapshot
        left_meta: Metadata for left snapshot
        right_meta: Metadata for right snapshot
        
    Returns:
        Path to the generated HTML file
    """
    left_meta = left_meta or {"server": "unknown", "database": "unknown", "created_at": "unknown"}
    right_meta = right_meta or {"server": "unknown", "database": "unknown", "created_at": "unknown"}
    
    summary = get_summary(comparison)
    
    diff_data = {}
    for category, items in comparison.items():
        diff_data[category] = [
            {
                "name": item.name,
                "status": item.status,
                "left_hash": item.left_hash,
                "right_hash": item.right_hash,
                "unified_diff": item.unified_diff,
                "left_def": item.left_def or "",
                "right_def": item.right_def or "",
            }
            for item in items
        ]
    
    diff_data["summary"] = summary
    
    template_dir = os.path.join(os.path.dirname(__file__), "templates")
    env = Environment(loader=FileSystemLoader(template_dir))
    template = env.get_template("report.html")
    
    html = template.render(
        left_name=left_name,
        right_name=right_name,
        left_meta=left_meta,
        right_meta=right_meta,
        summary=summary,
        diff_data=diff_data,
    )
    
    with open(output_path, 'w', encoding='utf-8') as f:
        f.write(html)
    
    return output_path
