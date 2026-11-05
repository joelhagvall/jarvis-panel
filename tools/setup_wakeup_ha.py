"""Wire the wakeup package/dashboard into HA YAML without rewriting unrelated configuration.

Run in HA: python /tmp/setup_wakeup_ha.py /config. Copy the two supplied YAML files first.
Native helper values are initialized separately through HA services, never raw .storage writes.
"""
from datetime import datetime
from pathlib import Path
import re
import sys

import yaml


def configure(text):
    original = yaml.load(text, Loader=yaml.BaseLoader)
    packages = original.get("homeassistant", {}).get("packages")
    dashboards = original.get("lovelace", {}).get("dashboards")
    if not isinstance(packages, dict) or not isinstance(dashboards, dict):
        raise ValueError("Expected existing HA packages and YAML dashboards; no automatic rewrite")
    expected = yaml.load(text, Loader=yaml.BaseLoader)
    expected["homeassistant"]["packages"]["jarvis_wakeup"] = "jarvis_wakeup.yaml"
    expected["lovelace"]["dashboards"]["jarvis-vackning"] = {
        "mode": "yaml", "title": "Jarvis väckning", "icon": "mdi:alarm",
        "show_in_sidebar": "true", "filename": "jarvis_wakeup_dashboard.yaml"}
    if "jarvis_wakeup" not in packages:
        text, count = re.subn(r"(?m)^(  packages:\s*)$", r"\1\n    jarvis_wakeup: !include jarvis_wakeup.yaml", text)
        if count != 1:
            raise ValueError("Cannot locate unique packages block")
    if "jarvis-vackning" not in dashboards:
        text, count = re.subn(r"(?m)^(  dashboards:\s*)$", "\\1\n    jarvis-vackning:\n      mode: yaml\n      title: Jarvis väckning\n      icon: mdi:alarm\n      show_in_sidebar: true\n      filename: jarvis_wakeup_dashboard.yaml", text)
        if count != 1:
            raise ValueError("Cannot locate unique dashboards block")
    if yaml.load(text, Loader=yaml.BaseLoader) != expected:
        raise ValueError("Configuration comparison failed; unrelated settings would change")
    return text


def main():
    root = Path(sys.argv[1])
    for name in ("jarvis_wakeup.yaml", "jarvis_wakeup_dashboard.yaml"):
        yaml.load((root / name).read_text(), Loader=yaml.BaseLoader)
    target = root / "configuration.yaml"
    before = target.read_text()
    after = configure(before)
    if after != before:
        backup = target.with_name("configuration.yaml.before-jarvis-wakeup-" + datetime.now().strftime("%Y%m%d-%H%M%S"))
        backup.write_text(before)
        backup.chmod(0o600)
        target.write_text(after)
    print("Wakeup package and dashboard configured; unrelated YAML verified unchanged")


if __name__ == "__main__":
    main()
