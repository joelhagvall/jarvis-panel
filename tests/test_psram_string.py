"""components/panel_debug/psram_string.h: the debug server's large replies are ordinary strings kept in PSRAM.

On the panel a failed internal-RAM new aborted the firmware while it built the /ui tree (2026-10-28). This checks
the allocator off the board: appends, moves, a std::string_view of it and std::string pieces work, under ASan."""
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
PROGRAM = r'''
#include <cassert>
#include <string>
#include <string_view>
#include "psram_string.h"
static panel_debug_memory::String tree() {
  panel_debug_memory::String out;
  out.reserve(64 * 1024);
  out += "{\"widgets\":[";
  for (int i = 0; i < 2000; ++i) { if (i) out += ','; out += std::string("{\"id\":\"screen/") + std::to_string(i) + "\"}"; }
  out += "]}";
  return out;
}
int main() {
  panel_debug_memory::String result = tree();
  std::string_view body = result;
  assert(body.size() > 30000 && body.front() == '{' && body.back() == '}');
  const std::string status = "{\"ok\":true}";
  result.assign(status.data(), status.size());
  result.insert(result.size() - 1, std::string(",\"x\":1").data(), 6);
  assert(std::string_view(result) == "{\"ok\":true,\"x\":1}");
  panel_debug_memory::String moved = std::move(result);
  assert(moved.size() == 17);
  return 0;
}
'''


@unittest.skipUnless(shutil.which("clang++") or shutil.which("g++"), "C++ compiler unavailable")
class PsramStringTest(unittest.TestCase):
    def test_reply_strings_behave_like_std_string(self):
        with tempfile.TemporaryDirectory() as tmp:
            source, binary = Path(tmp) / "p.cpp", Path(tmp) / "p"
            source.write_text(PROGRAM)
            subprocess.run([shutil.which("clang++") or shutil.which("g++"), "-std=c++17", "-O1", "-Wall", "-Werror",
                            "-fsanitize=address,undefined", "-I", str(ROOT / "components/panel_debug"),
                            str(source), "-o", str(binary)], check=True, capture_output=True)
            subprocess.run([str(binary)], check=True)

    def test_the_ui_tree_and_replies_use_it(self):
        source = (ROOT / "components/panel_debug/panel_debug.cpp").read_text()
        self.assertIn("panel_debug_memory::String PanelDebug::ui_() const", source)
        self.assertIn("panel_debug_memory::String wire;", source)
        self.assertNotIn('return out + "]', source)       # the copy that needed a second tree-sized block
        header = (ROOT / "components/panel_debug/panel_debug.h").read_text()
        self.assertIn("panel_debug_memory::String result;", header)


if __name__ == "__main__":
    unittest.main()
