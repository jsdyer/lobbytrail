#!/usr/bin/env python3
"""Build an organization-level index from the Lobbying Commission's reports."""
import argparse
from collections import Counter, defaultdict
import csv
from datetime import date, datetime, timezone
from hashlib import sha256
from http.client import IncompleteRead
import io
import json
from pathlib import Path
from shutil import copytree, rmtree
from tempfile import TemporaryDirectory
from time import sleep
from urllib.error import URLError
from urllib.request import Request, urlopen
from public_download import download_bytes
from zipfile import BadZipFile, ZIP_DEFLATED, ZipFile, ZipInfo

ROOT = Path(__file__).resolve().parents[1]
SOURCE = "https://lobbycanada.gc.ca/media/mqbbmaqk/communications_ocl_cal.zip"
LIMIT = 70_000_000
BUCKETS = 64
SEED_LIMIT = 18_000_000


def download():
    return download_bytes(SOURCE, limit=LIMIT, timeout=100)


def rows(archive, name, columns):
    with archive.open(name) as raw:
        reader = csv.DictReader(io.TextIOWrapper(raw, encoding="cp1252", errors="replace", newline=""))
        if not set(columns).issubset(reader.fieldnames or []):
            raise ValueError(f"{name} no longer has expected columns")
        for row in reader:
            yield tuple((row[c] or "").strip() for c in columns)


def integer(value):
    return int(value) if value.isascii() and value.isdigit() else None


def write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")


def prepare_output():
    dist = ROOT / "dist"
    if dist.exists():
        rmtree(dist)
    copytree(ROOT / "web", dist)
    (dist / "data").mkdir()
    return dist


def seed_manifest():
    return json.loads((ROOT / "data/seed/manifest.json").read_text())


def archive_path(directory, manifest):
    digest = manifest["sha256"]
    if len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
        raise ValueError("Invalid snapshot digest")
    return directory / f"{digest}.zip"


def validate_seed(archive):
    shards = [f"data/{i}.json" for i in range(BUCKETS)]
    expected = {"index.json", *shards}
    items = archive.infolist()
    if len(items) != len(expected) or {item.filename for item in items} != expected:
        raise ValueError("Bundled snapshot contains unexpected or duplicate paths")
    if sum(item.file_size for item in items) > SEED_LIMIT:
        raise ValueError("Bundled snapshot exceeds the uncompressed limit")

    index = json.loads(archive.read("index.json"))
    source_date = date.fromisoformat(index["sourceDate"])
    first_date = date.fromisoformat(index["from"])
    last_date = date.fromisoformat(index["to"])
    entries = index["entries"]
    if index["buckets"] != BUCKETS or not first_date <= last_date <= source_date or not entries:
        raise ValueError("Bundled snapshot index metadata is invalid")
    expected_counts = {str(row[0]): row[2] for row in entries}
    if len(expected_counts) != len(entries) or sum(expected_counts.values()) != index["reports"]:
        raise ValueError("Bundled snapshot index counts are inconsistent")

    seen = set()
    for bucket, name in enumerate(shards):
        organizations = json.loads(archive.read(name))
        for org_id, reports in organizations.items():
            if (org_id in seen or int(org_id) % BUCKETS != bucket
                    or len(reports) != expected_counts.get(org_id)):
                raise ValueError(f"Bundled snapshot contains inconsistent counts in {name}")
            seen.add(org_id)
    if seen != expected_counts.keys():
        raise ValueError("Bundled snapshot is missing one or more organizations")
    return index


def restore_seed():
    """Validate the archived data, then copy its index and expected report shards."""
    seed = ROOT / "data" / "seed"
    manifest = seed_manifest()
    zipped = archive_path(seed, manifest).read_bytes()
    if sha256(zipped).hexdigest() != manifest["sha256"]:
        raise ValueError("Bundled snapshot hash mismatch")
    with ZipFile(io.BytesIO(zipped)) as archive:
        index = validate_seed(archive)
        if index["sourceDate"] != manifest["sourceDate"]:
            raise ValueError("Bundled export date differs from its manifest")
        dist = prepare_output()
        index["snapshotKind"] = "bundled"
        index["checkedAt"] = manifest.get("checkedAt", index.get("retrievedAt"))
        write(dist / "index.json", index)
        for i in range(BUCKETS):
            name = f"data/{i}.json"
            (dist / name).write_bytes(archive.read(name))
    print(f"Restored source export {index['sourceDate']} with {index['reports']:,} reports", flush=True)


def preserve_snapshot(index, source_hash):
    """Archive exactly the browser data, validating it before touching the committed seed."""
    seed = ROOT / "data" / "seed"
    dist = ROOT / "dist"
    with TemporaryDirectory(dir=seed.parent) as temp:
        staged = Path(temp)
        buffer = io.BytesIO()
        with ZipFile(buffer, "w") as archive:
            for name in ["index.json", *(f"data/{i}.json" for i in range(BUCKETS))]:
                archive.writestr(ZipInfo(name), (dist / name).read_bytes(), compress_type=ZIP_DEFLATED)
        payload = buffer.getvalue()
        with ZipFile(io.BytesIO(payload)) as archive:
            validate_seed(archive)
        previous = archive_path(seed, seed_manifest())
        manifest = {"sha256": sha256(payload).hexdigest(), "sourceDate": index["sourceDate"],
                    "sourceSha256": source_hash, "checkedAt": index.get("checkedAt")}
        destination = archive_path(seed, manifest)
        (staged / "snapshot.zip").write_bytes(payload)
        (staged / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
        # Switch the manifest only after the matching archive exists. Git publishes both together.
        (staged / "snapshot.zip").replace(destination)
        (staged / "manifest.json").replace(seed / "manifest.json")
        if previous != destination:
            previous.unlink(missing_ok=True)


def build(archive, retrieved=None):
    primary = "Communication_PrimaryExport.csv"
    columns = ("COMLOG_ID", "CLIENT_ORG_CORP_NUM", "EN_CLIENT_ORG_CORP_NM_AN", "FR_CLIENT_ORG_CORP_NM",
               "COMM_DATE", "PREV_COMLOG_ID_PRECEDNT", "REG_TYPE_ENR")
    reports, superseded = {}, set()
    for report, entity, english, french, day, prev, kind in rows(archive, primary, columns):
        pid = integer(prev)
        if pid:
            superseded.add(pid)
        rid, oid = integer(report), integer(entity)
        if not rid or not oid or kind not in {"2", "3"}:
            continue
        name = next((n for n in (english, french) if n and n.lower() != "null"), "")
        try:
            day = date.fromisoformat(day).isoformat()
        except ValueError:
            continue
        if name:
            reports[rid] = (oid, name, day)
    for rid in superseded:
        reports.pop(rid, None)
    if len(reports) < 50_000:
        raise ValueError(f"Expected a complete report archive, got {len(reports)} in-house reports")

    inst = defaultdict(set)
    institutions = []
    inst_codes = {}
    for report, name in rows(archive, "Communication_DpohExport.csv", ("COMLOG_ID", "INSTITUTION")):
        rid = integer(report)
        if rid in reports and name and name.lower() != "null":
            if name not in inst_codes:
                inst_codes[name] = len(institutions)
                institutions.append(name)
            inst[rid].add(inst_codes[name])

    subjects = []
    subject_codes = {}
    for code, name in rows(archive, "Codes_SubjectMatterTypesExport.csv", ("SUBJECT_CODE_OBJET", "SMT_EN_DESC")):
        if code not in subject_codes:
            subject_codes[code] = len(subjects)
            subjects.append(name)
    subj = defaultdict(set)
    for report, code in rows(archive, "Communication_SubjectMattersExport.csv", ("COMLOG_ID", "SUBJECT_CODE_OBJET")):
        rid = integer(report)
        if rid in reports and code in subject_codes:
            subj[rid].add(subject_codes[code])

    details = defaultdict(list)
    aliases = defaultdict(Counter)
    newest = {}
    for rid, (oid, name, day) in reports.items():
        details[oid].append([day, rid, sorted(inst[rid]), sorted(subj[rid])])
        aliases[oid][name] += 1
        if (day, rid) >= newest.get(oid, ("", 0, ""))[:2]:
            newest[oid] = (day, rid, name)

    entries = []
    shards = [{} for _ in range(BUCKETS)]
    for oid, records in details.items():
        records.sort(reverse=True)
        name = newest[oid][2]
        alt = [n for n, _ in aliases[oid].most_common() if n != name][:12]
        its = sorted({i for row in records for i in row[2]})
        sts = sorted({i for row in records for i in row[3]})
        entries.append([oid, name, len(records), alt, its, sts])
        shards[oid % BUCKETS][oid] = records
    entries.sort(key=lambda row: (-row[2], row[1].casefold()))

    dist = prepare_output()
    index = {
        "sourceDate": date(*archive.getinfo(primary).date_time[:3]).isoformat(),
        "from": min(r[2] for r in reports.values()),
        "to": max(r[2] for r in reports.values()),
        "reports": len(reports), "institutions": institutions, "subjects": subjects,
        "buckets": BUCKETS, "entries": entries,
    }
    if retrieved:
        index["retrievedAt"] = retrieved
        index["checkedAt"] = retrieved
    write(dist / "index.json", index)
    for i, shard in enumerate(shards):
        write(dist / "data" / f"{i}.json", shard)
    print(f"Wrote {len(reports):,} in-house reports for {len(entries):,} organizations", flush=True)
    return index


def main():
    cli = argparse.ArgumentParser(description=__doc__)
    inputs = cli.add_mutually_exclusive_group()
    inputs.add_argument("--input", type=Path, help="Read a local official communication ZIP")
    inputs.add_argument("--seed", action="store_true", help="Build from the bundled, dated snapshot")
    inputs.add_argument("--refresh-seed", action="store_true",
                        help="Require a live official download and persist a verified snapshot")
    args = cli.parse_args()
    if args.seed:
        restore_seed()
        return
    if args.input:
        with ZipFile(args.input) as archive:
            build(archive)
        return
    try:
        payload = download()
        source_hash = sha256(payload).hexdigest()
        with ZipFile(io.BytesIO(payload)) as archive:
            index = build(archive, datetime.now(timezone.utc).isoformat(timespec="seconds"))
        manifest = seed_manifest()
        if date.fromisoformat(index["sourceDate"]) < date.fromisoformat(manifest["sourceDate"]):
            raise ValueError("Official export predates the stored snapshot")
        if args.refresh_seed:
            if source_hash == manifest["sourceSha256"]:
                restore_seed()  # verify the retained archive before declaring success
                manifest["checkedAt"] = index["checkedAt"]
                target = ROOT / "data/seed/manifest.json"
                temporary = target.with_suffix(".tmp")
                temporary.write_text(json.dumps(manifest, indent=2) + "\n")
                temporary.replace(target)
                restored = json.loads((ROOT / "dist/index.json").read_text())
                restored["checkedAt"] = index["checkedAt"]
                write(ROOT / "dist/index.json", restored)
                print("Official archive is unchanged; successful check recorded")
            else:
                preserve_snapshot(index, source_hash)
    except (URLError, TimeoutError, IncompleteRead, BadZipFile, KeyError, ValueError) as exc:
        if args.refresh_seed:
            raise
        print(f"Official export unavailable or invalid ({exc}). Using the bundled snapshot.", flush=True)
        restore_seed()


if __name__ == "__main__":
    main()
