from __future__ import annotations

import subprocess
import sys
from pathlib import Path


BLOCKED_PATH_PREFIXES = (
    "data/processed/",
    "data/raw/",
    ".streamlit/secrets.toml",
)

BLOCKED_FILE_SUFFIXES = (
    ".xlsx",
    ".xls",
)


def git_ls_files(repo: Path) -> list[str]:
    result = subprocess.run(
        ["git", "-C", str(repo), "ls-files"],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    return [line.strip().replace("\\", "/") for line in result.stdout.splitlines() if line.strip()]


def main() -> int:
    repo = Path(__file__).resolve().parents[1]
    tracked_files = git_ls_files(repo)
    blocked_paths = [
        path
        for path in tracked_files
        if path.startswith(BLOCKED_PATH_PREFIXES) or path.lower().endswith(BLOCKED_FILE_SUFFIXES)
    ]
    if blocked_paths:
        print("Blocked tracked files in public repo:")
        for path in blocked_paths:
            print(f"  - {path}")
        return 1
    print("Public repo safety check passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
