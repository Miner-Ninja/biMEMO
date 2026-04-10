"""Process entry points for biMEMO multi-process architecture.

Each subpackage/module in this package is a standalone process entry point:
- db_proxy/: Database proxy with named operations over IPC
- orchestrator/: LLM orchestration and tool execution
- fastapi_app/: HTTP/SSE frontend facade
- signing_proxy/: JWT signing and password authentication
- credential_proxy/: Credential and integration management
- supervisor: Process supervisor and health monitor
- targets: Socket paths, process targets, and health probes
"""
