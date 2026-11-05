#!/usr/bin/env python3
"""Stage only chosen hunks of a file another session also changed (AGENTS.md: never commit another session's work).

    .venv/bin/python tools/stage_hunks.py --list docs/now-playing.md            # numbered hunks, their first lines
    .venv/bin/python tools/stage_hunks.py docs/now-playing.md:2,3 styles/glass-v2/now_playing.py:1

Hunks are numbered from 1 in `git diff -U0` order. It builds the new index content itself, placing each chosen
hunk at the exact old-side line its header names and checking the lines it removes, then writes that blob to the
index; the working tree stays as it is. 2026-10-31 the same selection was written by hand twice in one session.
2026-11-01 `git apply --cached --unidiff-zero` put a zero-context insertion two lines too low, inside a repeated
`if not self.authorised():` block, and the staged hallpanel_server.py did not compile (tests/test_stage_hunks.py).
"""
import argparse
import re
import subprocess
import sys


def hunks(path):
    diff = subprocess.run(['git', 'diff', '-U0', '--', path], capture_output=True, text=True, check=True).stdout
    if not diff:
        return '', []
    head, *parts = re.split(r'(?m)^(?=@@ )', diff)
    return head, parts


HEADER = re.compile(r'^@@ -(\d+)(?:,(\d+))? \+\d+(?:,\d+)? @@')


def select(head, parts, numbers):
    missing = [n for n in numbers if not 1 <= n <= len(parts)]
    if missing:
        raise SystemExit(f'no hunk {missing} (the file has {len(parts)})')
    return [parts[n - 1] for n in sorted(set(numbers))]


def apply(index_text, chosen, path):
    """The index content with the chosen -U0 hunks applied at their exact old-side lines, bottom first."""
    lines = index_text.splitlines(keepends=True)
    for part in sorted(chosen, key=lambda h: int(HEADER.match(h).group(1)), reverse=True):
        if '\\ No newline at end of file' in part:
            raise SystemExit(f'{path}: a hunk touches the last line without a newline; stage it with git add -p')
        match = HEADER.match(part)
        start, count = int(match.group(1)), int(match.group(2) if match.group(2) is not None else 1)
        body = part.splitlines(keepends=True)[1:]
        removed = [l[1:] for l in body if l.startswith('-')]
        added = [l[1:] for l in body if l.startswith('+')]
        at = start - 1 if count else start          # -N,0 inserts after old line N
        if lines[at:at + count] != removed:
            raise SystemExit(f'{path}: hunk at old line {start} does not match the index; nothing staged')
        lines[at:at + count] = added
    return ''.join(lines)


def stage(path, text):
    git = lambda *a, **k: subprocess.run(['git', *a], capture_output=True, text=True, check=True, **k).stdout
    mode = git('ls-files', '-s', '--', path).split()[0]
    blob = git('hash-object', '-w', '--stdin', '--path', path, input=text).strip()
    git('update-index', '--cacheinfo', f'{mode},{blob},{path}')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--list', action='store_true')
    parser.add_argument('targets', nargs='+', help='FILE with --list, else FILE:N[,N...]')
    args = parser.parse_args(argv)
    if args.list:
        for path in args.targets:
            _, parts = hunks(path)
            for number, part in enumerate(parts, 1):
                lines = part.splitlines()
                first = next((l for l in lines[1:] if l[:1] in '+-'), '')
                print(f'{path}:{number}  {lines[0][:60]}  {first[:80]}')
        return 0
    staged = []
    for target in args.targets:                 # check every target before anything is staged
        path, _, numbers = target.rpartition(':')
        if not path or not numbers:
            raise SystemExit(f'{target}: expected FILE:N[,N...]')
        head, parts = hunks(path)
        chosen = select(head, parts, [int(n) for n in numbers.split(',')])
        index_text = subprocess.run(['git', 'show', f':{path}'], capture_output=True, text=True, check=True).stdout
        staged.append((path, apply(index_text, chosen, path)))
    for path, text in staged:
        stage(path, text)
    print(subprocess.run(['git', 'diff', '--cached', '--stat'], capture_output=True, text=True).stdout, end='')
    return 0


if __name__ == '__main__':
    sys.exit(main())
