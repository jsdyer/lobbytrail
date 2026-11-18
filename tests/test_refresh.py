"""Publishing must retain the last verified export if the registry denies a refresh."""
from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from zipfile import ZipFile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import build


class RefreshTests(unittest.TestCase):
    def setUp(self):
        self.output = redirect_stdout(io.StringIO())
        self.output.__enter__()
        self.addCleanup(self.output.__exit__, None, None, None)

    def test_scheduled_denial_is_an_error_and_never_restores_old_data(self):
        error = HTTPError(build.SOURCE, 403, "Forbidden", {}, None)
        with patch.object(sys, "argv", ["build.py", "--refresh-seed"]), \
                patch.object(build, "download", side_effect=error), \
                patch.object(build, "restore_seed") as fallback, \
                patch.object(build, "preserve_snapshot") as persist:
            with self.assertRaises(HTTPError):
                build.main()
            fallback.assert_not_called()
            persist.assert_not_called()

    def test_local_first_run_can_use_the_verified_seed_on_a_denial(self):
        error = HTTPError(build.SOURCE, 403, "Forbidden", {}, None)
        with patch.object(sys, "argv", ["build.py"]), \
                patch.object(build, "download", side_effect=error), \
                patch.object(build, "restore_seed") as fallback:
            build.main()
            fallback.assert_called_once_with()

    def test_refresh_rejects_a_source_older_than_the_stored_edition(self):
        stub = io.BytesIO()
        with ZipFile(stub, "w") as archive:
            archive.writestr("example", "data")
        with patch.object(sys, "argv", ["build.py", "--refresh-seed"]), \
                patch.object(build, "download", return_value=stub.getvalue()), \
                patch.object(build, "build", return_value={"sourceDate": "2026-01-05"}), \
                patch.object(build, "seed_manifest", return_value={"sourceDate": "2026-01-12"}), \
                patch.object(build, "preserve_snapshot") as persist:
            with self.assertRaisesRegex(ValueError, "predates"):
                build.main()
            persist.assert_not_called()

    def test_offline_release_uses_the_sample_capture_dates_and_all_shards(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "web").mkdir()
            (root / "data").mkdir()
            (root / "data/seed").symlink_to(build.ROOT / "data/seed")
            with patch.object(build, "ROOT", root), patch.object(sys, "argv", ["build.py", "--seed"]), \
                    patch.object(build, "download") as fetch:
                build.main()
                fetch.assert_not_called()
            index = json.loads((root / "dist/index.json").read_text())
            self.assertEqual(index["snapshotKind"], "bundled")
            self.assertGreaterEqual(index["retrievedAt"][:10], index["sourceDate"])
            self.assertEqual(len(list((root / "dist/data").glob("*.json"))), build.BUCKETS)

    def test_incomplete_official_export_cannot_become_the_new_snapshot(self):
        stub = io.BytesIO()
        with ZipFile(stub, "w") as archive:
            archive.writestr("wrong.csv", "nothing")
        with patch.object(sys, "argv", ["build.py", "--refresh-seed"]), \
                patch.object(build, "download", return_value=stub.getvalue()), \
                patch.object(build, "preserve_snapshot") as persist:
            with self.assertRaises(KeyError):
                build.main()
            persist.assert_not_called()


if __name__ == "__main__":
    unittest.main()
