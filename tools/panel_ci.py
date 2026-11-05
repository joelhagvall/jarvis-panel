#!/usr/bin/env python3
"""Local release orchestration. A local-only run can never authorize publication."""
import argparse
import ast
from datetime import datetime
import hashlib
import http.client
import json
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import sys
import time

from panel_ci_logs import check_logs, redact, runtime_logs, wait_log_subscription  # noqa: F401
from panel_ci_store import elf_bound_runs, private_json, prune_archived_elfs, prune_runs, prune_verified  # noqa: F401

ROOT = Path(__file__).resolve().parents[1]
STATE = ROOT / '.panel-ci'
BOARD = ROOT / 'styles/glass-v2'
BUILD = BOARD / '.esphome/build/hallpanel'


class ProcessFailure(RuntimeError):
    """Known failed local command; distinct from concurrency or unknown-state failures."""


def git(*args, root=ROOT):
    return subprocess.check_output(['git', '-C', str(root), *args], stderr=subprocess.PIPE).decode().strip()


def inventory(root=ROOT):
    """Hash all versionable candidate inputs, including untracked work and deletions."""
    names = subprocess.check_output(['git', '-C', str(root), 'ls-files', '-z', '--cached',
                                     '--others', '--exclude-standard']).split(b'\0')
    files = {}
    for raw in names:
        if not raw:
            continue
        name = os.fsdecode(raw)
        path = root / name
        if path.is_symlink():
            value = 'symlink:' + os.readlink(path)
        elif path.is_file():
            value = hashlib.sha256(path.read_bytes()).hexdigest()
        elif not path.exists():
            value = 'deleted'
        else:
            raise RuntimeError(f'Unsupported candidate input: {name}')
        files[name] = {'sha256': value, 'mode': path.lstat().st_mode & 0o777 if path.exists() else 0}
    digest = hashlib.sha256(json.dumps(files, sort_keys=True).encode()).hexdigest()
    return {'head': git('rev-parse', 'HEAD', root=root), 'branch': git('branch', '--show-current', root=root),
            'changes': git('status', '--short', root=root).splitlines(), 'files': files, 'build_id': digest[:32]}


def unchanged(candidate, root=ROOT):
    current = inventory(root)
    if any(current[k] != candidate[k] for k in ('head', 'branch', 'files')):
        raise RuntimeError('Candidate changed during verification; rerun CI on a stable workspace')


def command(argv, log, *, timeout=900, env=None, cwd=ROOT, options=None):
    """Bound child lifetime; stop its process group on interruption or timeout."""
    options = options or {}
    child = subprocess.Popen(list(map(str, argv)), cwd=cwd, env=env or options.get('env'),
                             pass_fds=options.get('pass_fds', ()), stdout=subprocess.PIPE,
                             stderr=subprocess.STDOUT, start_new_session=True)
    try:
        output, _ = child.communicate(timeout=timeout)
    except BaseException:
        os.killpg(child.pid, signal.SIGTERM)
        try:
            output, _ = child.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            os.killpg(child.pid, signal.SIGKILL)
            output, _ = child.communicate()
        Path(log).write_text(redact(output.decode(errors='replace')))
        Path(log).chmod(0o600)
        raise
    Path(log).write_text(redact(output.decode(errors='replace')))
    Path(log).chmod(0o600)
    if child.returncode:
        raise ProcessFailure(f'{Path(str(argv[0])).name} failed ({child.returncode}); see {log}')


def static_checks(candidate):
    """Python syntax, shell syntax and patch whitespace; C++ contracts run in check.py."""
    checked = []
    for name in candidate['files']:
        path = ROOT / name
        if not path.is_file():
            continue
        if path.suffix == '.py':
            ast.parse(path.read_text(), filename=name)
            checked.append(name)
        elif path.suffix == '.sh' or name == 'tools/panel':
            shell = 'bash' if 'bash' in path.read_text().splitlines()[0] else 'sh'
            subprocess.run([shell, '-n', str(path)], check=True, capture_output=True)
            checked.append(name)
    subprocess.run(['git', '-C', str(ROOT), 'diff', '--check'], check=True, capture_output=True)
    return {'ok': True, 'syntax_files': checked, 'lint': 'git diff --check; Python AST; sh -n'}


def recovery_support(build=BUILD):
    partitions = (build / 'partitions.csv').read_text()
    config = (build / 'sdkconfig.hallpanel').read_text()
    return {'two_ota_slots': all(re.search(r'\b' + name + r'\b', partitions) for name in ('ota_0', 'ota_1')),
            'rollback_configured': 'CONFIG_BOOTLOADER_APP_ROLLBACK_ENABLE=y' in config,
            'watchdog_configured': 'CONFIG_ESP_TASK_WDT_PANIC=y' in config,
            'automatic_host_restore': False,
            'reason': 'Host restore requires a separately reviewed physical recovery rehearsal; no proof exists.'}


def backup_built(folder):
    """Preserve previous build without claiming it is installed or hardware verified."""
    dest = folder / 'previous-build'
    dest.mkdir(mode=0o700)
    found = {}
    for name in ('firmware.ota.bin', 'firmware.factory.bin', 'firmware.elf', 'firmware.map'):
        source = BUILD / 'build' / name
        if source.exists():
            shutil.copyfile(source, dest / name)
            (dest / name).chmod(0o600)
            found[name] = hashlib.sha256((dest / name).read_bytes()).hexdigest()
    private_json(dest / 'manifest.json', {'hardware_verified': False, 'files': found,
                 'note': 'Previous Mac build; installed identity is not inferred from file timestamps.'})
    return found


def publish_preflight(files, root=ROOT):
    """A reviewed explicit allowlist, main upstream and no unrelated dirty inputs."""
    if not files or any(Path(p).is_absolute() or '..' in Path(p).parts or p.startswith('-') for p in files):
        raise RuntimeError('Publication needs an explicit repository-relative --files allowlist')
    if git('branch', '--show-current', root=root) != 'main':
        raise RuntimeError('Publication only runs on main')
    if git('rev-parse', '--abbrev-ref', '@{upstream}', root=root) != 'origin/main':
        raise RuntimeError('Expected main upstream origin/main')
    for kind in ('--get-url', '--get-url=push'):
        url = git('remote', *(['get-url', '--push'] if kind.endswith('push') else ['get-url']), 'origin', root=root)
        if url not in ('https://github.com/joelhagvall/jarvis-panel.git', 'git@github.com:joelhagvall/hallpanel.git'):
            raise RuntimeError('Unexpected origin URL; publication stopped')
    changed = subprocess.check_output(['git', '-C', str(root), 'diff', '--name-only', '-z', 'HEAD']).split(b'\0')
    unknown = subprocess.check_output(['git', '-C', str(root), 'ls-files', '--others', '--exclude-standard', '-z']).split(b'\0')
    if not {os.fsdecode(p) for p in changed + unknown if p} <= set(files):
        raise RuntimeError('Unrelated/pre-existing changes are present; no automatic commit')
    subprocess.run(['git', '-C', str(root), 'fetch', 'origin', 'main'], check=True, capture_output=True, timeout=60)
    if git('rev-parse', 'HEAD', root=root) != git('rev-parse', 'origin/main', root=root):
        raise RuntimeError('HEAD differs from refreshed origin/main; no automatic merge or push')


def publish(candidate, files, message):
    unchanged(candidate)
    publish_preflight(files)
    if not message.strip() or '\n' in message or re.search(r'(?i)(co-authored-by|claude-session|signed-off-by):', message):
        raise ValueError('Use one plain commit message without trailers')
    # git status is deliberately run immediately before committing.
    subprocess.run(['git', '-C', str(ROOT), 'status', '--short'], check=True)
    subprocess.run(['git', '-C', str(ROOT), 'add', '--', *files], check=True)
    subprocess.run(['git', '-C', str(ROOT), 'commit', '-m', message], check=True)
    subprocess.run(['git', '-C', str(ROOT), 'push', 'origin', 'main:main'], check=True)
    return git('rev-parse', 'HEAD')


def wait_boot(url, build_id, previous, seconds=120):
    """Retry read-only connection/DNS failures; never retry away a bad identity."""
    from panel_remote import PanelClient
    if previous.get('boot_id') is None:
        raise RuntimeError('Missing pre-restart boot identity')
    start = time.monotonic()
    last, observed_boot, client = None, None, None
    read_errors = 0
    try:
        while time.monotonic() - start < seconds:
            try:
                # Constructor resolves .local. It belongs inside the same retry
                # budget as status reads, not outside the loop/context manager.
                if client is None:
                    client = PanelClient(url, persistent=True)
                status = client.status()
            except (OSError, ValueError, http.client.HTTPException):
                read_errors += 1
                if client is not None:
                    client.close()
                    client = None
                time.sleep(min(1, max(0, seconds - (time.monotonic() - start))))
                continue
            boot = status.get('boot_id')
            if boot is None:
                raise RuntimeError('Missing boot identity during verification')
            if observed_boot is None and boot == previous.get('boot_id'):
                if time.monotonic() - start < 20:
                    time.sleep(min(1, max(0, seconds - (time.monotonic() - start))))
                    continue
                raise RuntimeError('OTA did not establish a fresh boot')
            if status.get('build_id') != build_id:
                raise RuntimeError('Running firmware identity differs from verified candidate')
            if observed_boot is not None and boot != observed_boot:
                raise RuntimeError('Unexpected restart during boot verification')
            observed_boot = boot
            uptime = status.get('uptime_ms')
            if type(uptime) is not int or uptime < 0:
                raise RuntimeError('Invalid uptime during boot verification')
            if last is not None and uptime < last:
                raise RuntimeError('Unexpected restart during boot verification')
            last = uptime
            if status.get('boot_healthy') is True and uptime >= 65000:
                return {'ok': True, 'host_observed_ready_ms': round((time.monotonic() - start) * 1000),
                        'read_errors': read_errors, 'status': status}
            time.sleep(min(1, max(0, seconds - (time.monotonic() - start))))
    finally:
        if client is not None:
            client.close()
    raise RuntimeError('Device unreachable or boot health not proven; automatic recovery stopped')


def idle_measurement(client, folder):
    """Use one reproducible tab, then restore the caller's original tab."""
    from panel_ui_test import select_tab
    from panel_metrics import collect
    tabs = {'page_glass_v2':'Gå hemifrån','page_hemmet':'Hemmet','page_tavla':'Tavla'}
    original = client.status()['page']
    if original not in tabs:
        raise RuntimeError('Idle measurement requires an idle tab')
    try:
        select_tab(client, 'page_hemmet')
        return collect(client,folder,workload='idle')
    finally:
        select_tab(client, original)


def qa_configuration(args, stage):
    from panel_visual import POLICY_PATH, DEFAULT_BASELINE, load_capture
    from panel_metrics import evaluate
    from panel_responsiveness import load_baseline as load_response_baseline
    from panel_throughput import load_baseline as load_capacity_baseline
    policy = json.loads(POLICY_PATH.read_text())
    visual = Path(getattr(args, 'visual_baseline', None) or DEFAULT_BASELINE).expanduser().resolve()
    stage('visual-reference-preflight', lambda: {'scenes': list(load_capture(visual, policy, reference=True)['scenes'])})
    idle_path = Path(getattr(args, 'performance_baseline', None) or STATE / 'baselines/performance.json').expanduser().resolve()
    animation_path = Path(getattr(args, 'animation_baseline', None) or STATE / 'baselines/animation.json').expanduser().resolve()
    idle = json.loads(idle_path.read_text())
    animation = json.loads(animation_path.read_text())
    for data, workload, path in ((idle, 'idle', idle_path), (animation, 'animation', animation_path)):
        if not evaluate(data)['ok'] or data.get('workload') != workload:
            raise RuntimeError('Invalid/incomplete '+workload+' performance baseline')
        stage(workload+'-reference-preflight', lambda path=path: {
            'path': str(path), 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()})
    response = stage('responsiveness-reference-preflight', lambda: load_response_baseline(STATE / 'baselines/responsiveness.json'))
    capacity = stage('60fps-reference-preflight', lambda: load_capacity_baseline(STATE / 'baselines/throughput.json'))
    return policy, visual, idle, animation, response, capacity


def physical_qa(client, folder, before, stage, configuration, args, options):
    """Identical gates after OTA or after a proven interruption; never uploads."""
    from panel_visual import capture, compare
    from panel_ui_test import run as smoke, run_test_mode
    from panel_metrics import collect_animation, evaluate
    from panel_responsiveness import collect as collect_response, evaluate as evaluate_response
    from panel_throughput import collect as collect_throughput, SOAK_SECONDS
    from panel_throughput import evaluate as evaluate_throughput, evaluate_soak
    policy, visual_baseline, baseline, animation_baseline, response_baseline, capacity_baseline = configuration
    with runtime_logs(folder, args.device, options):
        stage('physical-integration', lambda: smoke(client, folder / 'smoke',
              brightness=bool(getattr(args, 'brightness_drag', False))))
        stage('synthetic-voice-and-faults', lambda: run_test_mode(client, folder / 'test-mode'))
        visual_folder = folder / 'visual'; visual_folder.mkdir(mode=0o700)
        (visual_folder / 'candidate').mkdir(mode=0o700)
        stage('visual-capture', lambda: capture(client, visual_folder / 'candidate', policy))
        comparison = stage('visual-regression', lambda: compare(visual_baseline, visual_folder / 'candidate', visual_folder, policy))
        if not comparison['ok']:
            raise RuntimeError('Visual regression; reference preserved')
        metrics = stage('performance-capture', lambda: idle_measurement(client, folder / 'metrics'))
        performance = stage('performance-regression', lambda: evaluate(metrics, baseline))
        if not performance['ok'] or not performance['baseline_checked']:
            raise RuntimeError('Performance regression or missing baseline; reference preserved')
        animation = stage('animation-capture', lambda: collect_animation(client, folder / 'animation'))
        animation_check = stage('animation-regression', lambda: evaluate(animation, animation_baseline))
        if not animation_check['ok'] or not animation_check['baseline_checked']:
            raise RuntimeError('Animation performance regression; baseline preserved')
        response = stage('responsiveness-capture', lambda: collect_response(client, folder / 'responsiveness'))
        response_check = stage('responsiveness-regression', lambda: evaluate_response(response, response_baseline))
        if not response_check['ok'] or not response_check['baseline_checked']:
            raise RuntimeError('Responsiveness regression; baseline preserved')
        if getattr(args, 'compare_orb_methods', False):
            for method in ('linear', 'staged'):
                diagnostic = stage('60fps-method-'+method, lambda method=method:
                                   collect_throughput(client, folder / ('throughput-'+method), method=method))
                if not diagnostic.get('complete') or not diagnostic.get('restored'):
                    raise RuntimeError('Interpolation comparison incomplete or restoration unproven')
        capacity = stage('60fps-capacity-diagnostic', lambda: collect_throughput(client, folder / 'throughput'))
        if not capacity.get('complete') or not capacity.get('restored'):
            raise RuntimeError('60fps capacity measurement incomplete or restoration unproven')
        if before.get('orb_target_fps', 25) >= 60 and not capacity.get('target_met'):
            raise RuntimeError('Configured production 60fps target not met')
        if before.get('orb_target_fps', 25) >= 60:
            capacity_check = stage('60fps-capacity-regression', lambda: evaluate_throughput(capacity, capacity_baseline))
            if not capacity_check['ok'] or not capacity_check['baseline_checked']:
                raise RuntimeError('Production 60fps regression; measured reference preserved')
            # Stability over two minutes: a stall recurring every 60 s repeats frames at least
            # twice; one random hiccup is tolerated and a failed soak is measured once more.
            # Both measurements are retained; neither can become the reference.
            for attempt, name in enumerate(('60fps-soak', '60fps-soak-repeat')):
                soak = stage(name, lambda name=name: collect_throughput(client, folder / ('throughput-' + name[6:]), seconds=SOAK_SECONDS))
                if not soak.get('complete') or not soak.get('restored'):
                    raise RuntimeError('60fps soak incomplete or restoration unproven')
                verdict = stage(name + '-check', lambda soak=soak: evaluate_soak(soak))
                if verdict['ok']:
                    break
                if attempt:
                    raise RuntimeError('Production 60fps soak failed twice; stalls repeat frames')
        stage('final-health', lambda: final_health(client, before))
    stage('runtime-log-review', lambda: check_logs(folder / 'runtime.log'))
    if before.get('audio_bus_hz') == 48000:
        from panel_audio_check import evaluate as check_audio
        stage('audio-clock-integrity', lambda: check_audio(folder / 'runtime.log'))


POSTVERIFY_FILES = {'tools/panel_ci.py', 'tests/test_panel_ci_postverify.py', 'docs/autonomous-ci.md',
                   'tools/panel_ui_test.py', 'tools/panel_visual.py', 'tests/test_panel_ui_test.py',
                   'server/transcription.py', 'tests/test_transcription.py',
                   'AGENTS.md', 'tools/panel_recovery.py', 'tests/test_panel_recovery.py',
                   'docs/restart-crash.md', 'docs/now-playing.md'}


def transport_evidence(run, report, candidate, quarantine, diagnosis):
    """One same-boot verification after a host-only delay and a fixed A/B diagnosis.

    The slow release run stays failed. Neither A/B run counts as a release pass.
    A consumed continuation cannot be retried, even when QA was interrupted.
    """
    from panel_recovery import sealed_plan, sha, sidecar
    from panel_responsiveness import evaluate, load_baseline
    stages = report['stages']
    verdict = stages[-1].get('result', {})
    failures = [c['metric'] for c in verdict.get('comparisons', []) if not c.get('ok')]
    required = {'restart-linkage', 'ota', 'boot-and-identity', 'boot-log-review',
                'physical-integration', 'synthetic-voice-and-faults', 'visual-regression',
                'performance-regression', 'animation-regression', 'responsiveness-capture'}
    if (report.get('error') != 'Responsiveness regression; baseline preserved'
            or stages[-1]['name'] != 'responsiveness-regression'
            or any(not s.get('ok') for s in stages)
            or not required <= {s['name'] for s in stages}
            or any(isinstance(s.get('result'), dict) and s['result'].get('ok') is False for s in stages[:-1])
            or verdict.get('baseline_checked') is not True
            or failures != ['status_roundtrip_ms']):
        raise RuntimeError('Continuation requires an isolated host status-delay failure')
    authorization = next(s['result'] for s in stages if s['name'] == 'recovery-authorization')
    plan_path = Path(authorization['plan'])
    plan = json.loads(plan_path.read_text())
    checked_run = Path(plan['run'])
    # This proves a past installation, not permission for another OTA. The
    # current documentation may differ; its old digest remains in the manifest.
    installed = plan.get('installed_guard', {})
    if (not sealed_plan(plan_path) or authorization.get('plan_sha256') != sha(plan_path)
            or sha(checked_run / 'candidate.json') != plan['candidate_sha256']
            or json.loads((checked_run / 'candidate.json').read_text()) != candidate
            or candidate['files'][plan['diagnosis']]['sha256'] != plan['diagnosis_sha256']
            or sha(run / 'firmware.ota.bin') != plan['firmware_sha256']
            or sha(run / 'firmware.elf') != plan['elf_sha256']
            or not installed or sha(installed['report']) != installed['report_sha256']):
        raise RuntimeError('Historical recovery artifact or plan binding changed')
    claim_path = sidecar(plan_path, '.attempts') / 'attempt-01.json'
    claim = json.loads(claim_path.read_text())
    if (plan.get('max_attempts') != 1 or claim != report.get('recovery')
            or claim.get('plan_sha256') != sha(plan_path)
            or Path(claim['report']).resolve() != run / 'report.json'
            or sha(STATE / 'quarantine.json') != plan['quarantine_sha256']):
        raise RuntimeError('Continuation must belong to the sealed single recovery attempt and quarantine')
    before = next(s['result']['status'] for s in stages if s['name'] == 'boot-and-identity')
    evidence_paths = [plan_path, sidecar(plan_path, '.approval.json'), claim_path,
                      checked_run / 'candidate.json', run / 'firmware.elf', Path(installed['report'])]
    baseline = load_baseline(STATE / 'baselines/responsiveness.json')
    for path in (run / 'responsiveness/responsiveness.json',
                 Path(diagnosis) / 'without-logs/responsiveness.json',
                 Path(diagnosis) / 'with-logs/responsiveness.json'):
        sample = json.loads(path.read_text())
        if (not sample.get('complete') or not sample.get('restored')
                or not sample.get('records') or any(
                    str(r['status'].get('boot_id')) != str(before['boot_id'])
                    or r['status'].get('build_id') != candidate['build_id'] for r in sample['records'])):
            raise RuntimeError('Transport diagnosis lacks complete restored same-boot evidence')
        result = evaluate(sample, baseline)
        expected = path == run / 'responsiveness/responsiveness.json'
        if expected:
            if result != verdict:
                raise RuntimeError('Original transport failure no longer matches unchanged policy')
        elif not result.get('ok') or not result.get('baseline_checked'):
            raise RuntimeError('Both fixed A/B cases must pass unchanged responsiveness limits')
        evidence_paths.append(path)
    return before, {str(p.resolve()): sha(p) for p in evidence_paths}


def interrupted_install(run, current, quarantine, transport_diagnosis=None):
    """Only a measured voice interruption after clean boot/visual gates is resumable."""
    run = Path(run).resolve()
    report = json.loads((run / 'report.json').read_text())
    candidate = json.loads((run / 'candidate.json').read_text())
    stages = report['stages']
    if report.get('ok') or report.get('mode') != 'physical' or not stages:
        raise RuntimeError('Not an interrupted installed verification')
    external_evidence = {}
    if transport_diagnosis:
        before, external_evidence = transport_evidence(run, report, candidate, quarantine, transport_diagnosis)
    elif (stages[-1]['name'] != 'performance-capture' or stages[-1].get('ok')
          or any(not s.get('ok') for s in stages[:-1])):
        raise RuntimeError('Earlier verification failure cannot be resumed')
    required = {'boot-and-identity', 'boot-log-review', 'physical-integration', 'synthetic-voice-and-faults', 'visual-regression'}
    if not required <= {s['name'] for s in stages if s.get('ok')}:
        raise RuntimeError('Prior boot and physical gates are incomplete')
    if not transport_diagnosis and (Path(quarantine.get('report', '')).resolve() != run / 'report.json' or quarantine.get('build_id') != candidate['build_id']):
        raise RuntimeError('Quarantine belongs to another installation')
    if current['branch'] != 'main' or any(current[k] != candidate[k] for k in ('head', 'branch')):
        raise RuntimeError('Git identity changed since installation')
    changed = {p for p in current['files'].keys() | candidate['files'].keys()
               if current['files'].get(p) != candidate['files'].get(p)}
    if not changed <= POSTVERIFY_FILES:
        raise RuntimeError('Runtime/candidate inputs changed since installation')
    image = run / 'firmware.ota.bin'
    if hashlib.sha256(image.read_bytes()).hexdigest() != report['firmware_sha256']:
        raise RuntimeError('Installed artifact hash mismatch')
    samples = json.loads((run / 'metrics/metrics.json').read_text())
    before = next(s['result']['status'] for s in stages if s['name'] == 'boot-and-identity')
    if not transport_diagnosis and (samples.get('workload') != 'idle' or not any(s.get('voice_active') is True for s in samples.get('samples', []))
            or any(str(s.get('boot_id')) != str(before['boot_id']) or s.get('build_id') != candidate['build_id']
                   for s in samples.get('samples', []))):
        raise RuntimeError('No same-boot voice interruption evidence')
    check_logs(run / 'boot.log', ota_boot=before)
    check_logs(run / 'runtime.log')
    return {'candidate': candidate, 'report': report, 'boot': before, 'run': run,
            'external_evidence': external_evidence,
            'non_firmware_changes': sorted(changed),
            'input_sha256': {name: hashlib.sha256((run / name).read_bytes()).hexdigest()
                             for name in ('report.json', 'candidate.json', 'boot.log', 'runtime.log',
                                          'metrics/metrics.json', 'firmware.ota.bin')},
            'evidence_sha256': hashlib.sha256((run / 'metrics/metrics.json').read_bytes()).hexdigest()}


def verify_installed(args):
    """Complete QA on the same healthy boot, with no firmware build, upload or publication."""
    from panel_lock import PanelLock
    from panel_remote import PanelClient
    os.umask(0o077)
    with PanelLock('workspace') as workspace:
        prune_runs(STATE)
        current = inventory()
        quarantine_path = STATE / 'quarantine.json'
        quarantine_bytes = quarantine_path.read_bytes()
        transport_diagnosis = getattr(args, 'transport_diagnosis', None)
        proof = interrupted_install(args.verify_installed, current, json.loads(quarantine_bytes), transport_diagnosis)
        folder = STATE / datetime.now().strftime('run-%Y%m%d-%H%M%S-%f')
        folder.mkdir(mode=0o700)
        report = {'format': 1, 'mode': 'verify-installed', 'ok': False, 'published': False,
                  'build_id': proof['candidate']['build_id'], 'firmware_sha256': proof['report']['firmware_sha256'],
                  'head': current['head'], 'started_at': datetime.now().astimezone().isoformat(),
                  'verification_source_id': current['build_id'], 'non_firmware_changes': proof['non_firmware_changes'],
                  'prior_report': str(proof['run'] / 'report.json'),
                  'interruption': 'isolated host status delay after fixed A/B diagnosis' if transport_diagnosis else 'measured voice activity',
                  'external_evidence': proof['external_evidence'],
                  'evidence_sha256': proof['evidence_sha256'], 'input_sha256': proof['input_sha256'], 'stages': []}
        private_json(folder / 'candidate.json', proof['candidate'])
        private_json(folder / 'harness.json', current)
        def stage(name, fn):
            print('CI '+name, flush=True)
            started = time.monotonic()
            entry = {'name': name, 'ok': False}; report['stages'].append(entry)
            entry['result'] = fn(); entry.update(ok=True, elapsed_s=round(time.monotonic()-started, 2))
            unchanged(current)
            return entry['result']
        try:
            for name in proof['input_sha256']:
                target = folder / 'prior' / name; target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                shutil.copyfile(proof['run'] / name, target)
                if hashlib.sha256(target.read_bytes()).hexdigest() != proof['input_sha256'][name]:
                    raise RuntimeError('Prior evidence changed during capture')
            stage('static-analysis', lambda: static_checks(current))
            stage('unit-tests-and-config', lambda: command([sys.executable, ROOT / 'tools/check.py'],
                  folder / 'checks.log', timeout=300, options=workspace.subprocess_options()))
            configuration = qa_configuration(args, stage)
            if transport_diagnosis:
                private_json(proof['run'] / 'transport-continuation.json', {'report': str(folder / 'report.json'),
                             'evidence': proof['external_evidence']})
            with PanelLock('device') as device, PanelClient(args.url, persistent=True) as client, client.exclusive(require_device_lease=True):
                def guard():
                    status = final_health(client, proof['boot'])
                    if (status.get('firmware_elf_sha256') != proof['boot'].get('firmware_elf_sha256')
                            or status.get('wifi_connected') is not True
                            or status['page'] not in ('page_glass_v2', 'page_hemmet', 'page_musik', 'page_tavla')
                            or any(status.get(k) is not False for k in ('voice_active', 'alarm_active', 'announcement_active', 'physical_touch_active'))):
                        raise RuntimeError('Installed boot is not healthy and idle; no interaction')
                    return status
                initial = stage('installed-identity-and-idle', guard)
                physical_qa(client, folder, initial, stage, configuration, args, device.subprocess_options())
            if quarantine_path.read_bytes() != quarantine_bytes:
                raise RuntimeError('Quarantine changed during verification; retained')
            for name, expected in proof['input_sha256'].items():
                if hashlib.sha256((proof['run'] / name).read_bytes()).hexdigest() != expected:
                    raise RuntimeError('Prior evidence changed during verification; retained')
            for name, expected in proof['external_evidence'].items():
                if hashlib.sha256(Path(name).read_bytes()).hexdigest() != expected:
                    raise RuntimeError('External diagnosis changed during verification; retained')
            report.update(ok=True, verification='physical-installed')
            return 0
        except (Exception, KeyboardInterrupt) as error:
            report['error'] = redact(str(error)); print('CI STOP: '+report['error'], file=sys.stderr)
            return 1
        finally:
            report['finished_at'] = datetime.now().astimezone().isoformat()
            private_json(folder / 'report.json', report)
            if report['ok']:
                verified = STATE / 'verified' / report['build_id']; verified.mkdir(parents=True, exist_ok=True, mode=0o700)
                shutil.copyfile(folder / 'prior/firmware.ota.bin', verified / 'firmware.ota.bin')
                (verified / 'firmware.ota.bin').chmod(0o600)
                private_json(verified / ('verification-'+folder.name+'.json'), {'build_id': report['build_id'],
                             'firmware_sha256': report['firmware_sha256'], 'report': str(folder / 'report.json')})
                private_json(folder / 'quarantine-resolved.json', {'reason': report['interruption'] + '; all physical gates passed on the same boot',
                             'prior_quarantine': json.loads(quarantine_bytes), 'report': str(folder / 'report.json')})
                if quarantine_path.read_bytes() != quarantine_bytes:
                    raise RuntimeError('Quarantine changed before resolution; retained')
                quarantine_path.unlink()
                prune_verified(STATE / 'verified')
            print('CI report: '+str(folder / 'report.json'), flush=True)


def pipeline(args):
    from panel_lock import PanelLock
    os.umask(0o077)
    STATE.mkdir(mode=0o700, exist_ok=True)
    STATE.chmod(0o700)
    with PanelLock('workspace') as workspace:
        prune_runs(STATE)
        folder = STATE / datetime.now().strftime('run-%Y%m%d-%H%M%S-%f')
        folder.mkdir(mode=0o700)
        candidate = inventory()
        report = {'format': 1, 'started_at': datetime.now().astimezone().isoformat(), 'ok': False,
                  'mode': 'local-only' if args.local_only else 'physical', 'build_id': candidate['build_id'],
                  'head': candidate['head'], 'stages': [], 'published': False, 'repairs': []}
        report['brightness_drag_requested'] = bool(getattr(args, 'brightness_drag', False))
        private_json(folder / 'candidate.json', candidate)
        env = {**os.environ, 'HALLPANEL_BUILD_ID': candidate['build_id']}
        options = workspace.subprocess_options()
        env.update(options.get('env', {}))
        env['HALLPANEL_BUILD_ID'] = candidate['build_id']
        def stage(name, fn):
            print(f'CI {name}', flush=True)
            before = time.monotonic()
            entry = {'name': name, 'ok': False}
            report['stages'].append(entry)
            entry['result'] = fn()
            entry.update(ok=True, elapsed_s=round(time.monotonic() - before, 2))
            unchanged(candidate)
            return entry['result']
        try:
            if candidate['branch'] != 'main':
                raise RuntimeError('CI requires main; no branches are created')
            recovery_plan = getattr(args, 'recovery_plan', None)
            if recovery_plan:
                from panel_recovery import validate_plan
                if args.local_only or args.publish:
                    raise RuntimeError('Recovery requires full physical gates and cannot publish')
                recovery_authorization = stage('recovery-authorization', lambda: validate_plan(recovery_plan, candidate))
            if not args.local_only and (STATE / 'quarantine.json').exists() and not recovery_plan:
                raise RuntimeError('Physical CI is quarantined after an unresolved hardware failure; inspect .panel-ci/quarantine.json')
            if args.publish:
                publish_preflight(args.files)
            stage('static-analysis', lambda: static_checks(candidate))
            # Repair is an explicit bounded external adapter, and only for known local-check failures.
            attempts = 0
            while True:
                try:
                    stage('unit-tests-and-config', lambda: command([sys.executable, ROOT / 'tools/check.py'],
                          folder / f'checks-{attempts}.log', timeout=300, env=env, options=options))
                    break
                except ProcessFailure:
                    if not args.repair_argv or attempts >= 3:
                        raise
                    if not args.files:
                        raise RuntimeError('Repair requires an explicit --files scope')
                    previous = inventory()
                    attempts += 1
                    command(args.repair_argv, folder / f'repair-{attempts}.log', timeout=180, options=options)
                    updated = inventory()
                    changed = {n for n in previous['files'].keys() | updated['files'].keys()
                               if previous['files'].get(n) != updated['files'].get(n)}
                    if not changed or not changed <= set(args.files) or updated['head'] != candidate['head']:
                        raise RuntimeError('Repair changed nothing, changed Git, or exceeded its file scope; stopped')
                    report['repairs'].append({'attempt': attempts, 'files': sorted(changed)})
                    candidate = updated
                    report['build_id'] = candidate['build_id']
                    env['HALLPANEL_BUILD_ID'] = candidate['build_id']
                    private_json(folder / f'candidate-repair-{attempts}.json', candidate)
                    stage('static-analysis', lambda: static_checks(candidate))
            stage('backup-previous-build', lambda: backup_built(folder))
            stage('compile-firmware', lambda: command([ROOT / '.venv/bin/esphome', 'compile', 'panel.yaml'],
                  folder / 'compile.log', timeout=1200, env=env, cwd=BOARD, options=options))
            from panel_recovery import restart_linkage
            stage('restart-linkage', lambda: restart_linkage(BUILD / 'build/firmware.elf'))
            support = stage('recovery-capabilities', recovery_support)
            if not all(support[k] for k in ('two_ota_slots', 'rollback_configured', 'watchdog_configured')):
                raise RuntimeError('Expected OTA partitions, rollback or watchdog missing')
            # A recovery installs the immutable artifact the human approved.
            # Fresh checks and compilation above still have to pass for the
            # identical source inventory; no build/test gate is bypassed.
            artifact = Path(recovery_authorization['artifact_folder']) if recovery_plan else BUILD / 'build'
            image = artifact / 'firmware.ota.bin'
            shutil.copyfile(image, folder / 'firmware.ota.bin')
            (folder / 'firmware.ota.bin').chmod(0o600)
            shutil.copyfile(artifact / 'firmware.elf', folder / 'firmware.elf')
            (folder / 'firmware.elf').chmod(0o600)
            report['firmware_sha256'] = hashlib.sha256(image.read_bytes()).hexdigest()
            if args.local_only:
                report.update(ok=True, verification='local-only; physical gates not run; publication forbidden')
                return 0
            from panel_remote import PanelClient
            # Missing/stale baselines stop before OTA. Recording is a separate explicit operation.
            configuration = qa_configuration(args, stage)
            with PanelLock('device') as device:
                with runtime_logs(folder, args.device, device.subprocess_options(), filename='boot.log'):
                    with PanelClient(args.url, persistent=True) as before:
                        initial = before.status()
                        if recovery_plan and recovery_authorization.get('installed_guard'):
                            installed = recovery_authorization['installed_guard']
                            if (initial.get('build_id') != installed['build_id']
                                    or initial.get('boot_id') != installed['boot_id']
                                    or initial.get('firmware_elf_sha256') != installed['elf_sha256'][:9]
                                    or initial.get('boot_healthy') is not True):
                                raise RuntimeError('Installed guard identity/boot changed; single-attempt recovery refused')
                        if initial['page'] not in ('page_glass_v2','page_hemmet','page_tavla') or initial.get('test_mode', 'idle') != 'idle':
                            raise RuntimeError('Device busy; no upload or physical interaction performed')
                        for field in ('voice_active','alarm_active','announcement_active','physical_touch_active'):
                            if initial.get(field) is not False:
                                raise RuntimeError('Device activity guard is active or unavailable: ' + field)
                        # A lease is held throughout OTA. A reboot destroys it; obtain a fresh lease after identity verification.
                        with before.exclusive():
                            if recovery_plan:
                                from panel_recovery import claim_attempt
                                report['recovery'] = claim_attempt(recovery_plan, candidate, folder / 'report.json')
                            print('OTA startar om panelen.', flush=True)
                            stage('ota', lambda: command([ROOT / '.venv/bin/esphome', 'upload', 'panel.yaml',
                                  '--device', args.device, '--file', folder / 'firmware.ota.bin'], folder / 'ota.log',
                                  timeout=180, cwd=BOARD, options=device.subprocess_options()))
                    boot = stage('boot-and-identity', lambda: wait_boot(args.url, candidate['build_id'], initial))
                stage('boot-log-review', lambda: check_logs(folder / 'boot.log', ota_boot=boot['status']))
                with PanelClient(args.url, persistent=True) as client, client.exclusive(require_device_lease=True):
                    physical_qa(client, folder, boot['status'], stage, configuration, args, device.subprocess_options())
            unchanged(candidate)
            verified = STATE / 'verified' / candidate['build_id']
            verified.mkdir(parents=True, mode=0o700, exist_ok=True)
            shutil.copyfile(folder / 'firmware.ota.bin', verified / 'firmware.ota.bin')
            (verified / 'firmware.ota.bin').chmod(0o600)
            private_json(verified / f'verification-{folder.name}.json', {'build_id': candidate['build_id'],
                         'firmware_sha256': report['firmware_sha256'], 'report': str(folder / 'report.json')})
            prune_verified(STATE / 'verified')
            if args.publish:
                report['commit'] = publish(candidate, args.files, args.message)
                report['published'] = True
            report.update(ok=True, verification='physical')
            return 0
        except (Exception, KeyboardInterrupt) as exc:
            report['error'] = redact(str(exc))
            if any(item['name'] == 'ota' for item in report['stages']):
                quarantine = STATE / 'quarantine.json'
                if not quarantine.exists():
                    private_json(quarantine, {'reason': 'Physical verification failed after OTA began; cause must be resolved before another run',
                                 'build_id': candidate['build_id'], 'report': str(folder / 'report.json'),
                                 'error': report['error']})
            print(f'CI STOP: {report["error"]}', file=sys.stderr)
            return 1
        finally:
            report['finished_at'] = datetime.now().astimezone().isoformat()
            private_json(folder / 'report.json', report)
            print(f'CI report: {folder / "report.json"}', flush=True)


def final_health(client, before):
    status = client.status()
    for field in ('pixel_dma_errors', 'pixel_ppa_errors'):
        if field in status and (type(status[field]) is not int or status[field] != 0):
            raise RuntimeError('Hardware pixel-copy error: '+field)
    if status['uptime_ms'] < before['uptime_ms'] or status.get('build_id') != before.get('build_id') or status.get('boot_id') != before.get('boot_id'):
        raise RuntimeError('Device restarted or firmware identity changed during QA')
    if not status.get('boot_healthy') or status.get('test_mode', 'idle') != 'idle':
        raise RuntimeError('Final health or test cleanup not proven')
    if status['internal_free'] < 98304 or status['psram_free'] < 4194304:
        raise RuntimeError('Memory below physical safety floor; further operations stopped')
    if before['internal_free'] - status['internal_free'] > 65536 or before['psram_free'] - status['psram_free'] > 262144:
        raise RuntimeError('Abnormal memory decline during physical QA')
    return status


def main():
    def interrupted(signum, frame):
        raise KeyboardInterrupt('CI interrupted; subprocess and lease cleanup requested')
    signal.signal(signal.SIGTERM, interrupted)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--local-only', action='store_true', help='Checks and compile only; can never publish')
    parser.add_argument('--verify-installed', type=Path, metavar='INTERRUPTED_RUN',
                        help='No OTA: repeat all physical QA on the identical boot after a proven voice interruption')
    parser.add_argument('--transport-diagnosis', type=Path,
                        help='Fixed without-logs/with-logs diagnostic directory for one same-boot continuation after isolated host status delay')
    parser.add_argument('--url', default='http://hallpanel.local:8080')
    parser.add_argument('--device', default='hallpanel.local', help='OTA hostname; USB is never selected automatically')
    parser.add_argument('--visual-baseline', type=Path, help='Explicit reviewed golden directory; default .panel-visual/reference')
    parser.add_argument('--performance-baseline', type=Path, help='Explicit reviewed idle profile; default .panel-ci/baselines/performance.json')
    parser.add_argument('--animation-baseline', type=Path, help='Explicit reviewed animation profile; default .panel-ci/baselines/animation.json')
    parser.add_argument('--brightness-drag', action='store_true', help='Verify real settings slider detents and restore brightness')
    parser.add_argument('--compare-orb-methods', action='store_true', help='Measure bounded linear/staged A/B diagnostics before the mandatory production target')
    parser.add_argument('--recovery-plan', type=Path, help='Approved, candidate-bound recovery plan; all gates remain mandatory, never publishes')
    parser.add_argument('--publish', action='store_true', help='Explicit authorization to commit the allowlist and push main after all physical gates')
    parser.add_argument('--files', nargs='+', default=[])
    parser.add_argument('--message', default='Add autonomous local panel verification')
    parser.add_argument('--repair-argv', nargs=argparse.REMAINDER, help='Explicit local repair adapter; at most three invocations; must be last option')
    args = parser.parse_args()
    if args.verify_installed:
        if args.local_only or args.publish or args.recovery_plan or args.repair_argv or args.files:
            parser.error('--verify-installed cannot compile, publish, repair, recover or select files')
        return verify_installed(args)
    if args.local_only and args.publish:
        parser.error('--local-only cannot publish')
    if args.transport_diagnosis:
        parser.error('--transport-diagnosis requires --verify-installed')
    if args.device.startswith('/'):
        parser.error('CI uses OTA only; USB recovery requires a separate approved operation')
    return pipeline(args)


if __name__ == '__main__':
    raise SystemExit(main())
