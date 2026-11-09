#!/usr/bin/env python3
"""Check out the pinned revision of every git project in a repo manifest.

repo sync follows branch heads, which drift over time; the pinned manifest
records the exact revisions this device tree was authored and validated
against. Projects whose pinned revision is a 40-hex sha are enforced: the
revision is fetched (depth 1) from the hosting remote when the local clone
lacks it, and HEAD is detached onto it. Tag-based revisions are frozen
AOSP pins and are left as synced. Exits non-zero when an enforced revision
cannot be materialized.
"""

import argparse
import re
import subprocess
import sys
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

SHA_RE = re.compile(r"^[0-9a-f]{40}$")
FETCH_WORKERS = 8


def run_git(args, cwd):
    return subprocess.run(
        ["git"] + args,
        cwd=cwd,
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )


def enforce_project(top, rel, rev, fetch_url, results):
    repo = top / rel
    head = run_git(["rev-parse", "HEAD"], repo)
    if head.returncode != 0:
        results["failures"].append(f"{rel}: not a git repository")
        return
    if head.stdout.strip() == rev:
        results["matched"] += 1
        return
    if run_git(["cat-file", "-e", f"{rev}^{{commit}}"], repo).returncode != 0:
        fetched = run_git(["fetch", "--depth", "1", fetch_url, rev], repo)
        if fetched.returncode != 0:
            results["failures"].append(
                f"{rel}: fetch {rev} failed:\n{fetched.stdout.strip()}"
            )
            return
    checkout = run_git(["checkout", "--detach", rev], repo)
    if checkout.returncode != 0:
        checkout = run_git(["checkout", "--detach", "FETCH_HEAD"], repo)
    if checkout.returncode != 0:
        results["failures"].append(
            f"{rel}: checkout {rev} failed:\n{checkout.stdout.strip()}"
        )
        return
    results["detached"] += 1


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Check out pinned revisions from a repo manifest (fetching if needed)."
    )
    parser.add_argument("top", type=Path)
    parser.add_argument("manifest", type=Path)
    parser.add_argument("--workers", type=int, default=FETCH_WORKERS)
    args = parser.parse_args()

    root = ET.parse(args.manifest).getroot()
    remotes = {
        r.get("name"): (r.get("fetch") or "").rstrip("/")
        for r in root.findall("remote")
    }
    default_node = root.find("default")
    default_rev = default_node.get("revision") if default_node is not None else None

    jobs, skipped = [], 0
    for project in root.findall("project"):
        rel = project.get("path") or project.get("name")
        rev = project.get("revision") or default_rev
        if not rel or not rev or not SHA_RE.match(rev):
            skipped += 1
            continue
        if not (args.top / rel / ".git").exists():
            skipped += 1
            continue
        remote_fetch = remotes.get(project.get("remote") or "")
        name = project.get("name") or rel
        fetch_url = f"{remote_fetch}/{name}" if remote_fetch else "origin"
        jobs.append((rel, rev, fetch_url))

    results = {"matched": 0, "detached": 0, "failures": []}
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        list(
            pool.map(
                lambda job: enforce_project(args.top, job[0], job[1], job[2], results),
                jobs,
            )
        )

    print(
        f"Pinned source checkout: {results['matched']} matched, "
        f"{results['detached']} detached, {skipped} non-sha or absent skipped"
    )
    for failure in results["failures"]:
        print(f"ERROR: {failure}", file=sys.stderr)
    if results["failures"]:
        print(
            f"Failed to enforce {len(results['failures'])} of {len(jobs)} pinned projects",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
