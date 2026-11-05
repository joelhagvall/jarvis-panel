"""Decode the exact compiled boot mark with real LVGL, preserving pixels and heap."""
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
import pack_boot_logo


class BootLogoTest(unittest.TestCase):
    def test_native_decoder_preserves_pixels_releases_working_memory_and_reduces_resident_bytes(self):
        self.assertEqual(pack_boot_logo.HEADER.read_text(), pack_boot_logo.generate())
        build = ROOT / 'styles/glass-v2/.esphome/build/style-glass-v2'
        libraries = list(build.glob('.pioenvs/*/lib*/liblvgl.a'))
        headers = list(build.glob('.piolibdeps/*/lvgl/lvgl.h'))
        self.assertEqual((len(libraries), len(headers)), (1, 1), 'Native LVGL simulator dependency required')
        source = r'''
#include "boot_logo.h"
#include "src/draw/lv_image_decoder_private.h"
#include <cassert>
#include <cstdlib>
#include <cstring>
#include <iostream>
#include <iterator>
#include <unordered_map>
#include <vector>
static std::unordered_map<void *, size_t> allocations;
static size_t retained() { size_t n=0; for(auto p: allocations) n+=p.second; return n; }
extern "C" {
void lv_mem_init() {}
void lv_mem_deinit() {}
void *lv_malloc_core(size_t n) { auto p=std::malloc(n); if(p) allocations[p]=n; return p; }
void lv_free_core(void *p) { allocations.erase(p); std::free(p); }
void *lv_realloc_core(void *p,size_t n) {
  auto next=std::realloc(p,n);
  if(next) { allocations.erase(p); allocations[next]=n; }
  else if(!n) allocations.erase(p);
  return next;
}
lv_result_t lv_mem_test_core() { return LV_RESULT_OK; }
void lv_mem_monitor_core(lv_mem_monitor_t *p) { std::memset(p,0,sizeof(*p)); }
}
int main() {
  std::vector<uint8_t> expected((std::istreambuf_iterator<char>(std::cin)),{});
  assert(expected.size()==320*320*2);
  assert(sizeof(boot_brand::data)<expected.size()-160000);
  lv_init(); lv_image_cache_resize(0,true);
  size_t warm=0;
  for(unsigned i=0;i<101;++i) {
    lv_image_decoder_dsc_t d{};
    assert(lv_image_decoder_open(&d,boot_brand::image(),nullptr)==LV_RESULT_OK);
    assert(d.decoded->header.w==320 && d.decoded->header.h==320);
    assert(d.decoded->data_size==expected.size());
    assert(std::memcmp(d.decoded->data,expected.data(),expected.size())==0);
    lv_image_decoder_close(&d);
    if(!i) warm=retained(); else assert(retained()==warm);
  }
  lv_deinit();
  assert(allocations.empty());
}
'''
        with tempfile.TemporaryDirectory(prefix='hallpanel-boot-logo-') as tmp:
            cpp, binary = Path(tmp)/'test.cpp', Path(tmp)/'test'
            cpp.write_text(source)
            config = (build/'src/lv_conf.h').read_text().replace('#define LV_USE_LZ4 0', '#define LV_USE_LZ4 1')
            config += ('\n#undef LV_USE_LZ4_INTERNAL\n#define LV_USE_LZ4_INTERNAL 1\n'
                       '#undef LV_BIN_DECODER_RAM_LOAD\n#define LV_BIN_DECODER_RAM_LOAD 1\n')
            (Path(tmp)/'lv_conf.h').write_text(config)
            flags = ['-O1', '-g', '-fsanitize=address,undefined', '-DLV_CONF_INCLUDE_SIMPLE',
                     '-I'+tmp, '-I'+str(headers[0].parent)]
            objects = []
            for name in ('libs/bin_decoder/lv_bin_decoder.c', 'libs/lz4/lz4.c'):
                obj = Path(tmp)/(Path(name).stem+'.o')
                subprocess.run(['cc', *flags, '-c', str(headers[0].parent/'src'/name), '-o', str(obj)],
                               check=True, capture_output=True)
                objects.append(str(obj))
            compiler = shutil.which('clang++') or shutil.which('g++')
            self.assertIsNotNone(compiler, 'Native C++ compiler required')
            result = subprocess.run([compiler, '-std=c++17', *flags,
                            '-I'+str(ROOT/'styles/glass-v2'), str(cpp), *objects, str(libraries[0]), '-o', str(binary)],
                           capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            result = subprocess.run([str(binary)], input=pack_boot_logo.pixels(), capture_output=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stderr.decode(errors='replace'))
