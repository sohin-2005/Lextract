#!/usr/bin/env python3
"""Move stored receipt images into UPLOAD_DIR and repoint the database at them.

Why this exists
---------------
``UPLOAD_DIR`` is read at upload time and the resulting absolute path is stored
on the ``bills`` row. Change the setting later and the files stay where they
were, while ``Bill.original_path`` still points at the old location — so moving
the directory by hand breaks every image, and the endpoint answers 403 (path
outside UPLOAD_DIR) or 410 (file gone).

This does both halves in one pass: relocate the files, rewrite the paths.

Usage
-----
    cd backend
    python scripts/relocate_uploads.py --from ../tmp/bills          # dry run
    python scripts/relocate_uploads.py --from ../tmp/bills --apply

Destination defaults to the configured ``UPLOAD_DIR``. Safe to re-run: files
already in place are skipped, and byte-identical duplicates are removed rather
than copied twice.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select  # noqa: E402

from app.config import get_settings  # noqa: E402
from app.database import AsyncSessionLocal, engine  # noqa: E402
from app.models import Bill  # noqa: E402


def digest(path: Path) -> str:
    """SHA-256 of a file's contents, used to spot duplicates by content."""
    return hashlib.sha256(path.read_bytes()).hexdigest()


async def relocate(source: Path, destination: Path, apply: bool) -> int:
    """Move images and repoint the database. Returns a process exit code."""
    if not source.is_dir():
        print(f"  Source directory does not exist: {source}")
        return 1

    destination.mkdir(parents=True, exist_ok=True)
    existing = {digest(f): f for f in destination.iterdir() if f.is_file() and f.name != ".gitkeep"}

    moved: dict[str, Path] = {}
    removed_duplicates: list[Path] = []

    for candidate in sorted(source.iterdir()):
        if not candidate.is_file() or candidate.name.startswith("."):
            continue
        content = digest(candidate)

        if content in existing:
            # Same bytes already at the destination. Keep the destination copy
            # and drop the source, rather than storing the image twice under
            # two different generated names.
            removed_duplicates.append(candidate)
            moved[str(candidate.resolve())] = existing[content]
            continue

        target = destination / candidate.name
        if target.exists():
            target = destination / f"{candidate.stem}_{content[:8]}{candidate.suffix}"

        print(f"  move  {candidate.name}  ->  {target.relative_to(destination.parent)}")
        if apply:
            shutil.move(str(candidate), str(target))
        existing[content] = target
        moved[str(candidate.resolve())] = target

    for duplicate in removed_duplicates:
        print(f"  dupe  {duplicate.name}  (identical file already at destination)")
        if apply:
            duplicate.unlink()

    # ---- repoint the database ------------------------------------------
    updated = 0
    async with AsyncSessionLocal() as session:
        bills = list((await session.execute(select(Bill))).scalars().all())
        for bill in bills:
            current = Path(bill.original_path)
            new_path = moved.get(str(current.resolve())) or moved.get(str(current))
            if new_path is None and current.name in {p.name for p in existing.values()}:
                new_path = destination / current.name
            if new_path is None or str(new_path.resolve()) == str(current):
                continue
            print(f"  db    {bill.filename}: {current} -> {new_path}")
            if apply:
                bill.original_path = str(new_path.resolve())
            updated += 1
        if apply:
            await session.commit()

    await engine.dispose()

    print()
    print(f"  {len(moved)} file(s) accounted for, {len(removed_duplicates)} duplicate(s), "
          f"{updated} database row(s) repointed.")
    if not apply:
        print("  DRY RUN — nothing changed. Re-run with --apply.")
    else:
        print(f"  Done. Set UPLOAD_DIR={destination} in backend/.env and restart.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--from", dest="source", required=True, help="directory to move images out of")
    parser.add_argument("--to", dest="destination", default=None, help="defaults to UPLOAD_DIR")
    parser.add_argument("--apply", action="store_true", help="actually move; otherwise dry run")
    args = parser.parse_args()

    settings = get_settings()
    destination = Path(args.destination).resolve() if args.destination else settings.upload_dir
    print(f"  source      : {Path(args.source).resolve()}")
    print(f"  destination : {destination}\n")
    return asyncio.run(relocate(Path(args.source), destination, args.apply))


if __name__ == "__main__":
    sys.exit(main())
