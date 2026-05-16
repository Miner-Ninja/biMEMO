"""Pydantic parameter models for DB Proxy operations.

Each model validates the parameters dict from a DBOperation message.
All models use ``extra="forbid"`` to reject unexpected fields.
"""

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class ForbidExtraParams(BaseModel):
    """Base parameter model that rejects unexpected fields."""

    model_config = ConfigDict(extra="forbid")


def _empty_string() -> str:
    """Return an empty string for named default-factory use."""
    return ""


class LimitOffsetParams(ForbidExtraParams):
    """Common pagination fields for list operations."""

    limit: int
    offset: int


class LimitOnlyParams(ForbidExtraParams):
    """Common single-limit field for bounded list operations."""

    limit: int


class ConversationIdParams(ForbidExtraParams):
    """Common conversation identifier field."""

    conversation_id: str


class FileIdParams(ForbidExtraParams):
    """Common file identifier field."""

    file_id: str


class BridgeLinkIdParams(ForbidExtraParams):
    """Common bridge link identifier field."""

    bridge_link_id: str


class SaveMessageParams(ForbidExtraParams):
    """Parameters for save_message operation."""

    conversation_id: str
    role: str
    content: str | list[dict[str, Any]]
    channel: str | None = None
    meta: str | None = None


class LoadRecentMessagesParams(ForbidExtraParams):
    """Parameters for load_recent_messages operation."""

    conversation_id: str
    limit: int = 50


class FulltextSearchParams(ForbidExtraParams):
    """Parameters for fulltext_search operation."""

    query: str
    table: str = "messages"
    limit: int = 20


class LoadContextParams(ForbidExtraParams):
    """Parameters for load_context operation."""

    session_id: str
    conversation_id: str = Field(min_length=1)
    summary_limit: int = 10
    record_limit: int = 20
    message_limit: int = 50
    message_search_limit: int = 10
    query: str | None = None
    query_embedding: list[float] | None = None


class SaveConversationSummaryParams(ForbidExtraParams):
    """Parameters for save_conversation_summary operation."""

    conversation_id: str = Field(min_length=1)
    summary: str


class WriteAuditParams(ForbidExtraParams):
    """Parameters for write_audit operation."""

    event_type: str
    tool_name: str | None = None
    parameters: str | None = None
    result: str | None = None
    zone: str | None = None
    duration_ms: float | None = None


class WriteMemoryRouteDiagnosticParams(ForbidExtraParams):
    """Parameters for write_memory_route_diagnostic operation."""

    session_id: str = Field(default_factory=_empty_string)
    conversation_id: str = Field(default_factory=_empty_string)
    message_id: str = Field(default_factory=_empty_string)
    channel: str = Field(default_factory=_empty_string)
    initial_intent: str
    initial_confidence: str
    initial_role_filter: str | None = None
    initial_source: str
    initial_signal_groups: list[str] = Field(default_factory=list)
    fallback_attempted: bool = False
    fallback_reason: str | None = None
    fallback_intent: str | None = None
    fallback_confidence: str | None = None
    fallback_role_filter: str | None = None
    fallback_source: str | None = None
    final_intent: str
    final_confidence: str
    final_role_filter: str | None = None
    final_source: str
    final_signal_groups: list[str] = Field(default_factory=list)
    route_mismatch: bool = False
    mismatch_label: str = "no_mismatch"
    used_long_term_mode: bool = False


class ListMemoryRouteDiagnosticsParams(LimitOffsetParams):
    """Parameters for list_memory_route_diagnostics operation."""

    limit: int = 50
    offset: int = 0
    final_intent: str | None = None
    mismatch_only: bool = False
    fallback_only: bool = False


class GetMemoryRouteDiagnosticsSummaryParams(ForbidExtraParams):
    """Parameters for get_memory_route_diagnostics_summary operation."""

    recent_hours: int | None = None


class WriteRecordProcessingDiagnosticParams(ForbidExtraParams):
    """Parameters for write_record_processing_diagnostic operation."""

    decision: str
    conversation_id: str = ""
    session_id: str = ""
    record_id: str = ""
    model: str = ""
    message_count: int = 0
    source_message_ids: list[str] = Field(default_factory=list)
    messages: list[str] = Field(default_factory=list)
    injection_flagged: bool = False
    summary: str = ""
    key_facts: list[str] = Field(default_factory=list)
    action_items: list[str] = Field(default_factory=list)
    memory_kinds: list[str] = Field(default_factory=list)
    prompt: list[dict[str, Any]] = Field(default_factory=list)
    raw_output: str = ""
    error: str = ""
    detail: str = ""


class WriteSummaryProcessingDiagnosticParams(ForbidExtraParams):
    """Parameters for write_summary_processing_diagnostic operation."""

    decision: str
    summary_id: str = ""
    model: str = ""
    record_count: int = 0
    source_record_ids: list[str] = Field(default_factory=list)
    records: list[dict[str, Any]] = Field(default_factory=list)
    prompt: list[dict[str, Any]] = Field(default_factory=list)
    raw_output: str = ""
    main_point: str = ""
    based_on: list[str] = Field(default_factory=list)
    trust_level: str = ""
    scope: str = ""
    record_relations: list[str] = Field(default_factory=list)
    original_scope: str = ""
    scope_adjustment_reason: str = ""
    error: str = ""
    detail: str = ""


class ListRecordProcessingDiagnosticsParams(LimitOnlyParams):
    """Parameters for list_record_processing_diagnostics operation."""

    limit: int = 100
    decision: str | None = None


class ListSummaryProcessingDiagnosticsParams(LimitOnlyParams):
    """Parameters for list_summary_processing_diagnostics operation."""

    limit: int = 100
    decision: str | None = None


class GetMemoryTimelineParams(LimitOffsetParams):
    """Parameters for get_memory_timeline operation."""

    entry_type: str = Field(default="", alias="type")
    sort: str = "desc"
    limit: int = 50
    offset: int = 0


class SaveRecordParams(ForbidExtraParams):
    """Parameters for save_record operation."""

    conversation_id: str
    session_id: str
    summary: str
    key_facts: list[str] = Field(default_factory=list)
    action_items: list[str] = Field(default_factory=list)
    memory_kinds: list[str] = Field(default_factory=list)
    source_message_ids: list[str] = Field(default_factory=list)


class MarkTaintedParams(ForbidExtraParams):
    """Parameters for mark_tainted_record operation."""

    record_id: str


class RecordRelationParams(ForbidExtraParams):
    """Typed relation between a summary and a source record."""

    record_id: str
    relation: str


class SaveSummaryParams(ForbidExtraParams):
    """Parameters for save_summary operation."""

    main_point: str
    based_on: list[str] = Field(default_factory=list)
    trust_level: str = ""
    summary_scope: str = "long_term"
    source_record_ids: list[str] = Field(default_factory=list)
    record_relations: list[RecordRelationParams] = Field(default_factory=list)


class MergeSummarySourcesParams(ForbidExtraParams):
    """Parameters for merge_summary_sources operation."""

    summary_id: str
    main_point: str = ""
    based_on: list[str] = Field(default_factory=list)
    trust_level: str = ""
    summary_scope: str = "long_term"
    trust_level_score: int | None = None
    source_record_ids: list[str] = Field(default_factory=list)
    record_relations: list[RecordRelationParams] = Field(default_factory=list)


class ListSummariesParams(LimitOffsetParams):
    """Parameters for list_summaries operation."""

    limit: int = 100
    offset: int = 0


class CreateConversationParams(ForbidExtraParams):
    """Parameters for create_conversation operation."""

    title: str = ""


class ListConversationsParams(LimitOffsetParams):
    """Parameters for list_conversations operation."""

    limit: int = 50
    offset: int = 0


class DeleteConversationParams(ConversationIdParams):
    """Parameters for delete_conversation operation."""


class UpdateConversationTitleParams(ConversationIdParams):
    """Parameters for update_conversation_title operation."""

    title: str = ""


class UpdateConversationProcessingStateParams(ConversationIdParams):
    """Parameters for update_conversation_processing_state operation."""

    processing_status: str = "idle"
    processing_stage: str = ""
    processing_started_at: str | None = None


class StoreCredentialParams(ForbidExtraParams):
    """Parameters for store_credential operation."""

    integration_id: str
    credential_type: str
    value: str


class GetCredentialParams(ForbidExtraParams):
    """Parameters for get_credential operation."""

    integration_id: str
    credential_type: str


class DeleteCredentialParams(ForbidExtraParams):
    """Parameters for delete_credential operation."""

    credential_id: str


class CreateIntegrationParams(ForbidExtraParams):
    """Parameters for create_integration operation."""

    name: str
    service_type: str
    config: dict[str, Any] = Field(default_factory=dict)


class DeleteIntegrationParams(ForbidExtraParams):
    """Parameters for delete_integration operation."""

    name: str


class GetIntegrationConfigParams(ForbidExtraParams):
    """Parameters for get_integration_config operation."""

    service_type: str


class StoreLLMConfigParams(ForbidExtraParams):
    """Parameters for store_llm_config operation."""

    service_type: str
    credentials: dict[str, str]


class GetLLMConfigMetaParams(ForbidExtraParams):
    """Parameters for get_llm_config_meta operation."""

    service_type: str


class CreateLLMConfigParams(ForbidExtraParams):
    """Parameters for create_llm_config operation."""

    service_type: str
    credentials: dict[str, str]


class UpdateLLMConfigParams(ForbidExtraParams):
    """Parameters for update_llm_config operation."""

    config_id: str
    credentials: dict[str, str]


class DeleteLLMConfigParams(ForbidExtraParams):
    """Parameters for delete_llm_config operation."""

    config_id: str


class ActivateLLMConfigParams(ForbidExtraParams):
    """Parameters for activate_llm_config operation."""

    config_id: str


class GetLLMConfigFullParams(ForbidExtraParams):
    """Parameters for get_llm_config_full operation."""

    config_id: str


class StorePasswordHashParams(ForbidExtraParams):
    """Parameters for store_password_hash operation."""

    password_hash: str


class StoreEmbeddingParams(ForbidExtraParams):
    """Parameters for store_embedding operation."""

    record_id: str
    embedding: list[float]


class FetchUntaintedRecordsParams(LimitOnlyParams):
    """Parameters for fetch_untainted_records operation."""

    limit: int = 50


class CountSummariesParams(ForbidExtraParams):
    """Parameters for count_distillations operation."""


class RecordRateLimitCallParams(ForbidExtraParams):
    """Parameters for record_rate_limit_call operation."""

    tool_name: str
    called_at: float


class GetRecentRateLimitCallsParams(ForbidExtraParams):
    """Parameters for get_recent_rate_limit_calls operation."""

    tool_name: str
    since: float


class InitVectorTableParams(ForbidExtraParams):
    """Parameters for init_vector_table operation."""

    embedding_model: str
    dimensions: int


class FetchUnembeddedRecordsParams(LimitOnlyParams):
    """Parameters for fetch_unembedded_records operation."""

    limit: int = 50


class StoreUserProfileParams(ForbidExtraParams):
    """Parameters for store_user_profile operation."""

    first_name: str = ""
    last_name: str = ""
    display_name: str = ""
    assistant_name: str = ""
    timezone: str = ""
    language: str = ""
    custom_instructions: str = ""


# --- Avatar operations ---


class StoreAvatarParams(ForbidExtraParams):
    """Parameters for store_avatar operation."""

    avatar_type: str
    data: str
    mime_type: str


class GetAvatarParams(ForbidExtraParams):
    """Parameters for get_avatar operation."""

    avatar_type: str


class DeleteAvatarParams(ForbidExtraParams):
    """Parameters for delete_avatar operation."""

    avatar_type: str


# --- Scheduled task operations ---


class CreateScheduledTaskParams(ForbidExtraParams):
    """Parameters for create_scheduled_task operation."""

    name: str
    task_type: str
    prompt: str
    schedule_kind: str
    schedule_expr: str
    next_run_at: str
    allowed_tools: str = "[]"
    conversation_id: str = ""
    deliver: str = "origin"
    repeat_limit: int | None = None
    timezone: str = "UTC"


class ListScheduledTasksParams(ForbidExtraParams):
    """Parameters for list_scheduled_tasks operation."""

    status: str = "all"


class UpdateScheduledTaskParams(ForbidExtraParams):
    """Parameters for update_scheduled_task operation."""

    task_id: str
    status: str | None = None
    next_run_at: str | None = None
    last_run_at: str | None = None
    last_status: str | None = None
    last_error: str | None = None
    repeat_count: int | None = None
    name: str | None = None
    prompt: str | None = None
    schedule_kind: str | None = None
    schedule_expr: str | None = None
    allowed_tools: str | None = None
    repeat_limit: int | None = None
    timezone: str | None = None


class DeleteScheduledTaskParams(ForbidExtraParams):
    """Parameters for delete_scheduled_task operation."""

    task_id: str


class GetDueTasksParams(ForbidExtraParams):
    """Parameters for get_due_tasks operation."""

    now: str


class AdvanceTaskParams(ForbidExtraParams):
    """Parameters for advance_task_next_run operation."""

    task_id: str
    next_run_at: str
    repeat_count: int


class RecordTaskRunParams(ForbidExtraParams):
    """Parameters for record_task_run operation."""

    task_id: str
    started_at: str
    finished_at: str
    status: str
    result_text: str = ""
    error_message: str = ""
    duration_ms: int = 0


class ListTaskRunsParams(ForbidExtraParams):
    """Parameters for list_task_runs operation."""

    task_id: str
    limit: int = 20


# --- Memory debug operations ---


class ListRecordsParams(LimitOffsetParams):
    """Parameters for list_records operation."""

    limit: int = 50
    offset: int = 0


class ListSummariesFullParams(LimitOffsetParams):
    """Parameters for list_summaries_full operation."""

    limit: int = 50
    offset: int = 0


class ListMessagesParams(LimitOffsetParams):
    """Parameters for list_messages operation."""

    limit: int = 50
    offset: int = 0
    conversation_id: str | None = None


class ListAuditLogParams(LimitOffsetParams):
    """Parameters for list_audit_log operation."""

    limit: int = 50
    offset: int = 0
    event_type: str | None = None


class CountMemoryEntriesParams(ForbidExtraParams):
    """Parameters for count_memory_entries operation."""


class CreateFileMetadataParams(ForbidExtraParams):
    """Parameters for create_file_metadata operation."""

    id: str
    original_name: str
    size: int
    mime_type: str


class DeleteFileMetadataParams(FileIdParams):
    """Parameters for delete_file_metadata operation."""


class GetFileMetadataParams(FileIdParams):
    """Parameters for get_file_metadata operation."""


class CreateChatBridgeLinkParams(ForbidExtraParams):
    """Parameters for create_chat_bridge_link operation."""

    conversation_id: str
    provider: str
    external_space_id: str
    external_channel_id: str
    created_by: str
    sync_direction: str = "bidirectional"
    status: str = "active"
    delivery_mode: str = "bot_only"
    persona_mode: str = "neutral"
    persona_user_id: str = ""


class ListChatBridgeLinksParams(LimitOffsetParams):
    """Parameters for list_chat_bridge_links operation."""

    limit: int = 100
    offset: int = 0
    provider: str | None = None
    conversation_id: str | None = None


class GetChatBridgeForConversationParams(ConversationIdParams):
    """Parameters for get_chat_bridge_for_conversation operation."""


class GetChatBridgeLinkParams(BridgeLinkIdParams):
    """Parameters for get_chat_bridge_link operation."""


class GetChatBridgeByExternalTargetParams(ForbidExtraParams):
    """Parameters for get_chat_bridge_by_external_target operation."""

    provider: str
    external_space_id: str
    external_channel_id: str


class DisableChatBridgeLinkParams(BridgeLinkIdParams):
    """Parameters for disable_chat_bridge_link operation."""


class EnableChatBridgeLinkParams(BridgeLinkIdParams):
    """Parameters for enable_chat_bridge_link operation."""


class UpdateChatBridgeLinkSettingsParams(BridgeLinkIdParams):
    """Parameters for update_chat_bridge_link_settings operation."""

    delivery_mode: str = "bot_only"
    persona_mode: str = "neutral"
    persona_user_id: str = ""


class CreateChatBridgeMessageLinkParams(BridgeLinkIdParams):
    """Parameters for create_chat_bridge_message_link operation."""

    message_id: str
    external_message_id: str
    direction: str


class ListChatBridgeMessageLinksParams(BridgeLinkIdParams, LimitOffsetParams):
    """Parameters for list_chat_bridge_message_links operation."""

    limit: int = 100
    offset: int = 0
    direction: str | None = None


class GetChatBridgeMessageLinkByExternalMessageParams(BridgeLinkIdParams):
    """Parameters for get_chat_bridge_message_link_by_external_message operation."""

    external_message_id: str


class GetChatBridgeMessageLinkByMessageIdParams(BridgeLinkIdParams):
    """Parameters for get_chat_bridge_message_link_by_message_id operation."""

    message_id: str


class FileContextChunkParams(ForbidExtraParams):
    """Stored chunk data for one persisted file context."""

    chunk_id: str
    chunk_index: int
    text: str
    line_start: int | None = None
    line_end: int | None = None
    estimated_tokens: int


class SaveFileContextParams(FileIdParams):
    """Parameters for save_file_context operation."""

    file_kind: str
    estimated_tokens: int
    processing_mode: str
    processing_reason: str = ""
    summary_overview: str = ""
    summary_structure: list[str] = Field(default_factory=list)
    summary_key_points: list[str] = Field(default_factory=list)
    summary_warnings: list[str] = Field(default_factory=list)
    chunks: list[FileContextChunkParams] = Field(default_factory=list)


class GetFileContextParams(FileIdParams):
    """Parameters for get_file_context operation."""


class SaveFileContextUsageParams(FileIdParams):
    """Parameters for save_file_context_usage operation."""

    processing_mode: str
    mode_reason: str = ""
    user_query: str = ""
    selected_chunk_ids: list[str] = Field(default_factory=list)
    selected_chunk_indexes: list[int] = Field(default_factory=list)


class SkillCreateParams(ForbidExtraParams):
    """Parameters for skill_create operation."""

    name: str
    title: str
    description: str
    trigger: str = ""
    procedure: str = "[]"
    errors: str = "[]"
    verification: str = ""
    tool_sequence: str = ""


class SkillSearchParams(ForbidExtraParams):
    """Parameters for skill_search operation."""

    query: str


class SkillUseParams(ForbidExtraParams):
    """Parameters for skill_use (increment use_count) operation."""

    skill_id: str


class SkillDeleteParams(ForbidExtraParams):
    """Parameters for skill_delete operation."""

    name: str


class SkillLoadParams(ForbidExtraParams):
    """Parameters for skill_load (get by name) operation."""

    name: str


class SkillUpdateParams(ForbidExtraParams):
    """Parameters for skill_update operation."""

    name: str
    title: str | None = None
    description: str | None = None
    trigger: str | None = None
    procedure: str | list | None = None
    errors: str | list | None = None
    verification: str | None = None
    enabled: int | None = None


class SkillListParams(ForbidExtraParams):
    """Parameters for skill_list operation."""


class ChatSettingEntry(ForbidExtraParams):
    """Single key-value entry for a chat setting."""

    key: str
    value: str


class ListChatSettingsParams(ForbidExtraParams):
    """Parameters for list_chat_settings operation."""

    conversation_id: str = ""


class UpsertChatSettingsParams(ConversationIdParams):
    """Parameters for upsert_chat_settings operation."""

    entries: list[ChatSettingEntry]


# --- Assistant permissions ---


class ListProjectRootsParams(ForbidExtraParams):
    """Parameters for list_project_roots operation."""


class AddProjectRootParams(ForbidExtraParams):
    """Parameters for add_project_root operation."""

    name: str = Field(min_length=1, max_length=120)
    root_path: str = Field(min_length=1, max_length=4096)


class ProjectRootIdParams(ForbidExtraParams):
    """Common project root identifier field."""

    id: str = Field(min_length=1, max_length=128)


class ResolveProjectRootParams(ProjectRootIdParams):
    """Parameters for resolve_project_root operation."""


class RenameProjectRootParams(ProjectRootIdParams):
    """Parameters for rename_project_root operation."""

    name: str = Field(min_length=1, max_length=120)


class CreateCapabilityGrantParams(ForbidExtraParams):
    """Parameters for create_capability_grant operation."""

    scope_type: str = Field(min_length=1, max_length=64)
    scope_value: str = Field(min_length=1, max_length=4096)
    capability: str = Field(min_length=1, max_length=128)
    decision: str = Field(min_length=1, max_length=64)
    channel: str = Field(default="", max_length=128)
    expires_at: str | None = None


class ListCapabilityGrantsParams(ForbidExtraParams):
    """Parameters for list_capability_grants operation."""

    scope_type: str | None = Field(default=None, max_length=64)
    scope_value: str | None = Field(default=None, max_length=4096)
    capability: str | None = Field(default=None, max_length=128)
    channel: str | None = Field(default=None, max_length=128)


class LookupCapabilityGrantParams(ForbidExtraParams):
    """Parameters for lookup_capability_grant operation."""

    scope_type: str = Field(min_length=1, max_length=64)
    scope_value: str = Field(min_length=1, max_length=4096)
    capability: str = Field(min_length=1, max_length=128)
    channel: str = Field(default="", max_length=128)


class RevokeCapabilityGrantParams(ForbidExtraParams):
    """Parameters for revoke_capability_grant operation."""

    id: str = Field(min_length=1, max_length=128)


class ExpireCapabilityGrantsParams(ForbidExtraParams):
    """Parameters for expire_capability_grants operation."""

    now: str | None = None


class CreateAgentRunParams(ForbidExtraParams):
    """Parameters for create_agent_run operation."""

    run_id: str | None = Field(default=None, max_length=128)
    conversation_id: str = Field(min_length=1, max_length=128)
    session_id: str = Field(default="", max_length=128)
    channel: str = Field(default="", max_length=128)
    request_id: str = Field(default="", max_length=128)
    status: str = Field(default="running", max_length=64)
    current_step: str = Field(default="", max_length=128)
    resume_payload: dict[str, Any] = Field(default_factory=dict)


class AgentRunIdParams(ForbidExtraParams):
    """Common agent run identifier field."""

    run_id: str = Field(min_length=1, max_length=128)


class GetAgentRunParams(AgentRunIdParams):
    """Parameters for get_agent_run operation."""


class CreateAgentRunEventParams(AgentRunIdParams):
    """Parameters for create_agent_run_event operation."""

    request_id: str = Field(default="", max_length=128)
    conversation_id: str = Field(min_length=1, max_length=128)
    event_type: str = Field(min_length=1, max_length=64)
    payload: dict[str, Any] = Field(default_factory=dict)


class ListAgentRunEventsParams(ForbidExtraParams):
    """Parameters for list_agent_run_events operation."""

    run_id: str | None = Field(default=None, max_length=128)
    request_id: str | None = Field(default=None, max_length=128)
    conversation_id: str | None = Field(default=None, max_length=128)
    after_sequence: int = Field(default=0, ge=0)
    limit: int = Field(default=200, ge=1, le=1000)


class UpdateAgentRunStatusParams(AgentRunIdParams):
    """Parameters for update_agent_run_status operation."""

    status: str = Field(min_length=1, max_length=64)
    current_step: str = Field(default="", max_length=128)
    resume_payload: dict[str, Any] | None = None


class ListPendingActionsParams(ForbidExtraParams):
    """Parameters for list_pending_actions operation."""

    run_id: str | None = Field(default=None, max_length=128)
    status: str | None = Field(default=None, max_length=64)
    channel: str | None = Field(default=None, max_length=128)


class CreatePendingActionParams(AgentRunIdParams):
    """Parameters for create_pending_action operation."""

    action_id: str | None = Field(default=None, max_length=128)
    tool_name: str = Field(min_length=1, max_length=128)
    parameters: dict[str, Any] = Field(default_factory=dict)
    channel: str = Field(default="", max_length=128)
    status: str = Field(default="waiting", max_length=64)
    preview: dict[str, Any] = Field(default_factory=dict)
    resume_snapshot: dict[str, Any] = Field(default_factory=dict)
    expires_at: str | None = None


class DecidePendingActionParams(ForbidExtraParams):
    """Parameters for decide_pending_action operation."""

    action_id: str = Field(min_length=1, max_length=128)
    decision: str = Field(min_length=1, max_length=64)
    channel: str = Field(default="", max_length=128)


class CompletePendingActionParams(ForbidExtraParams):
    """Parameters for complete_pending_action operation."""

    action_id: str = Field(min_length=1, max_length=128)
    status: str = Field(min_length=1, max_length=64)
    result: dict[str, Any] = Field(default_factory=dict)
    error_message: str = Field(default="", max_length=4096)


class GetPendingActionParams(ForbidExtraParams):
    """Parameters for get_pending_action operation."""

    action_id: str = Field(min_length=1, max_length=128)


class GetResumeSnapshotParams(ForbidExtraParams):
    """Parameters for get_resume_snapshot operation."""

    action_id: str = Field(default="", max_length=128)
    snapshot_id: str = Field(default="", max_length=128)


class ExpirePendingActionsParams(ForbidExtraParams):
    """Parameters for expire_pending_actions operation."""

    now: str | None = None


class PruneRuntimeStateParams(ForbidExtraParams):
    """Parameters for prune_runtime_state operation."""

    now: str | None = None
    snapshot_retention_hours: int = Field(default=24, ge=1, le=720)
    run_retention_days: int = Field(default=7, ge=1, le=365)
