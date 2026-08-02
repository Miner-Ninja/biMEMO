"""Route subpackage for FastAPI app."""

from .admin import admin_router
from .admin_memory import memory_router
from .auth import auth_router
from .avatars import avatars_router
from .chat import chat_router
from .chat_bridge import chat_bridge_router
from .files import files_router
from .health import health_router
from .llm_providers import llm_router
from .scheduled_tasks import scheduled_tasks_router, tools_catalog_router
from .tts import tts_router
from .workspace import workspace_router


__all__ = [
    "admin_router",
    "auth_router",
    "avatars_router",
    "chat_bridge_router",
    "chat_router",
    "files_router",
    "health_router",
    "llm_router",
    "memory_router",
    "scheduled_tasks_router",
    "tools_catalog_router",
    "tts_router",
    "workspace_router",
]
