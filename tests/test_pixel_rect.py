"""A hardware rectangle may touch only valid equal-size source/destination regions."""
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class PixelRectangleTest(unittest.TestCase):
    def test_clipping_stride_bounds_and_overflow(self):
        code = r'''
#include "pixel_rect.h"
#include <cassert>
#include <cstring>
#include <vector>
#include <limits>
using namespace panel_debug_copy;
int main(){
 constexpr int dw=320,dh=240,sw=512,sh=256;
 const Rect from{40,10,220,200},to{70,20,220,200};
 assert(valid_rect_copy(dw,dh,dw*dh*2,to,sw,sh,from));
 std::vector<uint16_t> source(sw*sh),dest(dw*dh,0xa5a5);
 for(size_t i=0;i<source.size();++i)source[i]=uint16_t(i*71+3);
 for(int y=0;y<to.height;++y)std::memcpy(dest.data()+(to.y+y)*dw+to.x,
     source.data()+(from.y+y)*sw+from.x,to.width*2);
 for(int y=0;y<dh;++y)for(int x=0;x<dw;++x){
  const bool inside=x>=to.x && x<to.x+to.width && y>=to.y && y<to.y+to.height;
  assert(dest[y*dw+x]==(inside?source[(from.y+y-to.y)*sw+from.x+x-to.x]:0xa5a5));
 }
 for(const Rect bad: {Rect{-1,0,220,200},Rect{0,-1,220,200},Rect{101,0,220,200},
                     Rect{0,41,220,200},Rect{0,0,0,200},Rect{0,0,220,-1},
                     Rect{std::numeric_limits<int32_t>::max(),0,220,200}})
  assert(!valid_rect_copy(dw,dh,dw*dh*2,bad,sw,sh,from));
 assert(!valid_rect_copy(dw,dh,dw*dh*2-1,to,sw,sh,from));
 assert(!valid_rect_copy(dw,dh,dw*dh*2,to,sw,sh,{40,10,219,200}));
 assert(!valid_rect_copy(dw,dh,dw*dh*2,to,sw,sh,{400,10,220,200}));
}
'''
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            (folder/'test.cpp').write_text(code)
            binary = folder/'test'
            subprocess.run(['c++', '-std=c++17', '-Wall', '-Wextra', '-Werror', '-fsanitize=address,undefined',
                            '-I'+str(ROOT/'components/panel_debug'), str(folder/'test.cpp'), '-o', str(binary)],
                           check=True, capture_output=True)
            subprocess.run([str(binary)], check=True, capture_output=True)


if __name__ == '__main__':
    unittest.main()
