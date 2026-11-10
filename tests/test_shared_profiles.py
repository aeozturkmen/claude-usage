"""Tests for several macOS profiles sharing one DB and one dashboard port."""

import json
import os
import socket
import stat
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

import dashboard
import scanner


def _write_jsonl(path, session_id, message_id):
    path.parent.mkdir(parents=True, exist_ok=True)
    rec = {
        "type": "assistant", "sessionId": session_id,
        "timestamp": "2026-11-01T10:00:00Z", "cwd": "/Users/someone/code/app",
        "message": {"id": message_id, "model": "claude-sonnet-4-6",
                    "usage": {"input_tokens": 10, "output_tokens": 5}},
    }
    path.write_text(json.dumps(rec) + "\n")


class _TmpCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.projects = self.tmp / "projects"
        self.projects.mkdir()
        self.db = self.tmp / "shared" / "usage.db"
        self.db.parent.mkdir()
        patches = [
            mock.patch.object(dashboard, "DB_PATH", self.db),
            mock.patch.object(scanner, "DB_PATH", self.db),
            mock.patch.object(scanner, "DEFAULT_PROJECTS_DIRS", [self.projects]),
            mock.patch.object(scanner, "COWORK_SESSIONS_DIR", self.tmp / "no-cowork"),
            mock.patch.dict(dashboard._peers, {"last_request": 0.0, "slow": set()}),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def tearDown(self):
        self._tmp.cleanup()


class TestDbPathFromEnv(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.pointer = Path(self._tmp.name) / "usage-db-path"
        env = mock.patch.dict(os.environ, {})
        env.start()
        self.addCleanup(env.stop)
        os.environ.pop("CLAUDE_USAGE_DB", None)

    def tearDown(self):
        self._tmp.cleanup()

    def test_default_is_home(self):
        self.assertEqual(scanner._db_path_from_env(self.pointer),
                         Path.home() / ".claude" / "usage.db")

    def test_pointer_file(self):
        self.pointer.write_text("/Users/Shared/ClaudeUsage/usage.db\n")
        self.assertEqual(scanner._db_path_from_env(self.pointer),
                         Path("/Users/Shared/ClaudeUsage/usage.db"))

    def test_env_beats_pointer(self):
        self.pointer.write_text("/Users/Shared/ClaudeUsage/usage.db\n")
        os.environ["CLAUDE_USAGE_DB"] = "/tmp/elsewhere.db"
        self.assertEqual(scanner._db_path_from_env(self.pointer), Path("/tmp/elsewhere.db"))


@unittest.skipUnless(os.name == "posix", "POSIX permissions")
class TestSharedPermissions(_TmpCase):
    def test_db_in_group_writable_dir_is_group_writable(self):
        os.chmod(self.db.parent, 0o775)
        old = os.umask(0o022)
        try:
            scanner.get_db(self.db).close()
            self.assertTrue(os.stat(self.db).st_mode & stat.S_IWGRP)
        finally:
            os.umask(old)

    def test_private_dir_is_left_alone(self):
        os.chmod(self.db.parent, 0o755)
        old = os.umask(0o022)
        try:
            scanner.get_db(self.db).close()
            self.assertFalse(os.stat(self.db).st_mode & stat.S_IWGRP)
            self.assertEqual(os.umask(0o022), 0o022)  # umask untouched
        finally:
            os.umask(old)


class TestFullRescanIsPerProfile(_TmpCase):
    def test_other_profiles_rows_survive_full_rescan(self):
        _write_jsonl(self.projects / "a" / "s1.jsonl", "s1", "m1")
        scanner.scan(db_path=self.db, projects_dirs=[self.projects], verbose=False)
        conn = scanner.get_db(self.db)
        conn.execute("INSERT INTO processed_files (path, mtime, lines) VALUES (?, 1, 1)",
                     ("/Users/otherprofile/.claude/projects/x/s9.jsonl",))
        conn.commit()
        conn.close()

        result = scanner.scan(db_path=self.db, projects_dirs=[self.projects],
                              verbose=False, full=True)
        self.assertEqual(result["new"], 1)  # own file re-read
        conn = scanner.get_db(self.db)
        paths = {r[0] for r in conn.execute("SELECT path FROM processed_files")}
        conn.close()
        self.assertIn("/Users/otherprofile/.claude/projects/x/s9.jsonl", paths)


class TestMergeDb(_TmpCase):
    def test_merge_adds_missing_turns_once(self):
        old_projects = self.tmp / "old-projects"
        _write_jsonl(old_projects / "a" / "s1.jsonl", "s1", "m1")
        _write_jsonl(old_projects / "b" / "s2.jsonl", "s2", "m2")
        old_db = self.tmp / "old.db"
        scanner.scan(db_path=old_db, projects_dirs=[old_projects], verbose=False)

        _write_jsonl(self.projects / "a" / "s1.jsonl", "s1", "m1")  # overlap
        scanner.scan(db_path=self.db, projects_dirs=[self.projects], verbose=False)

        self.assertEqual(scanner.merge_db(old_db, self.db), 1)
        self.assertEqual(scanner.merge_db(old_db, self.db), 0)  # idempotent
        conn = scanner.get_db(self.db)
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0], 2)
        cost = conn.execute("SELECT total_cost FROM sessions WHERE session_id='s2'").fetchone()[0]
        conn.close()
        self.assertGreater(cost, 0)


class TestProfilesInDashboardData(_TmpCase):
    def test_profiles_listed_from_processed_files(self):
        conn = scanner.get_db(self.db)
        scanner.init_db(conn)
        conn.executemany("INSERT INTO processed_files (path, mtime, lines) VALUES (?, 1, 1)", [
            ("/Users/alice/.claude/projects/a/1.jsonl",),
            ("/Users/bob/Library/Application Support/Claude/x/audit.jsonl",),
            ("/Users/alice/.claude/projects/b/2.jsonl",),
        ])
        conn.commit()
        conn.close()
        data = dashboard.get_dashboard_data(db_path=self.db)
        self.assertEqual(data["profiles"], ["alice", "bob"])


@unittest.skipUnless(os.name == "posix", "pid liveness via os.kill")
class TestPeerScanHandshake(_TmpCase):
    def setUp(self):
        super().setUp()
        self.sync = self.db.parent / ".usage-sync"
        self.sync.mkdir()
        # A fake standby profile "777" whose pid (this test process) is alive.
        (self.sync / "standby-777.pid").write_text(f"{os.getpid()}\n")

    def _answer_after(self, delay):
        def run():
            time.sleep(delay)
            stamp, _ = dashboard._read_request(self.sync / "scan.request")
            (self.sync / "standby-777.done").write_text(f"{stamp!r}\n")
        t = threading.Thread(target=run, daemon=True)
        t.start()
        return t

    def test_no_peers_means_no_request(self):
        (self.sync / "standby-777.pid").write_text("999999\n")  # dead pid
        self.assertEqual(dashboard.request_peer_scan(), (0.0, []))
        self.assertFalse((self.sync / "scan.request").exists())

    def test_waits_for_peer_answer(self):
        stamp, peers = dashboard.request_peer_scan(full=True)
        self.assertEqual(peers, ["777"])
        self.assertEqual(dashboard._read_request(self.sync / "scan.request"), (stamp, True))
        self._answer_after(0.3)
        t0 = time.monotonic()
        dashboard.wait_for_peers(stamp, peers)
        self.assertLess(time.monotonic() - t0, 3)
        self.assertNotIn("777", dashboard._peers["slow"])

    def test_silent_peer_is_skipped_until_it_answers(self):
        with mock.patch.object(dashboard, "PEER_WAIT", 0.3):
            stamp, peers = dashboard.request_peer_scan()
            dashboard.wait_for_peers(stamp, peers)
            self.assertIn("777", dashboard._peers["slow"])

            # Next page load does not wait for it
            stamp2, peers2 = dashboard.request_peer_scan()
            self.assertEqual(peers2, [])

            # Once it answers, it is waited for again
            (self.sync / "standby-777.done").write_text(f"{stamp2!r}\n")
            _, peers3 = dashboard.request_peer_scan()
            self.assertEqual(peers3, ["777"])


@unittest.skipUnless(os.name == "posix", "pid files use os.getuid")
class TestStandbyTakeover(_TmpCase):
    def test_standby_scans_on_request_then_takes_over_port(self):
        _write_jsonl(self.projects / "a" / "s1.jsonl", "s1", "m1")
        busy = socket.socket()
        busy.bind(("127.0.0.1", 0))
        busy.listen()
        port = busy.getsockname()[1]
        sync = self.db.parent / ".usage-sync"
        out = {}

        with mock.patch.multiple(dashboard, STANDBY_TICK=0.05, BIND_RETRY=0.3,
                                 SCAN_INTERVAL=3600):
            t = threading.Thread(
                target=lambda: out.update(server=dashboard._standby("127.0.0.1", port)),
                daemon=True)
            t.start()
            pid_file = sync / f"standby-{os.getuid()}.pid"
            for _ in range(100):
                if pid_file.exists():
                    break
                time.sleep(0.02)
            self.assertTrue(pid_file.exists())

            stamp = time.time()
            (sync / "scan.request").write_text(f"{stamp!r} incremental\n")
            done = sync / f"standby-{os.getuid()}.done"
            for _ in range(100):
                if dashboard._read_request(done)[0] == stamp:
                    break
                time.sleep(0.05)
            self.assertEqual(dashboard._read_request(done)[0], stamp)
            conn = scanner.get_db(self.db)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM turns").fetchone()[0], 1)
            conn.close()

            busy.close()  # the serving profile logs out
            t.join(timeout=5)

        self.assertFalse(t.is_alive())
        server = out["server"]
        self.assertEqual(server.server_address[1], port)
        server.server_close()
        self.assertFalse(pid_file.exists())


if __name__ == "__main__":
    unittest.main()
