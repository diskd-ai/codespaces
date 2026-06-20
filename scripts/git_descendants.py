#!/usr/bin/env python3
"""
Git Descendants Finder -- find all commits that descend from HEAD.

Builds a parent-child graph from git rev-list, then BFS-walks forward
from HEAD to find all descendant commits. Useful for understanding
what work was built on top of a given commit.

Usage::

    git_descendants.py              # descendants of HEAD
    git_descendants.py <ref>        # descendants of a specific commit/ref
"""

from __future__ import annotations

import subprocess
import sys


def run_git(cmd: str, cwd: str | None = None) -> list[str]:
    """Run a git command and return output lines."""
    result = subprocess.run(cmd, shell=True, capture_output=True, text=True, cwd=cwd)
    if result.returncode != 0:
        print(f"Error running: {cmd}", file=sys.stderr)
        print(result.stderr, file=sys.stderr)
        sys.exit(1)
    return result.stdout.strip().split("\n") if result.stdout.strip() else []


def get_commit_sha(ref: str = "HEAD", cwd: str | None = None) -> str:
    """Resolve a ref to a full SHA."""
    result = subprocess.run(
        ["git", "rev-parse", ref], capture_output=True, text=True, cwd=cwd,
    )
    if result.returncode != 0:
        print(f"Error: cannot resolve ref '{ref}'", file=sys.stderr)
        sys.exit(1)
    return result.stdout.strip()


def build_parent_child_map(
    cwd: str | None = None,
) -> tuple[dict[str, str], dict[str, list[str]]]:
    """Build a map of commit -> leftmost parent and parent -> children."""
    print("Building commit graph...", file=sys.stderr)

    commits = run_git("git rev-list --all --reflog --parents", cwd=cwd)

    parent_map: dict[str, str] = {}
    children_map: dict[str, list[str]] = {}

    for line in commits:
        parts = line.split()
        if not parts:
            continue

        commit = parts[0]
        parents = parts[1:]

        if parents:
            leftmost_parent = parents[0]
            parent_map[commit] = leftmost_parent

            if leftmost_parent not in children_map:
                children_map[leftmost_parent] = []
            children_map[leftmost_parent].append(commit)

    print(f"Found {len(parent_map)} commits with parents", file=sys.stderr)
    return parent_map, children_map


def find_all_descendants(
    commit: str, children_map: dict[str, list[str]],
) -> set[str]:
    """Find all descendants of a commit using BFS."""
    descendants: set[str] = set()
    queue = [commit]

    while queue:
        current = queue.pop(0)

        if current in children_map:
            for child in children_map[current]:
                if child not in descendants:
                    descendants.add(child)
                    queue.append(child)

    return descendants


def main() -> None:
    ref = sys.argv[1] if len(sys.argv) > 1 else "HEAD"

    full_sha = get_commit_sha(ref)
    short_sha = subprocess.run(
        ["git", "rev-parse", "--short", full_sha],
        capture_output=True, text=True,
    ).stdout.strip()

    print(f"Finding descendants of {ref}: {short_sha}", file=sys.stderr)
    print(f"Full SHA: {full_sha}", file=sys.stderr)
    print(file=sys.stderr)

    _parent_map, children_map = build_parent_child_map()

    descendants = find_all_descendants(full_sha, children_map)

    print(f"Found {len(descendants)} descendant commits", file=sys.stderr)
    print(file=sys.stderr)

    if descendants:
        for commit in sorted(descendants):
            commit_info = subprocess.run(
                ["git", "log", "-1", "--format=%h %s", commit],
                capture_output=True, text=True,
            ).stdout.strip()
            print(commit_info)
    else:
        print("No descendants found", file=sys.stderr)


if __name__ == "__main__":
    main()
