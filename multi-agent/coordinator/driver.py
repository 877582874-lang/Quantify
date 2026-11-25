#!/usr/bin/env python3
"""Serial brief runner with mechanical verification and bounded rework."""
import argparse
import fnmatch
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

COORDINATOR = Path(__file__).resolve().with_name("coordinator.py")
ROOT = COORDINATOR.parent.parent
REPORTS = ROOT / "reports"


def call(args, env):
    return subprocess.run([sys.executable, str(COORDINATOR), *args], env=env,
                          text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)


def porcelain(workdir):
    p = subprocess.run(["git", "status", "--porcelain"], cwd=workdir,
                       text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if p.returncode:
        return None
    paths = set()
    for line in p.stdout.splitlines():
        item = line[3:] if len(line) > 3 else ""
        if " -> " in item:
            item = item.split(" -> ", 1)[1]
        paths.add(item.strip().strip('"'))
    return paths


def matches(path, patterns):
    return any(fnmatch.fnmatch(path, pattern) for pattern in patterns)


def status_record(task_id, env):
    result = call(["status", task_id], env)
    if result.returncode:
        raise RuntimeError("status failed: " + result.stderr.strip())
    try:
        record, _ = json.JSONDecoder().raw_decode(result.stdout.lstrip())
    except json.JSONDecodeError as exc:
        raise RuntimeError("cannot parse status JSON: " + str(exc))
    return record, result.stdout


def verify(subtask, before, env):
    workdir = subtask["workdir"]
    record, status_output = status_record(subtask["task_id"], env)
    checks = {"codex_exit": {"passed": record.get("codex_exit") == 0,
                              "actual": record.get("codex_exit")}}
    cmd = subtask.get("verify_cmd")
    if cmd:
        p = subprocess.run(cmd, shell=True, cwd=workdir, env=env, text=True,
                           stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        checks["verify_cmd"] = {"passed": p.returncode == 0, "exit_code": p.returncode,
                                "output": p.stdout.strip()}
    after = porcelain(workdir)
    if before is None or after is None:
        checks["git_compliance"] = {"passed": True, "skipped": True, "new_paths": []}
    else:
        added = sorted(after - before)
        allowed = subtask.get("allowed_files", [])
        forbidden = subtask.get("forbidden_files", [])
        rejected = [p for p in added if not matches(p, allowed) or matches(p, forbidden)]
        checks["git_compliance"] = {"passed": not rejected, "new_paths": added,
                                    "rejected_paths": rejected}
    checks["passed"] = all(v["passed"] for v in checks.values())
    return checks


def run_brief(path):
    brief = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(brief.get("subtasks"), list):
        raise ValueError("brief subtasks must be a list")
    limit = int(brief.get("max_rework", 2))
    if limit < 0:
        raise ValueError("max_rework must be non-negative")
    env = os.environ.copy()
    result = {"brief_id": brief["brief_id"], "title": brief.get("title", ""), "subtasks": [],
              "exit_code": 0}
    for original in brief["subtasks"]:
        sub = dict(original)
        verify_cmd = sub.pop("verify_cmd", None)
        task_id = sub["task_id"]
        row = {"task_id": task_id, "title": sub.get("title", ""), "rounds": [], "rework_count": 0}
        result["subtasks"].append(row)
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".json", delete=False) as f:
            json.dump(sub, f, ensure_ascii=False)
            envelope_path = f.name
        try:
            added = call(["add", "--file", envelope_path], env)
        finally:
            Path(envelope_path).unlink(missing_ok=True)
        if added.returncode:
            row.update(status="ESCALATED", error=(added.stderr + added.stdout).strip())
            result["exit_code"] = 3
            break
        task = dict(sub)
        if verify_cmd:
            task["verify_cmd"] = verify_cmd
        escalated = False
        for round_no in range(1, limit + 2):
            before = porcelain(sub["workdir"])
            note = row["rounds"][-1].get("failure_evidence") if row["rounds"] else None
            args = ["run", task_id]
            if note:
                args += ["--note", note + "；逐项修复，不要扩大修改范围"]
            run = call(args, env)
            entry = {"round": round_no, "run_returncode": run.returncode,
                     "run_stdout": run.stdout.strip(), "run_stderr": run.stderr.strip()}
            row["rounds"].append(entry)
            row["exec_log"] = str(Path(env.get("COORDINATOR_TASKS_DIR", ROOT / "tasks")) / task_id / "exec.log")
            row["diff_patch"] = str(Path(env.get("COORDINATOR_TASKS_DIR", ROOT / "tasks")) / task_id / "diff.patch")
            denied = False
            if run.returncode == 2:
                try:
                    _, status_output = status_record(task_id, env)
                    denied = "run_denied" in status_output
                except Exception:
                    denied = True
            if denied:
                entry["acceptance"] = {"passed": False, "run_gate_denied": True}
                row.update(status="ESCALATED", failure_evidence=run.stderr.strip() or run.stdout.strip())
                result["exit_code"] = 3
                escalated = True
                break
            try:
                checks = verify(task, before, env)
            except Exception as exc:
                checks = {"passed": False, "status_error": str(exc)}
            entry["acceptance"] = checks
            if checks.get("passed"):
                row.update(status="returned", final_round=round_no)
                break
            evidence = json.dumps(checks, ensure_ascii=False, sort_keys=True)
            entry["failure_evidence"] = evidence
            row["failure_evidence"] = evidence
            if round_no > limit:
                row["status"] = "ESCALATED"
                result["exit_code"] = 3
                escalated = True
                break
            review = call(["review", task_id, "--verdict", "rework", "--note",
                           "driver机械验收失败：" + evidence], env)
            if review.returncode:
                entry["review_error"] = (review.stderr + review.stdout).strip()
                row["status"] = "ESCALATED"
                result["exit_code"] = 3
                escalated = True
                break
            row["rework_count"] += 1
        if escalated:
            break
    REPORTS.mkdir(parents=True, exist_ok=True)
    stem = "driver-" + str(brief["brief_id"])
    json_path = REPORTS / (stem + ".json")
    txt_path = REPORTS / (stem + ".txt")
    json_path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    lines = [f"Brief {result['brief_id']}: {result['title']}"]
    for row in result["subtasks"]:
        lines.append(f"{row['task_id']} {row.get('title', '')}: {row.get('status', 'pending')} "
                     f"(rounds={len(row['rounds'])}, rework_count={row['rework_count']})")
        lines.append(f"  exec.log: {row.get('exec_log', '')}")
        lines.append(f"  diff.patch: {row.get('diff_patch', '')}")
        for turn in row["rounds"]:
            lines.append(f"  round {turn['round']} acceptance: " + json.dumps(turn.get("acceptance", {}), ensure_ascii=False))
    lines.append(f"exit_code: {result['exit_code']}")
    report = "\n".join(lines) + "\n"
    txt_path.write_text(report, encoding="utf-8")
    print(report, end="")
    return result["exit_code"]


def main(argv=None):
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run")
    run.add_argument("--brief", required=True)
    args = parser.parse_args(argv)
    try:
        return run_brief(args.brief)
    except (OSError, ValueError, KeyError, json.JSONDecodeError) as exc:
        print("error: " + str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
