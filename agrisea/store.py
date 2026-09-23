"""SQLite 저장소 + 전문검색(FTS5 trigram, 2글자 이하 검색어는 LIKE 보조)."""
from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from .lexicon import match_issues, match_organizations
from .parser import MinutesDoc

SCHEMA = """
CREATE TABLE IF NOT EXISTS meetings (
    id TEXT PRIMARY KEY,
    dae TEXT, title TEXT, committee TEXT, date TEXT, class_name TEXT,
    session_no INTEGER, conf_no INTEGER,
    pdf_url TEXT, link_url TEXT, vod_url TEXT,
    agendas_json TEXT DEFAULT '[]',
    raw_json TEXT,
    text_status TEXT DEFAULT 'pending',   -- pending | parsed | failed | metadata_only
    is_sample INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS utterances (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    meeting_id TEXT REFERENCES meetings(id) ON DELETE CASCADE,
    idx INTEGER, speaker_name TEXT, speaker_role TEXT, speaker_type TEXT, org TEXT,
    agenda_idx INTEGER, text TEXT,
    is_question INTEGER, is_commitment INTEGER, is_data_request INTEGER,
    issues_json TEXT, orgs_json TEXT
);
CREATE INDEX IF NOT EXISTS ix_utt_meeting ON utterances(meeting_id, idx);
CREATE INDEX IF NOT EXISTS ix_utt_speaker ON utterances(speaker_name);
CREATE VIRTUAL TABLE IF NOT EXISTS utterances_fts USING fts5(
    text, content='utterances', content_rowid='id', tokenize='trigram'
);
CREATE TRIGGER IF NOT EXISTS utt_ai AFTER INSERT ON utterances BEGIN
    INSERT INTO utterances_fts(rowid, text) VALUES (new.id, new.text);
END;
CREATE TRIGGER IF NOT EXISTS utt_ad AFTER DELETE ON utterances BEGIN
    INSERT INTO utterances_fts(utterances_fts, rowid, text) VALUES('delete', old.id, old.text);
END;
CREATE TABLE IF NOT EXISTS summaries (
    meeting_id TEXT, kind TEXT, payload TEXT, PRIMARY KEY (meeting_id, kind)
);
"""


class Store:
    def __init__(self, path: Path | str):
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.conn.executescript(SCHEMA)

    @contextmanager
    def tx(self) -> Iterator[sqlite3.Connection]:
        with self.conn:
            yield self.conn

    # ── 쓰기 ─────────────────────────────────────────────
    def upsert_meeting(self, m: dict[str, Any], is_sample: bool = False) -> None:
        with self.tx() as c:
            c.execute(
                """INSERT INTO meetings (id, dae, title, committee, date, class_name, session_no,
                       conf_no, pdf_url, link_url, vod_url, agendas_json, raw_json, is_sample)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(id) DO UPDATE SET
                       dae=excluded.dae, title=excluded.title, committee=excluded.committee,
                       date=excluded.date, class_name=excluded.class_name,
                       session_no=excluded.session_no, conf_no=excluded.conf_no,
                       pdf_url=excluded.pdf_url, link_url=excluded.link_url,
                       vod_url=excluded.vod_url, agendas_json=excluded.agendas_json,
                       raw_json=excluded.raw_json""",
                (m["meeting_id"], m.get("dae"), m.get("title"), m.get("committee"), m.get("date"),
                 m.get("class_name"), m.get("session_no"), m.get("conf_no"), m.get("pdf_url"),
                 m.get("link_url"), m.get("vod_url"),
                 json.dumps(m.get("agendas", []), ensure_ascii=False),
                 json.dumps(m.get("raw", {}), ensure_ascii=False), int(is_sample)),
            )

    def save_minutes(self, meeting_id: str, doc: MinutesDoc) -> None:
        with self.tx() as c:
            c.execute("DELETE FROM utterances WHERE meeting_id=?", (meeting_id,))
            c.execute("DELETE FROM summaries WHERE meeting_id=?", (meeting_id,))
            if doc.agendas:
                row = c.execute("SELECT agendas_json FROM meetings WHERE id=?",
                                (meeting_id,)).fetchone()
                existing = json.loads(row["agendas_json"]) if row else []
                if not existing:
                    c.execute("UPDATE meetings SET agendas_json=? WHERE id=?",
                              (json.dumps(doc.agendas, ensure_ascii=False), meeting_id))
            for u in doc.utterances:
                c.execute(
                    """INSERT INTO utterances (meeting_id, idx, speaker_name, speaker_role,
                           speaker_type, org, agenda_idx, text, is_question, is_commitment,
                           is_data_request, issues_json, orgs_json)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (meeting_id, u.idx, u.speaker_name, u.speaker_role, u.speaker_type, u.org,
                     u.agenda_idx, u.text, int(u.is_question), int(u.is_commitment),
                     int(u.is_data_request),
                     json.dumps(match_issues(u.text), ensure_ascii=False),
                     json.dumps(match_organizations(u.text), ensure_ascii=False)),
                )
            c.execute("UPDATE meetings SET text_status=? WHERE id=?",
                      ("parsed" if doc.utterances else "failed", meeting_id))

    def set_status(self, meeting_id: str, status: str) -> None:
        with self.tx() as c:
            c.execute("UPDATE meetings SET text_status=? WHERE id=?", (status, meeting_id))

    def save_summary(self, meeting_id: str, kind: str, payload: dict) -> None:
        with self.tx() as c:
            c.execute("INSERT OR REPLACE INTO summaries VALUES (?,?,?)",
                      (meeting_id, kind, json.dumps(payload, ensure_ascii=False)))

    # ── 읽기 ─────────────────────────────────────────────
    @staticmethod
    def _meeting(row: sqlite3.Row) -> dict:
        d = dict(row)
        d["agendas"] = json.loads(d.pop("agendas_json") or "[]")
        d.pop("raw_json", None)
        return d

    @staticmethod
    def _utt(row: sqlite3.Row) -> dict:
        d = dict(row)
        d["issues"] = json.loads(d.pop("issues_json") or "{}")
        d["orgs"] = json.loads(d.pop("orgs_json") or "{}")
        for k in ("is_question", "is_commitment", "is_data_request"):
            d[k] = bool(d[k])
        return d

    def meetings(self, date_from: str = "", date_to: str = "", q: str = "",
                 status: str = "") -> list[dict]:
        sql, args = "SELECT * FROM meetings WHERE 1=1", []
        if date_from:
            sql += " AND date >= ?"; args.append(date_from)
        if date_to:
            sql += " AND date <= ?"; args.append(date_to)
        if q:
            sql += " AND (title LIKE ? OR agendas_json LIKE ?)"; args += [f"%{q}%"] * 2
        if status:
            sql += " AND text_status = ?"; args.append(status)
        sql += " ORDER BY date DESC, id DESC"
        return [self._meeting(r) for r in self.conn.execute(sql, args)]

    def meeting(self, meeting_id: str) -> dict | None:
        r = self.conn.execute("SELECT * FROM meetings WHERE id=?", (meeting_id,)).fetchone()
        return self._meeting(r) if r else None

    def utterances(self, meeting_id: str | None = None, speaker: str = "", org: str = "",
                   only_commitments: bool = False, only_questions: bool = False) -> list[dict]:
        sql = ("SELECT u.*, m.date AS meeting_date, m.title AS meeting_title "
               "FROM utterances u JOIN meetings m ON m.id = u.meeting_id WHERE 1=1")
        args: list[Any] = []
        if meeting_id:
            sql += " AND u.meeting_id=?"; args.append(meeting_id)
        if speaker:
            sql += " AND u.speaker_name=?"; args.append(speaker)
        if org:
            sql += " AND (u.org=? OR u.orgs_json LIKE ?)"; args += [org, f'%"{org}"%']
        if only_commitments:
            sql += " AND u.is_commitment=1"
        if only_questions:
            sql += " AND u.is_question=1"
        sql += " ORDER BY m.date, u.meeting_id, u.idx"
        return [self._utt(r) for r in self.conn.execute(sql, args)]

    def search(self, query: str, limit: int = 50, speaker_type: str = "",
               date_from: str = "", date_to: str = "") -> list[dict]:
        terms = [t for t in query.split() if t]
        if not terms:
            return []
        long_terms = [t for t in terms if len(t) >= 3]
        short_terms = [t for t in terms if len(t) < 3]
        sql = ("SELECT u.*, m.date AS meeting_date, m.title AS meeting_title "
               "FROM utterances u JOIN meetings m ON m.id = u.meeting_id WHERE 1=1")
        args: list[Any] = []
        if long_terms:
            fts = " AND ".join('"' + t.replace('"', '""') + '"' for t in long_terms)
            sql += " AND u.id IN (SELECT rowid FROM utterances_fts WHERE utterances_fts MATCH ?)"
            args.append(fts)
        for t in short_terms:
            sql += " AND u.text LIKE ?"; args.append(f"%{t}%")
        if speaker_type:
            sql += " AND u.speaker_type=?"; args.append(speaker_type)
        if date_from:
            sql += " AND m.date >= ?"; args.append(date_from)
        if date_to:
            sql += " AND m.date <= ?"; args.append(date_to)
        sql += " ORDER BY m.date DESC, u.idx LIMIT ?"
        args.append(limit)
        return [self._utt(r) for r in self.conn.execute(sql, args)]

    def summary(self, meeting_id: str, kind: str) -> dict | None:
        r = self.conn.execute("SELECT payload FROM summaries WHERE meeting_id=? AND kind=?",
                              (meeting_id, kind)).fetchone()
        return json.loads(r["payload"]) if r else None

    def speakers(self) -> list[dict]:
        sql = """SELECT speaker_name, speaker_role, speaker_type, org,
                        COUNT(*) AS n_utts, COUNT(DISTINCT meeting_id) AS n_meetings,
                        SUM(is_question) AS n_questions, SUM(is_commitment) AS n_commitments
                 FROM utterances GROUP BY speaker_name, speaker_type
                 ORDER BY n_utts DESC"""
        return [dict(r) for r in self.conn.execute(sql)]

    def stats(self) -> dict:
        c = self.conn
        return {
            "meetings": c.execute("SELECT COUNT(*) FROM meetings").fetchone()[0],
            "parsed": c.execute("SELECT COUNT(*) FROM meetings WHERE text_status='parsed'").fetchone()[0],
            "utterances": c.execute("SELECT COUNT(*) FROM utterances").fetchone()[0],
            "commitments": c.execute("SELECT COUNT(*) FROM utterances WHERE is_commitment=1").fetchone()[0],
            "questions": c.execute("SELECT COUNT(*) FROM utterances WHERE is_question=1").fetchone()[0],
            "samples": c.execute("SELECT COUNT(*) FROM meetings WHERE is_sample=1").fetchone()[0],
            "date_min": c.execute("SELECT MIN(date) FROM meetings").fetchone()[0],
            "date_max": c.execute("SELECT MAX(date) FROM meetings").fetchone()[0],
        }
