#!/usr/bin/env python3
"""Cooperative, crash-safe locks shared by all local panel agents.

Never unlink a lock file: contenders may already have its inode open. Child processes
can share a lock only via an inherited, inode-verified file descriptor.
"""
import fcntl
import json
import os
from pathlib import Path
import re
import threading
import time

ROOT = Path(__file__).resolve().parents[1]
_REGISTRY = {}
_REGISTRY_GUARD = threading.Lock()


def holder(path):
    """Who holds or last held a lock: pid, start time and, if it still runs, its command."""
    try:
        info = json.loads(Path(path).read_text() or '{}')
    except (OSError, ValueError):
        return {}
    pid = info.get('pid')
    if isinstance(pid, int):
        try:
            os.kill(pid, 0)
            info['alive'] = True
        except PermissionError:
            # EPERM establishes that the PID exists; a sandbox cannot signal it.
            info['alive'] = True
        except (OSError, ValueError):
            info['alive'] = False
        if info['alive']:
            try:
                import subprocess
                info['command'] = subprocess.run(['ps', '-o', 'command=', '-p', str(pid)], capture_output=True,
                                                 text=True, timeout=5).stdout.strip()[:300]
            except (OSError, ValueError, subprocess.TimeoutExpired):
                pass  # An unavailable process command does not establish that its PID died.
    return info


def held(path):
    """True while another open file description holds the lock. Never creates or unlinks the file."""
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    except FileNotFoundError:
        return False
    try:
        fcntl.flock(fd, fcntl.LOCK_SH | fcntl.LOCK_NB)
        return False
    except BlockingIOError:
        return True
    finally:
        os.close(fd)  # Closing releases only this probe's own shared lock.


def busy_message(name, path):
    info = holder(path)
    detail = ''
    if info.get('pid'):
        since = time.strftime('%H:%M:%S', time.localtime(info.get('started_unix', 0)))
        detail = f" (pid {info['pid']} since {since}" + (f": {info['command']}" if info.get('command') else '') + ')'
    return f'Panel {name} is locked by another local session{detail}; see tools/panel lock status'


class PanelLock:
    def __init__(self, name='device', root=None, timeout=0):
        if not re.fullmatch(r'[a-z][a-z0-9_-]{0,31}', name):
            raise ValueError('Invalid lock name')
        if timeout < 0:
            raise ValueError('Lock timeout must be nonnegative')
        self.name, self.timeout = name, timeout
        self.root = Path(root) if root is not None else ROOT / '.panel-ci/locks'
        self.path = self.root.resolve() / (name + '.lock')
        self.env_key = 'HALLPANEL_LOCK_' + name.upper().replace('-', '_') + '_FD'
        self.fd = None
        self._entry = None

    def __enter__(self):
        if self.fd is not None:
            raise RuntimeError('Lock context is already entered')
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        key = str(self.path)
        with _REGISTRY_GUARD:
            entry = _REGISTRY.setdefault(key, {'guard': threading.RLock(), 'fd': None, 'count': 0})
        if not entry['guard'].acquire(timeout=self.timeout):
            raise RuntimeError(busy_message(self.name, self.path))
        try:
            if entry['count']:
                entry['count'] += 1
                self.fd, self._entry = entry['fd'], entry
                return self
            inherited = os.environ.get(self.env_key)
            fd = None
            if inherited is not None:
                try:
                    candidate = int(inherited)
                    stat, expected = os.fstat(candidate), self.path.stat()
                    if candidate < 3 or (stat.st_dev, stat.st_ino) != (expected.st_dev, expected.st_ino):
                        raise ValueError('Lock fd does not match lock file')
                    fd = os.dup(candidate)
                except (OSError, ValueError):
                    raise RuntimeError('Invalid inherited panel lock descriptor') from None
            if fd is None:
                fd = os.open(self.path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
                os.fchmod(fd, 0o600)
            deadline = time.monotonic() + self.timeout
            while True:
                try:
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    if time.monotonic() >= deadline:
                        os.close(fd)
                        raise RuntimeError(busy_message(self.name, self.path)) from None
                    time.sleep(min(0.05, max(0, deadline - time.monotonic())))
            if inherited is None:
                os.ftruncate(fd, 0)
                os.write(fd, json.dumps({'pid': os.getpid(), 'name': self.name,
                                         'started_unix': time.time()}).encode())
            entry.update(fd=fd, count=1)
            self.fd, self._entry = fd, entry
            return self
        except BaseException:
            entry['guard'].release()
            raise

    def subprocess_options(self, env=None):
        if self.fd is None:
            raise RuntimeError('Enter the lock before passing it to a child')
        child_env = dict(os.environ if env is None else env)
        child_env[self.env_key] = str(self.fd)
        return {'env': child_env, 'pass_fds': (self.fd,)}

    def __exit__(self, *exc):
        if self.fd is None:
            return
        entry = self._entry
        entry['count'] -= 1
        if not entry['count']:
            # Closing the descriptor releases the lock. Explicit LOCK_UN would also
            # unlock a parent's shared open-file description in a child process.
            os.close(entry['fd'])
            entry['fd'] = None
        self.fd = None
        entry['guard'].release()


def main():
    import argparse
    from contextlib import ExitStack
    import signal
    import subprocess
    import sys
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('run', 'verify', 'status', 'wait'),
                        help='status: who holds each lock; wait: block until the locks are free')
    parser.add_argument('--lock', action='append', choices=('workspace', 'device'))
    parser.add_argument('--max-wait', type=float, default=600, help='wait: give up after this many seconds')
    parser.add_argument('--lease-url', help='Hold a device lease only for this bounded physical operation')
    parser.add_argument('--timeout', type=float, help='Child deadline; device-lease operations default to 240 seconds')
    parser.add_argument('--allow-legacy', action='store_true', help='Explicit first migration; local locks only on old firmware')
    # Split the command after an explicit -- to keep tool flags unambiguous.
    raw = sys.argv[1:]
    separator = raw.index('--') if '--' in raw else len(raw)
    args = parser.parse_args(raw[:separator])
    argv = raw[separator + 1:] if separator < len(raw) else []
    if args.action in ('status', 'wait'):
        if argv or args.lease_url:
            parser.error(f'{args.action} takes no command or lease')
        return report(args.lock or ['workspace', 'device'], args.action == 'wait', args.max_wait)
    if not args.lock:
        parser.error('--lock is required')
    if args.timeout is not None and args.timeout <= 0:
        parser.error('--timeout must be positive')
    if args.lease_url and args.timeout is not None and args.timeout > 240:
        parser.error('Device-lease child timeout cannot exceed 240 seconds')
    if args.action == 'run' and not argv:
        parser.error('run needs -- COMMAND [ARG...]')
    if args.action == 'verify' and (argv or args.lease_url):
        parser.error('verify only validates inherited local lock descriptors')
    if args.allow_legacy and not args.lease_url:
        parser.error('--allow-legacy requires --lease-url')
    if args.lease_url and 'device' not in args.lock:
        parser.error('--lease-url requires --lock device')
    # A single global lock order avoids workspace/device deadlocks.
    names = sorted(set(args.lock), key=lambda n: ('workspace', 'device').index(n))
    try:
        with ExitStack() as stack:
            env, descriptors = dict(os.environ), []
            for name in names:
                lock = PanelLock(name)
                if args.action == 'verify' and lock.env_key not in os.environ:
                    raise RuntimeError('A guarded script needs inherited lock descriptors')
                stack.enter_context(lock)
                options = lock.subprocess_options(env)
                env, descriptors = options['env'], descriptors + list(options['pass_fds'])
            if args.action == 'verify':
                return 0
            if args.lease_url:
                from panel_remote import PanelClient
                client = stack.enter_context(PanelClient(args.lease_url, persistent=True))
                stack.enter_context(client.exclusive(require_device_lease=not args.allow_legacy))
                if client._lease_id:
                    options = client.subprocess_options(env)
                    env, descriptors = options['env'], descriptors + list(options['pass_fds'])
            child = subprocess.Popen(argv, env=env, pass_fds=tuple(set(descriptors)), start_new_session=True)
            old = {}
            def forward(signum, frame):
                if child.poll() is None:
                    os.killpg(child.pid, signum)
            try:
                for signum in (signal.SIGTERM, signal.SIGHUP, signal.SIGINT):
                    old[signum] = signal.signal(signum, forward)
                return child.wait(timeout=args.timeout if args.timeout is not None else (240 if args.lease_url else None))
            finally:
                for signum, handler in old.items():
                    signal.signal(signum, handler)
                if child.poll() is None:
                    os.killpg(child.pid, signal.SIGTERM)
                    try:
                        child.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        os.killpg(child.pid, signal.SIGKILL); child.wait()
    except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired) as error:
        print(f'Panel lock failed: {error}', file=sys.stderr)
        return 1


def report(names, wait=False, max_wait=600, root=None, clock=time.monotonic, sleep=time.sleep):
    """Print one JSON line per lock. wait returns 0 once all are free, 1 after max_wait."""
    import sys
    folder = Path(root) if root is not None else ROOT / '.panel-ci/locks'
    deadline = clock() + max_wait
    while True:
        states = {name: held(folder / (name + '.lock')) for name in names}
        if not wait or not any(states.values()) or clock() >= deadline:
            for name, busy in states.items():
                info = holder(folder / (name + '.lock')) if busy else {}
                print(json.dumps({'lock': name, 'held': busy, **({'holder': info} if busy else {})}))
            return 1 if wait and any(states.values()) else 0
        sleep(1)


if __name__ == '__main__':
    # panel_remote imports panel_lock for --lease-url. Without this it would get a second
    # module with its own registry, and flock would then refuse this process's own lock.
    import sys
    sys.modules.setdefault('panel_lock', sys.modules[__name__])
    raise SystemExit(main())
