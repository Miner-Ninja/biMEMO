"""Tests for the transport headers of the avatar, file, and backup routes.

Covers the ETag/304 and Cache-Control behavior of the public avatar
and file-content routes (R7.1, R7.2) and the 429 Retry-After contract
of the admin backup route (R3.3).
"""

import base64
import hashlib
import uuid

from fastapi import FastAPI
from starlette.testclient import TestClient

import backend.files.paths as files_paths
from backend.channels.endpoints import BackupRateLimiter
from backend.processes.fastapi_app.deps import _get_admin_client, _require_auth
from backend.processes.fastapi_app.problem import register_problem_handlers
from backend.processes.fastapi_app.routes import admin as admin_routes
from backend.processes.fastapi_app.routes import avatars as avatars_routes
from backend.processes.fastapi_app.routes import files as files_routes


class _FakeAvatarAdmin:
    """Returns a canned avatar for the public avatar route."""

    def __init__(self, data: bytes | None) -> None:
        self._data = data

    async def get_avatar(self, avatar_type: str) -> dict:
        if self._data is None:
            return {"avatar": None}
        return {
            "avatar": {
                "data": base64.b64encode(self._data).decode("ascii"),
                "mime_type": "image/png",
            }
        }


class TestAvatarRoute:
    """Tests for avatar ETag, 304, and Cache-Control (R7.1)."""

    def _make_client(self, data: bytes | None):
        """Create a test client serving one canned avatar."""
        admin = _FakeAvatarAdmin(data)
        app = FastAPI()
        app.include_router(avatars_routes.avatars_router)
        app.dependency_overrides[_get_admin_client] = lambda: admin
        return TestClient(app)

    def test_serve_with_etag_and_cache_control(self):
        """Verify 200 carries sha256 ETag and private, max-age=300."""
        data = b"\x89PNG fake avatar bytes"
        client = self._make_client(data)
        resp = client.get("/avatars/user")
        assert resp.status_code == 200
        assert resp.content == data
        assert resp.headers["content-type"] == "image/png"
        assert resp.headers["etag"] == f'"{hashlib.sha256(data).hexdigest()}"'
        assert resp.headers["cache-control"] == "private, max-age=300"

    def test_if_none_match_returns_304_empty(self):
        """Verify a matching If-None-Match yields 304 with an empty body."""
        data = b"\x89PNG fake avatar bytes"
        client = self._make_client(data)
        etag = f'"{hashlib.sha256(data).hexdigest()}"'
        resp = client.get("/avatars/user", headers={"if-none-match": etag})
        assert resp.status_code == 304
        assert resp.content == b""
        assert resp.headers["etag"] == etag
        assert resp.headers["cache-control"] == "private, max-age=300"

    def test_if_none_match_list_contains_etag(self):
        """Verify a comma-separated If-None-Match list is matched."""
        data = b"\x89PNG fake avatar bytes"
        client = self._make_client(data)
        etag = f'"{hashlib.sha256(data).hexdigest()}"'
        resp = client.get(
            "/avatars/user",
            headers={"if-none-match": f'"stale-etag", {etag}'},
        )
        assert resp.status_code == 304

    def test_stale_etag_returns_200(self):
        """Verify a non-matching If-None-Match re-serves the avatar."""
        data = b"\x89PNG fake avatar bytes"
        client = self._make_client(data)
        resp = client.get("/avatars/user", headers={"if-none-match": '"stale-etag"'})
        assert resp.status_code == 200
        assert resp.content == data

    def test_no_avatar_404(self):
        """Verify a missing avatar still returns 404."""
        client = self._make_client(None)
        assert client.get("/avatars/user").status_code == 404


class _FakeFileAdmin:
    """Returns a canned file metadata record for the files route."""

    def __init__(self, file_info: dict | None) -> None:
        self._file_info = file_info

    async def send_operation(self, op: str, params: dict) -> dict:
        if op != "get_file_metadata":
            return {}
        return {"file": self._file_info}


class TestFileContentRoute:
    """Tests for file-content 304 via explicit If-None-Match (R7.2)."""

    def _make_client(self, tmp_path, monkeypatch):
        """Create a test client serving one on-disk file."""
        file_id = str(uuid.uuid4())
        file_path = tmp_path / f"{file_id}.txt"
        file_path.write_bytes(b"file body content")
        monkeypatch.setattr(files_paths, "uploaded_files_dir", lambda: tmp_path)
        admin = _FakeFileAdmin(
            {"original_name": f"{file_id}.txt", "mime_type": "text/plain"}
        )
        app = FastAPI()
        app.include_router(files_routes.files_router)
        app.dependency_overrides[_get_admin_client] = lambda: admin
        app.dependency_overrides[_require_auth] = lambda: object()
        return TestClient(app), file_id, file_path

    def test_content_has_etag_and_cache_control(self, tmp_path, monkeypatch):
        """Verify 200 carries the FileResponse ETag and max-age=86400."""
        client, file_id, _ = self._make_client(tmp_path, monkeypatch)
        resp = client.get(f"/files/{file_id}/content")
        assert resp.status_code == 200
        assert resp.content == b"file body content"
        etag = resp.headers["etag"]
        assert etag.startswith('"')
        assert etag.endswith('"')
        assert resp.headers["cache-control"] == "private, max-age=86400"

    def test_if_none_match_returns_304_empty(self, tmp_path, monkeypatch):
        """Verify If-None-Match with the emitted ETag yields a 304."""
        client, file_id, _ = self._make_client(tmp_path, monkeypatch)
        etag = client.get(f"/files/{file_id}/content").headers["etag"]
        resp = client.get(
            f"/files/{file_id}/content",
            headers={"if-none-match": etag},
        )
        assert resp.status_code == 304
        assert resp.content == b""
        assert resp.headers["etag"] == etag
        assert resp.headers["cache-control"] == "private, max-age=86400"

    def test_stale_etag_returns_200(self, tmp_path, monkeypatch):
        """Verify a non-matching If-None-Match re-serves the file."""
        client, file_id, _ = self._make_client(tmp_path, monkeypatch)
        resp = client.get(
            f"/files/{file_id}/content",
            headers={"if-none-match": '"stale-etag"'},
        )
        assert resp.status_code == 200
        assert resp.content == b"file body content"


class _FakeBackupAdmin:
    """Returns a canned backup result for the admin backup route."""

    def __init__(self) -> None:
        self.calls = 0

    async def create_backup(self) -> dict:
        self.calls += 1
        return {"backup_paths": {"db": "/backups/db.sqlite"}}


class TestBackupRouteRateLimit:
    """Tests for the backup route 429 with Retry-After (R3.3)."""

    def _make_client(self, monkeypatch):
        """Create a test client with a fresh backup rate limiter."""
        limiter = BackupRateLimiter()
        monkeypatch.setattr(admin_routes, "_backup_rate_limiter", limiter)
        admin = _FakeBackupAdmin()
        app = FastAPI()
        app.include_router(admin_routes.admin_router)
        register_problem_handlers(app)
        app.dependency_overrides[_get_admin_client] = lambda: admin
        app.dependency_overrides[_require_auth] = lambda: object()
        return TestClient(app), limiter, admin

    def test_allows_three_backups_per_hour(self, monkeypatch):
        """Verify three backup runs succeed within the hourly limit."""
        client, _, admin = self._make_client(monkeypatch)
        for _ in range(3):
            assert client.post("/admin/backup").status_code == 200
        assert admin.calls == 3

    def test_fourth_backup_429_with_retry_after(self, monkeypatch):
        """Verify the 4th backup gets 429 with Retry-After and problem body."""
        client, _, _ = self._make_client(monkeypatch)
        for _ in range(3):
            assert client.post("/admin/backup").status_code == 200
        resp = client.post("/admin/backup")
        assert resp.status_code == 429
        assert resp.headers["content-type"] == "application/problem+json"
        assert resp.json()["type"] == "urn:bimemo:problem:rate-limited"
        assert resp.json()["detail"] == "Backup limit reached (3 per hour)"
        retry_after = int(resp.headers["retry-after"])
        assert 0 < retry_after <= 3600

    def test_backup_retry_after_zero_when_window_empty(self):
        """Verify BackupRateLimiter.retry_after is 0 for an empty window."""
        assert BackupRateLimiter().retry_after() == 0

    def test_backup_retry_after_bounded_by_window(self):
        """Verify retry_after stays within the hourly window after records."""
        limiter = BackupRateLimiter()
        for _ in range(3):
            limiter.record()
        assert 0 < limiter.retry_after() <= 3600
