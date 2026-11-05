#!/usr/bin/env python3
"""Bounded recovery verification using the ordinary CI gates; no agent-specific steps."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import shlex
import subprocess
import time

import panel_ci as ci

# Joel's standing instruction, 2026-10-26: perform task-scoped restarts,
# flashes and verification autonomously. This never authorizes publication.
STANDING_AUTHORIZATION = 'Joel 2026-10-26: autonomous task-scoped panel restart, flash and verification'


def sealed_plan(path):
    """Bind the reviewed inputs without requiring another human confirmation."""
    approval = read(sidecar(path, '.approval.json'))
    return (approval.get('plan_sha256') == sha(path) and bool(
        approval.get('human_authorization') or
        approval.get('standing_authorization') == STANDING_AUTHORIZATION))


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def read(path):
    return json.loads(Path(path).read_text())


def sidecar(path, suffix):
    return Path(str(Path(path).resolve()) + suffix)


def restart_linkage(elf, build=None):
    """Inspect the linked executable, not just a source file or a compiler flag."""
    from elftools.elf.elffile import ELFFile
    with Path(elf).open('rb') as stream:
        image = ELFFile(stream)
        symbols = image.get_section_by_name('.symtab')
        found = {}
        for name in ('__wrap_esp_restart_noos', 'esp_restart_noos', 'esp_restart'):
            entries = symbols.get_symbol_by_name(name) if symbols else None
            if not entries:
                raise RuntimeError('Missing restart symbol in ELF: ' + name)
            symbol = entries[0]
            section = image.get_section(symbol['st_shndx']).name
            if name != 'esp_restart' and 'iram' not in section:
                raise RuntimeError('Restart routine is not in IRAM: ' + name)
            found[name] = {'address': hex(symbol['st_value']), 'section': section}
    commands = read((build or ci.BUILD) / 'build/compile_commands.json')
    compiler = Path(shlex.split(commands[0]['command'])[0])
    objdump = compiler.with_name('riscv32-esp-elf-objdump')
    output = subprocess.check_output([str(objdump), '-d', '--disassemble=esp_restart', str(elf)],
                                     text=True, timeout=15)
    if not re.search(r'\b(?:jal|jalr|j)\b[^\n]*<__wrap_esp_restart_noos>', output):
        raise RuntimeError('esp_restart does not call the guarded restart in the linked ELF')
    return {'ok': True, 'symbols': found, 'elf_sha256': sha(elf)}


def installed_guard_proof(report_path, candidate, smoke_report=None):
    """An already installed identical runtime permits one remaining restart proof."""
    report_path = Path(report_path).resolve()
    report = read(report_path)
    previous = read(report_path.parent / 'candidate.json')
    stages = {stage['name']: stage for stage in report['stages']}
    for name in ('restart-linkage', 'ota', 'boot-and-identity', 'boot-log-review'):
        if not stages.get(name, {}).get('ok'):
            raise RuntimeError('Installed guard has no positive proof for ' + name)
    boot = stages['boot-and-identity']['result']['status']
    supplement = {}
    if smoke_report:
        smoke_report = Path(smoke_report).resolve()
        smoke = read(smoke_report)
        required = 'real brightness detents: 48 and 52 snap to 50; exact value restoration'
        if (report.get('error') != 'Saved brightness is between detents; exact touch restoration is unavailable'
                or stages.get('physical-integration', {}).get('ok') is not False
                or smoke.get('ok') is not True or smoke.get('restore_error')
                or len(smoke.get('checks', [])) < 9
                or required not in smoke.get('checks', [])):
            raise RuntimeError('Supplement only accepts the diagnosed off-step preflight failure and passing detent smoke')
        for state in (smoke.get('before', {}), smoke.get('after', {})):
            if (state.get('build_id') != boot['build_id'] or state.get('boot_id') != boot['boot_id']
                    or state.get('boot_healthy') is not True or state.get('wifi_connected') is not True):
                raise RuntimeError('Supplemental smoke did not verify the same installed healthy boot')
        if smoke['after'].get('page') != smoke['before'].get('page'):
            raise RuntimeError('Supplemental smoke did not restore the original page')
        supplement = {'smoke_report':str(smoke_report), 'smoke_sha256':sha(smoke_report)}
    elif not stages.get('physical-integration', {}).get('ok'):
        raise RuntimeError('Installed guard has no positive proof for physical-integration')
    if not boot.get('boot_healthy') or boot.get('reset_reason') != 3 or boot.get('build_id') != previous['build_id']:
        raise RuntimeError('Installed guard boot identity/health is not proven')
    # Host test/docs corrections may differ. Runtime generators, SDK settings,
    # assets, native components and headers must be exactly the installed code.
    host_files = {'AGENTS.md', 'tools/panel', 'tools/panel_ci.py', 'tools/panel_recovery.py',
                  'tools/panel_ui_test.py'}
    changed = {name for name in previous['files'].keys() | candidate['files'].keys()
               if previous['files'].get(name) != candidate['files'].get(name)}
    if any(name not in host_files and not name.startswith(('tests/', 'docs/')) for name in changed):
        raise RuntimeError('Runtime source changed; two installation/restart proofs are required')
    guard = 'components/panel_restart/restart_guard.cpp'
    if guard not in previous['files'] or previous['files'][guard] != candidate['files'].get(guard):
        raise RuntimeError('Installed restart guard source is not identical')
    linkage = restart_linkage(report_path.parent / 'firmware.elf')
    if (boot.get('firmware_elf_sha256') != linkage['elf_sha256'][:9]
            or sha(report_path.parent / 'firmware.ota.bin') != report['firmware_sha256']):
        raise RuntimeError('Installed guard artifact identity is not proven')
    return {'report': str(report_path), 'report_sha256': sha(report_path),
            'build_id': boot['build_id'], 'boot_id': boot['boot_id'],
            'elf_sha256': linkage['elf_sha256'], 'host_changes': sorted(changed), **supplement}


def ui_failure_proof(report_path, native_path, candidate, prior_plan=None):
    """Bound a known UI navigation failure to a real LVGL reproduction and fix."""
    report_path, native_path = Path(report_path).resolve(), Path(native_path).resolve()
    failed, native = read(report_path), read(native_path)
    stages = {s['name']: s for s in failed['stages']}
    for name in ('restart-linkage', 'ota', 'boot-and-identity', 'boot-log-review'):
        if not stages.get(name, {}).get('ok'):
            raise RuntimeError('UI recovery requires a positively verified boot and clean crash log')
    if (failed.get('ok') is not False or stages.get('physical-integration', {}).get('ok') is not False
            or not failed.get('error', '').startswith('Expected physical page ')):
        raise RuntimeError('UI recovery only accepts a diagnosed navigation failure')
    original = Path(read(ci.STATE/'quarantine.json')['report']).resolve()
    lineage = {'repair_depth': 1}
    if original != report_path:
        if not prior_plan:
            raise RuntimeError('UI failure does not match quarantine or a sealed prior attempt')
        prior_plan = Path(prior_plan).resolve()
        prior = read(prior_plan)
        claim = read(sidecar(prior_plan, '.attempts')/'attempt-01.json')
        if (prior.get('kind') != 'ui-navigation-recovery' or prior.get('max_attempts') != 1
                or prior.get('root') != str(ci.ROOT) or prior.get('quarantine_sha256') != sha(ci.STATE/'quarantine.json')
                or not sealed_plan(prior_plan)
                or claim != failed.get('recovery') or claim.get('plan_sha256') != sha(prior_plan)
                or Path(claim['report']).resolve() != report_path):
            raise RuntimeError('Prior UI attempt is not sealed to this failure and quarantine')
        depth = prior['prior_ui_boot'].get('repair_depth', 1) + 1
        if depth > 2:
            raise RuntimeError('UI repair limit reached: initial installation plus at most two corrections')
        lineage = {'repair_depth': depth, 'previous_plan': str(prior_plan), 'previous_plan_sha256': sha(prior_plan)}
    expected = [{'gesture_bubble': True, 'explicit_pointer': True, 'gestures': 0, 'clicks': 1},
                {'gesture_bubble': False, 'explicit_pointer': False, 'gestures': 0, 'clicks': 1},
                {'gesture_bubble': False, 'explicit_pointer': True, 'gestures': 1, 'clicks': 0}]
    required = {'styles/glass-v2/gen.py', 'tests/native/tab_gesture.cpp', 'tools/check_tab_gesture.py',
                'components/panel_debug/panel_debug.cpp'}
    if (native.get('ok') is not True or native.get('actual_native_lvgl') is not True
            or native.get('cases') != expected or set(native.get('sources', {})) != required):
        raise RuntimeError('Real native LVGL reproduction and correction must both pass')
    if any(candidate['files'][name]['sha256'] != digest for name, digest in native['sources'].items()):
        raise RuntimeError('Native UI proof does not match checked source')
    previous = read(report_path.parent/'candidate.json')
    guard = 'components/panel_restart/restart_guard.cpp'
    if previous['files'].get(guard) != candidate['files'].get(guard):
        raise RuntimeError('Restart guard changed during UI recovery')
    boot = stages['boot-and-identity']['result']['status']
    elf_sha = sha(report_path.parent/'firmware.elf')
    if boot.get('reset_reason') != 3 or not boot.get('boot_healthy') or boot.get('firmware_elf_sha256') != elf_sha[:9]:
        raise RuntimeError('Prior UI failure had an unsafe boot or unproven firmware')
    return {'report': str(report_path), 'report_sha256': sha(report_path),
            'native_report': str(native_path), 'native_sha256': sha(native_path),
            'build_id': boot['build_id'], 'boot_id': boot['boot_id'], 'elf_sha256': elf_sha, **lineage}


def prepare(run, diagnosis, destination, installed_report=None, ui_report=None, native_report=None, prior_plan=None,
            smoke_report=None):
    run, diagnosis = Path(run).resolve(), Path(diagnosis).resolve()
    report, candidate = read(run / 'report.json'), read(run / 'candidate.json')
    if not report.get('ok') or report.get('mode') != 'local-only':
        raise RuntimeError('Recovery plan requires a successful local CI run')
    if report.get('build_id') != candidate['build_id']:
        raise RuntimeError('CI report and candidate identities differ')
    ci.unchanged(candidate)
    relative = str(diagnosis.relative_to(ci.ROOT))
    if relative not in candidate['files'] or sha(diagnosis) != candidate['files'][relative]['sha256']:
        raise RuntimeError('Diagnosis must be a versionable part of the checked candidate')
    if sha(run / 'firmware.ota.bin') != report['firmware_sha256']:
        raise RuntimeError('Checked firmware artifact changed')
    linkage = restart_linkage(run / 'firmware.elf')
    installed = installed_guard_proof(installed_report, candidate, smoke_report) if installed_report else None
    ui_failure = ui_failure_proof(ui_report, native_report, candidate, prior_plan) if ui_report and native_report else None
    plan = {'format': 1, 'kind': 'restart-cache-sync-recovery', 'root': str(ci.ROOT),
            'run': str(run), 'build_id': candidate['build_id'],
            'candidate_sha256': sha(run / 'candidate.json'),
            'firmware_sha256': report['firmware_sha256'], 'elf_sha256': linkage['elf_sha256'],
            'diagnosis': relative, 'diagnosis_sha256': sha(diagnosis),
            'quarantine_sha256': sha(ci.STATE / 'quarantine.json'),
            'max_attempts': 1 if installed or ui_failure else 2, 'expires_epoch': time.time() + 86400,
            'steps': ['Install checked fix and run all physical CI gates',
                      'Reinstall same checked fix to test its own restart and repeat every gate'],
            'stop': 'Any failed or incomplete attempt stops further attempts; quarantine remains',
            'publication': False}
    if installed:
        plan['installed_guard'] = installed
        plan['steps'] = ['Test the installed identical guard during one OTA restart, then run every physical CI gate']
    if ui_failure:
        plan['kind'] = 'ui-navigation-recovery'
        plan['prior_ui_boot'] = ui_failure
        plan['steps'] = ['Install the source-bound, native-LVGL-verified navigation correction once; run every physical CI gate']
    ci.private_json(destination, plan)
    ci.private_json(sidecar(destination, '.approval.json'), {
        'plan_sha256': sha(destination), 'standing_authorization': STANDING_AUTHORIZATION,
        'sealed_epoch': time.time()})
    return plan


def validate_plan(path, candidate, *, require_approval=True):
    path = Path(path).resolve()
    plan = read(path)
    if (plan.get('format') != 1 or plan.get('kind') not in ('restart-cache-sync-recovery', 'ui-navigation-recovery')
            or plan.get('root') != str(ci.ROOT) or plan.get('max_attempts') not in (1, 2)
            or plan.get('publication') is not False):
        raise RuntimeError('Invalid recovery plan')
    if not time.time() < plan['expires_epoch'] <= time.time() + 86401:
        raise RuntimeError('Recovery plan expired or has an invalid lifetime')
    if sha(ci.STATE / 'quarantine.json') != plan['quarantine_sha256']:
        raise RuntimeError('Quarantine changed; review and prepare a new plan')
    run = Path(plan['run'])
    if sha(run / 'candidate.json') != plan['candidate_sha256']:
        raise RuntimeError('Recovery candidate manifest changed')
    checked = read(run / 'candidate.json')
    if any(candidate[k] != checked[k] for k in ('head', 'branch', 'files', 'build_id')):
        raise RuntimeError('Recovery candidate changed; rerun local CI and prepare a new plan')
    for name, key in (('firmware.ota.bin', 'firmware_sha256'), ('firmware.elf', 'elf_sha256')):
        if sha(run / name) != plan[key]:
            raise RuntimeError('Approved recovery artifact changed: ' + name)
    if sha(ci.ROOT / plan['diagnosis']) != plan['diagnosis_sha256']:
        raise RuntimeError('Recovery diagnosis changed')
    if plan['kind'] == 'ui-navigation-recovery':
        prior = plan['prior_ui_boot']
        if plan['max_attempts'] != 1 or ui_failure_proof(prior['report'], prior['native_report'], candidate,
                                                     prior.get('previous_plan')) != prior:
            raise RuntimeError('UI recovery evidence changed')
    elif plan['max_attempts'] == 1:
        installed = plan['installed_guard']
        if sha(installed['report']) != installed['report_sha256'] or installed_guard_proof(
                installed['report'], candidate, installed.get('smoke_report')) != installed:
            raise RuntimeError('Installed guard evidence changed')
    if require_approval:
        if not sealed_plan(path):
            raise RuntimeError('Recovery plan seal changed; prepare a new checked plan')
    return {'ok': True, 'plan': str(path), 'plan_sha256': sha(path),
            'artifact_folder': str(run), 'build_id': plan['build_id'],
            'installed_guard': plan.get('installed_guard') or plan.get('prior_ui_boot')}


def approve(path, authorization):
    if not authorization.strip():
        raise RuntimeError('Record the actual human authorization, not an inferred approval')
    validate_plan(path, ci.inventory(), require_approval=False)
    suffix = '.human-authorization.json' if sidecar(path, '.approval.json').exists() else '.approval.json'
    ci.private_json(sidecar(path, suffix), {'plan_sha256': sha(path),
                    'human_authorization': authorization, 'approved_epoch': time.time()})
    return {'ok': True, 'plan': str(Path(path).resolve()), 'max_attempts': read(path)['max_attempts']}


def successful_attempts(path):
    claims = sorted(sidecar(path, '.attempts').glob('attempt-*.json'))
    if len(claims) > read(path)['max_attempts']:
        raise RuntimeError('Recovery attempt limit exceeded')
    reports = []
    for number, claim_path in enumerate(claims, 1):
        claim = read(claim_path)
        if claim.get('attempt') != number or claim.get('plan_sha256') != sha(path):
            raise RuntimeError('Recovery attempt ledger changed')
        report = read(claim['report'])  # An interrupted/missing report fails closed.
        if (not report.get('ok') or report.get('verification') != 'physical'
                or report.get('published') is not False
                or report.get('recovery') != claim):
            raise RuntimeError('Previous recovery attempt failed or is incomplete; further attempts stopped')
        boot = next((s.get('result', {}).get('status', {}) for s in report['stages']
                     if s['name'] == 'boot-and-identity' and s.get('ok')), {})
        if boot.get('reset_reason') != 3 or report.get('build_id') != read(path)['build_id']:
            raise RuntimeError('Recovery did not prove a normal software reset of the approved firmware')
        reports.append({'report': claim['report'], 'sha256': sha(claim['report']), 'boot_id': boot.get('boot_id')})
    if len(reports) == 2 and reports[0]['boot_id'] == reports[1]['boot_id']:
        raise RuntimeError('Recovery did not establish two distinct boots')
    return reports


def claim_attempt(path, candidate, report):
    validate_plan(path, candidate)
    number = len(successful_attempts(path)) + 1
    if number > read(path)['max_attempts']:
        raise RuntimeError('Recovery attempt limit reached')
    plan = read(path)
    for name, key in (('firmware.ota.bin', 'firmware_sha256'), ('firmware.elf', 'elf_sha256')):
        if sha(Path(report).parent / name) != plan[key]:
            raise RuntimeError('CI must install the exact approved recovery artifact')
    claim = {'attempt': number, 'plan_sha256': sha(path), 'report': str(Path(report).resolve())}
    ci.private_json(sidecar(path, '.attempts') / f'attempt-{number:02d}.json', claim)
    return claim


def finish(path):
    validate_plan(path, ci.inventory())
    reports = successful_attempts(path)
    plan = read(path)
    if len(reports) != plan['max_attempts']:
        raise RuntimeError('Two complete physical CI runs, or one with an installed identical guard, are required to resolve this quarantine')
    prior = plan.get('installed_guard') or plan.get('prior_ui_boot')
    if plan['max_attempts'] == 1 and reports[0]['boot_id'] == prior['boot_id']:
        raise RuntimeError('Installed guard did not establish a new boot')
    archive = sidecar(path, '.resolved.json')
    ci.private_json(archive, {'ok': True, 'plan_sha256': sha(path), 'reports': reports,
                    'quarantine': read(ci.STATE / 'quarantine.json'),
                    'note': 'Bounded physical verification; host automatic restore remains disabled'})
    (ci.STATE / 'quarantine.json').unlink()
    return {'ok': True, 'resolution': str(archive), 'automatic_host_restore': False}


REMOTE_SYNC = r'''
import hashlib,json,subprocess,sys
from pathlib import Path
config=json.loads(sys.argv[1])
root=Path(config['root']).expanduser()
state=Path(config['state']).expanduser()
sys.path.insert(0,str(root/'tools'))
from panel_remote import PanelClient
from panel_lock import PanelLock
def service(*args):
    return subprocess.run(['systemctl','--user',*args],check=True,
                          stdout=subprocess.PIPE,stderr=subprocess.STDOUT,timeout=15)
with PanelLock('workspace'):
    quarantine=state/'quarantine.json'
    raw=quarantine.read_bytes()
    if hashlib.sha256(raw).hexdigest()!=config['quarantine_sha256']:
        raise RuntimeError('Jarvis quarantine changed; nothing resumed')
    with PanelClient(config['url'],persistent=True) as client:
        status=client.status()
    for field in ('build_id','boot_id','firmware_elf_sha256'):
        if status.get(field)!=config['status'][field]:
            raise RuntimeError('Panel identity/boot changed; nothing resumed')
    if (status.get('boot_healthy') is not True or status.get('reset_reason')!=3
            or status.get('internal_free',0)<98304 or status.get('psram_free',0)<4194304):
        raise RuntimeError('Panel health not proven; nothing resumed')
    archived=state/('quarantine-resolved-'+config['resolution_sha256'][:16]+'.json')
    if archived.exists():
        raise RuntimeError('Resolution already exists; inspect before retry')
    service('stop','hallpanel-smoke.timer')
    # Recheck after the network request, before changing the active guard.
    if quarantine.read_bytes()!=raw:
        raise RuntimeError('Jarvis quarantine changed during verification')
    quarantine.rename(archived)
    try:
        service('enable','--now','hallpanel-health.timer')
        if config['resume_smoke']:
            service('enable','--now','hallpanel-smoke.timer')
        service('start','hallpanel-health.service')
        service('is-active','hallpanel-health.timer')
        if config['resume_smoke']:
            service('is-active','hallpanel-smoke.timer')
    except BaseException:
        service('disable','--now','hallpanel-smoke.timer')
        archived.rename(quarantine)
        raise
print(json.dumps({'ok':True,'archived_quarantine':str(archived),
                  'health_timer':'active','smoke_timer':'active' if config['resume_smoke'] else 'stopped',
                  'build_id':status['build_id'],'boot_id':status['boot_id']}))
'''


def sync_jarvis(resolution, host, url, quarantine_sha256, resume_smoke=False):
    """Resume existing Jarvis timers only for the exact resolved fault and live boot."""
    resolution = Path(resolution).resolve()
    suffix = '.resolved.json'
    if not str(resolution).endswith(suffix) or (ci.STATE / 'quarantine.json').exists():
        raise RuntimeError('An archived local resolution and no active local quarantine are required')
    archive = read(resolution)
    plan = Path(str(resolution)[:-len(suffix)])
    if archive.get('ok') is not True or archive.get('plan_sha256') != sha(plan):
        raise RuntimeError('Local resolution is not bound to its recovery plan')
    reports = archive['reports']
    for proof in reports:
        if sha(proof['report']) != proof['sha256']:
            raise RuntimeError('Physical verification report changed')
    report = read(reports[-1]['report'])
    if not report.get('ok') or report.get('verification') != 'physical':
        raise RuntimeError('Full physical verification is required before Jarvis resumes')
    status = next(s['result'] for s in report['stages'] if s['name'] == 'final-health' and s['ok'])
    if not re.fullmatch(r'[0-9a-f]{64}', quarantine_sha256) or not re.fullmatch(r'[A-Za-z0-9_][A-Za-z0-9_.@:-]*', host):
        raise RuntimeError('Invalid expected quarantine fingerprint or SSH host')
    config = {'root': '~/.local/share/hallpanel-harness/current', 'state': '~/.local/state/hallpanel',
              'url': url, 'quarantine_sha256': quarantine_sha256, 'resume_smoke': resume_smoke,
              'resolution_sha256': sha(resolution), 'status': {k: status[k] for k in
                  ('build_id', 'boot_id', 'firmware_elf_sha256')}}
    # Bootstrap with system Python, then use the installed harness's venv.
    # Only non-secret proof metadata travels in argv; the token stays on Jarvis.
    bootstrap = ("import json,os,sys;from pathlib import Path;"
                 "p=Path(json.loads(sys.argv[1])['root']).expanduser()/'.venv/bin/python';"
                 f"os.execv(str(p),[str(p),'-c',{REMOTE_SYNC!r},sys.argv[1]])")
    command = shlex.join(['python3', '-c', bootstrap, json.dumps(config)])
    log = sidecar(resolution, '.jarvis.log')
    if log.exists():
        raise RuntimeError('Jarvis synchronization already attempted; inspect its log before retrying')
    ci.command(['ssh', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=5', '-o', 'IgnoreUnknown=UseKeychain',
                '-o', 'UseKeychain=yes', '-o', 'ControlMaster=auto',
                '-o', f'ControlPath={Path.home()}/.ssh/cm-%C', '-o', 'ControlPersist=10m', host, command],
               log, timeout=75)
    result = read(log)
    if result.get('ok') is not True:
        raise RuntimeError('Jarvis synchronization did not establish success')
    ci.private_json(sidecar(resolution, '.jarvis.json'), result)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='operation', required=True)
    plan = commands.add_parser('plan', help='Prepare a reviewable plan without device access or approval')
    plan.add_argument('--run', type=Path, required=True, help='Successful local-only CI folder')
    plan.add_argument('--diagnosis', type=Path, required=True)
    plan.add_argument('--out', type=Path, required=True)
    plan.add_argument('--installed-report', type=Path,
                      help='Prior positive installation/boot/smoke proof of identical runtime: only one further OTA')
    plan.add_argument('--smoke-report', type=Path, help='Passing same-boot detent smoke after off-step preflight correction')
    plan.add_argument('--ui-report', type=Path, help='Quarantined navigation failure with clean boot/log proof')
    plan.add_argument('--native-report', type=Path, help='Real LVGL gesture reproduction/correction report for this candidate')
    plan.add_argument('--prior-plan', type=Path, help='Sealed prior failed UI correction; at most two corrections after initial installation')
    approval = commands.add_parser('approve', help='Optional record of additional human authorization; plans are already sealed automatically')
    approval.add_argument('plan', type=Path)
    approval.add_argument('--authorization', required=True, help='Verbatim human authorization')
    close = commands.add_parser('finish', help='Resolve quarantine only after both approved physical runs pass')
    close.add_argument('plan', type=Path)
    sync = commands.add_parser('sync-jarvis', help='Reconcile a resolved fault and resume existing Jarvis timers')
    sync.add_argument('resolution', type=Path)
    sync.add_argument('--host', required=True)
    sync.add_argument('--url', default='http://hallpanel.local:8080')
    sync.add_argument('--expected-quarantine-sha256', required=True)
    sync.add_argument('--resume-smoke', action='store_true', help='Resume the previously authorized idle-only smoke timer')
    args = parser.parse_args()
    if args.operation == 'plan' and args.smoke_report and not args.installed_report:
        parser.error('--smoke-report requires --installed-report')
    if args.operation == 'plan' and (bool(args.ui_report) != bool(args.native_report)
                                     or (args.ui_report and args.installed_report)
                                     or (args.prior_plan and not args.ui_report)):
        parser.error('Use --ui-report and --native-report together, independently of --installed-report')
    from panel_lock import PanelLock
    try:
        with PanelLock('workspace'):
            if args.operation == 'plan':
                result = prepare(args.run, args.diagnosis, args.out, args.installed_report,
                                 args.ui_report, args.native_report, args.prior_plan, args.smoke_report)
            elif args.operation == 'approve':
                result = approve(args.plan, args.authorization)
            elif args.operation == 'finish':
                result = finish(args.plan)
            else:
                result = sync_jarvis(args.resolution, args.host, args.url,
                                     args.expected_quarantine_sha256, args.resume_smoke)
        print(json.dumps({'ok': True, 'result': result}, ensure_ascii=False))
        return 0
    except (OSError, ValueError, KeyError, RuntimeError, StopIteration) as exc:
        print(json.dumps({'ok': False, 'error': ci.redact(str(exc))}, ensure_ascii=False))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
