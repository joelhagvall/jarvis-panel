"""Partial writes and interrupted syscalls must preserve a coalesced HTTP response exactly."""
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class WireReplyTest(unittest.TestCase):
    def test_exact_response_after_short_writes_eintr_and_disconnect(self):
        code = r'''
#include "wire_reply.h"
#include <cassert>
#include <algorithm>
#include <string>
int main(){
 const std::string wire="HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: 11\r\n\r\n{\"ok\":true}";
 std::string received;
 int calls=0;
 assert(panel_debug_wire::send_all(wire,[&](const char*p,size_t n)->int{
  if(calls++==0){errno=EINTR;return -1;}
  n=std::min(n,size_t(7));received.append(p,n);return int(n);
 }));
 assert(received==wire);
 for(int failure:{-1,0}){
  unsigned attempts=0;
  assert(!panel_debug_wire::send_all(wire,[&](const char*,size_t){++attempts;errno=EPIPE;return failure;}));
  assert(attempts==1);
 }
}
'''
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            (folder / 'test.cpp').write_text(code)
            binary = folder / 'test'
            subprocess.run(['c++', '-std=c++17', '-Wall', '-Wextra', '-Werror', '-fsanitize=address,undefined',
                            '-I'+str(ROOT / 'components/panel_debug'), str(folder / 'test.cpp'), '-o', str(binary)],
                           check=True, capture_output=True)
            subprocess.run([str(binary)], check=True, capture_output=True, timeout=10)


if __name__ == '__main__':
    unittest.main()
