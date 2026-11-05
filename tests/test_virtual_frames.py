"""Virtual timelines preserve loop/one-shot endpoints and RGB565/A8 channel precision."""
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class VirtualFramesTest(unittest.TestCase):
    def test_recipe_endpoints_cycles_and_exact_channel_interpolation(self):
        code = r'''
#include "virtual_frames.h"
#include <array>
#include <cassert>
#include <vector>
int main(){
 std::array<packed_orb::Recipe,10> r{};
 packed_orb::virtual_sequence(r.data(),0,4,0,2,true);
 assert(r[0].first==0 && r[0].weight==0 && r[0].next==1);
 assert(r[1].first==0 && r[1].second==1 && r[1].weight==128);
 assert(r[2].first==1 && r[2].weight==0);
 assert(r[3].first==1 && r[3].second==0 && r[3].weight==128 && r[3].next==0);
 packed_orb::virtual_sequence(r.data(),4,3,7,2,false);
 assert(r[4].first==7 && r[4].weight==0);
 assert(r[5].first==7 && r[5].second==8 && r[5].weight==128);
 assert(r[6].first==8 && r[6].second==8 && r[6].weight==0 && r[6].next==packed_orb::NO_FRAME);
 // RGB565 red -> blue; independent A8 values 0 -> 255.
 const std::array<uint8_t,3> a{0x00,0xf8,0},b{0x1f,0x00,255};
 std::array<uint8_t,3> out{};
 packed_orb::interpolate565a8(out.data(),a.data(),b.data(),1,0);assert(out==a);
 packed_orb::interpolate565a8(out.data(),a.data(),b.data(),1,256);assert(out==b);
 packed_orb::interpolate565a8(out.data(),a.data(),b.data(),1,128);
 assert(out[0]==0x10 && out[1]==0x80 && out[2]==128);
 std::vector<uint8_t> x(9003),y(9003),reference(9003),linear(9003),staged(9003),sparse(9003);
 std::array<packed_orb::ChangedWord,2250> changes{};
 std::array<uint8_t,packed_orb::INTERPOLATION_SCRATCH> scratch{};
 for(size_t i=0;i<x.size();++i){x[i]=uint8_t(i*71+3);y[i]=i%16<8?x[i]:uint8_t(i*19+99);}
 for(unsigned pixels:{1,2,3,7,1025,2049,3000})for(unsigned offset=0;offset<4;++offset)
 for(unsigned weight=0;weight<=256;++weight){
  std::fill(reference.begin(),reference.end(),0xa5);linear=reference;staged=reference;sparse=reference;
  packed_orb::interpolate565a8_original(reference.data()+offset,x.data()+offset,y.data()+offset,pixels,weight);
  packed_orb::interpolate565a8(linear.data()+offset,x.data()+offset,y.data()+offset,pixels,weight);
  packed_orb::interpolate565a8_staged(staged.data()+offset,x.data()+offset,y.data()+offset,pixels,weight,scratch.data());
  packed_orb::SparseMix mix{changes.data(),changes.size()};
  mix.blend(sparse.data()+offset,x.data()+offset,y.data()+offset,pixels,weight,0,1);
  assert(reference==linear && reference==staged && reference==sparse);
  // Reuse a pair, invalidate its identity/size and fall back exactly on overflow.
  for(size_t capacity:{changes.size(),size_t(1)}) {
   mix.capacity=capacity;mix.valid=false;
   mix.blend(sparse.data()+offset,x.data()+offset,y.data()+offset,pixels,weight,2,3);
   assert(reference==sparse);
   mix.blend(sparse.data()+offset,x.data()+offset,y.data()+offset,pixels,weight,2,3);
   assert(reference==sparse);
  }
 }
}
'''
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            (folder / 'test.cpp').write_text(code)
            binary = folder / 'test'
            subprocess.run(['c++', '-std=c++17', '-Wall', '-Wextra', '-Werror', '-fsanitize=address,undefined',
                            '-I'+str(ROOT / 'styles/glass-v2'), str(folder / 'test.cpp'), '-o', str(binary)],
                           check=True, capture_output=True)
            subprocess.run([str(binary)], check=True, capture_output=True, timeout=10)


if __name__ == '__main__':
    unittest.main()
