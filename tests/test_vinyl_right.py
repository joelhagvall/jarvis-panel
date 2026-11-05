"""Opaque outside-arm buffer ownership, clipping, background, stacking and offscreen publication."""
from pathlib import Path
import subprocess
import tempfile
import unittest
ROOT=Path(__file__).resolve().parents[1]
class RightArmTest(unittest.TestCase):
    def test_pixels_and_independent_output_ownership(self):
        source=r'''
#include "styles/glass-v2/vinyl_right.h"
#include <cassert>
#include <vector>
using namespace panel_vinyl;
int main(){
 uint8_t bgra[12]={0,0,0,255,200,100,50,128,255,255,255,0},alpha_out[3]{};
 alpha_from_argb(bgra,alpha_out,3);assert(alpha_out[0]==255&&alpha_out[1]==128&&alpha_out[2]==0);
 uint8_t empty_pivot[9]{};assert(!valid_pivot(empty_pivot,3,3));empty_pivot[4]=255;assert(valid_pivot(empty_pivot,3,3));
 std::vector<uint8_t> planar(407*43*3,255);
 assert(solid_top_region(planar.data(),407*43*2,407)); // Real ESPHome descriptor excludes the alpha plane.
 assert(!solid_top_region(planar.data(),407*43*2-1,407));
 planar[407*43*2]=0;assert(solid_top_region(planar.data(),407*43*2,407));
 planar[407*43*2+33*407+331]=0;assert(!solid_top_region(planar.data(),407*43*2,407));
 RightArm r;std::vector<uint16_t> top(409*43),side(78*288),a(r.W*r.H+2,0xBEEF),b(a),base(48*48),rest(18*18);
 std::vector<uint8_t> pivot(26*26,255);
 for(size_t i=0;i<top.size();++i)top[i]=uint16_t(i*37);for(size_t i=0;i<side.size();++i)side[i]=uint16_t(i*71);
 for(size_t i=0;i<base.size();++i)base[i]=uint16_t(i+100);for(auto &p:rest)p=0x1234;
 r.top_rgb=top.data();r.top_stride=409;r.right_rgb=side.data();r.right_stride=78;
 r.pixels[0]=a.data()+1;r.pixels[1]=b.data()+1;r.base=base.data();r.rest=rest.data();r.pivot=pivot.data();
 r.base_rect={367,117,414,164};r.rest_rect={382,241,399,258};r.pivot_rect={378,128,403,153};r.pivot_color=0x10A2;
 pivot[0]=0;pivot[1]=128;
 std::array<LineMask,6> masks{};uint8_t alpha[2]={255,255};masks[2].pixels=alpha;masks[2].x=378;
 masks[2].y=128;masks[2].w=2;masks[2].h=1;
 RightArm::Pose first{},second{};second[0]=1;
 r.request(first);assert(r.wanted&&r.target==0);r.render(masks);assert(r.ready==0);
 auto at=[&](int x,int y){return a[1+(y-r.Y)*r.W+x-r.X];};
 assert(at(360,100)==top[33*409+331]);assert(at(365,200)==side[90*78+5]);
 assert(at(367,117)==base[0]&&at(382,241)==rest[0]);assert(at(391,141)==r.pivot_color);
 assert(at(378,128)==ARM_COLORS[2]); // A transparent pivot corner must retain the arm, not restore wood.
 uint16_t expected=0;
 for(unsigned shift:{0U,5U,11U}){const unsigned bits=shift==5?63:31;
  expected|=uint16_t(((((r.pivot_color>>shift)&bits)+((ARM_COLORS[2]>>shift)&bits))/2)<<shift);}
 assert(at(379,128)==expected);
 r.consume(true);assert(r.displayed==0&&r.shown_pose==first);const auto held=a;
 r.request(second);assert(r.wanted&&r.target==1);r.render(masks);r.consume(false);
 assert(r.displayed==0&&r.shown_pose==first&&a==held);
 r.request(second);assert(r.wanted&&r.target==1);r.render(masks);r.consume(true);assert(r.displayed==1);
 const auto unchanged=b;r.request(second);assert(!r.wanted);r.render(masks);r.consume(true);
 assert(r.displayed==1&&b==unchanged&&a.front()==0xBEEF&&a.back()==0xBEEF&&b.front()==0xBEEF&&b.back()==0xBEEF);
}
'''
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp);(p/'test.cpp').write_text(source)
            subprocess.run(['c++','-std=c++17','-O1','-fsanitize=address,undefined','-I',str(ROOT),
                            str(p/'test.cpp'),'-o',str(p/'test')],check=True)
            subprocess.run([str(p/'test')],check=True)
