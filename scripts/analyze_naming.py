#!/usr/bin/env python3
"""Analyze all function names in biMEMO codebase for naming convention compliance."""

import ast
import re
from pathlib import Path


PROJECT_ROOT = Path("/home/recoder/Projects/biMEMO")
BACKEND_DIR = PROJECT_ROOT / "app" / "backend"
FRONTEND_DIR = PROJECT_ROOT / "app" / "frontend" / "src"

def is_snake_case(name: str) -> bool:
    if name.startswith("__") and name.endswith("__"):
        return True
    return bool(re.match(r"^_?[a-z][a-z0-9_]*$", name))

def is_camel_case(name: str) -> bool:
    clean = name.lstrip("_#")
    return bool(clean) and bool(re.match(r"^[a-zA-Z][a-zA-Z0-9]*$", clean))

def extract_python_functions(filepath: Path) -> list[tuple[int, str]]:
    functions = []
    try:
        source = filepath.read_text(encoding="utf-8", errors="replace")
        tree = ast.parse(source, filename=str(filepath))
    except (SyntaxError, UnicodeDecodeError):
        return functions
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            functions.append((node.lineno, node.name))
    return functions

def extract_typescript_functions(filepath: Path) -> list[tuple[int, str]]:
    functions = []
    try:
        content = filepath.read_text(encoding="utf-8", errors="replace")
    except (UnicodeDecodeError, PermissionError):
        return functions
    lines = content.split("\n")
    for i, line in enumerate(lines, 1):
        stripped = line.strip()
        if stripped.startswith("//") or stripped.startswith("*"):
            continue
        # function name(...) or async function name(...)
        m = re.match(r"(?:export\s+)?(?:async\s+)?function\s+([A-Za-z_$][A-Za-z0-9_$]*)", stripped)
        if m:
            functions.append((i, m.group(1)))
            continue
        # const name = (...) => or const name = async (...) =>
        m = re.match(r"(?:export\s+)?(?:const|let|var)\s+([A-Za-z_$][A-Za-z0-9_$]*)\s*=.*=>", stripped)
        if m:
            functions.append((i, m.group(1)))
            continue
        # name(...) { (method)
        m = re.match(r"(?:async\s+)?([A-Za-z_$][A-Za-z0-9_$]*)\s*\(.*\)\s*[:{]", stripped)
        if m:
            functions.append((i, m.group(1)))
            continue
        # class ClassName {
        m = re.match(r"(?:export\s+)?(?:default\s+)?class\s+([A-Za-z_$][A-Za-z0-9_$]*)", stripped)
        if m:
            functions.append((i, m.group(1)))
            continue
    return functions

def analyze_py(root: Path) -> list[dict]:
    results = []
    for filepath in sorted(root.rglob("*.py")):
        if "__pycache__" in filepath.parts or "node_modules" in filepath.parts:
            continue
        rel_path = filepath.relative_to(PROJECT_ROOT)
        for lineno, name in extract_python_functions(filepath):
            is_ok = is_snake_case(name)
            results.append({"file": str(rel_path), "line": lineno, "name": name, "language": "python", "ok": is_ok, "rule": "snake_case"})
    return results

def analyze_ts(root: Path) -> list[dict]:
    results = []
    for ext in ("*.ts", "*.tsx", "*.js", "*.jsx"):
        for filepath in sorted(root.rglob(ext)):
            if "node_modules" in filepath.parts or "dist" in filepath.parts:
                continue
            rel_path = filepath.relative_to(PROJECT_ROOT)
            for lineno, name in extract_typescript_functions(filepath):
                is_ok = is_camel_case(name)
                results.append({"file": str(rel_path), "line": lineno, "name": name, "language": "typescript", "ok": is_ok, "rule": "camelCase"})
    return results

def main():
    print("Analyzing Python files...")
    py_results = analyze_py(BACKEND_DIR)
    print(f"  Found {len(py_results)} Python functions")

    print("Analyzing TypeScript files...")
    ts_results = analyze_ts(FRONTEND_DIR)
    print(f"  Found {len(ts_results)} TypeScript functions")

    all_results = py_results + ts_results
    total = len(all_results)
    violations = [r for r in all_results if not r["ok"]]
    ok_count = total - len(violations)

    print(f"\n{'='*60}")
    print(f"TOTAL: {total} functions found")
    print(f"OK: {ok_count} ({ok_count/total*100:.1f}%)")
    print(f"VIOLATIONS: {len(violations)} ({len(violations)/total*100:.1f}%)")
    print(f"{'='*60}")

    # Write violations report
    with open(PROJECT_ROOT / "sandbox" / "naming_violations.md", "w", encoding="utf-8") as f:
        f.write("# Naming Convention Violations\n\n")
        f.write(f"**Total functions analyzed:** {total}\n")
        f.write(f"**Compliant:** {ok_count} ({ok_count/total*100:.1f}%)\n")
        f.write(f"**Violations:** {len(violations)} ({len(violations)/total*100:.1f}%)\n\n")
        if not violations:
            f.write("No violations found!\n")
        else:
            f.write("## Violations\n\n")
            f.write("| File | Line | Function Name | Language | Rule |\n")
            f.write("|------|------|---------------|----------|------|\n")
            for v in violations:
                f.write(f"| {v['file']} | {v['line']} | `{v['name']}` | {v['language']} | {v['rule']} |\n")

    print("\nReport: sandbox/naming_violations.md")

if __name__ == "__main__":
    main()
