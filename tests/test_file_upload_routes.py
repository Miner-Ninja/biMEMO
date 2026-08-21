"""Integration tests for file upload replacement and stored file context."""

from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import MagicMock

from backend.ipc.models import (
    ChatResponse as IPCChatResponse,  # noqa: TC001
)
from tests.llm_config_fakes import (
    FakeAdminClient,
    _authed_client_with_csrf,
    _build_test_app,
)


class TestFileReplacement:
    """Integration tests for replacement semantics in file uploads."""

    def test_upload_replaces_existing_file_with_same_name(self) -> None:
        """Repeated uploads should keep only the latest file metadata and disk file."""
        app, token, session_manager, signing_client, _ = _build_test_app()
        admin = FakeAdminClient()
        app.state.admin_client = admin
        client, csrf = _authed_client_with_csrf(
            app,
            token,
            session_manager,
            signing_client,
        )

        with TemporaryDirectory() as tmp_dir:
            import backend.files.paths as files_paths

            original_files_dir = files_paths.uploaded_files_dir
            files_paths.uploaded_files_dir = lambda: Path(tmp_dir)
            try:
                first_resp = client.post(
                    "/files/upload",
                    files={"file": ("records.txt", b"first version", "text/plain")},
                    headers={"X-CSRF-Token": csrf},
                )
                assert first_resp.status_code == 201, first_resp.text
                first_id = first_resp.json()["id"]

                second_resp = client.post(
                    "/files/upload",
                    files={"file": ("records.txt", b"second version", "text/plain")},
                    headers={"X-CSRF-Token": csrf},
                )
                assert second_resp.status_code == 201, second_resp.text
                second_id = second_resp.json()["id"]

                files_resp = client.get("/files")
                assert files_resp.status_code == 200
                listed_files = files_resp.json()["files"]
                assert len(listed_files) == 1
                assert listed_files[0]["id"] == second_id
                assert listed_files[0]["original_name"] == "records.txt"
                assert not (Path(tmp_dir) / f"{first_id}.txt").exists()
                replacement_file = Path(tmp_dir) / f"{second_id}.txt"
                assert replacement_file.read_text() == "second version"
            finally:
                files_paths.uploaded_files_dir = original_files_dir

    def test_chat_resolves_attached_files_from_file_ids(self) -> None:
        """Chat should resolve attached file identifiers to readable filenames."""
        app, token, session_manager, signing_client, _ = _build_test_app()
        admin = FakeAdminClient()
        app.state.admin_client = admin
        sent_requests: list[object] = []

        async def send_chat(request: object, session_id: str) -> IPCChatResponse:
            """Return one stubbed orchestrator response for file-id chat tests."""
            sent_requests.append(request)
            return MagicMock(
                text="ok",
                input_filtered=False,
                output_filtered=False,
                pending_confirmations=[],
                retrieved_memories=[],
                retrieved_messages=[],
                transcription=None,
                stats=None,
            )

        app.state.orchestrator_client.send_chat = send_chat

        client, csrf = _authed_client_with_csrf(
            app,
            token,
            session_manager,
            signing_client,
        )

        with TemporaryDirectory() as tmp_dir:
            import backend.files.paths as files_paths

            original_files_dir = files_paths.uploaded_files_dir
            files_paths.uploaded_files_dir = lambda: Path(tmp_dir)
            try:
                upload_resp = client.post(
                    "/files/upload",
                    files={"file": ("context.txt", b"context body", "text/plain")},
                    headers={"X-CSRF-Token": csrf},
                )
                assert upload_resp.status_code == 201, upload_resp.text
                file_id = upload_resp.json()["id"]

                chat_resp = client.post(
                    "/chat",
                    json={"message": "use file", "file_ids": [file_id]},
                    headers={"X-CSRF-Token": csrf},
                )
                assert chat_resp.status_code == 200, chat_resp.text
                assert chat_resp.json()["stream_started"] is True
                assert len(sent_requests) == 1
                assert file_id in admin._file_contexts
                assert sent_requests[0].attached_files == ["context.txt [inline]"]
                assert "--- File: context.txt ---" in sent_requests[0].message
            finally:
                files_paths.uploaded_files_dir = original_files_dir

    def test_chat_summarizes_large_attached_text_files(self) -> None:
        """Large text files should summarize instead of raw full inline injection."""
        app, token, session_manager, signing_client, _ = _build_test_app()
        admin = FakeAdminClient()
        app.state.admin_client = admin
        sent_requests: list[object] = []

        async def send_chat(request: object, session_id: str) -> IPCChatResponse:
            """Return one stubbed chat response for file summary tests."""
            sent_requests.append(request)
            return MagicMock(
                text="ok",
                input_filtered=False,
                output_filtered=False,
                pending_confirmations=[],
                retrieved_memories=[],
                retrieved_messages=[],
                transcription=None,
                stats=None,
            )

        app.state.orchestrator_client.send_chat = send_chat

        client, csrf = _authed_client_with_csrf(
            app,
            token,
            session_manager,
            signing_client,
        )

        with TemporaryDirectory() as tmp_dir:
            import backend.files.paths as files_paths

            original_files_dir = files_paths.uploaded_files_dir
            files_paths.uploaded_files_dir = lambda: Path(tmp_dir)
            try:
                large_content = "\n".join(
                    [
                        "# Rollout Plan",
                        "Prepare database backup before deploy.",
                        "Update status page before maintenance window starts.",
                        "Verify health checks after rollout.",
                    ]
                    * 200
                ).encode()
                upload_resp = client.post(
                    "/files/upload",
                    files={"file": ("rollout.md", large_content, "text/markdown")},
                    headers={"X-CSRF-Token": csrf},
                )
                assert upload_resp.status_code == 201, upload_resp.text
                file_id = upload_resp.json()["id"]

                chat_resp = client.post(
                    "/chat",
                    json={
                        "message": "Summarize the rollout file.",
                        "file_ids": [file_id],
                    },
                    headers={"X-CSRF-Token": csrf},
                )
                assert chat_resp.status_code == 200, chat_resp.text
                assert chat_resp.json()["stream_started"] is True
                assert len(sent_requests) == 1
                assert file_id in admin._file_contexts
                assert sent_requests[0].attached_files == ["rollout.md [summarized]"]
                assert "File summary:" in sent_requests[0].message
                assert (
                    "This file was summarized before prompt injection"
                    in sent_requests[0].message
                )
                assert "--- File: rollout.md ---" not in sent_requests[0].message
            finally:
                files_paths.uploaded_files_dir = original_files_dir

    def test_chat_reuses_stored_large_file_context_without_raw_disk_read(self) -> None:
        """Large-file chat should still work after raw bytes disappear."""
        app, token, session_manager, signing_client, _ = _build_test_app()
        admin = FakeAdminClient()
        app.state.admin_client = admin
        sent_requests: list[object] = []

        async def send_chat(request: object, session_id: str) -> IPCChatResponse:
            """Return one stubbed chat response for stored-file reuse tests."""
            sent_requests.append(request)
            return MagicMock(
                text="ok",
                input_filtered=False,
                output_filtered=False,
                pending_confirmations=[],
                retrieved_memories=[],
                retrieved_messages=[],
                transcription=None,
                stats=None,
            )

        app.state.orchestrator_client.send_chat = send_chat

        client, csrf = _authed_client_with_csrf(
            app,
            token,
            session_manager,
            signing_client,
        )

        with TemporaryDirectory() as tmp_dir:
            import backend.files.paths as files_paths

            original_files_dir = files_paths.uploaded_files_dir
            files_paths.uploaded_files_dir = lambda: Path(tmp_dir)
            try:
                large_content = "\n".join(
                    [
                        "# Incident Review",
                        "Root cause was a stale deployment artifact.",
                        "Recovery required a forced cache refresh.",
                        "Follow-up should document the rollback process.",
                    ]
                    * 200
                ).encode()
                upload_resp = client.post(
                    "/files/upload",
                    files={"file": ("incident.md", large_content, "text/markdown")},
                    headers={"X-CSRF-Token": csrf},
                )
                assert upload_resp.status_code == 201, upload_resp.text
                file_id = upload_resp.json()["id"]
                stored_path = Path(tmp_dir) / f"{file_id}.md"
                stored_path.unlink()

                chat_resp = client.post(
                    "/chat",
                    json={
                        "message": "What exact lines mention rollback details?",
                        "file_ids": [file_id],
                    },
                    headers={"X-CSRF-Token": csrf},
                )
                assert chat_resp.status_code == 200, chat_resp.text
                assert len(sent_requests) == 1
                assert sent_requests[0].attached_files == [
                    "incident.md [chunked_recall]"
                ]
                assert "Relevant file chunks:" in sent_requests[0].message
                assert "Follow-up should document the rollback process." in (
                    sent_requests[0].message
                )
            finally:
                files_paths.uploaded_files_dir = original_files_dir

    def test_chat_falls_back_to_metadata_block_when_small_raw_file_is_missing(
        self,
    ) -> None:
        """Small stored files should stay visible if raw bytes disappear."""
        app, token, session_manager, signing_client, _ = _build_test_app()
        admin = FakeAdminClient()
        app.state.admin_client = admin
        sent_requests: list[object] = []

        async def send_chat(request: object, session_id: str) -> IPCChatResponse:
            """Return one stubbed chat response for missing raw-file tests."""
            sent_requests.append(request)
            return MagicMock(
                text="ok",
                input_filtered=False,
                output_filtered=False,
                pending_confirmations=[],
                retrieved_memories=[],
                retrieved_messages=[],
                transcription=None,
                stats=None,
            )

        app.state.orchestrator_client.send_chat = send_chat

        client, csrf = _authed_client_with_csrf(
            app,
            token,
            session_manager,
            signing_client,
        )

        with TemporaryDirectory() as tmp_dir:
            import backend.files.paths as files_paths

            original_files_dir = files_paths.uploaded_files_dir
            files_paths.uploaded_files_dir = lambda: Path(tmp_dir)
            try:
                upload_resp = client.post(
                    "/files/upload",
                    files={
                        "file": (
                            "note.txt",
                            b"Short deployment note.",
                            "text/plain",
                        )
                    },
                    headers={"X-CSRF-Token": csrf},
                )
                assert upload_resp.status_code == 201, upload_resp.text
                file_id = upload_resp.json()["id"]
                stored_path = Path(tmp_dir) / f"{file_id}.txt"
                stored_path.unlink()

                chat_resp = client.post(
                    "/chat",
                    json={
                        "message": "Use the attached file.",
                        "file_ids": [file_id],
                    },
                    headers={"X-CSRF-Token": csrf},
                )
                assert chat_resp.status_code == 200, chat_resp.text
                assert len(sent_requests) == 1
                assert sent_requests[0].attached_files == ["note.txt [metadata_only]"]
                assert "Attached file: note.txt" in sent_requests[0].message
                assert "Mode: metadata_only" in sent_requests[0].message
                assert "raw file bytes are no longer available on disk" in (
                    sent_requests[0].message
                )
            finally:
                files_paths.uploaded_files_dir = original_files_dir

    def test_admin_memory_file_context_view_returns_stored_context(self) -> None:
        """Admin memory route should expose stored summary and chunks for one file."""
        app, token, session_manager, signing_client, _ = _build_test_app()
        admin = FakeAdminClient()
        app.state.admin_client = admin
        client, csrf = _authed_client_with_csrf(
            app,
            token,
            session_manager,
            signing_client,
        )

        with TemporaryDirectory() as tmp_dir:
            import backend.files.paths as files_paths

            original_files_dir = files_paths.uploaded_files_dir
            files_paths.uploaded_files_dir = lambda: Path(tmp_dir)
            try:
                large_content = "\n".join(
                    [
                        "# Runbook",
                        "Prepare rollback plan.",
                        "Restart edge workers after deploy.",
                        "Document health checks in the incident note.",
                    ]
                    * 200
                ).encode()
                upload_resp = client.post(
                    "/files/upload",
                    files={"file": ("runbook.md", large_content, "text/markdown")},
                    headers={"X-CSRF-Token": csrf},
                )
                assert upload_resp.status_code == 201, upload_resp.text
                file_id = upload_resp.json()["id"]

                files_resp = client.get("/admin/memory/files")
                assert files_resp.status_code == 200, files_resp.text
                assert files_resp.json()["files"][0]["id"] == file_id

                context_resp = client.get(f"/admin/memory/files/{file_id}/context")
                assert context_resp.status_code == 200, context_resp.text
                payload = context_resp.json()["file_context"]
                assert payload is not None
                assert payload["file_id"] == file_id
                assert payload["processing_mode"] == "summarized"
                assert payload["processing_reason"]
                assert payload["summary_overview"].startswith("runbook.md is a")
                assert payload["chunks"]
                assert payload["prompt_blocks"]
                assert any(
                    "File summary:" in block for block in payload["prompt_blocks"]
                )
            finally:
                files_paths.uploaded_files_dir = original_files_dir

    def test_admin_memory_file_context_view_shows_last_prompt_usage(self) -> None:
        """Admin memory file context should include the latest prompt usage snapshot."""
        app, token, session_manager, signing_client, _ = _build_test_app()
        admin = FakeAdminClient()
        app.state.admin_client = admin
        sent_requests: list[object] = []

        async def send_chat(request: object, session_id: str) -> IPCChatResponse:
            """Return one stubbed chat response for prompt-usage memory tests."""
            sent_requests.append(request)
            return MagicMock(
                text="ok",
                input_filtered=False,
                output_filtered=False,
                pending_confirmations=[],
                retrieved_memories=[],
                retrieved_messages=[],
                transcription=None,
                stats=None,
            )

        app.state.orchestrator_client.send_chat = send_chat

        client, csrf = _authed_client_with_csrf(
            app,
            token,
            session_manager,
            signing_client,
        )

        with TemporaryDirectory() as tmp_dir:
            import backend.files.paths as files_paths

            original_files_dir = files_paths.uploaded_files_dir
            files_paths.uploaded_files_dir = lambda: Path(tmp_dir)
            try:
                large_content = "\n".join(
                    [
                        "feature_flag=true",
                        "rollout_strategy=blue-green",
                        "healthcheck_path=/health",
                        "retry_limit=5",
                    ]
                    * 200
                ).encode()
                upload_resp = client.post(
                    "/files/upload",
                    files={"file": ("deploy.conf", large_content, "text/plain")},
                    headers={"X-CSRF-Token": csrf},
                )
                assert upload_resp.status_code == 201, upload_resp.text
                file_id = upload_resp.json()["id"]

                chat_resp = client.post(
                    "/chat",
                    json={
                        "message": "Which lines show the rollout strategy exactly?",
                        "file_ids": [file_id],
                    },
                    headers={"X-CSRF-Token": csrf},
                )
                assert chat_resp.status_code == 200, chat_resp.text

                context_resp = client.get(f"/admin/memory/files/{file_id}/context")
                assert context_resp.status_code == 200, context_resp.text
                payload = context_resp.json()["file_context"]
                assert payload is not None
                assert payload["last_usage"] is not None
                assert payload["last_usage"]["processing_mode"] == "chunked_recall"
                assert payload["last_usage"]["mode_reason"]
                assert payload["last_usage"]["user_query"] == (
                    "Which lines show the rollout strategy exactly?"
                )
                assert payload["last_usage"]["selected_chunk_indexes"]
                assert payload["prompt_blocks"]
                assert any(
                    "Relevant file chunks:" in block
                    for block in payload["prompt_blocks"]
                )
            finally:
                files_paths.uploaded_files_dir = original_files_dir
