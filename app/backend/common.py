"""Small stateless helpers shared across biMEMO backend subsystems.

Canonical JSON serialization, sync/async callback invocation, process
SIGTERM waiting, and user-profile timezone resolution. Standard library
only, so importing here never creates a dependency cycle.
"""
# ruff: noqa: ANN401

from __future__ import annotations

import asyncio
import inspect
import json
import logging
import signal
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo


if TYPE_CHECKING:
    from collections.abc import Callable, Mapping
    from typing import Any

logger = logging.getLogger(__name__)


def canonical_json(value: Any) -> str:
    """Serialize ``value`` in the stable canonical form.

    Sorted keys, compact separators, non-ASCII preserved, and NaN/Inf
    rejected. Use it wherever a byte-stable representation (or hash) of a
    JSON-able value is required.
    """
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


async def await_maybe(callback: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
    """Invoke a callback that may be sync or async and return its result.

    A coroutine function is awaited; a plain callable is called directly.
    A plain callable returning an awaitable is awaited as well.
    """
    if inspect.iscoroutinefunction(callback):
        return await callback(*args, **kwargs)
    result = callback(*args, **kwargs)
    if inspect.isawaitable(result):
        return await result
    return result


async def wait_for_sigterm() -> None:
    """Block until the process receives SIGTERM."""
    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()
    loop.add_signal_handler(signal.SIGTERM, stop_event.set)
    await stop_event.wait()


def resolve_user_timezone(profile: Mapping[str, Any]) -> ZoneInfo | None:
    """Return the user's ``ZoneInfo`` from their profile, or ``None``.

    The profile ``timezone`` value is an IANA name. A missing or invalid
    value yields ``None`` so the caller applies its own default.
    """
    tz_name = str(profile.get("timezone") or "")
    if not tz_name:
        return None
    try:
        return ZoneInfo(tz_name)
    except KeyError, ValueError:
        logger.warning(
            "Invalid timezone in user profile: %r (using UTC default)", tz_name
        )
        return None
