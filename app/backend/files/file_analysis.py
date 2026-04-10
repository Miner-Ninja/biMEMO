"""Stateless file analysis, parsing, and prompt block building.

Extracts structural hints, key points, and summary overviews from
file content. Builds prompt blocks for inline, preview, summarized,
and chunked recall modes. All functions are pure — they receive
dependencies as parameters and return results without side effects.
"""

from __future__ import annotations

import re
from collections import Counter
from pathlib import Path
from typing import TYPE_CHECKING


if TYPE_CHECKING:
    from backend.files.file_context_models import (
        FileChunk,
        FileContextAnalysis,
        FileSummary,
    )


# --- File kinds ---
PLAIN_TEXT_KIND = "plain_text"
CODE_KIND = "code"
STRUCTURED_KIND = "structured"
MARKUP_KIND = "markup"
UNKNOWN_KIND = "unknown"

# --- Content limits ---
MAX_PREVIEW_CHARS = 8_000
MAX_SUMMARY_PREVIEW_CHARS = 12_000
MAX_STRUCTURE_LINES = 12
MAX_KEY_POINT_LINES = 8
TOKEN_ESTIMATE_DIVISOR = 4
TOP_FIELD_COUNT = 6
TOP_REPEAT_COUNT = 5
MIN_PLAIN_KEY_POINT_LENGTH = 20

# --- Prompt labels ---
FILE_HEADER_TEMPLATE = "--- File: {file_name} ---"
FILE_FOOTER_TEMPLATE = "--- End of {file_name} ---"
FILE_SUMMARY_LABEL = "File summary:"
FILE_STRUCTURE_LABEL = "File structure:"
FILE_KEY_POINTS_LABEL = "Key points:"
FILE_WARNING_LABEL = "Warnings:"
FILE_CHUNKS_LABEL = "Relevant file chunks:"
TRUNCATION_WARNING = (
    "Only a preview or selected chunks are shown below. Do not assume the full file "
    "was loaded word-for-word unless the file mode is inline."
)
SUMMARY_WARNING = (
    "This file was summarized before prompt injection because the raw content was too "
    "large to include safely."
)

# --- File type hints ---
CODE_EXTENSIONS = {
    ".c",
    ".cc",
    ".cpp",
    ".cs",
    ".go",
    ".h",
    ".hpp",
    ".java",
    ".js",
    ".jsx",
    ".kt",
    ".php",
    ".py",
    ".rb",
    ".rs",
    ".scala",
    ".sh",
    ".sql",
    ".swift",
    ".ts",
    ".tsx",
}
STRUCTURED_EXTENSIONS = {
    ".csv",
    ".json",
    ".ndjson",
    ".toml",
    ".tsv",
    ".xml",
    ".yaml",
    ".yml",
}
MARKUP_EXTENSIONS = {
    ".html",
    ".md",
}

# --- Regex patterns ---
HEADING_RE = re.compile(r"^\s{0,3}(#{1,6}\s+.+)$")
CODE_ENTITY_RE = re.compile(
    r"^\s*(class|def|fn|function|interface|type|struct|enum|SELECT|INSERT|UPDATE|DELETE)\b",
    re.IGNORECASE,
)
QUERY_TOKEN_RE = re.compile(r"\w+", re.UNICODE)
STRUCTURED_FIELD_RE = re.compile(
    r'^\s*(?:"?([A-Za-z0-9_.-]+)"?\s*[:=]|<([A-Za-z0-9_.-]+)[^>]*>)'
)
CSV_SPLIT_RE = re.compile(r"\s*[,;\t]\s*")
CODE_IMPORT_RE = re.compile(r"^\s*(import|from)\b", re.IGNORECASE)
ASSIGNMENT_KEY_RE = re.compile(r"^\s*([A-Za-z0-9_.-]+)\s*=")
LOW_SIGNAL_QUERY_TOKENS = {
    "a",
    "about",
    "an",
    "and",
    "are",
    "for",
    "from",
    "how",
    "in",
    "is",
    "it",
    "of",
    "on",
    "or",
    "that",
    "the",
    "this",
    "to",
    "what",
    "which",
    "with",
}
EXACT_DETAIL_MARKERS = {
    "details",
    "evidence",
    "exact",
    "line",
    "lines",
    "quote",
    "quoted",
    "show",
}
EXACT_DETAIL_SPECIAL_CHARACTERS = ('"', "'", "`", "/", "\\", "_", "=", ":")
LONG_QUERY_TOKEN_LENGTH = 12
MIN_SEMANTIC_TOKEN_LENGTH = 3

# --- Processing modes (re-exported for convenience) ---
INLINE_MODE = "inline"
PREVIEW_MODE = "preview"
SUMMARIZED_MODE = "summarized"
CHUNKED_RECALL_MODE = "chunked_recall"


def estimate_tokens(content: str) -> int:
    """Estimate prompt tokens for one text block."""
    if not content:
        return 0
    return max(1, len(content) // TOKEN_ESTIMATE_DIVISOR)


def detect_file_kind(*, file_name: str, mime_type: str) -> str:
    """Infer a coarse file kind from extension and MIME type."""
    extension = Path(file_name).suffix.lower()
    normalized_mime_type = mime_type.strip().lower()
    if extension in CODE_EXTENSIONS or "javascript" in normalized_mime_type:
        return CODE_KIND
    if extension in STRUCTURED_EXTENSIONS or any(
        marker in normalized_mime_type
        for marker in ("json", "xml", "yaml", "csv", "toml")
    ):
        return STRUCTURED_KIND
    if extension in MARKUP_EXTENSIONS or any(
        marker in normalized_mime_type for marker in ("markdown", "html")
    ):
        return MARKUP_KIND
    if normalized_mime_type.startswith("text/"):
        return PLAIN_TEXT_KIND
    return UNKNOWN_KIND


def is_exact_detail_query(user_query: str) -> bool:
    """Return True when a file question asks for precise evidence."""
    lowered_query = user_query.casefold()
    query_tokens = QUERY_TOKEN_RE.findall(lowered_query)
    if any(token in EXACT_DETAIL_MARKERS for token in query_tokens):
        return True
    if any(character in user_query for character in EXACT_DETAIL_SPECIAL_CHARACTERS):
        return True
    if any(any(char.isdigit() for char in token) for token in query_tokens):
        return True
    return any(len(token) >= LONG_QUERY_TOKEN_LENGTH for token in query_tokens)


def semantic_query_tokens(text: str) -> set[str]:
    """Extract a compact semantic token set for query matching."""
    return {
        token
        for token in QUERY_TOKEN_RE.findall(text.casefold())
        if len(token) >= MIN_SEMANTIC_TOKEN_LENGTH
        and token not in LOW_SIGNAL_QUERY_TOKENS
    }


def chunk_overlap_score(
    text: str,
    query_tokens: set[str],
) -> tuple[int, int]:
    """Score one chunk for query overlap and compactness."""
    chunk_tokens = semantic_query_tokens(text)
    overlap = len(chunk_tokens & query_tokens)
    return overlap, -len(text)


def build_inline_block(file_name: str, content: str) -> str:
    """Return one raw inline block for a small file."""
    return "\n".join(
        [
            FILE_HEADER_TEMPLATE.format(file_name=file_name),
            content,
            FILE_FOOTER_TEMPLATE.format(file_name=file_name),
        ]
    )


def build_metadata_block(analysis: FileContextAnalysis) -> str:
    """Build a plain metadata header for one file."""
    return (
        f"Attached file: {analysis.file_name}\n"
        f"Kind: {analysis.file_kind}\n"
        f"Mode: {analysis.processing_mode}\n"
        f"MIME type: {analysis.mime_type}\n"
        f"Size: {analysis.size_bytes} bytes\n"
        f"Estimated tokens: {analysis.estimated_tokens}"
    )


def build_preview_block(
    analysis: FileContextAnalysis,
    content: str,
) -> str:
    """Return one bounded preview block for a medium file."""
    preview = content[:MAX_PREVIEW_CHARS].rstrip()
    return "\n".join(
        [
            build_metadata_block(analysis),
            TRUNCATION_WARNING,
            FILE_HEADER_TEMPLATE.format(file_name=analysis.file_name),
            preview + ("\n...[truncated]" if len(content) > len(preview) else ""),
            FILE_FOOTER_TEMPLATE.format(file_name=analysis.file_name),
        ]
    )


def build_summary_block(summary: FileSummary) -> str:
    """Render one structured file summary block."""
    lines = [FILE_SUMMARY_LABEL, summary.overview]
    if summary.structure:
        lines.append(FILE_STRUCTURE_LABEL)
        lines.extend(f"- {line}" for line in summary.structure)
    if summary.key_points:
        lines.append(FILE_KEY_POINTS_LABEL)
        lines.extend(f"- {line}" for line in summary.key_points)
    if summary.warnings:
        lines.append(FILE_WARNING_LABEL)
        lines.extend(f"- {warning}" for warning in summary.warnings)
    return "\n".join(lines)


def build_chunks_block(chunks: list[FileChunk]) -> str:
    """Render selected file chunks for exact-detail recall."""
    lines = [FILE_CHUNKS_LABEL, TRUNCATION_WARNING]
    for chunk in chunks:
        lines.append(
            f"- Chunk {chunk.chunk_index + 1} "
            f"(lines {chunk.line_start}-{chunk.line_end}, "
            f"~{chunk.estimated_tokens} tokens):"
        )
        lines.append(chunk.text)
    return "\n".join(lines)


def extract_structure_lines(*, file_kind: str, content: str) -> list[str]:
    """Extract light structural hints for one larger file."""
    lines = [line.rstrip() for line in content.splitlines() if line.strip()]
    if file_kind == MARKUP_KIND:
        return extract_markup_structure_lines(lines)
    if file_kind == CODE_KIND:
        return extract_code_structure_lines(lines)
    if file_kind == STRUCTURED_KIND:
        return extract_structured_structure_lines(lines, content)
    return lines[:MAX_STRUCTURE_LINES]


def extract_markup_structure_lines(lines: list[str]) -> list[str]:
    """Extract heading-like structure lines from markup content."""
    headings = [
        match.group(1).strip() for line in lines if (match := HEADING_RE.match(line))
    ]
    return headings[:MAX_STRUCTURE_LINES]


def extract_code_structure_lines(lines: list[str]) -> list[str]:
    """Extract entity-like structure lines from code content."""
    entities = [line.strip() for line in lines if CODE_ENTITY_RE.match(line)]
    return entities[:MAX_STRUCTURE_LINES]


def extract_structured_structure_lines(
    lines: list[str],
    content: str,
) -> list[str]:
    """Extract structure lines from structured text content."""
    structured_fields = extract_structured_fields(content)
    if structured_fields:
        return structured_fields[:MAX_STRUCTURE_LINES]
    if lines:
        return lines[: min(MAX_STRUCTURE_LINES, 3)]
    return []


def extract_key_points(*, file_kind: str, content: str) -> list[str]:
    """Extract a compact list of meaningful text lines."""
    if file_kind == STRUCTURED_KIND:
        repeated_key_points = extract_structured_key_points(content)
        if repeated_key_points:
            return repeated_key_points
    if file_kind == CODE_KIND:
        code_key_points = extract_code_key_points(content)
        if code_key_points:
            return code_key_points
    return extract_plain_key_points(content)


def extract_structured_key_points(content: str) -> list[str]:
    """Extract repeated-field highlights from structured content."""
    repeated_keys = extract_repeated_assignment_keys(content)
    return [f"Repeated field: {key} ({count} entries)" for key, count in repeated_keys]


def extract_code_key_points(content: str) -> list[str]:
    """Extract import highlights from code content."""
    imports = extract_code_import_lines(content)
    return imports[:MAX_KEY_POINT_LINES]


def extract_plain_key_points(content: str) -> list[str]:
    """Extract meaningful free-form lines from plain text content."""
    key_points: list[str] = []
    for line in content.splitlines():
        normalized = " ".join(line.split()).strip()
        if len(normalized) < MIN_PLAIN_KEY_POINT_LENGTH:
            continue
        key_points.append(normalized)
        if len(key_points) >= MAX_KEY_POINT_LINES:
            break
    return key_points


def extract_structured_fields(content: str) -> list[str]:
    """Extract likely structured field names from one file preview."""
    fields: list[str] = []
    seen_fields: set[str] = set()
    lines = [line.strip() for line in content.splitlines() if line.strip()]
    if not lines:
        return []
    header_fields = extract_csv_headers(lines[0])
    for field in header_fields:
        if field not in seen_fields:
            fields.append(field)
            seen_fields.add(field)
    for line in lines:
        match = STRUCTURED_FIELD_RE.match(line)
        if match is None:
            continue
        field_name = match.group(1) or match.group(2) or ""
        normalized = field_name.strip()
        if normalized and normalized not in seen_fields:
            fields.append(normalized)
            seen_fields.add(normalized)
        if len(fields) >= TOP_FIELD_COUNT:
            break
    return fields


def extract_csv_headers(line: str) -> list[str]:
    """Extract CSV-like headers from one first row."""
    parts = [part.strip().strip("\"'") for part in CSV_SPLIT_RE.split(line)]
    if len(parts) <= 1:
        return []
    filtered_parts = [part for part in parts if part]
    if not filtered_parts:
        return []
    if any(" " in part for part in filtered_parts):
        return []
    return filtered_parts[:TOP_FIELD_COUNT]


def extract_repeated_assignment_keys(
    content: str,
) -> list[tuple[str, int]]:
    """Extract repeated assignment-style keys from a file preview."""
    key_counter: Counter[str] = Counter()
    for line in content.splitlines():
        match = ASSIGNMENT_KEY_RE.match(line)
        if match is None:
            continue
        key_counter[match.group(1)] += 1
    return [
        (key, count)
        for key, count in key_counter.most_common(TOP_REPEAT_COUNT)
        if count > 1
    ]


def extract_code_import_lines(content: str) -> list[str]:
    """Extract import lines from a code file preview."""
    imports: list[str] = []
    for line in content.splitlines():
        normalized = line.strip()
        if CODE_IMPORT_RE.match(normalized):
            imports.append(normalized)
        if len(imports) >= MAX_KEY_POINT_LINES:
            break
    return imports


def build_summary_preview(content: str) -> str:
    """Return the bounded text window used for file summaries."""
    return content[:MAX_SUMMARY_PREVIEW_CHARS]


def build_summary_warnings(
    analysis: FileContextAnalysis,
) -> list[str]:
    """Build warning lines for one stored file summary."""
    warnings = [SUMMARY_WARNING]
    if analysis.processing_mode == CHUNKED_RECALL_MODE:
        warnings.append(TRUNCATION_WARNING)
    return warnings


def build_summary_overview(
    *,
    analysis: FileContextAnalysis,
    content: str,
    structure: list[str],
    key_points: list[str],
) -> str:
    """Build a more informative overview sentence for one larger file."""
    base_overview = build_base_summary_overview(
        analysis=analysis,
        content=content,
    )
    kind_specific_overview = build_kind_specific_summary_overview(
        analysis=analysis,
        content=content,
        structure=structure,
        key_points=key_points,
    )
    if not kind_specific_overview:
        return base_overview
    return f"{base_overview} {kind_specific_overview}"


def build_base_summary_overview(
    *,
    analysis: FileContextAnalysis,
    content: str,
) -> str:
    """Build the shared first sentence for file summaries."""
    all_lines = content.splitlines()
    non_empty_line_count = sum(1 for line in all_lines if line.strip())
    return (
        f"{analysis.file_name} is a {analysis.file_kind.replace('_', ' ')} file "
        f"with about {analysis.estimated_tokens} prompt tokens, "
        f"{len(all_lines)} lines, and {non_empty_line_count} non-empty lines."
    )


def build_kind_specific_summary_overview(
    *,
    analysis: FileContextAnalysis,
    content: str,
    structure: list[str],
    key_points: list[str],
) -> str:
    """Build the file-kind-specific suffix for one summary overview."""
    if analysis.file_kind == MARKUP_KIND:
        return (
            "Detected "
            f"{len(structure)} heading-style sections in the visible "
            "summary window."
        )
    if analysis.file_kind == CODE_KIND:
        return build_code_summary_overview(
            content=content,
            structure=structure,
        )
    if analysis.file_kind == STRUCTURED_KIND:
        return build_structured_summary_overview(content=content)
    if key_points:
        return f"First key point: {key_points[0]}"
    return ""


def build_code_summary_overview(
    *,
    content: str,
    structure: list[str],
) -> str:
    """Build the code-specific suffix for one summary overview."""
    entity_count = len([line for line in structure if CODE_ENTITY_RE.match(line)])
    import_count = len(extract_code_import_lines(content))
    return (
        f"Detected {entity_count} code entities and {import_count} "
        "import lines in the visible summary window."
    )


def build_structured_summary_overview(*, content: str) -> str:
    """Build the structured-file suffix for one summary overview."""
    field_count = len(extract_structured_fields(content))
    repeated_key_count = len(extract_repeated_assignment_keys(content))
    return (
        f"Detected {field_count} structured field names and "
        f"{repeated_key_count} repeated configuration fields in the "
        "visible summary window."
    )
