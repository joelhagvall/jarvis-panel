#!/usr/bin/env python3
"""Save or compare board.py's output for a fixed set of flag combinations; never builds or flashes.

    .venv/bin/python tools/board_snapshot.py save NEW_DIR      before a refactor of board.py/board_config/
    .venv/bin/python tools/board_snapshot.py compare NEW_DIR   after it: exit 1 on any differing output

A refactor that should not change the firmware must compare equal for every case. Cases that already fail
(gen.py with --voice, the default generator without --remote) must fail the same way.
"""
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
REMOTE = ['--remote', 'http://jarvis.local:8123/local/hallpanel']
STYLE = ROOT / 'styles/glass-v2'
CASES = [
    (STYLE, ['--voice', 'jarvis_orb.py', *REMOTE], None),
    (STYLE, ['--voice', '--direct-display', 'jarvis_orb.py', *REMOTE], None),
    (STYLE, ['--voice', '--direct-display', '--double-buffer', 'jarvis_orb.py', *REMOTE], None),
    (STYLE, ['--voice', '--fast-display', 'jarvis_orb.py', *REMOTE], None),
    (STYLE, ['--voice', '--es', 'jarvis_orb.py', *REMOTE], '0123456789abcdef0123456789abcdef'),
    (STYLE, ['--bench', '--voice', 'jarvis_orb.py', *REMOTE], None),
    (STYLE, ['--bench', 'jarvis_orb.py', '--demo', *REMOTE], None),
    (STYLE, ['--sim', 'jarvis_orb.py'], None),
    (STYLE, ['--sim', '--bench', 'jarvis_orb.py'], None),
    (STYLE, ['jarvis_orb.py', *REMOTE], None),
    (STYLE, ['--voice', 'gen.py'], None),
    (STYLE, ['gen.py'], None),
    (ROOT, ['--voice'], None),
    (ROOT, ['--bench', '--voice'], None),
]


def outputs():
    for number, (cwd, args, build_id) in enumerate(CASES, 1):
        env = {k: v for k, v in os.environ.items() if not k.startswith(('HALLPANEL_', 'JARVIS_'))}
        if build_id:
            env['HALLPANEL_BUILD_ID'] = build_id
        run = subprocess.run([sys.executable, str(ROOT / 'board.py'), *args], cwd=cwd, env=env, capture_output=True)
        # A failing case is recorded by its last error line, so it must keep failing the same way.
        error = run.stderr.decode(errors='replace').strip().splitlines()[-1:] if run.returncode else []
        yield f'{number}.json', run.stdout if not run.returncode else ('FAILED: ' + ''.join(error)).encode()


def main():
    if len(sys.argv) != 3 or sys.argv[1] not in ('save', 'compare'):
        sys.exit(__doc__)
    folder = Path(sys.argv[2])
    if sys.argv[1] == 'save':
        folder.mkdir(parents=True)
        for name, data in outputs():
            (folder / name).write_bytes(data)
        print(f'saved {len(CASES)} cases in {folder}')
        return
    differ = [name for name, data in outputs() if (folder / name).read_bytes() != data]
    print('identical' if not differ else 'differ: ' + ', '.join(differ))
    sys.exit(1 if differ else 0)


if __name__ == '__main__':
    main()
