"""Tests for summary-to-record-to-message evidence helpers."""

import pytest
from backend.memory.evidence_chain import (
    EvidenceBundle,
    build_evidence_bundle,
    extract_supporting_record_ids,
    load_evidence_bundle_for_query,
    load_evidence_bundle_for_summary,
    select_summary_for_query,
)


def test_extract_supporting_record_ids_prefers_support_relations() -> None:
    """Typed support relations should win over older source-record fallback data."""
    summary = {
        "source_record_ids": ["record-fallback"],
        "record_relations": [
            {"record_id": "record-2", "relation": "conflicts"},
            {"record_id": "record-1", "relation": "supports"},
            {"record_id": "record-1", "relation": "supports"},
            {"record_id": "record-3", "relation": "supports"},
        ],
    }

    supporting_record_ids = extract_supporting_record_ids(summary)

    assert supporting_record_ids == ["record-1", "record-3"]


def test_extract_supporting_record_ids_falls_back_to_source_record_ids() -> None:
    """Legacy source-record links should still work when relations are absent."""
    summary = {
        "source_record_ids": ["record-2", "record-1", "record-2", ""],
    }

    supporting_record_ids = extract_supporting_record_ids(summary)

    assert supporting_record_ids == ["record-2", "record-1"]


def test_build_evidence_bundle_collects_records_and_messages() -> None:
    """Evidence bundle should preserve summary, record, and message link order."""
    summary = {
        "id": "summary-1",
        "source_record_ids": ["record-3"],
        "record_relations": [
            {"record_id": "record-2", "relation": "supports"},
            {"record_id": "record-1", "relation": "supports"},
        ],
    }
    records = [
        {
            "id": "record-1",
            "summary": "First record",
            "source_message_ids": ["msg-2", "msg-1", "msg-2"],
        },
        {
            "id": "record-2",
            "summary": "Second record",
            "source_message_ids": ["msg-3"],
        },
        {
            "id": "record-3",
            "summary": "Fallback record",
            "source_message_ids": ["msg-9"],
        },
    ]
    messages = [
        {"id": "msg-1", "role": "user", "content": "first"},
        {"id": "msg-2", "role": "assistant", "content": "second"},
        {"id": "msg-3", "role": "user", "content": "third"},
        {"id": "msg-9", "role": "assistant", "content": "unused"},
    ]

    evidence_bundle = build_evidence_bundle(summary, records, messages)

    assert isinstance(evidence_bundle, EvidenceBundle)
    assert evidence_bundle.summary == summary
    assert [record["id"] for record in evidence_bundle.records] == [
        "record-2",
        "record-1",
    ]
    assert [message["id"] for message in evidence_bundle.source_messages] == [
        "msg-3",
        "msg-2",
        "msg-1",
    ]


def test_build_evidence_bundle_ignores_missing_links() -> None:
    """Missing records or messages should not break evidence bundle assembly."""
    summary = {
        "id": "summary-1",
        "source_record_ids": ["record-missing", "record-1"],
    }
    records = [
        {
            "id": "record-1",
            "source_message_ids": ["msg-1", "msg-missing"],
        }
    ]
    messages = [
        {"id": "msg-1", "role": "user", "content": "hello"},
    ]

    evidence_bundle = build_evidence_bundle(summary, records, messages)

    assert [record["id"] for record in evidence_bundle.records] == ["record-1"]
    assert [message["id"] for message in evidence_bundle.source_messages] == ["msg-1"]


class _FakeEvidenceAdminClient:
    """Minimal async client stub for evidence bundle loading tests."""

    def __init__(
        self,
        *,
        counts: dict[str, int],
        summaries: list[dict],
        records: list[dict],
        messages: list[dict],
    ) -> None:
        """Store fixed responses for the admin loader."""
        self._counts = counts
        self._summaries = summaries
        self._records = records
        self._messages = messages

    async def count_memory_entries(self) -> dict[str, int]:
        """Return fixed memory counts."""
        return self._counts

    async def list_summaries_full(
        self,
        limit: int = 50,
        offset: int = 0,
    ) -> list[dict]:
        """Return fixed summaries sliced by the requested range."""
        return self._summaries[offset : offset + limit]

    async def list_records(
        self,
        limit: int = 50,
        offset: int = 0,
    ) -> list[dict]:
        """Return fixed records sliced by the requested range."""
        return self._records[offset : offset + limit]

    async def list_messages(
        self,
        limit: int = 50,
        offset: int = 0,
        conversation_id: str | None = None,
    ) -> list[dict]:
        """Return fixed messages sliced by the requested range."""
        del conversation_id
        return self._messages[offset : offset + limit]


@pytest.mark.asyncio
async def test_load_evidence_bundle_for_summary_returns_none_when_missing() -> None:
    """Loader should return None when the requested summary is absent."""
    admin_client = _FakeEvidenceAdminClient(
        counts={"messages": 1, "records": 1, "summaries": 1},
        summaries=[{"id": "summary-1"}],
        records=[],
        messages=[],
    )

    evidence_bundle = await load_evidence_bundle_for_summary(
        "summary-missing",
        admin_client,
    )

    assert evidence_bundle is None


@pytest.mark.asyncio
async def test_load_evidence_bundle_for_summary_uses_existing_admin_reads() -> None:
    """Loader should build the bundle from current summary, record, and message APIs."""
    admin_client = _FakeEvidenceAdminClient(
        counts={"messages": 3, "records": 2, "summaries": 1},
        summaries=[
            {
                "id": "summary-1",
                "record_relations": [
                    {"record_id": "record-1", "relation": "supports"},
                    {"record_id": "record-2", "relation": "supports"},
                ],
            }
        ],
        records=[
            {"id": "record-1", "source_message_ids": ["msg-2"]},
            {"id": "record-2", "source_message_ids": ["msg-1", "msg-3"]},
        ],
        messages=[
            {"id": "msg-1", "content": "first"},
            {"id": "msg-2", "content": "second"},
            {"id": "msg-3", "content": "third"},
        ],
    )

    evidence_bundle = await load_evidence_bundle_for_summary(
        "summary-1",
        admin_client,
    )

    assert evidence_bundle is not None
    assert evidence_bundle.summary["id"] == "summary-1"
    assert [record["id"] for record in evidence_bundle.records] == [
        "record-1",
        "record-2",
    ]
    assert [message["id"] for message in evidence_bundle.source_messages] == [
        "msg-2",
        "msg-1",
        "msg-3",
    ]


def test_select_summary_for_query_prefers_highest_token_overlap() -> None:
    """Query-based summary selection should pick the closest matching summary."""
    summaries = [
        {
            "id": "summary-1",
            "main_point": "User prefers detailed Rust debugging explanations.",
            "based_on": [],
        },
        {
            "id": "summary-2",
            "main_point": "User works with Kubernetes and Podman in production.",
            "based_on": [],
        },
    ]

    selected_summary = select_summary_for_query(
        "What did I say about Kubernetes in production?",
        summaries,
        [],
    )

    assert selected_summary is not None
    assert selected_summary["id"] == "summary-2"


def test_select_summary_for_query_falls_back_to_first_summary() -> None:
    """Low-signal queries should still prefer the stronger ranked summary."""
    summaries = [
        {
            "id": "summary-1",
            "main_point": "First summary",
            "based_on": [],
            "scope": "long_term",
            "created_at": "2026-01-10T10:00:00Z",
        },
        {
            "id": "summary-2",
            "main_point": "Second summary",
            "based_on": [],
            "record_relations": [
                {"record_id": "record-1", "relation": "supports"},
                {"record_id": "record-2", "relation": "supports"},
            ],
            "scope": "conversation",
            "trust_level": "high",
            "created_at": "2026-03-24T10:00:00Z",
        },
    ]

    selected_summary = select_summary_for_query(
        "What did I say about that?",
        summaries,
        [],
    )

    assert selected_summary is not None
    assert selected_summary["id"] == "summary-2"


def test_select_summary_for_query_penalizes_conflicted_summary() -> None:
    """Conflicted summaries should lose to equally relevant clean summaries."""
    summaries = [
        {
            "id": "summary-conflicted",
            "main_point": "User works with Kubernetes in production.",
            "based_on": [],
            "record_relations": [
                {"record_id": "record-1", "relation": "supports"},
                {"record_id": "record-2", "relation": "conflicts"},
            ],
            "trust_level": "high",
            "scope": "long_term",
        },
        {
            "id": "summary-clean",
            "main_point": "User works with Kubernetes in production.",
            "based_on": [],
            "record_relations": [
                {"record_id": "record-3", "relation": "supports"},
                {"record_id": "record-4", "relation": "supports"},
            ],
            "trust_level": "high",
            "scope": "long_term",
        },
    ]

    selected_summary = select_summary_for_query(
        "What did I say about Kubernetes in production?",
        summaries,
        [],
    )

    assert selected_summary is not None
    assert selected_summary["id"] == "summary-clean"


def test_select_summary_for_query_prefers_deeper_linked_evidence() -> None:
    """Deeper linked evidence should win when summary relevance is otherwise close."""
    summaries = [
        {
            "id": "summary-thin",
            "main_point": "User works with Kubernetes and Podman in production.",
            "record_relations": [{"record_id": "record-1", "relation": "supports"}],
            "trust_level": "high",
            "scope": "long_term",
        },
        {
            "id": "summary-deep",
            "main_point": "User works with Kubernetes and Podman in production.",
            "record_relations": [
                {"record_id": "record-2", "relation": "supports"},
                {"record_id": "record-3", "relation": "supports"},
            ],
            "trust_level": "high",
            "scope": "long_term",
        },
    ]
    records = [
        {"id": "record-1", "source_message_ids": []},
        {"id": "record-2", "source_message_ids": ["msg-1", "msg-2"]},
        {"id": "record-3", "source_message_ids": ["msg-3"]},
    ]

    selected_summary = select_summary_for_query(
        "What did I say about Kubernetes in production?",
        summaries,
        records,
    )

    assert selected_summary is not None
    assert selected_summary["id"] == "summary-deep"


@pytest.mark.asyncio
async def test_load_evidence_bundle_for_query_uses_best_matching_summary() -> None:
    """Query loader should resolve the bundle through the best summary match."""
    admin_client = _FakeEvidenceAdminClient(
        counts={"messages": 2, "records": 2, "summaries": 2},
        summaries=[
            {
                "id": "summary-1",
                "main_point": "User prefers detailed Rust debugging explanations.",
                "record_relations": [{"record_id": "record-1", "relation": "supports"}],
            },
            {
                "id": "summary-2",
                "main_point": "User works with Kubernetes and Podman in production.",
                "record_relations": [{"record_id": "record-2", "relation": "supports"}],
            },
        ],
        records=[
            {"id": "record-1", "source_message_ids": ["msg-1"]},
            {"id": "record-2", "source_message_ids": ["msg-2"]},
        ],
        messages=[
            {"id": "msg-1", "content": "rust"},
            {"id": "msg-2", "content": "kubernetes"},
        ],
    )

    evidence_bundle = await load_evidence_bundle_for_query(
        "What did I say about Kubernetes in production?",
        admin_client,
    )

    assert evidence_bundle is not None
    assert evidence_bundle.summary["id"] == "summary-2"
    assert [record["id"] for record in evidence_bundle.records] == ["record-2"]
    assert [message["id"] for message in evidence_bundle.source_messages] == ["msg-2"]


@pytest.mark.asyncio
async def test_load_evidence_bundle_for_query_ranks_records_by_query() -> None:
    """Query loader should promote the record that best matches the question."""
    admin_client = _FakeEvidenceAdminClient(
        counts={"messages": 2, "records": 2, "summaries": 1},
        summaries=[
            {
                "id": "summary-1",
                "main_point": "User works with Kubernetes and Podman in production.",
                "record_relations": [
                    {"record_id": "record-1", "relation": "supports"},
                    {"record_id": "record-2", "relation": "supports"},
                ],
            }
        ],
        records=[
            {
                "id": "record-1",
                "summary": "User runs Kubernetes in production.",
                "source_message_ids": ["msg-1"],
            },
            {
                "id": "record-2",
                "summary": "User plans a Podman migration.",
                "source_message_ids": ["msg-2"],
            },
        ],
        messages=[
            {"id": "msg-1", "content": "kubernetes"},
            {"id": "msg-2", "content": "podman"},
        ],
    )

    evidence_bundle = await load_evidence_bundle_for_query(
        "What did I say about Podman?",
        admin_client,
    )

    assert evidence_bundle is not None
    assert [record["id"] for record in evidence_bundle.records] == [
        "record-2",
        "record-1",
    ]
    assert [message["id"] for message in evidence_bundle.source_messages] == [
        "msg-2",
        "msg-1",
    ]


@pytest.mark.asyncio
async def test_load_evidence_bundle_for_query_returns_none_without_summaries() -> None:
    """Query loader should return None when no summaries exist yet."""
    admin_client = _FakeEvidenceAdminClient(
        counts={"messages": 0, "records": 0, "summaries": 0},
        summaries=[],
        records=[],
        messages=[],
    )

    evidence_bundle = await load_evidence_bundle_for_query(
        "What did I say about Kubernetes in production?",
        admin_client,
    )

    assert evidence_bundle is None
