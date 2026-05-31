"""Workspace routes — file tree and workspace management API."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request

from ..deps import _require_auth


logger = logging.getLogger(__name__)

workspace_router = APIRouter(prefix="/workspace", tags=["workspace"])


def _get_workspace_dirs() -> list[str]:
    """Get configured workspace directories.

    For now, returns dirs from env or defaults to current project root.
    Later this will be synced with WorkspaceDirs from the orchestrator.
    """
    from backend.config import get_settings

    settings = get_settings()
    # Use WORKSPACE_DIRS env var if set, otherwise default to project root
    dirs_raw = getattr(settings, "workspace_dirs", None) or []
    if dirs_raw:
        return [str(Path(d).resolve()) for d in dirs_raw]
    # Default: project root (where biMEMO lives)
    return [str(Path(__file__).resolve().parents[4].parent)]


@workspace_router.get("/tree")
async def get_workspace_tree(
    request: Request,
    path: str = "",
):
    """List files and directories in the workspace.

    Args:
        path: Relative path within workspace (empty = root).

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
    except PermissionError:
        raise HTTPException(
            status_code=403,
            detail=f"Permission denied: {path}",
        )

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
async def get_workspace_dirs_endpoint(request: Request):
    """List configured workspace directories."""
    _require_auth(request)
    return {"directories": _get_workspace_dirs()}
