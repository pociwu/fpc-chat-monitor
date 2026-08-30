import importlib.util
import os
import tempfile
import unittest
from pathlib import Path


SOURCE = Path(__file__).resolve().parents[1] / "fpc_watch_ui_login_telegram_v2026.08.30.2.py"
SPEC = importlib.util.spec_from_file_location("watcher_retention_ui", SOURCE)
watcher = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(watcher)


class AttachmentRetentionTests(unittest.TestCase):
    def test_attachment_path_components_cannot_escape_storage_root(self):
        self.assertEqual(watcher._safe_attachment_component("..", "unknown"), "unknown")
        self.assertEqual(watcher._safe_attachment_component("../report.pdf"), "_report.pdf")

    def test_cleanup_only_removes_expired_files_inside_attachments(self):
        now = 2_000_000_000.0
        with tempfile.TemporaryDirectory() as temp_dir:
            out_dir = Path(temp_dir)
            old_file = out_dir / "attachments" / "群組" / "20260801" / "old.pdf"
            boundary_file = out_dir / "attachments" / "群組" / "20260823" / "boundary.pdf"
            fresh_file = out_dir / "attachments" / "群組" / "20260830" / "fresh.png"
            outside_file = out_dir / "messages_20260830.csv"
            for path in (old_file, boundary_file, fresh_file, outside_file):
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b"test")

            cutoff = now - 7 * 86400
            os.utime(old_file, (cutoff - 1, cutoff - 1))
            os.utime(boundary_file, (cutoff, cutoff))
            os.utime(fresh_file, (now, now))
            os.utime(outside_file, (cutoff - 100, cutoff - 100))

            result = watcher._cleanup_expired_attachments(out_dir, retention_days=7, now=now)

            self.assertEqual(result["removed_files"], 1)
            self.assertFalse(old_file.exists())
            self.assertTrue(boundary_file.exists())
            self.assertTrue(fresh_file.exists())
            self.assertTrue(outside_file.exists())

    def test_non_positive_retention_disables_cleanup(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            old_file = Path(temp_dir) / "attachments" / "old.pdf"
            old_file.parent.mkdir(parents=True)
            old_file.write_bytes(b"test")

            result = watcher._cleanup_expired_attachments(Path(temp_dir), retention_days=0, now=2_000_000_000.0)

            self.assertTrue(old_file.exists())
            self.assertEqual(result["removed_files"], 0)
            self.assertFalse(result["enabled"])

    def test_attachment_root_symlink_is_never_followed(self):
        with tempfile.TemporaryDirectory() as temp_dir, tempfile.TemporaryDirectory() as outside_dir:
            out_dir = Path(temp_dir)
            outside_file = Path(outside_dir) / "old.pdf"
            outside_file.write_bytes(b"outside")
            os.utime(outside_file, (1, 1))
            try:
                os.symlink(outside_dir, out_dir / "attachments", target_is_directory=True)
            except (OSError, NotImplementedError):
                self.skipTest("directory symlinks are unavailable on this host")

            result = watcher._cleanup_expired_attachments(out_dir, retention_days=7, now=2_000_000_000.0)

            self.assertTrue(outside_file.exists())
            self.assertEqual(result["removed_files"], 0)
            self.assertTrue(result["errors"])


class ResponsiveUiTests(unittest.TestCase):
    def test_metrics_fit_common_screen_sizes_and_scale_monotonically(self):
        screens = [(1024, 768), (1366, 768), (1920, 1080), (2560, 1440)]
        metrics = [watcher._responsive_ui_metrics(width, height, dpi=96) for width, height in screens]

        for (screen_width, screen_height), item in zip(screens, metrics):
            self.assertLessEqual(item["window_width"], screen_width)
            self.assertLessEqual(item["window_height"], screen_height)
            self.assertGreaterEqual(item["browser_width"], 800)
            self.assertGreaterEqual(item["browser_height"], 600)
            self.assertGreater(item["message_content_width"], item["time_column_width"])

        font_sizes = [item["font_size"] for item in metrics]
        self.assertEqual(font_sizes, sorted(font_sizes))
        self.assertTrue(metrics[0]["compact"])
        self.assertFalse(metrics[-1]["compact"])

    def test_extreme_dpi_is_clamped(self):
        low = watcher._responsive_ui_metrics(1920, 1080, dpi=50)
        high = watcher._responsive_ui_metrics(1920, 1080, dpi=300)

        self.assertGreaterEqual(low["tk_scaling"], 1.0)
        self.assertLessEqual(high["tk_scaling"], 2.5)
        self.assertLessEqual(high["window_width"], 1920)
        self.assertLessEqual(high["window_height"], 1080)

    def test_high_dpi_uses_logical_dimensions_for_font_and_compact_mode(self):
        metrics = watcher._responsive_ui_metrics(2560, 1440, dpi=192)

        self.assertLessEqual(metrics["font_size"], 11)
        self.assertTrue(metrics["compact"])
        self.assertGreater(metrics["window_width"], 2000)

    def test_high_dpi_small_screen_still_fits_physical_bounds(self):
        metrics = watcher._responsive_ui_metrics(1024, 768, dpi=192)

        self.assertLessEqual(metrics["window_width"], 1024)
        self.assertLessEqual(metrics["window_height"], 768)


if __name__ == "__main__":
    unittest.main()
