"""Code search — grep-based text search across workspace files.

Searches for text or regex patterns in files within the workspace
directory. Returns matching files with line numbers and context.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from backend.workers.cmd_worker import execute_command


logger = logging.getLogger(__name__)


@dataclass
class SearchMatch:
    """One match from code search."""

    file: str
    line: int
    text: str


async def code_search(
    pattern: str,
    cwd: str | None = None,
    file_pattern: str = "**/*",
    max_results: int = 50,
    context_lines: int = 2,
    use_regex: bool = False,
) -> dict[str, Any]:
    """Search for text/regex patterns across workspace files.

    Uses grep under the hood.

    Args:
        pattern: Text or regex pattern to search for.
        cwd: Working directory (workspace root).
        file_pattern: Glob pattern for files to search (default: all).
        max_results: Maximum number of matches to return.
        context_lines: Number of context lines before/after match.
        use_regex: If True, treat pattern as regex.

    Returns:
        Dict with matches, file count, and total matches.
    """
    if not pattern:
        return {"error": "pattern is required", "matches": [], "count": 0}

    # Build grep command
    import shlex
    grep_args = [
        "grep",
        "-E" if use_regex else "-F",
        "-n",
        "-r",
        f"-C{context_lines}",
        pattern,
        ".",  # Search current directory
    ]

    # Add file pattern filter if not default
    if file_pattern and file_pattern != "**/*":
        grep_args.insert(len(grep_args) - 2, f"--include={file_pattern}")

    # Use shlex.join for proper shell escaping (Python 3.8+)
    command = shlex.join(grep_args)

    result = await execute_command(
        command=command,
        cwd=cwd,
        timeout=30.0,
    )

    if result.error and result.exit_code not in (0, 1):
        # exit_code 1 = no matches (normal), other = error
        return {"error": result.error, "matches": [], "count": 0}

    # Parse grep output
    matches: list[SearchMatch] = []
    files_seen: set[str] = set()
    current_file = ""

    output_text = result.stdout or ""
    for line in output_text.split("\n"):
        if not line:
            continue

        # grep output format: filename:line_number:text
        # With -C context, we get "---" separators and context lines
        if line == "--":
            continue

        # Try to parse as filename:line:text
        parts = line.split(":", 2)
        if len(parts) == 3:
            try:
                filename = parts[0]
                line_num = int(parts[1])
                text = parts[2]
                current_file = filename
                files_seen.add(filename)
                matches.append(SearchMatch(
                    file=filename,
                    line=line_num,
                    text=text.strip(),
                ))
            except ValueError:
                # Context line without line number
                if current_file:
                    matches.append(SearchMatch(
                        file=current_file,
                        line=0,
                        text=line.strip(),
                    ))

    # Limit results
    matches = matches[:max_results]

    return {
        "matches": [
            {"file": m.file, "line": m.line, "text": m.text}
            for m in matches
        ],
        "count": len(matches),
        "files": sorted(files_seen),
        "file_count": len(files_seen),
    }
