"""Tests for file context processing in chat-attached files.

Covers file mode selection, summary generation, chunk selection, and
prompt block rendering. Verifies that small files stay inline while
larger files move through preview, summarized, and chunked recall
paths without falling back to raw full-content injection.
"""

from backend.files import FileContextManager
from backend.files.file_context_manager import (
    CHUNKED_RECALL_MODE,
    INLINE_MODE,
    PREVIEW_MODE,
    SUMMARIZED_MODE,
)


class TestFileContextManager:
    """Tests for mode-based file context assembly."""

    def test_small_text_file_stays_inline(self):
        """Small files should remain in one raw inline block."""
        manager = FileContextManager()

        result = manager.build_file_context(
            file_id="f1",
            file_name="notes.txt",
            mime_type="text/plain",
            content="Short deployment note.",
            user_query="Summarize this file.",
        )

        assert result.analysis.processing_mode == INLINE_MODE
        assert result.summary is None
        assert result.chunks is None
        assert len(result.prompt_blocks) == 1
        assert "--- File: notes.txt ---" in result.prompt_blocks[0]

    def test_medium_text_file_uses_preview_mode(self):
        """Medium files should render metadata plus a bounded preview."""
        manager = FileContextManager(
            inline_token_limit=10,
            preview_token_limit=100,
        )
        content = "A" * 400

        result = manager.build_file_context(
            file_id="f2",
            file_name="report.txt",
            mime_type="text/plain",
            content=content,
            user_query="What is in this report?",
        )

        assert result.analysis.processing_mode == PREVIEW_MODE
        assert result.summary is None
        assert result.chunks is None
        assert any("Mode: preview" in block for block in result.prompt_blocks)
        assert any(
            "Only a preview or selected chunks are shown below." in block
            for block in result.prompt_blocks
        )

    def test_medium_file_uses_chunked_recall_for_exact_query(self):
        """Medium files should not get stuck in preview for exact-detail questions."""
        manager = FileContextManager(
            inline_token_limit=10,
            preview_token_limit=100,
            chunk_token_size=30,
            max_prompt_chunks=2,
        )
        content = "\n".join(
            [
                "deployment_id=release-2026-03-24",
                "healthcheck_path=/health",
                "retry_limit=5",
                "status_page_enabled=true",
            ]
            * 8
        )

        result = manager.build_file_context(
            file_id="f2-exact",
            file_name="report.txt",
            mime_type="text/plain",
            content=content,
            user_query="Show the exact /health line from this file.",
        )

        assert result.analysis.processing_mode == CHUNKED_RECALL_MODE
        assert result.summary is not None
        assert result.chunks is not None
        assert any("/health" in chunk.text for chunk in result.chunks)
        assert any("Relevant file chunks:" in block for block in result.prompt_blocks)

    def test_large_file_uses_summary_mode_without_exact_query(self):
        """Large files should summarize when the query is not asking for proof."""
        manager = FileContextManager(
            inline_token_limit=10,
            preview_token_limit=20,
        )
        content = "\n".join(
            [
                "# Rollout Plan",
                "Prepare database backup before deploy.",
                "Update status page before maintenance window starts.",
                "Verify health checks after rollout.",
            ]
            * 200
        )

        result = manager.build_file_context(
            file_id="f3",
            file_name="rollout.md",
            mime_type="text/markdown",
            content=content,
            user_query="Summarize this rollout plan.",
        )

        assert result.analysis.processing_mode == SUMMARIZED_MODE
        assert result.summary is not None
        assert result.chunks is None
        assert any("File summary:" in block for block in result.prompt_blocks)
        assert "heading-style sections" in result.summary.overview
        assert any(
            "This file was summarized before prompt injection" in block
            for block in result.prompt_blocks
        )

    def test_large_file_uses_chunked_recall_for_exact_query(self):
        """Large files should expose selected chunks for exact-detail questions."""
        manager = FileContextManager(
            inline_token_limit=10,
            preview_token_limit=20,
            chunk_token_size=30,
            max_prompt_chunks=2,
        )
        content = "\n".join(
            [
                "cpu_limit=2000m",
                "memory_limit=4Gi",
                "replicas=3",
                "service_type=ClusterIP",
                "healthcheck_path=/health",
                "rollout_strategy=blue-green",
                "status_page_update=true",
            ]
            * 50
        )

        result = manager.build_file_context(
            file_id="f4",
            file_name="deploy.conf",
            mime_type="text/plain",
            content=content,
            user_query=(
                "Which lines show the healthcheck_path and rollout strategy exactly?"
            ),
        )

        assert result.analysis.processing_mode == CHUNKED_RECALL_MODE
        assert result.summary is not None
        assert result.chunks is not None
        assert len(result.chunks) <= 2
        assert any("Relevant file chunks:" in block for block in result.prompt_blocks)
        assert any(
            "healthcheck_path" in block or "rollout_strategy" in block
            for block in result.prompt_blocks
        )

    def test_large_file_uses_chunked_recall_for_multilingual_exact_query(self):
        """Large files should detect exact-detail requests beyond English."""
        manager = FileContextManager(
            inline_token_limit=10,
            preview_token_limit=20,
            chunk_token_size=30,
            max_prompt_chunks=2,
        )
        content = "\n".join(
            [
                "service_name=bimemo",
                "healthcheck_path=/health",
                "rollout_strategy=blue-green",
                "retry_limit=5",
            ]
            * 50
        )

        result = manager.build_file_context(
            file_id="f4-lt",
            file_name="deploy.conf",
            mime_type="text/plain",
            content=content,
            user_query="Parodyk /health eilutę iš failo",
        )

        assert result.analysis.processing_mode == CHUNKED_RECALL_MODE
        assert result.chunks is not None
        assert any("/health" in chunk.text for chunk in result.chunks)

    def test_code_file_summary_extracts_entities(self):
        """Code files should expose structural entities in the summary."""
        manager = FileContextManager(
            inline_token_limit=10,
            preview_token_limit=20,
        )
        content = "\n".join(
            [
                "def build_context():",
                "    return True",
                "",
                "class MemoryRanker:",
                "    pass",
                "",
                "def select_chunks():",
                "    return []",
            ]
            * 100
        )

        result = manager.build_file_context(
            file_id="f5",
            file_name="memory.py",
            mime_type="text/x-python",
            content=content,
            user_query="Summarize this file.",
        )

        assert result.summary is not None
        assert "def build_context():" in result.summary.structure
        assert "class MemoryRanker:" in result.summary.structure
        assert "code entities" in result.summary.overview

    def test_structured_file_summary_extracts_headers_and_repeated_fields(self):
        """Structured files should expose field names and repeated config keys."""
        manager = FileContextManager(
            inline_token_limit=10,
            preview_token_limit=20,
        )
        content = "\n".join(
            [
                "service,region,status",
                "api,eu,healthy",
                "worker,us,degraded",
                "timeout=30",
                "timeout=60",
                "retry_limit=3",
                "retry_limit=5",
            ]
            * 80
        )

        result = manager.build_file_context(
            file_id="f7",
            file_name="services.csv",
            mime_type="text/csv",
            content=content,
            user_query="Summarize this file.",
        )

        assert result.summary is not None
        assert "service" in result.summary.structure
        assert "region" in result.summary.structure
        assert any(
            point.startswith("Repeated field: timeout")
            for point in result.summary.key_points
        )
        assert "structured field names" in result.summary.overview

    def test_stored_large_file_context_round_trips_into_chunked_recall(self):
        """Persisted large-file context should rebuild query-specific chunks later."""
        manager = FileContextManager(
            inline_token_limit=10,
            preview_token_limit=20,
            chunk_token_size=30,
            max_prompt_chunks=2,
        )
        content = "\n".join(
            [
                "cpu_limit=2000m",
                "memory_limit=4Gi",
                "replicas=3",
                "service_type=ClusterIP",
                "healthcheck_path=/health",
                "rollout_strategy=blue-green",
            ]
            * 50
        )

        stored_context = manager.build_stored_file_context(
            file_id="f6",
            file_name="deploy.conf",
            mime_type="text/plain",
            content=content,
        )
        payload = manager.serialize_stored_file_context(stored_context=stored_context)
        restored_context = manager.deserialize_stored_file_context(
            file_id="f6",
            file_name="deploy.conf",
            mime_type="text/plain",
            size_bytes=len(content.encode()),
            payload=payload,
        )

        assert restored_context is not None
        result = manager.build_file_context_from_storage(
            stored_context=restored_context,
            user_query="Which lines show the healthcheck_path exactly?",
        )

        assert result.analysis.processing_mode == CHUNKED_RECALL_MODE
        assert result.chunks is not None
        assert any("healthcheck_path" in chunk.text for chunk in result.chunks)

    def test_unknown_binary_file_is_not_treated_as_text(self):
        """Binary-like files should stay outside the text context pipeline."""
        manager = FileContextManager()

        assert (
            manager.is_textual_file(
                file_name="manual.pdf",
                mime_type="application/pdf",
            )
            is False
        )

    def test_usage_snapshot_tracks_last_chunk_selection(self):
        """Usage snapshots should preserve selected chunk ids and chunk indexes."""
        manager = FileContextManager(
            inline_token_limit=10,
            preview_token_limit=20,
            chunk_token_size=30,
            max_prompt_chunks=2,
        )
        content = "\n".join(
            [
                "feature_flag=true",
                "rollout_strategy=blue-green",
                "healthcheck_path=/health",
                "retry_limit=5",
            ]
            * 50
        )

        result = manager.build_file_context(
            file_id="f8",
            file_name="deploy.conf",
            mime_type="text/plain",
            content=content,
            user_query="Which lines show the rollout strategy exactly?",
        )
        usage_snapshot = manager.build_usage_snapshot(
            file_context=result,
            user_query="Which lines show the rollout strategy exactly?",
        )

        assert usage_snapshot.processing_mode == CHUNKED_RECALL_MODE
        assert usage_snapshot.mode_reason
        assert usage_snapshot.selected_chunk_ids
        assert usage_snapshot.selected_chunk_indexes
