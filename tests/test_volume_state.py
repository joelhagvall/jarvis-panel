"""Native test of the exact deferred volume policy used by the board."""
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(shutil.which("clang++") or shutil.which("g++"), "C++ compiler unavailable")
class VolumeStateTest(unittest.TestCase):
    def test_night_caps_and_deferred_changes_do_not_overwrite_manual_preference(self):
        with tempfile.TemporaryDirectory(prefix="hallpanel-volume-") as tmp:
            source, binary = Path(tmp) / "volume.cpp", Path(tmp) / "volume"
            source.write_text(r'''
#include <cassert>
#include <limits>
#include "volume_state.h"
int main() {
  PanelVolumeState state;
  float saved = 0.2f;
  state.observed(0.8f, saved);  // media player's old restore callback during setup
  assert(saved == 0.2f);
  state.ready = true;
  state.automatic(0.2f);
  state.observed(0.2f, saved);
  state.observed(0.8f, saved);  // explicit manual increase
  assert(saved == 0.8f);
  state.automatic(0.35f);
  state.automatic(0.8f);        // two programmatic changes, callbacks still queued
  state.observed(0.35f, saved);
  assert(saved == 0.8f);
  state.observed(0.8f, saved);
  state.observed(0.2f, saved);  // Joel's reduction must survive voice/night/day
  assert(saved == 0.2f);
  state.automatic(0.2f);
  state.observed(0.2f, saved);
  assert(saved == 0.2f);
  state.observed(std::numeric_limits<float>::quiet_NaN(), saved);
  state.observed(2.0f, saved);
  assert(saved == 0.2f);
}
''')
            subprocess.run([shutil.which("clang++") or shutil.which("g++"), "-std=c++17", "-O2",
                            "-I", str(ROOT / "bench"), str(source), "-o", str(binary)],
                           check=True, capture_output=True)
            subprocess.run([str(binary)], check=True, capture_output=True)
