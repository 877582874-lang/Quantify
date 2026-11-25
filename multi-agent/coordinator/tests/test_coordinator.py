import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "coordinator.py"


class CoordinatorTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)
        self.db = self.base / "ledger.db"
        self.cwd = self.base / "work"
        self.cwd.mkdir()
        self.task_dir = self.base / "tasks"
        self.env = dict(os.environ, COORDINATOR_DB=str(self.db), COORDINATOR_TASKS_DIR=str(self.task_dir))
        self.call("init")

    def tearDown(self):
        self.temp.cleanup()

    def call(self, *args, check=True, env=None):
        p = subprocess.run([sys.executable, str(SCRIPT), *args], env=env or self.env,
                           text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        if check:
            self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        return p

    def envelope(self, task_id="TASK-20261125-01", level="P1"):
        data = {"task_id": task_id, "title": "test", "objective": "verify", "workdir": str(self.cwd),
                "allowed_files": ["*"], "forbidden_files": [], "requirements": ["stdlib"],
                "acceptance": ["passes"], "tests": ["unit"], "evidence_required": ["output"],
                "git_commit": "none", "deploy": "no", "permission_level": level,
                "approval": "", "timeout": 15}
        f = self.base / (task_id + ".json")
        f.write_text(json.dumps(data), encoding="utf-8")
        return f

    def add(self, **kwargs):
        self.call("add", "--file", str(self.envelope(**kwargs)))

    def test_add_list_status_and_review_transition(self):
        self.add()
        listing = self.call("list").stdout
        self.assertIn("TASK-20261125-01", listing)
        self.assertIn("created", listing)
        self.call("review", "TASK-20261125-01", "--verdict", "accepted", "--note", "looks good")
        self.assertIn('"status": "accepted"', self.call("status", "TASK-20261125-01").stdout)

    def test_add_initializes_fresh_database(self):
        self.db.unlink()
        self.call("add", "--file", str(self.envelope()))
        self.assertIn("TASK-20261125-01", self.call("list").stdout)

    def test_p2_without_approval_denied_and_audited(self):
        self.add(level="P2")
        result = self.call("run", "TASK-20261125-01", check=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("run_denied", self.call("status", "TASK-20261125-01").stdout)

    def test_stub_codex_run_returns_and_records_event_chain(self):
        self.add()
        stub = self.base / "codex-stub"
        stub.write_text("#!/bin/sh\nprintf 'argv:'\nprintf ' <%s>' \"$@\"\nprintf '\\nstub-output\\n'\nexit 0\n", encoding="utf-8")
        stub.chmod(0o755)
        env = dict(self.env, COORDINATOR_CODEX_BIN=str(stub))
        result = self.call("run", "TASK-20261125-01", env=env)
        self.assertIn("exit_code=0", result.stdout)
        log = (self.task_dir / "TASK-20261125-01" / "exec.log")
        self.assertIn("stub-output", log.read_text())
        self.assertIn("--skip-git-repo-check", log.read_text())
        status = self.call("status", "TASK-20261125-01").stdout
        for event in ("status_transition", "codex_started", "returned"):
            self.assertIn(event, status)

    def test_recover_marks_dead_running_task_interrupted(self):
        self.add()
        import sqlite3
        with sqlite3.connect(self.db) as db:
            db.execute("UPDATE tasks SET status='running',codex_pid=99999999 WHERE task_id=?", ("TASK-20261125-01",))
        self.call("recover")
        output = self.call("status", "TASK-20261125-01").stdout
        self.assertIn('"status": "interrupted"', output)
        self.assertIn("status_transition", output)


if __name__ == "__main__":
    unittest.main()
