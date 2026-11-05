"""tools/stage_hunks.py stages only the chosen hunks and leaves the rest of the file unstaged."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

TOOL = Path(__file__).resolve().parents[1] / 'tools/stage_hunks.py'


class StageHunksTest(unittest.TestCase):
    def test_only_the_chosen_hunk_is_staged(self):
        with tempfile.TemporaryDirectory() as tmp:
            run = lambda *a: subprocess.run(a, cwd=tmp, check=True, capture_output=True, text=True).stdout
            run('git', 'init', '-q'); run('git', 'config', 'user.email', 't@t'); run('git', 'config', 'user.name', 't')
            doc = Path(tmp, 'doc.md')
            doc.write_text(''.join(f'line {i}\n' for i in range(20)))
            run('git', 'add', 'doc.md'); run('git', 'commit', '-qm', 'base')
            lines = doc.read_text().splitlines(keepends=True)
            lines[2] = 'theirs\n'; lines[15] = 'mine\n'          # another session's hunk, then this session's
            doc.write_text(''.join(lines))
            listed = subprocess.run([sys.executable, str(TOOL), '--list', 'doc.md'], cwd=tmp, check=True,
                                    capture_output=True, text=True).stdout
            self.assertIn('doc.md:1', listed); self.assertIn('doc.md:2', listed)
            subprocess.run([sys.executable, str(TOOL), 'doc.md:2'], cwd=tmp, check=True, capture_output=True)
            staged = run('git', 'diff', '--cached')
            self.assertIn('+mine', staged); self.assertNotIn('theirs', staged)
            self.assertIn('+theirs', run('git', 'diff'))       # still in the working tree, unstaged
            bad = subprocess.run([sys.executable, str(TOOL), 'doc.md:3'], cwd=tmp, capture_output=True, text=True)
            self.assertNotEqual(bad.returncode, 0)

    def test_an_insertion_before_a_repeated_block_lands_on_its_own_line(self):
        """2026-11-01: `git apply --unidiff-zero` put hallpanel_server.py's new `/speaker/reconnect` branch two lines
        too low, inside the next `if not self.authorised():`, after an unselected hunk above it changed the line
        count; the staged file did not compile. Staging must place a hunk exactly where git diff says."""
        with tempfile.TemporaryDirectory() as tmp:
            run = lambda *a: subprocess.run(a, cwd=tmp, check=True, capture_output=True, text=True).stdout
            run('git', 'init', '-q'); run('git', 'config', 'user.email', 't@t'); run('git', 'config', 'user.name', 't')
            block = lambda name: [f'if path == "{name}":\n', '    if not ok():\n', '        return\n', '    go()\n']
            base = ['top\n', 'theirs here\n', *block('/speaker'), *block('/speed'), 'end\n']
            src = Path(tmp, 'server.py')
            src.write_text(''.join(base))
            run('git', 'add', 'server.py'); run('git', 'commit', '-qm', 'base')
            work = list(base)
            work[1:2] = ['theirs\n', 'grew\n', 'by three\n']           # another session: one line becomes three
            i = work.index('if path == "/speed":\n')
            work[i:i] = block('/speaker/reconnect')                     # this session: a new branch before /speed
            src.write_text(''.join(work))
            subprocess.run([sys.executable, str(TOOL), 'server.py:2'], cwd=tmp, check=True, capture_output=True)
            want = list(base)
            j = want.index('if path == "/speed":\n')
            want[j:j] = block('/speaker/reconnect')
            self.assertEqual(run('git', 'show', ':server.py'), ''.join(want))
            self.assertEqual(src.read_text(), ''.join(work))            # the working tree is untouched


if __name__ == '__main__':
    unittest.main()
