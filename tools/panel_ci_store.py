"""Private .panel-ci evidence: write-once JSON and the run history's archiving. Raw measurements are never deleted."""
import json
import os
from pathlib import Path
import re
import shutil


def private_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), 'w') as out:
        json.dump(data, out, ensure_ascii=False, indent=2, allow_nan=False)


def prune_runs(path, keep=20, max_bytes=512 * 1024 * 1024):
    """Archive completed runs with stable links; never delete raw measurements."""
    protected = None
    quarantine = path / 'quarantine.json'
    if quarantine.is_file():
        try:
            protected = Path(json.loads(quarantine.read_text())['report']).parent.resolve()
        except (ValueError, KeyError, TypeError):
            raise RuntimeError('Invalid quarantine metadata; history pruning stopped') from None
    runs = sorted((p for p in path.glob('run-*') if p.is_dir() and not p.is_symlink()
                   and (p / 'report.json').is_file()), key=lambda p: p.name, reverse=True)
    total = 0
    for index, run in enumerate(runs):
        size = sum(p.stat().st_size for p in run.rglob('*') if p.is_file() and not p.is_symlink())
        total += size
        if run.resolve() != protected and (index >= keep or (total > max_bytes and index > 0)):
            history = path / 'history'
            history.mkdir(mode=0o700, exist_ok=True)
            if history.is_symlink() or not history.is_dir():
                raise RuntimeError('History must be a real private directory')
            destination = history / run.name
            if destination.exists() or destination.is_symlink():
                raise RuntimeError('History already contains '+run.name+'; evidence preserved')
            run.rename(destination)
            run.symlink_to(Path('history') / run.name, target_is_directory=True)
    prune_archived_elfs(path)


def elf_bound_runs(path):
    """Run names that quarantine, a sealed recovery plan or a baseline still points at."""
    from panel_recovery import sidecar
    names = set()
    sources = [path / 'quarantine.json', *path.glob('baselines/*.json')]
    sources += [plan for plan in path.glob('*.json') if sidecar(plan, '.approval.json').exists()]
    for source in sources:
        if source.is_file():
            names.update(re.findall(r'run-\d{8}-\d{6}-\d{6}', source.read_text(errors='replace')))
    return names


def prune_archived_elfs(path, keep=10):
    """Drop ~46 MB debug ELFs from old archived runs; measurements and binaries stay.

    The hashes are kept in elf-pruned.json, so the report still says which ELF was built.
    """
    from panel_recovery import sha
    history = path / 'history'
    if not history.is_dir() or history.is_symlink():
        return
    bound = elf_bound_runs(path)
    runs = sorted((p for p in history.glob('run-*') if p.is_dir() and not p.is_symlink()),
                  key=lambda p: p.name, reverse=True)
    for run in runs[keep:]:
        if run.name in bound:
            continue
        elfs = [p for p in (run / 'firmware.elf', run / 'previous-build/firmware.elf')
                if p.is_file() and not p.is_symlink()]
        if not elfs:
            continue
        record = run / 'elf-pruned.json'
        previous = json.loads(record.read_text()) if record.is_file() else {}
        previous.update({str(p.relative_to(run)): sha(p) for p in elfs})
        private_json(record, previous)
        for elf in elfs:
            elf.unlink()


def prune_verified(path, keep=3):
    images = sorted((p for p in path.glob('*') if p.is_dir() and not p.is_symlink()
                     and list(p.glob('verification-*.json'))), key=lambda p: p.stat().st_mtime, reverse=True)
    for old in images[keep:]:
        shutil.rmtree(old)
