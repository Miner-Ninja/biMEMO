"""Seed (or remove) a scratch conversation directly in the biMEMO app DB.

Runs INSIDE the bimemo backend container:

    docker cp helpers/seed_history.py <container>:/tmp/seed_history.py
    docker exec <container> python3 /tmp/seed_history.py            # seed
    docker exec <container> python3 /tmp/seed_history.py --delete <cid>
    docker exec <container> python3 /tmp/seed_history.py --gone <cid>

It uses the application's own DatabaseManager and decrypts the master key
from the same encrypted key file production uses (BIMEMO_ENCRYPTED_KEY_FILE_PATH
+ BIMEMO_UNLOCK_PASSWORD, both set in the container env by docker-compose),
so the seeded rows are exactly what the REST API and the UI will serve.

Seed mode creates ONE new conversation with a unique id and
E2E_SEED_MESSAGE_COUNT messages (default 250): user/assistant pairs with
strictly increasing created_at timestamps and unique short content
"seed N". The conversation id is printed as the LAST stdout line.

NOTE: only ever run this against a stack you are allowed to modify;
deleting the seeded conversation afterwards is the test's job.
"""

import os
import sys
import uuid
from datetime import datetime, timedelta, timezone

sys.path.insert(0, "/app")

from backend.security.database import DatabaseManager
from backend.security.encrypted_key_file import decrypt_master_key, read_key_file

# Child tables: rows keyed by a conversation_id column.
CONVERSATION_TABLES = (
    "messages",
    "records",
    "record_queue",
    "chat_settings",
    "chat_bridge_links",
    "agent_turns",
)


def open_db() -> DatabaseManager:
    key = decrypt_master_key(
        read_key_file(os.environ["BIMEMO_ENCRYPTED_KEY_FILE_PATH"]),
        os.environ["BIMEMO_UNLOCK_PASSWORD"],
    )
    db = DatabaseManager(os.environ["BIMEMO_DATABASE_PATH"])
    db.open(bytearray(key))
    return db


def timestamp_at(base: datetime, offset_seconds: int) -> str:
    """Same format the app's SQLite defaults use: %Y-%m-%dT%H:%M:%f (UTC)."""
    value = base + timedelta(seconds=offset_seconds)
    return value.strftime("%Y-%m-%dT%H:%M:%S.%f")


def seed(message_count: int, title: str) -> str:
    db = open_db()
    try:
        conversation_id = str(uuid.uuid4())
        # All timestamps in the past and strictly increasing per message.
        base = datetime.now(timezone.utc) - timedelta(seconds=message_count)

        db.execute(
            "INSERT INTO conversations "
            "(id, title, processing_status, processing_stage, created_at, updated_at) "
            "VALUES (?, ?, 'idle', '', ?, ?)",
            (
                conversation_id,
                title,
                timestamp_at(base, 0),
                timestamp_at(base, message_count - 1),
            ),
        )
        db.executemany(
            "INSERT INTO messages "
            "(id, conversation_id, role, content, channel, meta, created_at) "
            "VALUES (?, ?, ?, ?, NULL, NULL, ?)",
            [
                (
                    str(uuid.uuid4()),
                    conversation_id,
                    "user" if i % 2 == 0 else "assistant",
                    f"seed {i}",
                    timestamp_at(base, i),
                )
                for i in range(message_count)
            ],
        )
        db.commit()

        stored = db.execute(
            "SELECT COUNT(*) FROM messages WHERE conversation_id = ?",
            (conversation_id,),
        ).fetchone()[0]
        if stored != message_count:
            raise SystemExit(f"seed verification failed: {stored} != {message_count}")
        print(
            f"seeded conversation {conversation_id}: "
            f"{stored} messages (user/assistant pairs, increasing timestamps)"
        )
        print(conversation_id)
        return conversation_id
    finally:
        db.close()


def _delete_conversation_rows(db: DatabaseManager, conversation_id: str) -> None:
    for table in CONVERSATION_TABLES:
        db.execute(f"DELETE FROM {table} WHERE conversation_id = ?", (conversation_id,))
    db.execute("DELETE FROM conversations WHERE id = ?", (conversation_id,))
    db.commit()


def _count_conversation_rows(db: DatabaseManager, conversation_id: str) -> int:
    leftover = 0
    for table in CONVERSATION_TABLES:
        leftover += db.execute(
            f"SELECT COUNT(*) FROM {table} WHERE conversation_id = ?",
            (conversation_id,),
        ).fetchone()[0]
    leftover += db.execute(
        "SELECT COUNT(*) FROM conversations WHERE id = ?",
        (conversation_id,),
    ).fetchone()[0]
    return leftover


def remove(conversation_id: str) -> None:
    db = open_db()
    try:
        _delete_conversation_rows(db, conversation_id)
        print(f"deleted all rows for conversation {conversation_id}")
    finally:
        db.close()


def verify_gone(conversation_id: str) -> None:
    db = open_db()
    try:
        leftover = _count_conversation_rows(db, conversation_id)
        print("gone" if leftover == 0 else f"leftover rows: {leftover}")
    finally:
        db.close()


def main() -> None:
    args = sys.argv[1:]
    if args and args[0] == "--delete":
        if len(args) != 2:
            raise SystemExit("usage: seed_history.py --delete <conversation_id>")
        remove(args[1])
    elif args and args[0] == "--gone":
        if len(args) != 2:
            raise SystemExit("usage: seed_history.py --gone <conversation_id>")
        verify_gone(args[1])
    else:
        count = int(os.environ.get("E2E_SEED_MESSAGE_COUNT", "250"))
        title = os.environ.get("E2E_SEED_TITLE", "e2e scroll seed")
        seed(count, title)


if __name__ == "__main__":
    main()
