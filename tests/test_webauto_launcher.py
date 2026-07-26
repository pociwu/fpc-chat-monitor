import unittest
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "webauto.sh"


class WebautoLauncherContractTests(unittest.TestCase):
    def test_selects_a_versioned_program_and_archives_other_versions(self):
        source = SCRIPT.read_text(encoding="utf-8")
        self.assertIn("fpc_watch_ui_login_telegram_v[0-9]", source)
        self.assertIn("sort -V", source)
        self.assertIn('HISTORY_DIR="$APP_DIR/History"', source)
        self.assertIn('for candidate in fpc_watch_ui_login_telegram_*.py', source)
        self.assertIn('mv -- "$candidate" "$destination"', source)

    def test_refuses_to_fall_back_to_a_legacy_filename(self):
        source = SCRIPT.read_text(encoding="utf-8")
        self.assertIn("no versioned program found", source)
        self.assertIn('exec python "$latest"', source)
        self.assertNotIn("python fpc_watch_ui_login_telegram_v2025", source)


if __name__ == "__main__":
    unittest.main()
