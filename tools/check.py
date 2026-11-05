#!/usr/bin/env python3
"""Free release checks. No model calls, flashing, service restarts or live configuration writes."""
import argparse
import io
import os
from pathlib import Path
import shlex
import subprocess
import sys
import tarfile
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
HA_TESTS = ('test_ha_conversation.py', 'test_panel_voice.py', 'test_wakeup_ha.py', 'test_media_ha.py')


def clean_env():
    # Do not let an exported production configuration turn a test server into a real agent.
    return {k: v for k, v in os.environ.items()
            if not k.startswith(('JARVIS_', 'HALLPANEL_', 'MOCK_'))
            and k not in ('OPENAI_API_KEY', 'ANTHROPIC_API_KEY', 'GROQ_API_KEY', 'PYTHONPATH')
            and not k.lower().endswith('_proxy')}


def offline_env():
    env = clean_env()
    env.update(HALLPANEL_OFFLINE_TESTS='1', PYTHONPATH=str(ROOT / 'tests/offline'))
    return env


def run(command, **kwargs):
    print('+ ' + shlex.join(map(str, command)), flush=True)
    subprocess.run(command, cwd=ROOT, check=True, **kwargs)


def local_tests():
    with tempfile.TemporaryDirectory(prefix='hallpanel-check-') as tmp:
        env = offline_env()
        env.update(JARVIS_WRITE_GUARD_FILE=str(Path(tmp) / 'recent-creates.json'),
                   HALLPANEL_PROFILE=str(Path(tmp) / 'profile.json'),
                   HALLPANEL_AUTH_PIN=str(Path(tmp) / 'panel-auth.json'))
        run([sys.executable, ROOT / 'tools/offline_tests.py'], env=env, timeout=180)


def native_image_sampling():
    manifest = ROOT / 'native/image_sampling/Cargo.toml'
    for command in (
        ['cargo', 'fmt', '--manifest-path', manifest, '--check'],
        ['cargo', 'clippy', '--manifest-path', manifest, '--locked', '--all-targets', '--', '-D', 'warnings'],
        ['cargo', 'test', '--manifest-path', manifest, '--locked'],
        ['cargo', 'build', '--manifest-path', manifest, '--locked', '--release'],
    ):
        run(command, env=clean_env(), timeout=120)


CONFIG_ERROR_WORDS = ('Found ', 'Invalid', 'not ', 'must ', 'expected', "Couldn't", 'required', 'duplicate',
                      'Unknown', 'Cannot', 'should ')
PRIVATE_WORDS = ('wifi', 'ssid', 'password', 'key', 'token', 'secret')


def config_errors(text, limit=5):
    """The reasons in ESPHome's "Failed config" output, which echoes the whole failing block first: a duplicate
    font glyph was one line after 3,700 of glyphs (2026-10-28). Only message lines, never ones that could carry
    the local Wi-Fi or a key, so the summary may be printed while the full log stays local."""
    lines = text.split('Failed config', 1)[-1].splitlines()
    out = []
    for line in lines:
        stripped = line.strip()
        if (not stripped or stripped.startswith(('-', '[', '#')) or any(w in stripped.lower() for w in PRIVATE_WORDS)
                or not any(w in stripped for w in CONFIG_ERROR_WORDS)):
            continue
        out.append(stripped[:200])
        if len(out) == limit:
            break
    return out


def panel_config(direct=False, double=False, fast=False):
    # Regenerate the same source consumed by panel.yaml; never compile or flash in a check.
    folder = ROOT / 'styles/glass-v2'
    generated = subprocess.run([sys.executable, ROOT / 'board.py', '--voice', *(['--direct-display'] if direct or double else []), *(['--double-buffer'] if double else []), *(['--fast-display'] if fast else []), 'jarvis_orb.py', '--remote',
                                'http://jarvis.local:8123/local/hallpanel'], cwd=folder,
                               check=True, capture_output=True, env={**clean_env(), **(
                                   {'HALLPANEL_BUILD_ID': os.environ['HALLPANEL_BUILD_ID']}
                                   if 'HALLPANEL_BUILD_ID' in os.environ else {})})
    (folder / 'voice-board.yaml').write_bytes(generated.stdout)
    # Without local secrets, validate the standalone board with its harmless placeholder Wi-Fi.
    config = folder / 'panel.yaml' if (folder / 'secrets.yaml').exists() else folder / 'voice-board.yaml'
    with tempfile.TemporaryFile() as log:
        result = subprocess.run([sys.executable, '-m', 'esphome', 'config', str(config)], cwd=folder,
                                stdout=log, stderr=subprocess.STDOUT, timeout=90, env=clean_env())
        if result.returncode:
            # ESPHome diagnostics can contain local network configuration. Keep output local.
            log.seek(0)
            fd, path = tempfile.mkstemp(prefix='hallpanel-config-', suffix='.log')
            with os.fdopen(fd, 'wb') as out:
                out.write(log.read())
            log.seek(0)
            reasons = config_errors(log.read().decode(errors='replace'))
            raise RuntimeError('ESPHome config failed' + (': ' + ' | '.join(reasons) if reasons else '')
                               + f'; diagnostics kept locally at {path}')
    print('PASS ESPHome configuration (no firmware upload)', flush=True)


def ha_tests(host):
    # Test the candidate files in a temporary directory in the existing HA container.
    # Nothing is copied into /config and the HA process is not restarted.
    files = [ROOT / 'tools/offline_tests.py', ROOT / 'tests/offline/sitecustomize.py',
             *(ROOT / 'tests' / name for name in HA_TESTS),
             *sorted((ROOT / 'ha/custom_components/jarvis_agent').glob('*.py'))]
    archive = io.BytesIO()
    with tarfile.open(fileobj=archive, mode='w:gz') as tar:
        for path in files:
            tar.add(path, arcname=str(path.relative_to(ROOT)))
    remote = '''set -eu
candidate=$(docker exec homeassistant mktemp -d /tmp/hallpanel-check.XXXXXX)
trap 'docker exec homeassistant rm -rf "$candidate"' EXIT HUP INT TERM
docker exec -i homeassistant tar xzf - -C "$candidate"
docker exec -e HALLPANEL_OFFLINE_TESTS=1 -e PYTHONPATH="$candidate/tests/offline" homeassistant python "$candidate/tools/offline_tests.py" --ha
'''
    run(['ssh', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=8', '-o', 'IgnoreUnknown=UseKeychain',
         '-o', 'UseKeychain=yes', '-o', 'ControlMaster=auto',
         '-o', f'ControlPath={Path.home()}/.ssh/cm-%C', '-o', 'ControlPersist=10m', host,
         'sh -c ' + shlex.quote(remote)], input=archive.getvalue(), timeout=120)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--ha', metavar='SSH_HOST', help='also test candidate HA files inside its existing container')
    parser.add_argument('--tests-only', action='store_true', help='offline tests without ESPHome configuration')
    parser.add_argument('--config-only', action='store_true',
                        help='only regenerate and validate the ESPHome configuration (seconds): run it after a YAML '
                             'option change before flash_panel.sh, whose full checks come first')
    parser.add_argument('--fast-display', action='store_true', help='test a small internal-memory draw buffer')
    parser.add_argument('--double-buffer', action='store_true', help='explicitly select the default two-buffer display')
    parser.add_argument('--direct-display', action='store_true', help='validate the experimental single-buffer display')
    args = parser.parse_args()
    started = time.monotonic()
    try:
        if args.config_only:
            panel_config(direct=args.direct_display, double=args.double_buffer, fast=args.fast_display)
            print(f'PASS ESPHome configuration only in {time.monotonic() - started:.1f}s; not a release check')
            return 0
        native_image_sampling()
        local_tests()
        if not args.tests_only:
            panel_config(direct=args.direct_display, double=args.double_buffer, fast=args.fast_display)
        if args.ha:
            ha_tests(args.ha)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError, RuntimeError) as exc:
        print(f'FAIL release checks: {exc}', file=sys.stderr)
        return 1
    print(f'PASS release checks in {time.monotonic() - started:.1f}s', flush=True)
    if not args.ha:
        print('HA tests were not run; deploy_jarvis.sh requires them via --ha SSH_HOST.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
