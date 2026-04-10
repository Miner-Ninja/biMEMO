"""Orchestrator subpackage — re-exports all public symbols."""

from importlib import import_module

from backend.ipc.models import (
    ConfirmResponse,
)
from backend.tools.handlers.system_status import (
    make_system_status_handler,
)

from .client import (
    DB_OPERATION_TIMEOUT,
    DB_TYPE_MAP,
    DBProxyClient,
    DBProxyError,
)
from .factories import (
    make_audit_fn,
    make_context_fn,
    make_evidence_bundle_fn,
    make_save_fn,
    make_save_record_fn,
)


__all__ = [
    "DB_OPERATION_TIMEOUT",
    "DB_TYPE_MAP",
    "FRONTEND_TYPE_MAP",
    "ConfirmResponse",
    "DBProxyClient",
    "DBProxyError",
    "OrchestratorIPCServer",
    "_InProcessWorker",
    "_connect_db",
    "_init_llm",
    "_setup_dialog_recorder_pipeline",
    "_setup_tools",
    "_start_bot",
    "make_audit_fn",
    "make_context_fn",
    "make_evidence_bundle_fn",
    "make_save_fn",
    "make_save_record_fn",
    "make_system_status_handler",
    "orchestrator_main",
    "seed_bot_configs_from_env",
    "seed_llm_config_from_env",
]


_IPC_SERVER_EXPORTS = {
    "FRONTEND_TYPE_MAP",
    "OrchestratorIPCServer",
}

_LAZY_EXPORT_MAP = {
    "_InProcessWorker": ".tool_setup",
    "_connect_db": ".process",
    "_init_llm": ".llm_setup",
    "_setup_dialog_recorder_pipeline": ".dialog_recorder_setup",
    "_setup_tools": ".tool_setup",
    "_start_bot": ".bot_launcher",
    "orchestrator_main": ".process",
    "seed_bot_configs_from_env": ".env_config_loader",
    "seed_llm_config_from_env": ".env_config_loader",
}


def __getattr__(name: str) -> object:
    """Load heavy orchestrator submodules only when one export is requested."""
    if name in _IPC_SERVER_EXPORTS:
        module = import_module(".ipc_server", __name__)
        return getattr(module, name)
    if name in _LAZY_EXPORT_MAP:
        module = import_module(_LAZY_EXPORT_MAP[name], __name__)
        return getattr(module, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
