"""Tests for scanner.py - JSONL parsing, DB operations, and scanning."""

import json
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path

import scanner
from pricing import calc_cost
from scanner import (
    get_db, init_db, project_name_from_cwd, parse_jsonl_file,
    aggregate_sessions, upsert_sessions, insert_turns, scan,
)

_real_cowork_dir = scanner.COWORK_SESSIONS_DIR


def setUpModule():
    # scan() also reads the Claude desktop app's Cowork dir; keep the
    # developer's real sessions out of these tests.
    scanner.COWORK_SESSIONS_DIR = Path(tempfile.mkdtemp()) / "no-cowork"


def tearDownModule():
    scanner.COWORK_SESSIONS_DIR = _real_cowork_dir


class TestProjectNameFromCwd(unittest.TestCase):
    def test_two_components(self):
        self.assertEqual(project_name_from_cwd("/home/user/myproject"), "user/myproject")

    def test_deep_path(self):
        self.assertEqual(project_name_from_cwd("/a/b/c/d"), "c/d")

    def test_single_component(self):
        self.assertEqual(project_name_from_cwd("/root"), "/root")

    def test_windows_path(self):
        self.assertEqual(project_name_from_cwd("C:\\Users\\me\\project"), "me/project")

    def test_trailing_slash(self):
        self.assertEqual(project_name_from_cwd("/home/user/project/"), "user/project")

    def test_empty_string(self):
        self.assertEqual(project_name_from_cwd(""), "unknown")

    def test_none(self):
        self.assertEqual(project_name_from_cwd(None), "unknown")


def _make_assistant_record(session_id="sess-1", model="claude-sonnet-4-6",
                           input_tokens=100, output_tokens=50,
                           cache_read=10, cache_creation=5,
                           timestamp="2026-04-08T10:00:00Z",
                           cwd="/home/user/project",
                           message_id="", cache_creation_1h=None):
    usage = {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "cache_read_input_tokens": cache_read,
        "cache_creation_input_tokens": cache_creation,
    }
    if cache_creation_1h is not None:
        usage["cache_creation"] = {
            "ephemeral_5m_input_tokens": cache_creation - cache_creation_1h,
            "ephemeral_1h_input_tokens": cache_creation_1h,
        }
    msg = {
        "model": model,
        "usage": usage,
        "content": [],
    }
    if message_id:
        msg["id"] = message_id
    return json.dumps({
        "type": "assistant",
        "sessionId": session_id,
        "timestamp": timestamp,
        "cwd": cwd,
        "message": msg,
    })


def _make_user_record(session_id="sess-1", timestamp="2026-04-08T09:59:00Z",
                      cwd="/home/user/project"):
    return json.dumps({
        "type": "user",
        "sessionId": session_id,
        "timestamp": timestamp,
        "cwd": cwd,
    })


class TestParseJsonlFile(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()

    def _write_jsonl(self, filename, lines):
        path = os.path.join(self.tmpdir, filename)
        with open(path, "w") as f:
            for line in lines:
                f.write(line + "\n")
        return path

    def test_basic_parsing(self):
        path = self._write_jsonl("test.jsonl", [
            _make_user_record(),
            _make_assistant_record(),
        ])
        metas, turns, line_count = parse_jsonl_file(path)
        self.assertEqual(len(metas), 1)
        self.assertEqual(len(turns), 1)
        self.assertEqual(metas[0]["session_id"], "sess-1")
        self.assertEqual(turns[0]["input_tokens"], 100)
        self.assertEqual(turns[0]["output_tokens"], 50)
        self.assertEqual(line_count, 2)

    def test_skips_zero_token_records(self):
        path = self._write_jsonl("test.jsonl", [
            _make_assistant_record(input_tokens=0, output_tokens=0,
                                   cache_read=0, cache_creation=0),
        ])
        _, turns, _ = parse_jsonl_file(path)
        self.assertEqual(len(turns), 0)

    def test_skips_non_assistant_user_types(self):
        path = self._write_jsonl("test.jsonl", [
            json.dumps({"type": "system", "sessionId": "s1"}),
            _make_assistant_record(session_id="s1"),
        ])
        metas, turns, _ = parse_jsonl_file(path)
        self.assertEqual(len(turns), 1)

    def test_handles_malformed_json(self):
        path = self._write_jsonl("test.jsonl", [
            "not valid json",
            _make_assistant_record(),
        ])
        _, turns, _ = parse_jsonl_file(path)
        self.assertEqual(len(turns), 1)

    def test_handles_empty_file(self):
        path = self._write_jsonl("test.jsonl", [])
        metas, turns, _ = parse_jsonl_file(path)
        self.assertEqual(len(metas), 0)
        self.assertEqual(len(turns), 0)

    def test_multiple_sessions(self):
        path = self._write_jsonl("test.jsonl", [
            _make_assistant_record(session_id="s1"),
            _make_assistant_record(session_id="s2"),
        ])
        metas, turns, _ = parse_jsonl_file(path)
        self.assertEqual(len(metas), 2)
        self.assertEqual(len(turns), 2)

    def test_session_timestamps_tracked(self):
        path = self._write_jsonl("test.jsonl", [
            _make_user_record(timestamp="2026-04-08T09:00:00Z"),
            _make_assistant_record(timestamp="2026-04-08T09:05:00Z"),
            _make_assistant_record(timestamp="2026-04-08T09:10:00Z"),
        ])
        metas, _, _ = parse_jsonl_file(path)
        self.assertEqual(metas[0]["first_timestamp"], "2026-04-08T09:00:00Z")
        self.assertEqual(metas[0]["last_timestamp"], "2026-04-08T09:10:00Z")

    def test_tool_name_extracted(self):
        record = json.dumps({
            "type": "assistant",
            "sessionId": "s1",
            "timestamp": "2026-04-08T10:00:00Z",
            "cwd": "/tmp",
            "message": {
                "model": "claude-sonnet-4-6",
                "usage": {"input_tokens": 100, "output_tokens": 50,
                          "cache_read_input_tokens": 0,
                          "cache_creation_input_tokens": 0},
                "content": [{"type": "tool_use", "name": "Read"}],
            },
        })
        path = self._write_jsonl("test.jsonl", [record])
        _, turns, _ = parse_jsonl_file(path)
        self.assertEqual(turns[0]["tool_name"], "Read")


class TestMessageIdDedup(unittest.TestCase):
    """Test deduplication of streaming events by message.id."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()

    def _write_jsonl(self, filename, lines):
        path = os.path.join(self.tmpdir, filename)
        with open(path, "w") as f:
            for line in lines:
                f.write(line + "\n")
        return path

    def test_streaming_events_deduped(self):
        """Multiple records with same message.id should produce one turn."""
        path = self._write_jsonl("test.jsonl", [
            # Streaming event 1: partial usage
            _make_assistant_record(message_id="msg-abc", input_tokens=50, output_tokens=10),
            # Streaming event 2: more usage (same message)
            _make_assistant_record(message_id="msg-abc", input_tokens=100, output_tokens=50),
            # Streaming event 3: final usage (same message)
            _make_assistant_record(message_id="msg-abc", input_tokens=150, output_tokens=80),
        ])
        _, turns, _ = parse_jsonl_file(path)
        self.assertEqual(len(turns), 1)
        # Last record wins (has final tallies)
        self.assertEqual(turns[0]["input_tokens"], 150)
        self.assertEqual(turns[0]["output_tokens"], 80)
        self.assertEqual(turns[0]["message_id"], "msg-abc")

    def test_different_message_ids_kept(self):
        """Records with different message.id are separate turns."""
        path = self._write_jsonl("test.jsonl", [
            _make_assistant_record(message_id="msg-1", input_tokens=100),
            _make_assistant_record(message_id="msg-2", input_tokens=200),
        ])
        _, turns, _ = parse_jsonl_file(path)
        self.assertEqual(len(turns), 2)

    def test_records_without_message_id_kept(self):
        """Records without message.id are kept as-is (no dedup)."""
        path = self._write_jsonl("test.jsonl", [
            _make_assistant_record(input_tokens=100),
            _make_assistant_record(input_tokens=200),
        ])
        _, turns, _ = parse_jsonl_file(path)
        self.assertEqual(len(turns), 2)

    def test_mixed_with_and_without_ids(self):
        """Mix of records with and without message.id."""
        path = self._write_jsonl("test.jsonl", [
            _make_assistant_record(message_id="msg-1", input_tokens=50),
            _make_assistant_record(message_id="msg-1", input_tokens=100),  # deduped
            _make_assistant_record(input_tokens=200),  # no id, kept
        ])
        _, turns, _ = parse_jsonl_file(path)
        self.assertEqual(len(turns), 2)  # 1 deduped + 1 without id
        token_sums = sorted([t["input_tokens"] for t in turns])
        self.assertEqual(token_sums, [100, 200])


class TestMessageIdDedupIntegration(unittest.TestCase):
    """Integration test: dedup across scan cycles."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.projects_dir = Path(self.tmpdir) / "projects" / "user" / "proj"
        self.projects_dir.mkdir(parents=True)
        self.db_path = Path(self.tmpdir) / "usage.db"
        self.filepath = self.projects_dir / "sess-1.jsonl"

    def test_streaming_dedup_reduces_turn_count(self):
        """3 streaming events for 2 messages should produce 2 turns."""
        with open(self.filepath, "w") as f:
            f.write(_make_user_record(session_id="sess-1") + "\n")
            f.write(_make_assistant_record(session_id="sess-1",
                                           message_id="msg-1",
                                           input_tokens=50, output_tokens=20) + "\n")
            f.write(_make_assistant_record(session_id="sess-1",
                                           message_id="msg-1",
                                           input_tokens=100, output_tokens=50) + "\n")
            f.write(_make_assistant_record(session_id="sess-1",
                                           message_id="msg-2",
                                           input_tokens=200, output_tokens=100) + "\n")

        result = scan(projects_dir=self.projects_dir.parent.parent,
                      db_path=self.db_path, verbose=False)
        self.assertEqual(result["turns"], 2)

        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        turns = conn.execute("SELECT * FROM turns ORDER BY input_tokens").fetchall()
        self.assertEqual(len(turns), 2)
        # msg-1: last record wins (100/50), msg-2: 200/100
        self.assertEqual(turns[0]["input_tokens"], 100)
        self.assertEqual(turns[1]["input_tokens"], 200)
        # Session totals should reflect deduped values
        session = conn.execute("SELECT * FROM sessions").fetchone()
        self.assertEqual(session["total_input_tokens"], 300)  # 100 + 200
        conn.close()

    def test_cross_file_dedup_via_unique_index(self):
        """Re-scanning a file shouldn't create duplicate turns for same message_id."""
        with open(self.filepath, "w") as f:
            f.write(_make_user_record(session_id="sess-1") + "\n")
            f.write(_make_assistant_record(session_id="sess-1",
                                           message_id="msg-1",
                                           input_tokens=100, output_tokens=50) + "\n")

        scan(projects_dir=self.projects_dir.parent.parent,
             db_path=self.db_path, verbose=False)

        conn = sqlite3.connect(self.db_path)
        count1 = conn.execute("SELECT COUNT(*) FROM turns").fetchone()[0]
        self.assertEqual(count1, 1)

        # Delete processed_files to force re-scan
        conn.execute("DELETE FROM processed_files")
        conn.commit()
        conn.close()

        scan(projects_dir=self.projects_dir.parent.parent,
             db_path=self.db_path, verbose=False)

        conn = sqlite3.connect(self.db_path)
        count2 = conn.execute("SELECT COUNT(*) FROM turns").fetchone()[0]
        conn.close()
        # Should still be 1 turn (UNIQUE index prevents duplicate)
        self.assertEqual(count2, 1)

    def test_schema_migration_adds_message_id(self):
        """Existing DBs without message_id column should be upgraded."""
        conn = sqlite3.connect(self.db_path)
        conn.executescript("""
            CREATE TABLE turns (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT,
                timestamp TEXT,
                model TEXT,
                input_tokens INTEGER DEFAULT 0,
                output_tokens INTEGER DEFAULT 0,
                cache_read_tokens INTEGER DEFAULT 0,
                cache_creation_tokens INTEGER DEFAULT 0,
                tool_name TEXT,
                cwd TEXT
            );
        """)
        conn.commit()
        conn.close()

        # init_db should add message_id column
        from scanner import get_db, init_db
        conn = get_db(self.db_path)
        init_db(conn)
        # Verify column exists
        row = conn.execute("PRAGMA table_info(turns)").fetchall()
        col_names = [r["name"] for r in row]
        self.assertIn("message_id", col_names)
        conn.close()


class TestAggregateSessions(unittest.TestCase):
    def test_aggregation(self):
        metas = [{"session_id": "s1", "project_name": "test",
                  "first_timestamp": "t1", "last_timestamp": "t2",
                  "git_branch": "main", "model": None}]
        turns = [
            {"session_id": "s1", "input_tokens": 100, "output_tokens": 50,
             "cache_read_tokens": 10, "cache_creation_tokens": 5, "model": "claude-sonnet-4-6"},
            {"session_id": "s1", "input_tokens": 200, "output_tokens": 100,
             "cache_read_tokens": 20, "cache_creation_tokens": 10, "model": "claude-sonnet-4-6"},
        ]
        sessions = aggregate_sessions(metas, turns)
        self.assertEqual(len(sessions), 1)
        self.assertEqual(sessions[0]["total_input_tokens"], 300)
        self.assertEqual(sessions[0]["total_output_tokens"], 150)
        self.assertEqual(sessions[0]["turn_count"], 2)
        self.assertEqual(sessions[0]["model"], "claude-sonnet-4-6")

    def test_empty_turns(self):
        metas = [{"session_id": "s1", "project_name": "test",
                  "first_timestamp": "t1", "last_timestamp": "t2",
                  "git_branch": "main", "model": None}]
        sessions = aggregate_sessions(metas, [])
        self.assertEqual(len(sessions), 1)
        self.assertEqual(sessions[0]["total_input_tokens"], 0)
        self.assertEqual(sessions[0]["turn_count"], 0)


class TestDatabaseOperations(unittest.TestCase):
    def setUp(self):
        self.tmpfile = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.tmpfile.close()
        self.db_path = Path(self.tmpfile.name)
        self.conn = get_db(self.db_path)
        init_db(self.conn)

    def tearDown(self):
        self.conn.close()
        os.unlink(self.db_path)

    def test_init_db_creates_tables(self):
        tables = self.conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
        table_names = {r["name"] for r in tables}
        self.assertIn("sessions", table_names)
        self.assertIn("turns", table_names)
        self.assertIn("processed_files", table_names)

    def test_init_db_is_idempotent(self):
        # Running init_db twice should not error
        init_db(self.conn)
        init_db(self.conn)

    def test_upsert_new_session(self):
        sessions = [{
            "session_id": "s1", "project_name": "test",
            "first_timestamp": "2026-04-08T09:00:00Z",
            "last_timestamp": "2026-04-08T10:00:00Z",
            "git_branch": "main", "model": "claude-sonnet-4-6",
            "total_input_tokens": 1000, "total_output_tokens": 500,
            "total_cache_read": 100, "total_cache_creation": 50,
            "turn_count": 5,
        }]
        upsert_sessions(self.conn, sessions)
        row = self.conn.execute("SELECT * FROM sessions WHERE session_id = 's1'").fetchone()
        self.assertIsNotNone(row)
        self.assertEqual(row["total_input_tokens"], 1000)
        self.assertEqual(row["turn_count"], 5)

    def test_upsert_updates_existing_session(self):
        session = {
            "session_id": "s1", "project_name": "test",
            "first_timestamp": "2026-04-08T09:00:00Z",
            "last_timestamp": "2026-04-08T10:00:00Z",
            "git_branch": "main", "model": "claude-sonnet-4-6",
            "total_input_tokens": 1000, "total_output_tokens": 500,
            "total_cache_read": 100, "total_cache_creation": 50,
            "turn_count": 5,
        }
        upsert_sessions(self.conn, [session])
        # Add more tokens
        session2 = {**session, "total_input_tokens": 200, "total_output_tokens": 100,
                    "total_cache_read": 20, "total_cache_creation": 10, "turn_count": 2}
        upsert_sessions(self.conn, [session2])
        row = self.conn.execute("SELECT * FROM sessions WHERE session_id = 's1'").fetchone()
        self.assertEqual(row["total_input_tokens"], 1200)  # 1000 + 200
        self.assertEqual(row["turn_count"], 7)  # 5 + 2

    def test_insert_turns(self):
        turns = [{
            "session_id": "s1", "timestamp": "2026-04-08T10:00:00Z",
            "model": "claude-sonnet-4-6", "input_tokens": 100,
            "output_tokens": 50, "cache_read_tokens": 10,
            "cache_creation_tokens": 5, "tool_name": "Read", "cwd": "/tmp",
        }]
        insert_turns(self.conn, turns)
        rows = self.conn.execute("SELECT * FROM turns").fetchall()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["model"], "claude-sonnet-4-6")


class TestScanIntegration(unittest.TestCase):
    """Integration test: create fake JSONL files and run scan()."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.projects_dir = Path(self.tmpdir) / "projects"
        self.projects_dir.mkdir()
        self.db_path = Path(self.tmpdir) / "usage.db"

    def _write_project_jsonl(self, project_name, session_id, num_turns=3):
        project_dir = self.projects_dir / project_name
        project_dir.mkdir(parents=True, exist_ok=True)
        path = project_dir / f"{session_id}.jsonl"
        with open(path, "w") as f:
            f.write(_make_user_record(session_id=session_id) + "\n")
            for i in range(num_turns):
                ts = f"2026-04-08T10:{i:02d}:00Z"
                f.write(_make_assistant_record(
                    session_id=session_id,
                    timestamp=ts,
                    input_tokens=100 * (i + 1),
                    output_tokens=50 * (i + 1),
                ) + "\n")

    def test_scan_new_files(self):
        self._write_project_jsonl("user/myproject", "sess-1", num_turns=3)
        result = scan(projects_dir=self.projects_dir, db_path=self.db_path, verbose=False)
        self.assertEqual(result["new"], 1)
        self.assertEqual(result["turns"], 3)
        self.assertEqual(result["sessions"], 1)

    def test_scan_is_incremental(self):
        self._write_project_jsonl("user/myproject", "sess-1")
        scan(projects_dir=self.projects_dir, db_path=self.db_path, verbose=False)
        # Second scan should skip
        result = scan(projects_dir=self.projects_dir, db_path=self.db_path, verbose=False)
        self.assertEqual(result["skipped"], 1)
        self.assertEqual(result["new"], 0)

    def test_scan_empty_directory(self):
        result = scan(projects_dir=self.projects_dir, db_path=self.db_path, verbose=False)
        self.assertEqual(result["new"], 0)
        self.assertEqual(result["turns"], 0)

    def test_scan_multiple_files(self):
        self._write_project_jsonl("user/project-a", "sess-1", num_turns=2)
        self._write_project_jsonl("user/project-b", "sess-2", num_turns=4)
        result = scan(projects_dir=self.projects_dir, db_path=self.db_path, verbose=False)
        self.assertEqual(result["new"], 2)
        self.assertEqual(result["turns"], 6)
        self.assertEqual(result["sessions"], 2)


class TestScanIncrementalUpdate(unittest.TestCase):
    """Test that updating a file only processes new lines (no double reads)."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.projects_dir = Path(self.tmpdir) / "projects" / "user" / "proj"
        self.projects_dir.mkdir(parents=True)
        self.db_path = Path(self.tmpdir) / "usage.db"
        self.filepath = self.projects_dir / "sess-1.jsonl"

    def _write_initial(self):
        with open(self.filepath, "w") as f:
            f.write(_make_user_record(session_id="sess-1",
                                      timestamp="2026-04-08T09:00:00Z") + "\n")
            f.write(_make_assistant_record(session_id="sess-1",
                                           timestamp="2026-04-08T09:01:00Z",
                                           input_tokens=100, output_tokens=50) + "\n")

    def _append_turns(self):
        # Ensure mtime visibly changes (filesystem resolution can be ~10ms)
        import time
        time.sleep(0.05)
        with open(self.filepath, "a") as f:
            f.write(_make_assistant_record(session_id="sess-1",
                                           timestamp="2026-04-08T09:05:00Z",
                                           input_tokens=200, output_tokens=100) + "\n")
            f.write(_make_assistant_record(session_id="sess-1",
                                           timestamp="2026-04-08T09:10:00Z",
                                           input_tokens=300, output_tokens=150) + "\n")

    def test_no_duplicate_turns_on_update(self):
        """Growing a file must add only new turns, not re-insert old ones."""
        self._write_initial()
        scan(projects_dir=self.projects_dir.parent.parent, db_path=self.db_path, verbose=False)

        self._append_turns()
        result = scan(projects_dir=self.projects_dir.parent.parent, db_path=self.db_path, verbose=False)

        self.assertEqual(result["updated"], 1)
        self.assertEqual(result["turns"], 2)  # only the 2 new turns

        conn = sqlite3.connect(self.db_path)
        total_turns = conn.execute("SELECT COUNT(*) FROM turns").fetchone()[0]
        conn.close()
        self.assertEqual(total_turns, 3)  # 1 original + 2 new

    def test_token_counts_accumulate_correctly(self):
        """Session totals should reflect all turns, not double-count."""
        self._write_initial()
        scan(projects_dir=self.projects_dir.parent.parent, db_path=self.db_path, verbose=False)

        self._append_turns()
        scan(projects_dir=self.projects_dir.parent.parent, db_path=self.db_path, verbose=False)

        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        session = conn.execute("SELECT * FROM sessions WHERE session_id = 'sess-1'").fetchone()
        conn.close()
        # 100 + 200 + 300 = 600
        self.assertEqual(session["total_input_tokens"], 600)
        # 50 + 100 + 150 = 300
        self.assertEqual(session["total_output_tokens"], 300)
        self.assertEqual(session["turn_count"], 3)

    def test_session_timestamp_updated(self):
        """Last timestamp should advance after file grows."""
        self._write_initial()
        scan(projects_dir=self.projects_dir.parent.parent, db_path=self.db_path, verbose=False)

        self._append_turns()
        scan(projects_dir=self.projects_dir.parent.parent, db_path=self.db_path, verbose=False)

        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        session = conn.execute("SELECT * FROM sessions WHERE session_id = 'sess-1'").fetchone()
        conn.close()
        self.assertEqual(session["last_timestamp"], "2026-04-08T09:10:00Z")

    def test_mtime_change_without_growth_skipped(self):
        """If mtime changes but line count doesn't grow, skip the file."""
        self._write_initial()
        scan(projects_dir=self.projects_dir.parent.parent, db_path=self.db_path, verbose=False)

        # Touch the file (change mtime) without adding content
        import time
        time.sleep(0.05)
        os.utime(self.filepath, None)

        result = scan(projects_dir=self.projects_dir.parent.parent, db_path=self.db_path, verbose=False)
        self.assertEqual(result["skipped"], 1)
        self.assertEqual(result["updated"], 0)
        self.assertEqual(result["turns"], 0)


class TestCrossFileSessionTotals(unittest.TestCase):
    """Test that session totals are correct when the same session spans multiple files."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.projects_dir = Path(self.tmpdir) / "projects" / "user" / "proj"
        self.projects_dir.mkdir(parents=True)
        self.db_path = Path(self.tmpdir) / "usage.db"

    def test_session_across_files_not_inflated(self):
        """Same session in 2 files with duplicate message_ids should not inflate totals."""
        # File 1: message msg-1 with 100 input
        f1 = self.projects_dir / "file1.jsonl"
        with open(f1, "w") as f:
            f.write(_make_user_record(session_id="sess-1") + "\n")
            f.write(_make_assistant_record(session_id="sess-1", message_id="msg-1",
                                           input_tokens=100, output_tokens=50,
                                           cache_read=0, cache_creation=0) + "\n")

        # File 2: same message msg-1 (duplicate) + new message msg-2
        f2 = self.projects_dir / "file2.jsonl"
        with open(f2, "w") as f:
            f.write(_make_user_record(session_id="sess-1") + "\n")
            f.write(_make_assistant_record(session_id="sess-1", message_id="msg-1",
                                           input_tokens=100, output_tokens=50,
                                           cache_read=0, cache_creation=0) + "\n")
            f.write(_make_assistant_record(session_id="sess-1", message_id="msg-2",
                                           input_tokens=200, output_tokens=100,
                                           cache_read=0, cache_creation=0) + "\n")

        scan(projects_dir=self.projects_dir.parent.parent, db_path=self.db_path, verbose=False)

        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row

        # Turns table should have 2 turns (msg-1 deduped across files)
        turns = conn.execute("SELECT COUNT(*) as c FROM turns").fetchone()["c"]
        self.assertEqual(turns, 2)

        # Session totals should match turns table, not be inflated
        session = conn.execute("SELECT * FROM sessions WHERE session_id = 'sess-1'").fetchone()
        self.assertEqual(session["total_input_tokens"], 300)  # 100 + 200
        self.assertEqual(session["total_output_tokens"], 150)  # 50 + 100
        self.assertEqual(session["turn_count"], 2)
        conn.close()


class TestParseJsonlFileLineCount(unittest.TestCase):
    """Test that parse_jsonl_file returns correct line count."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()

    def test_line_count_matches_file(self):
        path = os.path.join(self.tmpdir, "test.jsonl")
        with open(path, "w") as f:
            f.write(_make_user_record() + "\n")
            f.write(_make_assistant_record() + "\n")
            f.write(_make_assistant_record(timestamp="2026-04-08T10:01:00Z") + "\n")
        _, _, line_count = parse_jsonl_file(path)
        self.assertEqual(line_count, 3)

    def test_empty_file_returns_zero(self):
        path = os.path.join(self.tmpdir, "empty.jsonl")
        with open(path, "w") as f:
            pass
        _, _, line_count = parse_jsonl_file(path)
        self.assertEqual(line_count, 0)


class TestCacheTTLTracking(unittest.TestCase):
    """1h-TTL cache writes are stored separately so they can be priced at 2x."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.projects_dir = Path(self.tmpdir) / "projects" / "user"
        self.projects_dir.mkdir(parents=True)
        self.db_path = Path(self.tmpdir) / "usage.db"
        self.jsonl = self.projects_dir / "s.jsonl"
        self.jsonl.write_text("\n".join([
            _make_user_record(),
            _make_assistant_record(message_id="m1", cache_creation=1000, cache_creation_1h=800),
            _make_assistant_record(message_id="m2", cache_creation=50),
        ]) + "\n")

    def test_parse_extracts_1h_tokens(self):
        _, turns, _ = parse_jsonl_file(self.jsonl)
        by_id = {t["message_id"]: t for t in turns}
        self.assertEqual(by_id["m1"]["cache_creation_1h_tokens"], 800)
        self.assertEqual(by_id["m2"]["cache_creation_1h_tokens"], 0)

    def test_scan_stores_turn_and_session_totals(self):
        scan(projects_dir=self.projects_dir.parent, db_path=self.db_path, verbose=False)
        conn = sqlite3.connect(self.db_path)
        self.assertEqual(conn.execute("SELECT SUM(cache_creation_1h_tokens) FROM turns").fetchone()[0], 800)
        self.assertEqual(conn.execute("SELECT total_cache_creation_1h FROM sessions").fetchone()[0], 800)
        conn.close()

    def test_old_schema_is_migrated_and_backfilled(self):
        scan(projects_dir=self.projects_dir.parent, db_path=self.db_path, verbose=False)
        conn = sqlite3.connect(self.db_path)
        # Simulate a DB created before the 1h columns existed
        conn.execute("ALTER TABLE turns DROP COLUMN cache_creation_1h_tokens")
        conn.execute("ALTER TABLE sessions DROP COLUMN total_cache_creation_1h")
        conn.execute("PRAGMA user_version = 0")
        conn.commit()
        conn.row_factory = sqlite3.Row
        init_db(conn)
        self.assertEqual(conn.execute("SELECT SUM(cache_creation_1h_tokens) FROM turns").fetchone()[0], 800)
        self.assertEqual(conn.execute("SELECT total_cache_creation_1h FROM sessions").fetchone()[0], 800)
        conn.close()


def _write(path, records):
    path.write_text("\n".join(records) + "\n")


def _append(path, records):
    """Append lines and bump mtime so an immediate rescan sees the change."""
    with open(path, "a") as f:
        f.write("\n".join(records) + "\n")
    later = os.path.getmtime(path) + 10
    os.utime(path, (later, later))


class _ScanCase(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.projects_dir = Path(self.tmpdir) / "projects"
        (self.projects_dir / "user").mkdir(parents=True)
        self.db_path = Path(self.tmpdir) / "usage.db"
        self.jsonl = self.projects_dir / "user" / "s.jsonl"

    def scan(self, **kw):
        return scan(projects_dir=self.projects_dir, db_path=self.db_path, verbose=False, **kw)

    def query(self, sql):
        conn = sqlite3.connect(self.db_path)
        try:
            return conn.execute(sql).fetchall()
        finally:
            conn.close()


class TestMidStreamTallies(_ScanCase):
    """A scan that lands while a response is still streaming must not freeze
    the partial token counts (upstream #169)."""

    def test_final_tallies_replace_partial_ones(self):
        _write(self.jsonl, [_make_user_record(),
                            _make_assistant_record(message_id="m1", output_tokens=5)])
        self.scan()
        _append(self.jsonl, [_make_assistant_record(message_id="m1", output_tokens=900)])
        self.scan()
        self.assertEqual(self.query("SELECT COUNT(*), SUM(output_tokens) FROM turns")[0], (1, 900))
        self.assertEqual(self.query("SELECT total_output_tokens FROM sessions")[0][0], 900)
        cost = self.query("SELECT cost_usd FROM turns")[0][0]
        self.assertAlmostEqual(cost, calc_cost("claude-sonnet-4-6", 100, 900, 10, 5))


class TestStoredCosts(_ScanCase):
    def _assistant(self, message_id, **usage_extra):
        rec = json.loads(_make_assistant_record(message_id=message_id, model="claude-opus-5",
                                                input_tokens=1_000_000, output_tokens=0,
                                                cache_read=0, cache_creation=0))
        rec["message"]["usage"].update(usage_extra)
        return json.dumps(rec)

    def test_fast_mode_uses_multiplier(self):
        _write(self.jsonl, [self._assistant("m1", speed="fast"), self._assistant("m2", speed="standard")])
        self.scan()
        rows = dict(self.query("SELECT speed, cost_usd FROM turns"))
        self.assertAlmostEqual(rows["standard"], 5.00)
        self.assertAlmostEqual(rows["fast"], 10.00)

    def test_web_search_requests_are_billed(self):
        _write(self.jsonl, [self._assistant("m1", server_tool_use={"web_search_requests": 3})])
        self.scan()
        self.assertEqual(self.query("SELECT web_search_requests FROM turns")[0][0], 3)
        self.assertAlmostEqual(self.query("SELECT total_cost FROM sessions")[0][0], 5.00 + 0.03)

    def test_costs_recomputed_when_pricing_changes(self):
        _write(self.jsonl, [self._assistant("m1")])
        self.scan()
        conn = get_db(self.db_path)
        conn.execute("UPDATE turns SET cost_usd = 0")
        conn.execute("UPDATE meta SET value = 'stale' WHERE key = 'pricing'")
        conn.commit()
        init_db(conn)
        conn.close()
        self.assertAlmostEqual(self.query("SELECT cost_usd FROM turns")[0][0], 5.00)


class TestSessionTitles(_ScanCase):
    def _title(self, kind, text, sid="sess-1"):
        key = "customTitle" if kind == "custom-title" else "aiTitle"
        return json.dumps({"type": kind, key: text, "sessionId": sid})

    def _prompt(self, text):
        rec = json.loads(_make_user_record())
        rec["message"] = {"role": "user", "content": text}
        return json.dumps(rec)

    def title(self):
        return self.query("SELECT title FROM sessions")[0][0]

    def test_first_prompt_is_fallback(self):
        _write(self.jsonl, [self._prompt("<command-name>/init</command-name>"),
                            self._prompt("Fix the   login\nbug"),
                            _make_assistant_record(message_id="m1")])
        self.scan()
        self.assertEqual(self.title(), "Fix the login bug")

    def test_ai_title_beats_prompt_and_custom_beats_ai(self):
        _write(self.jsonl, [self._prompt("hello"), _make_assistant_record(message_id="m1"),
                            self._title("ai-title", "AI title")])
        self.scan()
        self.assertEqual(self.title(), "AI title")
        _append(self.jsonl, [self._title("custom-title", "My rename")])
        self.scan()
        self.assertEqual(self.title(), "My rename")


class TestWorktreeFolding(unittest.TestCase):
    def test_claude_worktree_folds_into_repo(self):
        self.assertEqual(
            project_name_from_cwd("/home/me/code/app/.claude/worktrees/nifty-pike-5220d0"),
            "code/app")

    def test_git_worktree_resolved_via_git_file(self):
        root = Path(tempfile.mkdtemp())
        wt = root / "elsewhere" / "feature-x"
        wt.mkdir(parents=True)
        (wt / ".git").write_text(f"gitdir: {root}/src/myrepo/.git/worktrees/feature-x\n")
        self.assertEqual(project_name_from_cwd(str(wt)), "src/myrepo")

    def test_regular_checkout_unchanged(self):
        self.assertEqual(project_name_from_cwd("/home/me/code/app"), "code/app")


class TestFullRescanKeepsHistory(_ScanCase):
    """Rescan must not drop usage whose transcript Claude Code has deleted."""

    def test_deleted_transcript_turns_survive_full_rescan(self):
        other = self.projects_dir / "user" / "old.jsonl"
        _write(other, [_make_assistant_record(session_id="old", message_id="o1")])
        _write(self.jsonl, [_make_assistant_record(message_id="m1")])
        self.scan()
        other.unlink()
        result = self.scan(full=True)
        self.assertEqual(result["new"], 1)
        self.assertEqual(self.query("SELECT COUNT(*) FROM turns")[0][0], 2)
        self.assertEqual(self.query("SELECT COUNT(*) FROM sessions")[0][0], 2)


if __name__ == "__main__":
    unittest.main()
