import importlib.util
import tempfile
import unittest
from pathlib import Path


SOURCE = Path(__file__).resolve().parents[1] / "fpc_watch_ui_login_telegram_v2026.08.30.1.py"
SPEC = importlib.util.spec_from_file_location("watcher_version", SOURCE)
watcher = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(watcher)


class VersionLabelTests(unittest.TestCase):
    def test_version_has_the_deployment_format(self):
        self.assertRegex(watcher.APP_VERSION, r"^v\d{4}\.\d{2}\.\d{2}\.\d+$")
        self.assertEqual(SOURCE.stem, f"fpc_watch_ui_login_telegram_{watcher.APP_VERSION}")

    def test_new_message_csv_starts_with_the_version_label(self):
        app = object.__new__(watcher.App)
        app.csv_seen = {}
        app.log_sys = lambda _text: None
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "messages.csv"
            app._csv_append_row(path, ["2026-07-26 12:00:00", "群組", "完整訊息", "發送者", "1"])
            lines = path.read_text(encoding="utf-8-sig").splitlines()
        self.assertEqual(lines[0], f"# FPC Watch {watcher.APP_VERSION}")
        self.assertEqual(lines[1].split(",")[:3], ["時間", "群組", "內容"])


if __name__ == "__main__":
    unittest.main()
