"""Bounded DMA chunks: alignment, exact byte ranges and fail-stop after a partial transfer."""
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class DmaCopyTest(unittest.TestCase):
    def test_alignment_chunk_boundaries_and_partial_failure_preserve_copy_contract(self):
        code = r'''
#include "dma_copy.h"
#include <cassert>
#include <cstring>
using panel_debug_copy::CopyResult;
int main(){
 alignas(64) static unsigned char source[600000],dest[600000];
 for(unsigned i=0;i<600000;++i)source[i]=(i*71+3)&255;
 unsigned calls=0,bytes=0;
 auto copy=[&](unsigned char*out,const unsigned char*in,size_t n){
  assert(n<=256*1024 && !(n&15));++calls;bytes+=n;std::memcpy(out,in,n);return true;
 };
 assert(panel_debug_copy::dma_chunks(dest,source,sizeof(source),copy)==CopyResult::COMPLETE);
 assert(calls==3 && bytes==600000 && !std::memcmp(dest,source,sizeof(source)));
 const auto previous=calls;
 assert(panel_debug_copy::dma_chunks(dest+1,source,4096,copy)==CopyResult::UNSUPPORTED);
 assert(panel_debug_copy::dma_chunks(dest,source,4097,copy)==CopyResult::UNSUPPORTED);
 assert(panel_debug_copy::dma_chunks(dest,source,2048,copy)==CopyResult::UNSUPPORTED);
 assert(calls==previous);
 assert(panel_debug_copy::dma_chunks(dest,source+4,4096,copy,4)==CopyResult::COMPLETE);
 assert(!std::memcmp(dest,source+4,4096));
 assert(panel_debug_copy::dma_chunks(dest,source+1,4096,copy,4)==CopyResult::UNSUPPORTED);
 assert(panel_debug_copy::dma_chunks(dest,source+4,4096,copy,16)==CopyResult::UNSUPPORTED);
 assert(panel_debug_copy::dma_chunks(dest,source,4096,copy,3)==CopyResult::UNSUPPORTED);
 // A 128-byte write burst needs a 128-byte aligned destination and length; the source keeps its own alignment.
 alignas(128) static unsigned char wide[8192];
 assert(panel_debug_copy::dma_chunks(wide,source+4,8192,copy,4,128)==CopyResult::COMPLETE && !std::memcmp(wide,source+4,8192));
 assert(panel_debug_copy::dma_chunks(wide+64,source,4096,copy,4,128)==CopyResult::UNSUPPORTED);
 assert(panel_debug_copy::dma_chunks(wide,source,4096+64,copy,4,128)==CopyResult::UNSUPPORTED);
 assert(panel_debug_copy::dma_chunks(wide,source,4096,copy,4,96)==CopyResult::UNSUPPORTED);
 assert(panel_debug_copy::dma_chunks(wide,source,4096,copy,4,0)==CopyResult::UNSUPPORTED);
 assert(panel_debug_copy::dma_chunks(wide,source,4096,copy,4,512*1024)==CopyResult::UNSUPPORTED);
 std::memset(dest,0,sizeof(dest));calls=0;
 assert(panel_debug_copy::dma_chunks(dest,source,sizeof(source),[&](unsigned char*out,const unsigned char*in,size_t n){
  if(++calls==2)return false;std::memcpy(out,in,n);return true;
 })==CopyResult::FAILED);
 assert(calls==2 && !std::memcmp(dest,source,256*1024) && dest[256*1024]==0);
 std::memcpy(dest,source,sizeof(source));assert(!std::memcmp(dest,source,sizeof(source)));
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
