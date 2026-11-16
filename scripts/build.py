#!/usr/bin/env python3
"""Build an organization-level index from the Lobbying Commission's reports."""
import argparse
from collections import Counter, defaultdict
import csv
from datetime import date
from hashlib import sha256
from http.client import IncompleteRead
import io
import json
from pathlib import Path
from shutil import copytree, rmtree
from time import sleep
from urllib.error import URLError
from urllib.request import Request, urlopen
from zipfile import BadZipFile, ZipFile

ROOT = Path(__file__).resolve().parents[1]
SOURCE = "https://lobbycanada.gc.ca/media/mqbbmaqk/communications_ocl_cal.zip"
LIMIT = 70_000_000
BUCKETS = 64
SEED_SHA256 = "2e60f9f0bb2226144156f8933d58925082c1e49e66e7c9a1977d63ccec8a6851"
SEED_PARTS = 6
SEED_LIMIT = 18_000_000


def acquire(path):
    if path:
        return ZipFile(path)
    request = Request(SOURCE, headers={"User-Agent": "CoolCode Lobby Trail"})
    for attempt in range(3):
        try:
            with urlopen(request, timeout=100) as response:
                data = response.read(LIMIT + 1)
            if len(data) > LIMIT:
                raise ValueError("Communication archive exceeded the expected size")
            return ZipFile(io.BytesIO(data))
        except (URLError, TimeoutError, IncompleteRead):
            if attempt == 2:
                raise
            sleep(2 ** attempt)


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


def restore_seed():
    """Copy only the expected, verified index and report shards."""
    parts = ROOT / "data" / "seed"
    zipped = b"".join((parts / f"part-{i:02d}").read_bytes() for i in range(SEED_PARTS))
    if sha256(zipped).hexdigest() != SEED_SHA256:
        raise ValueError("Bundled snapshot hash mismatch")
    shards = [f"data/{i}.json" for i in range(BUCKETS)]
    expected = {"index.json", *shards}
    with ZipFile(io.BytesIO(zipped)) as archive:
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

        dist = prepare_output()
        index["snapshotKind"] = "bundled"
        write(dist / "index.json", index)
        for name in shards:
            (dist / name).write_bytes(archive.read(name))
    print(f"Restored bundled source export {source_date} with {index['reports']:,} in-house reports "
          f"for {len(entries):,} organizations", flush=True)


def build(archive):
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
    write(dist / "index.json", {
        "sourceDate": date(*archive.getinfo(primary).date_time[:3]).isoformat(),
        "from": min(r[2] for r in reports.values()),
        "to": max(r[2] for r in reports.values()),
        "reports": len(reports), "institutions": institutions, "subjects": subjects,
        "buckets": BUCKETS, "entries": entries,
    })
    for i, shard in enumerate(shards):
        write(dist / "data" / f"{i}.json", shard)
    print(f"Wrote {len(reports):,} in-house reports for {len(entries):,} organizations", flush=True)


def main():
    cli = argparse.ArgumentParser(description=__doc__)
    inputs = cli.add_mutually_exclusive_group()
    inputs.add_argument("--input", type=Path, help="Read a local official communication ZIP")
    inputs.add_argument("--seed", action="store_true", help="Build from the bundled, dated snapshot")
    args = cli.parse_args()
    if args.seed:
        restore_seed()
        return
    try:
        with acquire(args.input) as archive:
            build(archive)
    except (URLError, TimeoutError, IncompleteRead, BadZipFile, KeyError, ValueError) as exc:
        if args.input:
            raise
        print(f"Official export unavailable or invalid ({exc}). Using the bundled snapshot.", flush=True)
        restore_seed()


if __name__ == "__main__":
    main()
