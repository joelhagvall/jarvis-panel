"""Compile the actual contiguous RGB565 hook and verify pixels, bounds and native fallback."""
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class PixelCopyTest(unittest.TestCase):
    def test_actual_hook_preserves_unaligned_pixels_and_rejects_strided_regions(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            files = {
                'lvgl.h': '''#pragma once
#include <cstdint>
enum lv_result_t {LV_RESULT_OK,LV_RESULT_INVALID};
typedef uint8_t lv_opa_t;
struct lv_color_t {uint8_t blue,green,red;};
struct lv_area_t {int32_t x1,y1,x2,y2;};
inline uint16_t lv_color_to_u16(lv_color_t c){return uint16_t(((c.red&0xF8)<<8)|((c.green&0xFC)<<3)|(c.blue>>3));}
''',
                'src/draw/sw/blend/lv_draw_sw_blend_private.h': '''#pragma once
struct lv_draw_sw_blend_image_dsc_t {void *dest_buf; const void *src_buf; int dest_w,dest_h,dest_stride,src_stride;};
struct lv_draw_sw_blend_fill_dsc_t {void *dest_buf; int32_t dest_w,dest_h,dest_stride; const lv_opa_t *mask_buf;
 int32_t mask_stride; lv_color_t color; lv_opa_t opa; lv_area_t relative_area;};
''',
                'esp_heap_caps.h': '''#pragma once
#include <cstdlib>
#define MALLOC_CAP_INTERNAL 1
#define MALLOC_CAP_8BIT 2
inline void *heap_caps_aligned_alloc(size_t a,size_t n,unsigned){void*p=nullptr; return posix_memalign(&p,a,n)?nullptr:p;}
''',
                'esphome/core/log.h': '#pragma once\n#define ESP_LOGW(...) ((void)0)\n',
                'esphome/core/defines.h': '#pragma once\n',
                'test.cpp': r'''
#include "pixel_copy.h"
#include <cassert>
#include <vector>
#include <cstring>
int main(){
 constexpr int bytes=1024*32*2;
 std::vector<unsigned char> source(bytes+128),dest(bytes+128);
 for(int i=0;i<int(source.size());++i)source[i]=unsigned(i*71+3);
 lv_draw_sw_blend_image_dsc_t d{dest.data(),source.data(),1024,32,2048,2048};
 assert(panel_pixel_copy_rgb565(&d)==LV_RESULT_INVALID);
 panel_pixel_copy_install();
 for(int a=0;a<64;++a)for(int b=0;b<64;++b){
  std::memset(dest.data(),0xA5,dest.size());
  d.dest_buf=dest.data()+a;d.src_buf=source.data()+b;
  assert(panel_pixel_copy_rgb565(&d)==LV_RESULT_OK);
  assert(!std::memcmp(dest.data()+a,source.data()+b,bytes));
  for(int i=0;i<a;++i)assert(dest[i]==0xA5);
  for(size_t i=a+bytes;i<dest.size();++i)assert(dest[i]==0xA5);
 }
 for(int stride:{2049,4096}){d.src_stride=stride;assert(panel_pixel_copy_rgb565(&d)==LV_RESULT_INVALID);}
 d.src_stride=2048;d.dest_stride=4096;assert(panel_pixel_copy_rgb565(&d)==LV_RESULT_INVALID);
 d.dest_stride=2048;d.dest_w=220;assert(panel_pixel_copy_rgb565(&d)==LV_RESULT_INVALID);
 // The colour hooks (color_blend.h, bit-identity in test_color_blend.py) blend inside the area only.
 std::vector<uint16_t> px(5*4,0x1234); std::vector<uint8_t> mask(3*2,255);
 lv_draw_sw_blend_fill_dsc_t f{px.data()+6,3,2,10,nullptr,0,{0,0,255},128,{0,0,2,1}};
 assert(panel_color_opa_rgb565(&f)==LV_RESULT_OK);
 assert(px[5]==0x1234&&px[6]!=0x1234&&px[8]!=0x1234&&px[9]==0x1234&&px[11]!=0x1234&&px[14]==0x1234);
 f.mask_buf=mask.data();f.mask_stride=3;
 assert(panel_color_mask_rgb565(&f)==LV_RESULT_OK&&px[6]==0xF800&&px[13]==0xF800&&px[14]==0x1234);
 f.mask_buf=nullptr;assert(panel_color_mask_rgb565(&f)==LV_RESULT_INVALID&&panel_color_mask_opa_rgb565(&f)==LV_RESULT_INVALID);
}
'''}
            for name, text in files.items():
                path = folder / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(text)
            component = ROOT / 'components/panel_debug'
            binary = folder / 'copy'
            subprocess.run(['c++', '-std=c++17', '-O1', '-Wall', '-Wextra', '-Werror',
                            '-fsanitize=address,undefined', '-I'+str(folder), '-I'+str(component),
                            str(component / 'pixel_copy.cpp'), str(folder / 'test.cpp'), '-o', str(binary)],
                           capture_output=True, check=True)
            subprocess.run([str(binary)], capture_output=True, check=True, timeout=20)


if __name__ == '__main__':
    unittest.main()
