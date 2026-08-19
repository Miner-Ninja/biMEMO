"""Workspace routes — file tree and workspace management API."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Request

from ..deps import _require_auth


logger = logging.getLogger(__name__)

workspace_router = APIRouter(prefix="/workspace", tags=["workspace"])


def _get_workspace_dirs() -> list[str]:
    """Get configured workspace directories.

    Defaults to the same safe dirs used by the orchestrator:
    data_dir (containing the database) and UPLOADED_FILES_DIR.
    """
    from backend.config import get_settings
    from backend.files.paths import UPLOADED_FILES_DIR

    settings = get_settings()
    # Use WORKSPACE_DIRS env var if set
    dirs_raw = getattr(settings, "workspace_dirs", None) or []
    if dirs_raw:
        return [str(Path(d).resolve()) for d in dirs_raw]
    # Default: same safe dirs as the orchestrator tool_setup
    data_dir = str(Path(settings.database_path).parent)
    return [data_dir, str(UPLOADED_FILES_DIR)]


@workspace_router.get("/tree")
async def get_workspace_tree(
    request: Request,
    path: str = "",
) -> dict[str, Any]:
    """List files and directories in the workspace.

    Args:
        path: Relative path within workspace (empty = root).
        request: Authenticated request (validated by _require_auth).

    Returns:
        List of items with name, type (file/dir), size, and path.
    """
    _require_auth(request)

    workspace_dirs = _get_workspace_dirs()
    if not workspace_dirs:
        return {"items": [], "path": path, "parent": None}

    # Resolve the target path within workspace dirs
    target = None
    for ws_dir in workspace_dirs:
        candidate = Path(ws_dir) / path
        if candidate.exists():
            target = candidate
            break

    if target is None or not target.exists():
        # Try to find in any workspace dir (for root)
        if not path:
            target = Path(workspace_dirs[0])
        else:
            raise HTTPException(
                status_code=404,
                detail=f"Path not found in workspace: {path}",
            )

    if not target.is_dir():
        raise HTTPException(
            status_code=400,
            detail=f"Not a directory: {path}",
        )

    # Determine which workspace dir this belongs to
    ws_base = None
    for ws_dir in workspace_dirs:
        try:
            target.relative_to(ws_dir)
            ws_base = Path(ws_dir)
            break
        except ValueError:
            continue

    if ws_base is None:
        ws_base = Path(workspace_dirs[0])

    items: list[dict[str, Any]] = []
    try:
        for item in sorted(target.iterdir()):
            rel_path = str(item.relative_to(ws_base))
            entry: dict[str, Any] = {
                "name": item.name,
                "type": "directory" if item.is_dir() else "file",
                "path": rel_path,
            }
            if item.is_file():
                try:
                    entry["size"] = item.stat().st_size
                except OSError:
                    entry["size"] = 0
            items.append(entry)
    except PermissionError as err:
        raise HTTPException(
            status_code=403,
            detail=f"Permission denied: {path}",
        ) from err

    # Build parent path
    parent = None
    if path:
        parent = str(Path(path).parent) or ""

    return {
        "items": items,
        "path": path,
        "parent": parent,
        "workspace": str(ws_base),
    }


@workspace_router.get("/dirs")
async def get_workspace_dirs_endpoint(request: Request) -> dict[str, list[str]]:
    """List configured workspace directories."""
    _require_auth(request)
    return {"directories": _get_workspace_dirs()}
