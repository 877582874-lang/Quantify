import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

DRIVER = Path(__file__).resolve().parents[1] / "driver.py"


class DriverTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)
        self.work = self.base / "work"
        self.work.mkdir()
        self.tasks = self.base / "tasks"
        self.env = dict(os.environ, COORDINATOR_DB=str(self.base / "db.sqlite"),
                        COORDINATOR_TASKS_DIR=str(self.tasks))
        self.counter = self.base / "counter"
        self.stub = self.base / "codex-stub"
        self.stub.write_text("""#!/usr/bin/env python3
import os, pathlib, sys
p = pathlib.Path(os.environ['STUB_COUNTER'])
n = int(p.read_text()) + 1 if p.exists() else 1
p.write_text(str(n))
mode = os.environ.get('STUB_MODE', 'always')
if mode == 'always' or (mode == 'second' and n >= 2):
    pathlib.Path(os.environ['STUB_TARGET']).write_text('ok')
if mode == 'forbidden':
    pathlib.Path('forbidden.txt').write_text('bad')
print('stub invocation', n)
""", encoding="utf-8")
        self.stub.chmod(0o755)
        self.env.update(COORDINATOR_CODEX_BIN=str(self.stub), STUB_COUNTER=str(self.counter),
                        STUB_TARGET=str(self.work / "target.txt"))

    def tearDown(self):
        self.temp.cleanup()

    def envelope(self, task_id, allowed=None, forbidden=None):
        return {"task_id": task_id, "title": task_id, "objective": "exercise driver",
                "workdir": str(self.work), "allowed_files": allowed or ["target.txt"],
                "forbidden_files": forbidden or ["forbidden.txt"], "requirements": [],
                "acceptance": [], "tests": [], "evidence_required": [], "git_commit": "none",
                "deploy": "no", "permission_level": "P1", "approval": "", "timeout": 15}

    def brief(self, tasks, max_rework=0):
        path = self.base / "brief.json"
        path.write_text(json.dumps({"brief_id": "BRIEF-20261125-99", "title": "test",
                                    "max_rework": max_rework, "subtasks": tasks}), encoding="utf-8")
        return path

    def invoke(self, path, mode):
        self.env["STUB_MODE"] = mode
        p = subprocess.run([sys.executable, str(DRIVER), "run", "--brief", str(path)], env=self.env,
                           text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        report = json.loads((Path(__file__).resolve().parents[2] / "reports" /
                             "driver-BRIEF-20261125-99.json").read_text())
        return p, report

    def test_a_normal_success(self):
        p, report = self.invoke(self.brief([dict(self.envelope("TASK-20261125-21"),
                                                  verify_cmd="test -f target.txt")]), "always")
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertEqual(report["subtasks"][0]["status"], "returned")

    def test_b_rework_then_success(self):
        p, report = self.invoke(self.brief([dict(self.envelope("TASK-20261125-22"),
                                                  verify_cmd="test -f target.txt")], 1), "second")
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertEqual(report["subtasks"][0]["rework_count"], 1)
        self.assertEqual(len(report["subtasks"][0]["rounds"]), 2)

    def test_c_exhaustion_escalates_and_stops_brief(self):
        tasks = [dict(self.envelope("TASK-20261125-23"), verify_cmd="test -f target.txt"),
                 self.envelope("TASK-20261125-24")]
        p, report = self.invoke(self.brief(tasks, 1), "never")
        self.assertEqual(p.returncode, 3)
        self.assertIn("ESCALATED", p.stdout)
        self.assertEqual(len(report["subtasks"]), 1)
        listing = subprocess.run([sys.executable, str(Path(__file__).resolve().parents[1] / "coordinator.py"),
                                  "list"], env=self.env, text=True, capture_output=True, check=True).stdout
        self.assertNotIn("TASK-20261125-24", listing)

    def test_d_forbidden_change_escalates(self):
        subprocess.run(["git", "init", "-q"], cwd=self.work, check=True)
        subprocess.run(["git", "-c", "user.name=test", "-c", "user.email=test@example.com",
                        "commit", "--allow-empty", "-qm", "baseline"], cwd=self.work, check=True)
        p, report = self.invoke(self.brief([dict(self.envelope("TASK-20261125-25"),
                                                  verify_cmd="true")], 0), "forbidden")
        self.assertEqual(p.returncode, 3)
        checks = report["subtasks"][0]["rounds"][0]["acceptance"]
        self.assertFalse(checks["git_compliance"]["passed"])
        self.assertEqual(report["subtasks"][0]["status"], "ESCALATED")


if __name__ == "__main__":
    unittest.main()
