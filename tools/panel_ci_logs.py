"""The panel's native API logs for a CI run: subscribe before protected work, mask credentials, find crashes."""
from contextlib import contextmanager
import os
from pathlib import Path
import re
import signal
import subprocess
import time

ROOT = Path(__file__).resolve().parents[1]
BOARD = ROOT / 'styles/glass-v2'


def redact(value):
    """Logs remain private and local; mask every local credential before writing."""
    value = re.sub(r'\x1b\[[0-9;]*m', '', value)
    try:
        from ruamel.yaml import YAML
        secrets = YAML(typ='safe').load((BOARD / 'secrets.yaml').read_text())
        for secret in sorted((v for v in secrets.values() if isinstance(v, str) and v), key=len, reverse=True):
            value = value.replace(secret, '<redacted>')
    except FileNotFoundError:
        pass
    except Exception:
        return 'Log withheld: local credential redaction failed.\n'
    return re.sub(r'(?im)^.*(?:authorization:|bearer |api_key|password|encryption.key).*$','<redacted line>',value)


@contextmanager
def runtime_logs(folder, host, options, filename='runtime.log'):
    """Read-only encrypted native API logs. Never opens USB or resets the device."""
    import tempfile
    with tempfile.TemporaryFile() as raw:
        child = subprocess.Popen([str(ROOT / '.venv/bin/esphome'), 'logs', 'panel.yaml', '--device', host],
                                 cwd=BOARD, stdout=raw, stderr=subprocess.STDOUT, start_new_session=True,
                                 **options)
        try:
            wait_log_subscription(child, raw)
            yield child
        finally:
            if child.poll() is None:
                os.killpg(child.pid, signal.SIGTERM)
            try:
                child.wait(timeout=5)
            except subprocess.TimeoutExpired:
                os.killpg(child.pid, signal.SIGKILL)
                child.wait()
            raw.seek(0)
            text = redact(raw.read().decode(errors='replace'))
            (folder / filename).write_text(text)
            (folder / filename).chmod(0o600)


def wait_log_subscription(child, raw, seconds=20):
    """Prove receipt of native log records before allowing OTA or test actions.

    Popen is not a subscription. DNS/TCP/auth can take longer than an upload.
    pread does not disturb the file offset shared with the child writer.
    """
    deadline = time.monotonic() + seconds
    while True:
        if child.poll() is not None:
            raise RuntimeError('Native log process exited before subscription was proven; protected work not started')
        text = os.pread(raw.fileno(), 512 * 1024, 0).decode(errors='replace')
        authenticated = re.search(r'Successful handshake|Successfully authenticated', text, re.I)
        native_record = re.search(r'\[[VDIWE]\]\[[^\]\r\n]+\]:', text)
        if authenticated and native_record and native_record.start() > authenticated.start():
            return
        if time.monotonic() >= deadline:
            raise RuntimeError('Native log subscription timed out; protected work not started')
        time.sleep(.05)


def check_logs(path, *, ota_boot=None):
    text = path.read_text()
    if not re.search(r'Connected to|Successfully connected|Successfully authenticated', text, re.I):
        raise RuntimeError('Native runtime log connection was not established')
    historical = []
    if ota_boot is not None:
        # ESPHome replays retained crash data when an API log client connects.
        # Only a positively observed OTA/restart boundary can separate it from
        # this installation. Reset reason also catches a panic the API missed.
        boundary = text.split('Rebooting safely')
        if len(boundary) != 2 or 'Update complete' not in boundary[0]:
            raise RuntimeError('OTA/restart log boundary was not proven')
        if ota_boot.get('reset_reason') != 3:
            raise RuntimeError('OTA boot was not a normal software reset')
        historical, text = boundary
    pattern = r'^.*(?:Guru Meditation|panic.ed|Stack protection fault|SAFE MODE IS ACTIVE|Boot loop detected|CORRUPT HEAP|Brownout detector|CRASH DETECTED|underrun happens|Frame presentation failed).*$'
    faults = re.findall(pattern, text, re.M | re.I)
    if isinstance(historical, str):
        # Only ESPHome's retained crash marker is a historical replay. A live
        # panic/heap/brownout message before OTA must still stop verification.
        faults += [line for line in re.findall(pattern, historical, re.M | re.I)
                   if 'CRASH DETECTED ON PREVIOUS BOOT' not in line]
    if faults:
        raise RuntimeError(f'Crash/bootloop/heap faults found in private {path.name}')
    return {'ok': True, 'crashes': len(faults), 'historical_fault_lines':
            len(re.findall(pattern, historical, re.M | re.I)) if isinstance(historical, str) else 0}
