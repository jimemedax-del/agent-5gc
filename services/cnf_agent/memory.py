"""SQLite session snapshots, append-only events and execution deduplication."""
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3

from .schemas import Session


class ConcurrentUpdate(RuntimeError):
    pass


class Memory:
    def __init__(self, path: str | Path):
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path, timeout=5)
        self.db.row_factory = sqlite3.Row
        self.db.executescript('''
            CREATE TABLE IF NOT EXISTS sessions (
                id TEXT PRIMARY KEY, revision INTEGER NOT NULL, payload TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS events (
                sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL, at TEXT NOT NULL, kind TEXT NOT NULL, payload TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS operations (
                id TEXT PRIMARY KEY, session_id TEXT NOT NULL, requirement_version INTEGER NOT NULL,
                UNIQUE(session_id, requirement_version));
        ''')

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    def close(self):
        self.db.close()

    def create(self, session: Session) -> Session:
        with self.db:
            self.db.execute('INSERT INTO sessions VALUES (?, ?, ?)',
                            (session.id, session.revision, session.model_dump_json()))
            self._event(session.id, 'session_created', session.model_dump(mode='json'))
        return session

    def load(self, session_id: str) -> Session:
        row = self.db.execute('SELECT payload FROM sessions WHERE id=?', (session_id,)).fetchone()
        if row is None:
            raise KeyError('Session does not exist')
        return Session.model_validate_json(row['payload'])

    def save(self, session: Session, kind: str, payload: dict | None = None,
             claim_operation: bool = False) -> Session:
        updated = session.model_copy(update={'revision': session.revision + 1})
        with self.db:
            cursor = self.db.execute(
                'UPDATE sessions SET revision=?, payload=? WHERE id=? AND revision=?',
                (updated.revision, updated.model_dump_json(), session.id, session.revision))
            if cursor.rowcount != 1:
                raise ConcurrentUpdate('Session changed; reload before continuing')
            if claim_operation:
                self.db.execute('INSERT INTO operations VALUES (?, ?, ?)',
                                (session.operation_id, session.id, session.requirement_version))
            self._event(session.id, kind, payload if payload is not None else updated.model_dump(mode='json'))
        return updated

    def _event(self, session_id: str, kind: str, payload: dict):
        self.db.execute('INSERT INTO events(session_id, at, kind, payload) VALUES (?, ?, ?, ?)',
                        (session_id, datetime.now(timezone.utc).isoformat(), kind,
                         json.dumps(payload, ensure_ascii=False, allow_nan=False)))

    def events(self, session_id: str) -> list[dict]:
        session = self.load(session_id)
        rows = self.db.execute('SELECT * FROM events WHERE session_id=? ORDER BY sequence', (session_id,))
        return [dict(sequence=row['sequence'], session_id=row['session_id'], at=row['at'],
                     kind=row['kind'], mode=session.mode, payload=json.loads(row['payload'])) for row in rows]
