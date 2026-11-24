#!/usr/bin/env python3
"""Validate a browser-downloaded official ZIP and save it for Pages publication."""
import argparse
from datetime import datetime, timezone
from hashlib import sha256
import io
from pathlib import Path
from zipfile import BadZipFile, ZipFile

import build


def acquisition_time(value, now):
    stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if stamp.tzinfo is None:
        raise ValueError("The download time must include Z or a timezone offset")
    stamp = stamp.astimezone(timezone.utc)
    if stamp > now:
        raise ValueError("The download time cannot be in the future")
    return stamp


def import_snapshot(path, retrieved_at, now=None):
    now = now or datetime.now(timezone.utc)
    retrieved = acquisition_time(retrieved_at, now)
    manifest = build.seed_manifest()
    previous_check = manifest.get("checkedAt")
    if previous_check and retrieved < acquisition_time(previous_check, now):
        raise ValueError("The download predates the last verified source check")
    with Path(path).open("rb") as source:
        payload = source.read(build.LIMIT + 1)
    if len(payload) > build.LIMIT:
        raise ValueError("Official archive exceeds the source size limit")
    with ZipFile(io.BytesIO(payload)) as archive:
        if len(archive.namelist()) != len(set(archive.namelist())):
            raise ValueError("Official archive contains duplicate paths")
        index = build.build(archive, retrieved.isoformat(timespec="seconds"))
    if index["sourceDate"] < manifest["sourceDate"]:
        raise ValueError("Official export predates the stored snapshot")
    if index["sourceDate"] > retrieved.date().isoformat():
        raise ValueError("The source export date is later than the download date")
    # The same parser, completeness checks and seed validation as an online refresh.
    # Only generated dist/ changes before this validated snapshot is saved.
    build.preserve_snapshot(index, sha256(payload).hexdigest())
    print("Saved verified data/seed/ snapshot. Review and commit data/seed/ together.")
    return index


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path, help="Unmodified official communications ZIP")
    parser.add_argument("--retrieved-at", required=True,
                        help="Actual browser download time, e.g. 2026-11-24T06:00:00-07:00")
    args = parser.parse_args()
    try:
        import_snapshot(args.input, args.retrieved_at)
    except (OSError, BadZipFile, KeyError, ValueError) as error:
        parser.exit(1, f"Import failed; do not publish: {error}\n")


if __name__ == "__main__":
    main()
