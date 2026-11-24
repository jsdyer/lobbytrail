"""An offline source import must publish validated data and preserve real dates."""
from contextlib import redirect_stdout
from datetime import datetime, timezone
import io
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
from zipfile import BadZipFile, ZIP_DEFLATED, ZipFile, ZipInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import build
import import_snapshot


class ImportSnapshotTests(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        (self.root / "web").mkdir()
        (self.root / "web/index.html").write_text("Test page")
        self.seed = self.root / "data/seed"
        self.seed.mkdir(parents=True)
        self.original = {"sha256": "a" * 64, "sourceSha256": "b" * 64,
                         "sourceDate": "2026-11-20", "checkedAt": "2026-11-20T12:00:00Z"}
        (self.seed / "manifest.json").write_text(json.dumps(self.original))
        (self.seed / ("a" * 64 + ".zip")).write_bytes(b"previous snapshot")
        root_patch = patch.object(build, "ROOT", self.root)
        root_patch.start()
        self.addCleanup(root_patch.stop)
        output = redirect_stdout(io.StringIO())
        output.__enter__()
        self.addCleanup(output.__exit__, None, None, None)
        self.now = datetime(2026, 11, 24, 14, tzinfo=timezone.utc)
        self.path = self.root / "official.zip"

    def archive(self, count=50_001, day=(2026, 11, 23)):
        primary = "COMLOG_ID,CLIENT_ORG_CORP_NUM,EN_CLIENT_ORG_CORP_NM_AN,FR_CLIENT_ORG_CORP_NM,COMM_DATE,PREV_COMLOG_ID_PRECEDNT,REG_TYPE_ENR\n"
        primary += "".join(f"{i},100,Test organization,,2026-11-19,,2\n" for i in range(1, count + 1))
        files = {
            "Communication_PrimaryExport.csv": primary,
            "Communication_DpohExport.csv": "COMLOG_ID,INSTITUTION\n1,Test institution\n",
            "Codes_SubjectMatterTypesExport.csv": "SUBJECT_CODE_OBJET,SMT_EN_DESC\n1,Test subject\n",
            "Communication_SubjectMattersExport.csv": "COMLOG_ID,SUBJECT_CODE_OBJET\n1,1\n",
        }
        with ZipFile(self.path, "w") as archive:
            for name, content in files.items():
                archive.writestr(ZipInfo(name, (*day, 0, 0, 0)), content, compress_type=ZIP_DEFLATED)

    def preserved(self):
        self.assertEqual(json.loads((self.seed / "manifest.json").read_text()), self.original)
        self.assertEqual((self.seed / ("a" * 64 + ".zip")).read_bytes(), b"previous snapshot")
        self.assertEqual(len(list(self.seed.iterdir())), 2)

    def test_local_download_survives_offline_rebuild_without_network_or_redating(self):
        self.archive()
        with patch.object(build, "download", side_effect=AssertionError("Must stay offline")):
            index = import_snapshot.import_snapshot(self.path, "2026-11-24T06:00:00-07:00", self.now)
            build.restore_seed()
        restored = json.loads((self.root / "dist/index.json").read_text())
        self.assertEqual(index["reports"], 50_001)
        self.assertEqual(restored["reports"], 50_001)
        self.assertEqual(restored["sourceDate"], "2026-11-23")
        self.assertEqual(restored["checkedAt"], "2026-11-24T13:00:00+00:00")
        self.assertEqual(restored["retrievedAt"], restored["checkedAt"])
        self.assertEqual(len(list((self.root / "dist/data").glob("*.json"))), 64)
        self.assertFalse((self.seed / ("a" * 64 + ".zip")).exists())

    def test_incomplete_download_preserves_previous_snapshot(self):
        self.archive(count=2)
        with self.assertRaisesRegex(ValueError, "complete report archive"):
            import_snapshot.import_snapshot(self.path, "2026-11-24T13:00:00Z", self.now)
        self.preserved()

    def test_older_export_preserves_previous_snapshot(self):
        self.archive(day=(2026, 11, 19))
        with self.assertRaisesRegex(ValueError, "export predates"):
            import_snapshot.import_snapshot(self.path, "2026-11-24T13:00:00Z", self.now)
        self.preserved()

    def test_invalid_download_dates_cannot_advance_or_backdate_the_snapshot(self):
        self.archive()
        for value in ("2026-11-24T15:00:00Z", "2026-11-19T12:00:00Z", "2026-11-24T13:00:00", "not-a-date"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                import_snapshot.import_snapshot(self.path, value, self.now)
            self.preserved()

    def test_download_cannot_predate_the_export(self):
        self.archive()
        with self.assertRaisesRegex(ValueError, "later than the download"):
            import_snapshot.import_snapshot(self.path, "2026-11-22T13:00:00Z", self.now)
        self.preserved()

    def test_invalid_or_oversized_file_preserves_previous_snapshot(self):
        self.path.write_bytes(b"<html>Access denied</html>")
        with self.assertRaises(BadZipFile):
            import_snapshot.import_snapshot(self.path, "2026-11-24T13:00:00Z", self.now)
        self.preserved()
        with patch.object(build, "LIMIT", 10), self.assertRaisesRegex(ValueError, "size limit"):
            import_snapshot.import_snapshot(self.path, "2026-11-24T13:00:00Z", self.now)
        self.preserved()


if __name__ == "__main__":
    unittest.main()
