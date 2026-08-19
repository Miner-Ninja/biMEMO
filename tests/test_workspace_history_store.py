"""Tests for workspace history store.

Covers in-memory fallback, save/get/clear operations.
"""

from backend.workspace.history_store import WorkspaceHistoryStore
from backend.workspace.history_tracker import WorkspaceHistoryEntry


def test_store_in_memory_save_and_get():
    """Saving an entry and reading it back returns the same turn and intent."""
    store = WorkspaceHistoryStore()
    entry = WorkspaceHistoryEntry(
        turn=1,
        timestamp="2024-01-01T00:00:00",
        files_read=["a.py"],
        user_intent="test",
    )
    import asyncio

    asyncio.run(store.save("conv1", entry))
    result = asyncio.run(store.get_all("conv1"))
    assert len(result) == 1
    assert result[0].turn == 1
    assert result[0].user_intent == "test"


def test_store_in_memory_multiple_entries():
    """Saves are listed by get_all and the newest N are returned by get_latest."""
    store = WorkspaceHistoryStore()

    async def run():
        for i in range(3):
            entry = WorkspaceHistoryEntry(
                turn=i,
                timestamp=f"2024-01-0{i}T00:00:00",
                files_read=[f"file{i}.py"],
            )
            await store.save("conv1", entry)

        all_entries = await store.get_all("conv1")
        assert len(all_entries) == 3

        latest = await store.get_latest("conv1", last_n=2)
        assert len(latest) == 2
        assert latest[0].turn == 1
        assert latest[1].turn == 2

    import asyncio

    asyncio.run(run())


def test_store_in_memory_clear():
    """Clearing a conversation removes all of its stored entries."""
    store = WorkspaceHistoryStore()

    async def run():
        entry = WorkspaceHistoryEntry(turn=1, timestamp="")
        await store.save("conv1", entry)
        assert len(await store.get_all("conv1")) == 1

        await store.clear("conv1")
        assert len(await store.get_all("conv1")) == 0

    import asyncio

    asyncio.run(run())


def test_store_empty_conversation():
    """Unsaved conversations return empty lists from get_all and get_latest."""
    store = WorkspaceHistoryStore()

    async def run():
        result = await store.get_all("nonexistent")
        assert result == []

        result = await store.get_latest("nonexistent")
        assert result == []

    import asyncio

    asyncio.run(run())


def test_store_multiple_conversations():
    """Entries saved to different conversation ids are kept isolated from each other."""
    store = WorkspaceHistoryStore()

    async def run():
        e1 = WorkspaceHistoryEntry(turn=1, timestamp="", files_read=["a.py"])
        e2 = WorkspaceHistoryEntry(turn=1, timestamp="", files_read=["b.py"])
        await store.save("conv1", e1)
        await store.save("conv2", e2)

        assert len(await store.get_all("conv1")) == 1
        assert len(await store.get_all("conv2")) == 1
        assert (await store.get_all("conv1"))[0].files_read == ["a.py"]
        assert (await store.get_all("conv2"))[0].files_read == ["b.py"]

    import asyncio

    asyncio.run(run())
