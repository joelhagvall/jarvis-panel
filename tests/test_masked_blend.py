"""Transparent image pixels preserve the destination and all row padding exactly."""
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class MaskedBlendTest(unittest.TestCase):
    def test_all_alpha_values_strides_quantization_and_guard_pixels(self):
        code = r'''
#include "masked_blend.h"
#include <cassert>
#include <vector>
uint16_t reference(uint16_t foreground,uint16_t background,uint8_t alpha){
 const unsigned level=(unsigned(alpha)+4)/8;
 unsigned value=0;
 for(unsigned shift:{0U,5U,11U}){
  const unsigned bits=shift==5?63:31;
  value|=(((((foreground>>shift)&bits)*level+((background>>shift)&bits)*(32-level))/32)<<shift);
 }
 return uint16_t(value);
}
int main(){
 constexpr unsigned w=17,h=11,ds=22,ss=24,ms=23;
 std::vector<uint16_t> source(ss*h+8),dest(ds*h+8),expected;
 std::vector<uint8_t> mask(ms*h+8);
 for(size_t i=0;i<source.size();++i)source[i]=uint16_t(i*71+3);
 for(unsigned alpha=0;alpha<256;++alpha){
  for(size_t i=0;i<dest.size();++i)dest[i]=uint16_t(i*19+99);
  expected=dest;
  for(size_t i=0;i<mask.size();++i)mask[i]=uint8_t(alpha+i*31);
  unsigned skipped=0,opaque=0,calls=0;
  for(unsigned y=0;y<h;++y)for(unsigned x=0;x<w;++x){
   const auto amount=mask[1+y*ms+x];skipped+=amount<=3;opaque+=amount>=252;
   auto &pixel=expected[3+y*ds+x];pixel=reference(source[1+y*ss+x],pixel,amount);
  }
  auto result=panel_debug_copy::masked_rows<false>(reinterpret_cast<uint8_t*>(dest.data()+3),ds*2,
      reinterpret_cast<const uint8_t*>(source.data()+1),ss*2,0,mask.data()+1,ms,w,h,
      [&](uint16_t f,uint16_t b,uint8_t a){++calls;return reference(f,b,a);});
  assert(dest==expected && result.pixels==w*h && result.skipped==skipped && result.opaque==opaque);
  assert(calls==w*h-skipped-opaque);
 }
}
'''
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            (folder/'test.cpp').write_text(code)
            binary = folder/'test'
            subprocess.run(['c++', '-std=c++17', '-O1', '-Wall', '-Wextra', '-Werror', '-fsanitize=address,undefined',
                            '-I'+str(ROOT/'components/panel_debug'), str(folder/'test.cpp'), '-o', str(binary)],
                           check=True, capture_output=True)
            subprocess.run([str(binary)], check=True, capture_output=True, timeout=10)


if __name__ == '__main__':
    unittest.main()
