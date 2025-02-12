from __future__ import annotations

import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DIRECTORIES = [
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    "build",
    "dist",
    "htmlcov",
]
FILES = [".coverage", "coverage.xml"]


def remove_path(path: Path) -> None:
    if path.is_dir():
        shutil.rmtree(path, ignore_errors=True)
        return
    if path.exists():
        path.unlink()


def main() -> None:
    for relative_dir in DIRECTORIES:
        remove_path(ROOT / relative_dir)

    for relative_file in FILES:
        remove_path(ROOT / relative_file)

    for egg_info_dir in ROOT.glob("*.egg-info"):
        remove_path(egg_info_dir)

    for pycache_dir in ROOT.rglob("__pycache__"):
        remove_path(pycache_dir)


if __name__ == "__main__":
    main()
