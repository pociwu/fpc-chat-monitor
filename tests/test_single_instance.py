import importlib.util
import io
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr
from pathlib import Path
from unittest.mock import Mock, patch


SOURCE = Path(__file__).resolve().parents[1] / "fpc_watch_ui_login_telegram_v2026.08.31.1.py"
SPEC = importlib.util.spec_from_file_location("watcher_single_instance", SOURCE)
watcher = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(watcher)


class SingleInstanceTests(unittest.TestCase):
    def test_second_process_cannot_acquire_the_same_runtime_lock(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            lock_path = Path(temp_dir) / ".fpc_chat_monitor.lock"
            holder_code = """
import importlib.util
import pathlib
import sys
spec = importlib.util.spec_from_file_location('holder_watcher', sys.argv[1])
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
lock = module.SingleInstanceLock(pathlib.Path(sys.argv[2]))
print('READY' if lock.acquire() else 'FAILED', flush=True)
sys.stdin.readline()
lock.release()
"""
            process = subprocess.Popen(
                [sys.executable, "-c", holder_code, str(SOURCE), str(lock_path)],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            try:
                self.assertEqual(process.stdout.readline().strip(), "READY")
                second = watcher.SingleInstanceLock(lock_path)
                self.assertFalse(second.acquire())
            finally:
                process.stdin.write("\n")
                process.stdin.flush()
                _, stderr = process.communicate(timeout=10)
                self.assertEqual(process.returncode, 0, stderr)

            self.assertTrue(second.acquire())
            second.release()

    def test_main_exits_before_creating_ui_when_lock_is_held(self):
        fake_lock = Mock()
        fake_lock.acquire.return_value = False

        with patch.object(watcher, "SingleInstanceLock", return_value=fake_lock), \
                patch.object(watcher, "App") as app_class:
            with redirect_stderr(io.StringIO()):
                self.assertEqual(watcher.main(), 2)

        app_class.assert_not_called()


if __name__ == "__main__":
    unittest.main()
