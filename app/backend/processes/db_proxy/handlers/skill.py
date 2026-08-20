"""Skill-related DB proxy handler factories.

Provides CRUD handlers for the skills table with structured columns
(trigger_text, procedure, errors, verification), FTS5 search,
usage log, and list operations.
"""

import json
import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from ..common import OperationHandler, validate_params
from ..params import (
    SkillCreateParams,
    SkillDeleteParams,
    SkillListParams,
    SkillLoadParams,
    SkillSearchParams,
    SkillUpdateParams,
    SkillUseParams,
)


if TYPE_CHECKING:
    from backend.security.database import DatabaseManager


# --- Column order for SELECT queries ---
# id, name, title, description, trigger_text, procedure, errors,
# verification, tool_sequence, use_count, enabled, created_at, updated_at
_SKILL_COLUMNS = (
    "s.id, s.name, s.title, s.description, s.trigger_text, s.procedure, s.errors, "
    "s.verification, s.tool_sequence, s.use_count, s.enabled, "
    "s.created_at, s.updated_at"
)

_SKILL_COLUMNS_PLAIN = (
    "id, name, title, description, trigger_text, procedure, errors, "
    "verification, tool_sequence, use_count, enabled, created_at, updated_at"
)


def _current_utc_timestamp() -> str:
    """Return the current UTC timestamp in ISO format."""
    return datetime.now(tz=UTC).isoformat()


def _parse_json_field(value: str, default: list | dict) -> list | dict:
    """Parse a JSON string, returning default on failure."""
    if not value:
        return default
    try:
        return json.loads(value)
    except json.JSONDecodeError, TypeError:
        return default


def _skill_item(row: tuple[Any, ...]) -> dict[str, Any]:
    """Convert one full skill row into the API shape."""
    return {
        "id": row[0],
        "name": row[1],
        "title": row[2],
        "description": row[3],
        "trigger": row[4],
        "procedure": _parse_json_field(row[5], []),
        "errors": _parse_json_field(row[6], []),
        "verification": row[7],
        "tool_sequence": row[8],
        "use_count": row[9],
        "enabled": bool(row[10]),
        "created_at": row[11],
        "updated_at": row[12],
    }


def _assemble_skill_text(skill: dict[str, Any]) -> str:
    """Assemble readable markdown text from structured skill fields."""
    parts = []

    trigger = skill.get("trigger", "")
    if trigger:
        parts.append(f"## When to Use\n{trigger}")

    procedure = skill.get("procedure", [])
    if procedure:
        steps = []
        for i, step in enumerate(procedure, 1):
            tool = step.get("tool", "")
            query = step.get("query", "")
            note = step.get("note", "")
            line = f"{i}. **{tool}**: {query}"
            if note:
                line += f" ({note})"
            steps.append(line)
        parts.append("## Procedure\n" + "\n".join(steps))

    errors = skill.get("errors", [])
    if errors:
        error_lines = []
        for err in errors:
            name = err.get("name", "")
            fix = err.get("fix", "")
            error_lines.append(f"- **{name}**: {fix}")
        parts.append("## Pitfalls\n" + "\n".join(error_lines))

    verification = skill.get("verification", "")
    if verification:
        parts.append(f"## Verification\n{verification}")

    return "\n\n".join(parts)


# --- Stop words for FTS search ---

_STOP_WORDS = frozenset(
    {
        "в",
        "на",
        "и",
        "за",
        "к",
        "с",
        "по",
        "из",
        "от",
        "до",
        "о",
        "об",
        "про",
        "для",
        "что",
        "как",
        "где",
        "кто",
        "чем",
        "при",
        "ли",
        "же",
        "бы",
        "не",
        "ни",
        "да",
        "нет",
        "а",
        "но",
        "или",
        "ещё",
        "еще",
        "сколько",
        "какая",
        "какой",
        "какие",
        "каким",
        "этот",
        "эта",
        "это",
        "тот",
        "та",
        "те",
        "мне",
        "мой",
        "мои",
        "все",
        "всё",
        "уже",
        "очень",
        "сейчас",
        "сегодня",
        "завтра",
        "данный",
        "момент",
        "the",
        "a",
        "an",
        "in",
        "on",
        "at",
        "to",
        "for",
        "of",
        "and",
        "or",
        "is",
        "are",
        "was",
        "were",
        "be",
        "been",
        "with",
        "from",
        "this",
        "that",
        "what",
        "how",
        "which",
        "who",
        "when",
        "where",
    }
)

MIN_MATCHING_WORDS = 2


def _extract_search_words(text: str) -> list[str]:
    """Extract meaningful words from text, filtering stop words."""
    return [
        w.lower()
        for w in text.split()
        if w.isalnum() and w.lower() not in _STOP_WORDS and len(w) > 1
    ]


def _count_matching_words(
    query_words: list[str],
    skill: dict[str, Any],
) -> int:
    """Count how many query words appear in skill title, description, or trigger."""
    skill_text = (
        skill.get("name", "")
        + " "
        + skill.get("title", "")
        + " "
        + skill.get("description", "")
        + " "
        + skill.get("trigger", "")
    ).lower()
    return sum(1 for w in query_words if w in skill_text)


# --- Handlers ---


def make_skill_create_handler(db: DatabaseManager) -> OperationHandler:
    """Create handler for skill_create operation (INSERT or UPDATE on conflict)."""

    async def handle(params: dict[str, Any] | None) -> dict[str, Any]:
        """Create or update a skill with structured fields."""
        p = validate_params(params, SkillCreateParams)
        now = _current_utc_timestamp()
        skill_id = str(uuid.uuid4())
        trigger = getattr(p, "trigger", "") or ""
        procedure = getattr(p, "procedure", "[]") or "[]"
        errors = getattr(p, "errors", "[]") or "[]"
        verification = getattr(p, "verification", "") or ""
        # Ensure procedure/errors are JSON strings
        if isinstance(procedure, list):
            procedure = json.dumps(procedure)
        if isinstance(errors, list):
            errors = json.dumps(errors)
        db.execute(
            "INSERT INTO skills (id, name, title, description, trigger_text, "
            "procedure, errors, verification, tool_sequence, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(name) DO UPDATE SET "
            "title = excluded.title, description = excluded.description, "
            "trigger_text = excluded.trigger_text, procedure = excluded.procedure, "
            "errors = excluded.errors, verification = excluded.verification, "
            "tool_sequence = excluded.tool_sequence, updated_at = excluded.updated_at",
            (
                skill_id,
                p.name,
                p.title,
                p.description,
                trigger,
                procedure,
                errors,
                verification,
                p.tool_sequence,
                now,
                now,
            ),
        )
        db.commit()
        return {"name": p.name, "status": "saved"}

    return handle


def make_skill_search_handler(db: DatabaseManager) -> OperationHandler:
    """Create handler for skill_search operation (FTS5 query with post-filter)."""

    async def handle(params: dict[str, Any] | None) -> dict[str, Any]:
        """Search skills via FTS5 with stop-word filtering and relevance post-filter."""
        p = validate_params(params, SkillSearchParams)
        words = _extract_search_words(p.query)
        if not words:
            return {"skills": []}
        safe_query = " OR ".join(words)
        rows = db.execute(
            f"SELECT {_SKILL_COLUMNS} "  # noqa: S608 — dynamic identifiers only; values bound
            "FROM skills s "
            "JOIN skills_fts f ON s.rowid = f.rowid "
            "WHERE skills_fts MATCH ? AND s.enabled = 1 "
            "ORDER BY rank LIMIT 10",
            (safe_query,),
        ).fetchall()
        candidates = [_skill_item(row) for row in rows]
        filtered = [
            s
            for s in candidates
            if _count_matching_words(words, s) >= MIN_MATCHING_WORDS
        ]
        return {"skills": filtered[:3]}

    return handle


def make_skill_use_handler(db: DatabaseManager) -> OperationHandler:
    """Create handler for skill_use operation (increment use_count)."""

    async def handle(params: dict[str, Any] | None) -> dict[str, Any]:
        """Increment use_count for a skill."""
        p = validate_params(params, SkillUseParams)
        db.execute(
            "UPDATE skills SET use_count = use_count + 1, updated_at = ? WHERE id = ?",
            (_current_utc_timestamp(), p.skill_id),
        )
        db.commit()
        return {"status": "ok"}

    return handle


def make_skill_load_handler(db: DatabaseManager) -> OperationHandler:
    """Create handler for skill_load operation (get by name, assemble text)."""

    async def handle(params: dict[str, Any] | None) -> dict[str, Any]:
        """Load a skill by name and return assembled text."""
        p = validate_params(params, SkillLoadParams)
        row = db.execute(
            f"SELECT {_SKILL_COLUMNS_PLAIN} FROM skills WHERE name = ?",  # noqa: S608 — dynamic identifiers only; values bound
            (p.name,),
        ).fetchone()
        if row is None:
            return {"skill": None}
        skill = _skill_item(row)
        skill["assembled_text"] = _assemble_skill_text(skill)
        return {"skill": skill}

    return handle


def make_skill_update_handler(db: DatabaseManager) -> OperationHandler:
    """Create handler for skill_update operation (partial update)."""

    async def handle(params: dict[str, Any] | None) -> dict[str, Any]:
        """Update skill fields by name."""
        p = validate_params(params, SkillUpdateParams)
        updates = []
        values = []
        if p.title is not None:
            updates.append("title = ?")
            values.append(p.title)
        if p.description is not None:
            updates.append("description = ?")
            values.append(p.description)
        if getattr(p, "trigger", None) is not None:
            updates.append("trigger_text = ?")
            values.append(p.trigger)
        if getattr(p, "procedure", None) is not None:
            proc = p.procedure
            if isinstance(proc, list):
                proc = json.dumps(proc)
            updates.append("procedure = ?")
            values.append(proc)
        if getattr(p, "errors", None) is not None:
            errs = p.errors
            if isinstance(errs, list):
                errs = json.dumps(errs)
            updates.append("errors = ?")
            values.append(errs)
        if getattr(p, "verification", None) is not None:
            updates.append("verification = ?")
            values.append(p.verification)
        if p.enabled is not None:
            updates.append("enabled = ?")
            values.append(p.enabled)
        if not updates:
            return {"name": p.name, "status": "no_changes"}
        updates.append("updated_at = ?")
        values.append(_current_utc_timestamp())
        values.append(p.name)
        db.execute(
            f"UPDATE skills SET {', '.join(updates)} WHERE name = ?",  # noqa: S608 — dynamic identifiers only; values bound
            tuple(values),
        )
        db.commit()
        return {"name": p.name, "status": "updated"}

    return handle


def make_skill_delete_handler(db: DatabaseManager) -> OperationHandler:
    """Create handler for skill_delete operation."""

    async def handle(params: dict[str, Any] | None) -> dict[str, Any]:
        """Delete a skill by name."""
        p = validate_params(params, SkillDeleteParams)
        db.execute("DELETE FROM skills WHERE name = ?", (p.name,))
        db.commit()
        return {"name": p.name, "status": "deleted"}

    return handle


def make_skill_list_handler(db: DatabaseManager) -> OperationHandler:
    """Create handler for skill_list operation (full data with last usage status)."""

    async def handle(params: dict[str, Any] | None) -> dict[str, Any]:
        """List all skills with structured fields and last usage status."""
        validate_params(params, SkillListParams)
        rows = db.execute(
            f"SELECT {_SKILL_COLUMNS_PLAIN} FROM skills "  # noqa: S608 — dynamic identifiers only; values bound
            "ORDER BY use_count DESC LIMIT 50",
        ).fetchall()
        skills = [_skill_item(row) for row in rows]

        # Enrich with last usage status from skill_usage_log
        for skill in skills:
            log_row = db.execute(
                "SELECT status, created_at FROM skill_usage_log "
                "WHERE skill_id = ? ORDER BY created_at DESC LIMIT 1",
                (skill["id"],),
            ).fetchone()
            if log_row:
                skill["last_status"] = log_row[0]
                skill["last_used_at"] = log_row[1]
            else:
                skill["last_status"] = None
                skill["last_used_at"] = None

        return {"skills": skills}

    return handle


# --- Usage log handlers ---


def make_skill_usage_log_create_handler(db: DatabaseManager) -> OperationHandler:
    """Create handler for skill_usage_log_create operation."""

    async def handle(params: dict[str, Any] | None) -> dict[str, Any]:
        """Log a skill usage entry."""
        params = params or {}
        log_id = str(uuid.uuid4())
        db.execute(
            "INSERT INTO skill_usage_log "
            "(id, skill_id, question, status, duration_ms, note, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                log_id,
                params.get("skill_id", ""),
                params.get("question", ""),
                params.get("status", "success"),
                params.get("duration_ms", 0),
                params.get("note", ""),
                _current_utc_timestamp(),
            ),
        )
        # Also increment use_count on the skill
        skill_id = params.get("skill_id", "")
        if skill_id:
            db.execute(
                "UPDATE skills SET use_count = use_count + 1, "
                "updated_at = ? WHERE id = ?",
                (_current_utc_timestamp(), skill_id),
            )
        db.commit()
        return {"log_id": log_id}

    return handle


def make_skill_usage_log_list_handler(db: DatabaseManager) -> OperationHandler:
    """Create handler for skill_usage_log_list operation."""

    async def handle(params: dict[str, Any] | None) -> dict[str, Any]:
        """List usage logs for a skill."""
        params = params or {}
        skill_id = params.get("skill_id", "")
        rows = db.execute(
            "SELECT id, skill_id, question, status, duration_ms, note, created_at "
            "FROM skill_usage_log WHERE skill_id = ? "
            "ORDER BY created_at DESC LIMIT 20",
            (skill_id,),
        ).fetchall()
        logs = [
            {
                "id": r[0],
                "skill_id": r[1],
                "question": r[2],
                "status": r[3],
                "duration_ms": r[4],
                "note": r[5],
                "created_at": r[6],
            }
            for r in rows
        ]
        return {"logs": logs}

    return handle
