"""The production SSH wrapper must work identically for shell commands and rsync; no network."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]


class SSHTest(unittest.TestCase):
    def test_tool_scripts_reach_jarvis_only_through_the_wrapper(self):
        # Plain ssh fails from the Mac with "Permission denied (publickey)".
        import re
        for script in sorted((ROOT / "tools").glob("*.sh")):
            if script.name == "jarvis_ssh.sh":
                continue
            for number, line in enumerate(script.read_text().splitlines(), 1):
                code = line.split("#", 1)[0]
                self.assertIsNone(re.search(r'(^|[\s;&|(`])ssh\s', code), f"{script.name}:{number} uses plain ssh")

    def test_a_command_in_the_host_place_names_the_usage(self):
        for args in ([], ["systemctl --user list-timers"]):
            result=subprocess.run(["sh",str(ROOT/"tools/jarvis_ssh.sh"),*args],capture_output=True,text=True)
            self.assertEqual(result.returncode,2)
            self.assertIn("usage:",result.stderr)

    def test_shell_and_rsync_keep_keychain_control_socket_and_argument_boundaries(self):
        with tempfile.TemporaryDirectory(prefix="ssh transport ") as tmp:
            folder=Path(tmp); log=folder/"args.json"; fake=folder/"ssh"
            fake.write_text("#!/usr/bin/env python3\nimport json,sys,os\nfrom pathlib import Path\nPath(os.environ['SSH_ARGUMENT_LOG']).write_text(json.dumps(sys.argv[1:]))\nsys.exit(int(os.environ.get('SSH_FAKE_EXIT','0')))\n")
            fake.chmod(0o755)
            env={**os.environ,"PATH":str(folder)+os.pathsep+os.environ['PATH'],"SSH_ARGUMENT_LOG":str(log)}
            wrapper=ROOT/"tools/jarvis_ssh.sh"
            subprocess.run([str(wrapper),"joel@example.test","printf '%s' 'a b'"],env=env,check=True)
            args=json.loads(log.read_text())
            self.assertEqual(args[-2:],["joel@example.test","printf '%s' 'a b'"])
            for option in ["BatchMode=yes","IgnoreUnknown=UseKeychain","UseKeychain=yes","ControlMaster=auto","ControlPersist=10m"]:
                self.assertIn(option,args)
            self.assertIn("ControlPath="+str(Path.home()/".ssh/cm-%C"),args)
            source=folder/"source";source.write_text("test")
            result=subprocess.run(["rsync","-an","-e",f"sh '{wrapper}'",str(source),"example.test:/tmp/destination"],
                env={**env,"SSH_FAKE_EXIT":"1"},capture_output=True)
            self.assertNotEqual(result.returncode,0)
            args=json.loads(log.read_text())
            self.assertIn("UseKeychain=yes",args)
            self.assertIn("ControlMaster=auto",args)
