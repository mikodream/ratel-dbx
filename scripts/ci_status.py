"""轮询 GitHub Actions 运行状态，带错误处理与重试。

用法：
    GITHUB_TOKEN=$(gh auth token) python scripts/ci_status.py <run_id> <label> ...

token 只从环境变量读取，绝不写进仓库。
"""
import json
import os
import subprocess
import sys
import time

TOKEN = os.environ.get("GITHUB_TOKEN", "")
REPO = os.environ.get("GITHUB_REPOSITORY", "mikodream/ratel-dbx")


def api(path):
    for attempt in range(4):
        out = subprocess.run(
            [
                "curl", "-sS", "--max-time", "30",
                "-H", f"Authorization: Bearer {TOKEN}",
                "-H", "Accept: application/vnd.github+json",
                f"https://api.github.com{path}",
            ],
            capture_output=True,
        )
        body = out.stdout.decode("utf-8", "replace").strip()
        if body.startswith("{"):
            try:
                return json.loads(body)
            except json.JSONDecodeError:
                pass
        # 非 JSON：可能是限流或空响应
        print(f"  [retry {attempt + 1}] {path} -> {body[:120] or '(empty)'}", file=sys.stderr)
        time.sleep(3 * (attempt + 1))
    return None


def show_run(run_id, label):
    print(f"=== {label} (run {run_id}) ===")
    run = api(f"/repos/{REPO}/actions/runs/{run_id}")
    if run:
        print(f"  status={run.get('status')} conclusion={run.get('conclusion')}")
    jobs = api(f"/repos/{REPO}/actions/runs/{run_id}/jobs?per_page=20")
    if not jobs:
        print("  (jobs 获取失败)")
        return None
    for job in jobs.get("jobs", []):
        print(f"  {job['name']:26s} {job['status']:10s} {job['conclusion']}")
        if job["status"] == "in_progress" or job["conclusion"] == "failure":
            for step in job.get("steps", []):
                print(f"      - {step['number']:2d} {step['name'][:50]:52s} {step['status']:10s} {step['conclusion']}")
    return jobs


if __name__ == "__main__":
    pairs = []
    args = sys.argv[1:]
    for i in range(0, len(args), 2):
        pairs.append((args[i], args[i + 1]))
    for run_id, label in pairs:
        show_run(run_id, label)
