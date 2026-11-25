#!/usr/bin/env python3
"""Small, auditable task coordinator backed by SQLite."""
import argparse
import json
import os
import re
import shlex
import signal
import sqlite3
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DB = Path(os.environ.get("COORDINATOR_DB", Path(__file__).resolve().parent / "coordinator.db"))
TASKS_DIR = Path(os.environ.get("COORDINATOR_TASKS_DIR", ROOT / "tasks"))
FIELDS = ("task_id", "title", "objective", "workdir", "allowed_files", "forbidden_files",
          "requirements", "acceptance", "tests", "evidence_required", "git_commit", "deploy",
          "permission_level", "approval", "timeout")
STATUSES = {"created", "dispatched", "running", "returned", "reviewing", "rework", "accepted",
            "rejected", "blocked", "interrupted"}


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def connect():
    DB.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(DB, timeout=30)
    db.row_factory = sqlite3.Row
    return db


def event(db, task_id, actor, name, detail=""):
    db.execute("INSERT INTO events(task_id,ts,actor,event,detail) VALUES(?,?,?,?,?)",
               (task_id, now(), actor, name, str(detail)))


def transition(db, task_id, status, actor="system", detail=""):
    if status not in STATUSES:
        raise ValueError("invalid status")
    old = db.execute("SELECT status FROM tasks WHERE task_id=?", (task_id,)).fetchone()
    if not old:
        raise ValueError("unknown task: " + task_id)
    db.execute("UPDATE tasks SET status=?,updated_at=? WHERE task_id=?", (status, now(), task_id))
    event(db, task_id, actor, "status_transition", f"{old['status']} -> {status}; {detail}".strip())


def init_db():
    with connect() as db:
        db.execute("""CREATE TABLE IF NOT EXISTS tasks (
          task_id TEXT PRIMARY KEY, title TEXT NOT NULL, objective TEXT NOT NULL, workdir TEXT NOT NULL,
          allowed_files TEXT NOT NULL, forbidden_files TEXT NOT NULL, requirements TEXT NOT NULL,
          acceptance TEXT NOT NULL, tests TEXT NOT NULL, evidence_required TEXT NOT NULL,
          git_commit TEXT NOT NULL, deploy TEXT NOT NULL, permission_level TEXT NOT NULL,
          approval_by TEXT, approval_note TEXT, status TEXT NOT NULL, rework_count INTEGER NOT NULL DEFAULT 0,
          codex_pid INTEGER, codex_exit INTEGER, session_note TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
          timeout INTEGER NOT NULL DEFAULT 1800)""")
        db.execute("""CREATE TABLE IF NOT EXISTS events (
          id INTEGER PRIMARY KEY AUTOINCREMENT, task_id TEXT NOT NULL, ts TEXT NOT NULL,
          actor TEXT NOT NULL, event TEXT NOT NULL, detail TEXT NOT NULL,
          FOREIGN KEY(task_id) REFERENCES tasks(task_id))""")


def human_envelope(obj):
    lines = [f"# {obj['task_id']} — {obj['title']}", ""]
    for k in FIELDS:
        if k in ("task_id", "title"):
            continue
        v = obj.get(k, "")
        lines.extend((f"## {k}", ""))
        lines.extend(v if isinstance(v, list) else [str(v)])
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def add(path):
    obj = json.loads(Path(path).read_text(encoding="utf-8"))
    missing = [f for f in FIELDS if f not in obj]
    if missing:
        raise ValueError("missing envelope fields: " + ", ".join(missing))
    if not re.fullmatch(r"TASK-\d{8}-\d{2}", str(obj["task_id"])):
        raise ValueError("invalid task_id")
    approval = obj.get("approval") or {}
    approved_by = approval.get("by") if isinstance(approval, dict) else None
    approval_note = approval.get("note") if isinstance(approval, dict) else None
    t = now()
    values = [obj[f] if isinstance(obj[f], str) else json.dumps(obj[f], ensure_ascii=False) for f in FIELDS]
    with connect() as db:
        stored_fields = [f for f in FIELDS if f not in ("approval", "timeout")]
        cols = ",".join(stored_fields) + ",approval_by,approval_note,status,rework_count,created_at,updated_at,timeout"
        marks = ",".join("?" for _ in range(20))
        db.execute(f"INSERT INTO tasks({cols}) VALUES({marks})",
                   [values[FIELDS.index(f)] for f in stored_fields if f != "timeout"] +
                   [approved_by, approval_note, "created", 0, t, t, int(obj.get("timeout") or 1800)])
        event(db, obj["task_id"], "claude", "task_added", "signed task envelope registered")
    task_dir = TASKS_DIR / obj["task_id"]
    task_dir.mkdir(parents=True, exist_ok=True)
    (task_dir / "task.md").write_text(human_envelope(obj), encoding="utf-8")
    (task_dir / "envelope.json").write_text(json.dumps(obj, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(obj["task_id"])


def get_task(db, task_id):
    row = db.execute("SELECT * FROM tasks WHERE task_id=?", (task_id,)).fetchone()
    if not row:
        raise ValueError("unknown task: " + task_id)
    return row


def run_task(task_id, note=None):
    with connect() as db:
        task = get_task(db, task_id)
        if task["permission_level"] in ("P2", "P3") and not task["approval_by"]:
            event(db, task_id, "system", "run_denied", "P2/P3 task has no approval")
            print("run denied: approval required", file=sys.stderr)
            return 2
        if task["deploy"].lower() == "yes" and not task["approval_by"]:
            event(db, task_id, "system", "run_denied", "deploy=yes task has no approval")
            print("run denied: approval required for deploy=yes", file=sys.stderr)
            return 2
        cwd = Path(task["workdir"])
        if not cwd.is_dir():
            event(db, task_id, "system", "run_denied", "workdir does not exist")
            print("run denied: workdir does not exist", file=sys.stderr)
            return 2
        env_obj = json.loads((TASKS_DIR / task_id / "envelope.json").read_text()) if (TASKS_DIR / task_id / "envelope.json").exists() else None
        full_envelope = json.dumps(env_obj, ensure_ascii=False, indent=2) if env_obj else (TASKS_DIR / task_id / "task.md").read_text(encoding="utf-8")
        if note is not None:
            full_envelope += "\n\n## 调度补充说明\n\n" + note
            event(db, task_id, "system", "run_note", note)
        transition(db, task_id, "dispatched", "system", "run requested")
        transition(db, task_id, "running", "system", "codex process starting")
        binary = os.environ.get("COORDINATOR_CODEX_BIN", "codex")
        argv = shlex.split(binary) + ["exec", "--skip-git-repo-check", "-s", "workspace-write", full_envelope]
        proc = subprocess.Popen(argv, cwd=cwd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, text=True, start_new_session=True)
        db.execute("UPDATE tasks SET codex_pid=?,updated_at=? WHERE task_id=?", (proc.pid, now(), task_id))
        event(db, task_id, "system", "codex_started", f"pid={proc.pid}")
    log = TASKS_DIR / task_id / "exec.log"
    try:
        output, _ = proc.communicate(timeout=int(task["timeout"] or 1800))
    except subprocess.TimeoutExpired:
        os.killpg(proc.pid, signal.SIGKILL)
        output, _ = proc.communicate()
        output += "\n[coordinator] execution timed out\n"
        exit_code = 124
    else:
        exit_code = proc.returncode
    with log.open("a", encoding="utf-8") as f:
        f.write(output)
        if not output.endswith("\n"):
            f.write("\n")
        f.write(f"[coordinator] exit_code={exit_code}\n")
    if (cwd / ".git").exists():
        diff = subprocess.run(["git", "diff"], cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        (TASKS_DIR / task_id / "diff.patch").write_text(diff.stdout, encoding="utf-8")
    with connect() as db:
        db.execute("UPDATE tasks SET codex_exit=?,codex_pid=NULL,updated_at=? WHERE task_id=?", (exit_code, now(), task_id))
        event(db, task_id, "system", "returned_failed" if exit_code else "returned", f"exit_code={exit_code}")
        transition(db, task_id, "returned", "system", f"codex exit_code={exit_code}")
    print(f"returned exit_code={exit_code}")
    return 0 if exit_code == 0 else exit_code or 1


def status(task_id):
    with connect() as db:
        task = get_task(db, task_id)
        print(json.dumps(dict(task), ensure_ascii=False, indent=2))
        for ev in db.execute("SELECT ts,actor,event,detail FROM events WHERE task_id=? ORDER BY id", (task_id,)):
            print(f"{ev['ts']} {ev['actor']} {ev['event']}: {ev['detail']}")


def list_tasks():
    with connect() as db:
        for row in db.execute("SELECT task_id,title,status,permission_level,updated_at FROM tasks ORDER BY created_at"):
            print("\t".join(str(row[k]) for k in row.keys()))


def approve(task_id, by, note):
    with connect() as db:
        get_task(db, task_id)
        db.execute("UPDATE tasks SET approval_by=?,approval_note=?,updated_at=? WHERE task_id=?", (by, note, now(), task_id))
        event(db, task_id, "claude", "approved", f"by={by}; note={note}")
    print("approved")


def review(task_id, verdict, note):
    with connect() as db:
        task = get_task(db, task_id)
        transition(db, task_id, "reviewing", "claude", "review started")
        if verdict == "rework":
            count = task["rework_count"] + 1
            if count > 3:
                transition(db, task_id, "rejected", "claude", "rework limit exceeded; escalate")
                event(db, task_id, "claude", "review_note", note)
                print("rework limit exceeded; escalate", file=sys.stderr)
                return 2
            db.execute("UPDATE tasks SET rework_count=?,session_note=?,updated_at=? WHERE task_id=?", (count, note, now(), task_id))
        event(db, task_id, "claude", "review_note", note)
        transition(db, task_id, verdict, "claude", note)
    print(verdict)
    return 0


def recover():
    recovered = 0
    with connect() as db:
        rows = db.execute("SELECT task_id,codex_pid FROM tasks WHERE status='running'").fetchall()
        for row in rows:
            pid = row["codex_pid"]
            alive = False
            if pid:
                try:
                    os.kill(pid, 0)
                    alive = True
                except ProcessLookupError:
                    alive = False
                except PermissionError:
                    alive = True
            if not alive:
                transition(db, row["task_id"], "interrupted", "system", f"pid={pid} not found")
                recovered += 1
    print(f"recovered={recovered}")
    return 0


def main(argv=None):
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("init")
    a = sub.add_parser("add"); a.add_argument("--file", required=True)
    r = sub.add_parser("run"); r.add_argument("task_id"); r.add_argument("--note")
    s = sub.add_parser("status"); s.add_argument("task_id")
    sub.add_parser("list")
    v = sub.add_parser("review"); v.add_argument("task_id"); v.add_argument("--verdict", required=True, choices=("accepted", "rework", "rejected")); v.add_argument("--note", required=True)
    ap = sub.add_parser("approve"); ap.add_argument("task_id"); ap.add_argument("--by", required=True); ap.add_argument("--note", required=True)
    sub.add_parser("recover")
    args = p.parse_args(argv)
    try:
        if args.cmd == "init": init_db(); print(f"initialized {DB}"); return 0
        if args.cmd == "add": init_db(); add(args.file); return 0
        init_db()
        if args.cmd == "run": return run_task(args.task_id, args.note)
        if args.cmd == "status": status(args.task_id); return 0
        if args.cmd == "list": list_tasks(); return 0
        if args.cmd == "approve": approve(args.task_id, args.by, args.note); return 0
        if args.cmd == "review": return review(args.task_id, args.verdict, args.note)
        if args.cmd == "recover": return recover()
    except (ValueError, sqlite3.Error, OSError, json.JSONDecodeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
