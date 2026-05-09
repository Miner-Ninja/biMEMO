"""Tests for skill storage, structured fields, FTS5 search, and handlers.

Covers skill CRUD with structured columns (trigger, procedure, errors,
verification), FTS5 search, usage log, and schema migration.
"""

import json
import secrets

import pytest
from backend.security.database import DatabaseManager
from backend.security.schema import SCHEMA_VERSION, initialize_schema
from backend.tools.builtin import SkillCreateInput
from pydantic import ValidationError


@pytest.fixture
def master_key():
    """Generate a test master key."""
    return bytearray(secrets.token_bytes(32))


@pytest.fixture
def db(tmp_path, master_key):
    """Create and return an open DatabaseManager with schema."""
    db_path = str(tmp_path / "test_skills.db")
    manager = DatabaseManager(db_path)
    manager.open(master_key)
    initialize_schema(manager)
    yield manager
    manager.close()


def _insert_skill(
    db,
    name,
    title,
    description,
    trigger="",
    procedure="[]",
    errors="[]",
    verification="",
    tool_sequence="",
):
    """Insert a skill directly into the database."""
    import uuid
    from datetime import UTC, datetime

    skill_id = str(uuid.uuid4())
    now = datetime.now(tz=UTC).isoformat()
    db.execute(
        "INSERT INTO skills (id, name, title, description, trigger_text, "
        "procedure, errors, verification, tool_sequence, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            skill_id,
            name,
            title,
            description,
            trigger,
            procedure,
            errors,
            verification,
            tool_sequence,
            now,
            now,
        ),
    )
    db.commit()
    return skill_id


class TestSkillSchema:
    """Tests for skills table schema."""

    def test_skills_table_exists(self, db):
        """Verify skills table is created."""
        rows = db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='skills'"
        ).fetchall()
        assert len(rows) == 1

    def test_structured_columns_exist(self, db):
        """Verify structured columns exist."""
        columns = {row[1] for row in db.execute("PRAGMA table_info(skills)").fetchall()}
        assert "trigger_text" in columns
        assert "procedure" in columns
        assert "errors" in columns
        assert "verification" in columns
        assert "enabled" in columns
        assert "content" not in columns

    def test_usage_log_table_exists(self, db):
        """Verify skill_usage_log table is created."""
        rows = db.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type='table' AND name='skill_usage_log'"
        ).fetchall()
        assert len(rows) == 1

    def test_schema_version_is_current(self, db):
        """Verify schema version is current."""
        row = db.execute("SELECT version FROM schema_version LIMIT 1").fetchone()
        assert row[0] == SCHEMA_VERSION

    def test_fts_table_exists(self, db):
        """Verify FTS5 table exists."""
        rows = db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='skills_fts'"
        ).fetchall()
        assert len(rows) == 1


class TestSkillCRUD:
    """Tests for skill create, read, update."""

    def test_insert_skill(self, db):
        """Verify skill insertion with structured fields."""
        skill_id = _insert_skill(
            db,
            "test-skill",
            "Test Skill",
            "A test",
            trigger="When user asks X",
            procedure='[{"tool":"web_search","query":"test"}]',
            errors='[{"name":"No data","fix":"Retry"}]',
            verification="Check result",
        )
        row = db.execute(
            "SELECT name, trigger_text, procedure FROM skills WHERE id = ?", (skill_id,)
        ).fetchone()
        assert row[0] == "test-skill"
        assert row[1] == "When user asks X"
        assert "web_search" in row[2]

    def test_use_count_increment(self, db):
        """Verify use_count increments."""
        skill_id = _insert_skill(db, "counter", "Counter", "desc")
        db.execute(
            "UPDATE skills SET use_count = use_count + 1 WHERE id = ?", (skill_id,)
        )
        db.execute(
            "UPDATE skills SET use_count = use_count + 1 WHERE id = ?", (skill_id,)
        )
        db.commit()
        row = db.execute(
            "SELECT use_count FROM skills WHERE id = ?", (skill_id,)
        ).fetchone()
        assert row[0] == 2


class TestSkillFTS:
    """Tests for FTS5 search on structured columns."""

    def test_search_by_trigger(self, db):
        """Verify FTS5 finds skills by trigger_text."""
        _insert_skill(
            db,
            "weather",
            "Weather",
            "desc",
            trigger="weather forecast comparison cities",
        )
        rows = db.execute(
            "SELECT s.name FROM skills s "
            "JOIN skills_fts f ON s.rowid = f.rowid "
            "WHERE skills_fts MATCH ? LIMIT 5",
            ("forecast",),
        ).fetchall()
        assert len(rows) == 1
        assert rows[0][0] == "weather"

    def test_search_by_verification(self, db):
        """Verify FTS5 finds skills by verification."""
        _insert_skill(
            db,
            "verify-test",
            "Test",
            "desc",
            verification="check unicorn rainbow results",
        )
        rows = db.execute(
            "SELECT s.name FROM skills s "
            "JOIN skills_fts f ON s.rowid = f.rowid "
            "WHERE skills_fts MATCH ? LIMIT 5",
            ("unicorn",),
        ).fetchall()
        assert len(rows) == 1

    def test_search_no_results(self, db):
        """Verify FTS5 returns empty for non-matching query."""
        _insert_skill(db, "some-skill", "Some", "desc")
        rows = db.execute(
            "SELECT s.name FROM skills s "
            "JOIN skills_fts f ON s.rowid = f.rowid "
            "WHERE skills_fts MATCH ? LIMIT 5",
            ("nonexistent",),
        ).fetchall()
        assert len(rows) == 0


class TestSkillInputModels:
    """Tests for skill Pydantic input models."""

    def test_skill_create_input_structured(self):
        """Verify structured SkillCreateInput accepted."""
        inp = SkillCreateInput(
            name="test",
            title="Test",
            description="desc",
            trigger="when user asks",
            procedure='[{"tool":"web_search","query":"test"}]',
            errors='[{"name":"err","fix":"fix"}]',
            verification="check it",
        )
        assert inp.trigger == "when user asks"
        assert inp.procedure == '[{"tool":"web_search","query":"test"}]'

    def test_skill_create_input_extra_rejected(self):
        """Verify extra fields rejected."""
        with pytest.raises(ValidationError):
            SkillCreateInput(
                name="test",
                title="Test",
                description="desc",
                extra_field="bad",
            )


class TestSkillDBProxyHandlers:
    """Tests for skill DB proxy handlers."""

    @pytest.mark.asyncio
    async def test_skill_create_handler_structured(self, db):
        """Verify skill_create handler saves structured fields."""
        from backend.processes.db_proxy.handlers.skill import make_skill_create_handler

        handler = make_skill_create_handler(db)
        result = await handler(
            {
                "name": "structured-test",
                "title": "Structured",
                "description": "testing",
                "trigger": "when X happens",
                "procedure": '[{"tool":"web_search","query":"test"}]',
                "errors": '[{"name":"err","fix":"solution"}]',
                "verification": "verify result",
                "tool_sequence": "web_search",
            }
        )
        assert result["status"] == "saved"

        row = db.execute(
            "SELECT trigger_text, procedure, errors, verification "
            "FROM skills WHERE name = ?",
            ("structured-test",),
        ).fetchone()
        assert row[0] == "when X happens"
        assert "web_search" in row[1]
        assert "solution" in row[2]
        assert row[3] == "verify result"

    @pytest.mark.asyncio
    async def test_skill_load_assembles_text(self, db):
        """Verify skill_load returns assembled markdown."""
        from backend.processes.db_proxy.handlers.skill import (
            make_skill_create_handler,
            make_skill_load_handler,
        )

        create = make_skill_create_handler(db)
        await create(
            {
                "name": "assemble-test",
                "title": "Assemble",
                "description": "testing assembly",
                "trigger": "when user asks for crypto prices",
                "procedure": json.dumps(
                    [
                        {"tool": "web_search", "query": "bitcoin price"},
                        {"tool": "web_search", "query": "ethereum price"},
                    ]
                ),
                "errors": json.dumps([{"name": "No data", "fix": "Retry search"}]),
                "verification": "All prices present",
            }
        )

        load = make_skill_load_handler(db)
        result = await load({"name": "assemble-test"})
        skill = result["skill"]
        text = skill["assembled_text"]

        assert "## When to Use" in text
        assert "crypto prices" in text
        assert "## Procedure" in text
        assert "web_search" in text
        assert "bitcoin price" in text
        assert "## Pitfalls" in text
        assert "No data" in text
        assert "## Verification" in text
        assert "All prices present" in text

    @pytest.mark.asyncio
    async def test_skill_update_structured(self, db):
        """Verify skill_update updates structured fields."""
        from backend.processes.db_proxy.handlers.skill import (
            make_skill_create_handler,
            make_skill_update_handler,
        )

        create = make_skill_create_handler(db)
        await create(
            {
                "name": "update-struct",
                "title": "Original",
                "description": "desc",
                "trigger": "original trigger",
            }
        )

        update = make_skill_update_handler(db)
        await update(
            {
                "name": "update-struct",
                "trigger": "updated trigger",
                "verification": "new verification",
                "enabled": 0,
            }
        )

        row = db.execute(
            "SELECT trigger_text, verification, enabled FROM skills WHERE name = ?",
            ("update-struct",),
        ).fetchone()
        assert row[0] == "updated trigger"
        assert row[1] == "new verification"
        assert row[2] == 0

    @pytest.mark.asyncio
    async def test_skill_search_excludes_disabled(self, db):
        """Verify disabled skills excluded from search."""
        from backend.processes.db_proxy.handlers.skill import (
            make_skill_create_handler,
            make_skill_search_handler,
            make_skill_update_handler,
        )

        create = make_skill_create_handler(db)
        await create(
            {
                "name": "disabled-skill",
                "title": "Disabled Skill",
                "description": "unicorns rainbows magical",
                "trigger": "unicorns rainbows magical content",
            }
        )

        search = make_skill_search_handler(db)
        result = await search({"query": "unicorns rainbows magical"})
        assert len(result["skills"]) == 1

        update = make_skill_update_handler(db)
        await update({"name": "disabled-skill", "enabled": 0})

        result = await search({"query": "unicorns rainbows magical"})
        assert len(result["skills"]) == 0

    @pytest.mark.asyncio
    async def test_skill_list_returns_structured(self, db):
        """Verify skill_list returns structured fields."""
        from backend.processes.db_proxy.handlers.skill import (
            make_skill_create_handler,
            make_skill_list_handler,
        )

        create = make_skill_create_handler(db)
        await create(
            {
                "name": "list-test",
                "title": "Listed",
                "description": "desc",
                "trigger": "trigger text",
                "procedure": '[{"tool":"web_search","query":"test"}]',
            }
        )

        lister = make_skill_list_handler(db)
        result = await lister({})
        assert len(result["skills"]) == 1
        skill = result["skills"][0]
        assert skill["trigger"] == "trigger text"
        assert isinstance(skill["procedure"], list)
        assert skill["procedure"][0]["tool"] == "web_search"

    @pytest.mark.asyncio
    async def test_usage_log_create_and_list(self, db):
        """Verify skill usage log create and list."""
        from backend.processes.db_proxy.handlers.skill import (
            make_skill_create_handler,
            make_skill_usage_log_create_handler,
            make_skill_usage_log_list_handler,
        )

        create = make_skill_create_handler(db)
        await create({"name": "log-test", "title": "Log", "description": "d"})
        skill_id = db.execute(
            "SELECT id FROM skills WHERE name = ?", ("log-test",)
        ).fetchone()[0]

        log_create = make_skill_usage_log_create_handler(db)
        await log_create(
            {
                "skill_id": skill_id,
                "question": "test question",
                "status": "success",
                "duration_ms": 1234,
            }
        )
        await log_create(
            {
                "skill_id": skill_id,
                "question": "another question",
                "status": "error",
                "duration_ms": 5000,
                "note": "timeout",
            }
        )

        log_list = make_skill_usage_log_list_handler(db)
        result = await log_list({"skill_id": skill_id})
        assert len(result["logs"]) == 2
        assert result["logs"][0]["status"] in ("success", "error")
