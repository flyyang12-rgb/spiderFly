"""Durable run deduplication and ordered, replayable events."""

import json
from pathlib import Path
import sqlite3
import threading


class Journal:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=FULL")
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS runs (
                id INTEGER PRIMARY KEY, descriptor TEXT NOT NULL,
                state TEXT NOT NULL DEFAULT 'claimed', ack_seq INTEGER NOT NULL DEFAULT 0,
                pid INTEGER, birth REAL, job_owned INTEGER NOT NULL DEFAULT 0,
                outcome TEXT, recovery_required INTEGER NOT NULL DEFAULT 0
            );
            CREATE UNIQUE INDEX IF NOT EXISTS one_active_run ON runs ((1)) WHERE state != 'acked';
            CREATE TABLE IF NOT EXISTS events (
                run_id INTEGER NOT NULL, seq INTEGER NOT NULL, payload TEXT NOT NULL,
                PRIMARY KEY(run_id, seq)
            );
            CREATE TABLE IF NOT EXISTS artifacts (
                run_id INTEGER NOT NULL, name TEXT NOT NULL, path TEXT NOT NULL,
                sha256 TEXT NOT NULL, uploaded INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY(run_id, name)
            );
        """)

    def close(self):
        self.db.close()

    def claim(self, run: dict) -> bool:
        descriptor = json.dumps(run, sort_keys=True, ensure_ascii=False)
        with self.lock, self.db:
            existing = self.db.execute("SELECT descriptor FROM runs WHERE id=?", (run["id"],)).fetchone()
            if existing:
                # The server may enrich display fields; execution identity must never change.
                previous = json.loads(existing["descriptor"])
                if previous["package_hash"] != run["package_hash"]:
                    raise ValueError("同一运行 ID 的任务包哈希发生变化")
                return False
            self.db.execute("INSERT INTO runs(id,descriptor) VALUES (?,?)", (run["id"], descriptor))
            return True

    def active(self) -> dict | None:
        with self.lock:
            row = self.db.execute("SELECT * FROM runs WHERE state != 'acked'").fetchone()
            return dict(row) if row else None

    def set_process(self, run_id: int, pid=None, birth=None, *, state="running", job_owned=False):
        with self.lock, self.db:
            self.db.execute("UPDATE runs SET state=?,pid=?,birth=?,job_owned=? WHERE id=?",
                            (state, pid, birth, int(job_owned), run_id))

    def append(self, run_id: int, kind: str, text="", **fields) -> int:
        with self.lock, self.db:
            return self._append(run_id, kind, text, **fields)

    def _append(self, run_id: int, kind: str, text="", **fields) -> int:
        seq = self.db.execute("SELECT COALESCE(MAX(seq),0)+1 FROM events WHERE run_id=?", (run_id,)).fetchone()[0]
        payload = {"seq": seq, "kind": kind, "text": text[:16000], **fields}
        self.db.execute("INSERT INTO events VALUES(?,?,?)", (run_id, seq, json.dumps(payload, ensure_ascii=False)))
        return seq

    def pending(self, run_id: int, limit=100) -> list[dict]:
        with self.lock:
            rows = self.db.execute("SELECT payload FROM events WHERE run_id=? AND seq > "
                                   "(SELECT ack_seq FROM runs WHERE id=?) ORDER BY seq LIMIT ?",
                                   (run_id, run_id, limit)).fetchall()
            return [json.loads(row[0]) for row in rows]

    def acknowledge(self, run_id: int, sequence: int):
        with self.lock, self.db:
            maximum = self.db.execute("SELECT COALESCE(MAX(seq),0) FROM events WHERE run_id=?", (run_id,)).fetchone()[0]
            if not isinstance(sequence, int) or sequence < 0 or sequence > maximum:
                raise ValueError("主控确认了不存在的事件序号")
            self.db.execute("UPDATE runs SET ack_seq=MAX(ack_seq,?) WHERE id=?", (sequence, run_id))
            self.db.execute("UPDATE runs SET state='acked' WHERE id=? AND state='terminal' AND ack_seq=?",
                            (run_id, maximum))

    def complete(self, run_id: int, status: str, text: str, exit_code=None):
        with self.lock, self.db:
            self.db.execute("UPDATE runs SET state='uploading',pid=NULL,birth=NULL,outcome=? WHERE id=?",
                            (json.dumps({"status": status, "text": text, "exit_code": exit_code}), run_id))

    def uncertain(self, run_id: int, text: str):
        with self.lock, self.db:
            row = self.db.execute("SELECT recovery_required FROM runs WHERE id=?", (run_id,)).fetchone()
            if not row[0]:
                self._append(run_id, "uncertain", text)
            self.db.execute("UPDATE runs SET state='uncertain',recovery_required=1 WHERE id=?", (run_id,))

    def add_artifact(self, run_id: int, name: str, path: Path, sha256: str):
        with self.lock, self.db:
            self.db.execute("INSERT OR IGNORE INTO artifacts(run_id,name,path,sha256) VALUES(?,?,?,?)",
                            (run_id, name, str(path), sha256))

    def pending_artifacts(self, run_id: int) -> list[dict]:
        with self.lock:
            return [dict(row) for row in self.db.execute("SELECT * FROM artifacts WHERE run_id=? AND uploaded=0", (run_id,))]

    def artifact_uploaded(self, run_id: int, name: str):
        with self.lock, self.db:
            self.db.execute("UPDATE artifacts SET uploaded=1 WHERE run_id=? AND name=?", (run_id, name))

    def artifact_skipped(self, run_id: int, name: str, detail: str):
        with self.lock, self.db:
            self._append(run_id, "stderr", detail)
            self.db.execute("UPDATE artifacts SET uploaded=2 WHERE run_id=? AND name=?", (run_id, name))
            row = self.db.execute("SELECT outcome FROM runs WHERE id=?", (run_id,)).fetchone()
            if row and row["outcome"]:
                outcome = json.loads(row["outcome"])
                if outcome["status"] == "succeeded":
                    outcome.update(status="failed", text="Python 执行结束，但产物回传失败，详见错误日志")
                    self.db.execute("UPDATE runs SET outcome=? WHERE id=?", (json.dumps(outcome), run_id))

    def terminal_after_upload(self, run_id: int):
        with self.lock, self.db:
            row = self.db.execute("SELECT state,outcome FROM runs WHERE id=?", (run_id,)).fetchone()
            if row["state"] != "uploading" or self.pending_artifacts(run_id):
                return
            outcome = json.loads(row["outcome"])
            self._append(run_id, "finished", **outcome)
            self.db.execute("UPDATE runs SET state='terminal' WHERE id=?", (run_id,))
