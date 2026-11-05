"""tools/flash_panel.sh keeps its whole output in a private log and still exits with its own status."""
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = (ROOT / 'tools/flash_panel.sh').read_text()


class FlashLogTest(unittest.TestCase):
    def test_the_run_is_logged_and_the_exit_status_kept(self):
        self.assertIn('RUN_LOG="$ROOT/.panel-ci/flash-logs/', SCRIPT)
        self.assertIn('tee -a "$RUN_LOG" < "$FIFO" &', SCRIPT)
        self.assertIn('tee -a "$RUN_LOG" < "$FIFO.err" >&2 &', SCRIPT)
        self.assertRegex(SCRIPT, r"trap '[^']*finish \"\$code\"; exec >&- 2>&-; wait \"\$TEE_PID\" \"\$TEE_ERR_PID\"[^']*exit \"\$code\"' EXIT")
        # The same FIFO pattern, run: output shown, logged, and the status of the script itself.
        setup = SCRIPT[SCRIPT.index('FIFO=$(mktemp'):SCRIPT.index('printf \'Flash log:')]
        with tempfile.TemporaryDirectory() as tmp:
            for code in (0, 3):
                log = Path(tmp) / f'{code}.log'
                body = (f'set -eu\nRUN_LOG={log}\n' + setup +
                        "trap 'code=$?; echo \"END $code\"; exec >&- 2>&-; wait \"$TEE_PID\" \"$TEE_ERR_PID\" 2>/dev/null; exit \"$code\"' EXIT\n"
                        f'echo out; echo err >&2; exit {code}\n')
                run = subprocess.run(['sh', '-c', body], capture_output=True, text=True, timeout=20)
                self.assertEqual(run.returncode, code)
                self.assertIn('out', run.stdout)
                self.assertIn('err', run.stderr)                       # stderr stays stderr
                self.assertEqual(sorted(log.read_text().split()), sorted(['out', 'err', 'END', str(code)]))


if __name__ == '__main__':
    unittest.main()
