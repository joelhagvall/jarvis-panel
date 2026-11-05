#!/usr/bin/env python3
"""Optional native LVGL pixel/worker experiment; no device access, model calls or simulator rebuild."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import tempfile
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'orbs'))
import atlas

STUBS = {
'esphome/core/hal.h': '''#pragma once
#include <chrono>
namespace esphome { inline uint32_t micros() { return uint32_t(std::chrono::duration_cast<std::chrono::microseconds>(std::chrono::steady_clock::now().time_since_epoch()).count()); } }
''',
'esphome/core/log.h': '#pragma once\n#define ESP_LOGI(...) ((void)0)\n#define ESP_LOGW(...) ((void)0)\n',
'esp_heap_caps.h': '''#pragma once
#include <cstdlib>
#include <vector>
#include <algorithm>
#define MALLOC_CAP_SPIRAM 1
#define MALLOC_CAP_8BIT 2
#define MALLOC_CAP_INTERNAL 4
inline std::vector<void*> fake_allocations;
inline void *heap_caps_aligned_alloc(size_t alignment,size_t size,unsigned) { void *p=nullptr; if(posix_memalign(&p,alignment,size)) return nullptr; fake_allocations.push_back(p); return p; }
inline void *heap_caps_calloc(size_t n,size_t size,unsigned) { void*p=std::calloc(n,size);if(p)fake_allocations.push_back(p);return p; }
inline void heap_caps_free(void *p) { if(!p)return; auto it=std::find(fake_allocations.begin(),fake_allocations.end(),p); if(it!=fake_allocations.end())fake_allocations.erase(it); std::free(p); }
inline void fake_heap_free() { for(auto *p:fake_allocations) std::free(p); fake_allocations.clear(); }
''',
'freertos/FreeRTOS.h': '''#pragma once
#include <atomic>
#include <condition_variable>
#include <mutex>
#include <thread>
#include <vector>
#define pdPASS 1
#define pdTRUE 1
#define portMAX_DELAY 0xffffffffU
struct FakeStop {};
struct FakeTask { std::mutex mutex; std::condition_variable ready; unsigned count=0; std::thread thread; };
using TaskHandle_t=FakeTask*;
inline thread_local FakeTask *fake_current=nullptr;
inline std::atomic<bool> fake_stopping{false};
inline std::vector<FakeTask*> fake_tasks;
inline int xTaskCreatePinnedToCore(void (*fn)(void*),const char*,unsigned,void *arg,unsigned,TaskHandle_t *out,int) {
 auto *task=new FakeTask; *out=task; fake_tasks.push_back(task);
 task->thread=std::thread([task,fn,arg](){fake_current=task; try { fn(arg); } catch(const FakeStop&) {} }); return pdPASS;
}
inline void xTaskNotifyGive(TaskHandle_t task) { { std::lock_guard<std::mutex> lock(task->mutex); ++task->count; } task->ready.notify_one(); }
inline unsigned ulTaskNotifyTake(int,unsigned) { auto *task=fake_current; std::unique_lock<std::mutex> lock(task->mutex); task->ready.wait(lock,[task](){return task->count || fake_stopping.load();}); if(fake_stopping.load()) throw FakeStop{}; auto result=task->count;task->count=0;return result; }
inline void fake_rtos_stop() { fake_stopping.store(true); for(auto *task:fake_tasks)task->ready.notify_all(); for(auto *task:fake_tasks){ task->thread.join();delete task; } fake_tasks.clear(); }
''',
'freertos/task.h': '#pragma once\n#include "FreeRTOS.h"\n',
}


def run():
    build = ROOT / 'styles/glass-v2/.esphome/build/style-glass-v2'
    libraries = list(build.glob('.pioenvs/*/lib*/liblvgl.a'))
    headers = list(build.glob('.piolibdeps/*/lvgl/lvgl.h'))
    if len(libraries) != 1 or len(headers) != 1:
        raise RuntimeError('Build the native glass-v2 simulator first')
    lvgl = headers[0].parent
    with tempfile.TemporaryDirectory(prefix='hallpanel-prefetch-') as tmp:
        folder = Path(tmp)
        for name, text in STUBS.items():
            file = folder / name
            file.parent.mkdir(parents=True, exist_ok=True)
            file.write_text(text)
        config = (build / 'src/lv_conf.h').read_text().replace('#define LV_USE_LZ4 0', '#define LV_USE_LZ4 1')
        config += '\n#undef LV_USE_LZ4_INTERNAL\n#define LV_USE_LZ4_INTERNAL 1\n#undef LV_BIN_DECODER_RAM_LOAD\n#define LV_BIN_DECODER_RAM_LOAD 1\n'
        (folder / 'lv_conf.h').write_text(config)
        flags = ['-O1', '-g', '-fsanitize=address,undefined', '-DLV_CONF_INCLUDE_SIMPLE', '-I'+str(folder), '-I'+str(lvgl)]
        objects = []
        for name in ('libs/bin_decoder/lv_bin_decoder.c', 'libs/lz4/lz4.c'):
            obj = folder / (Path(name).stem + '.o')
            subprocess.run(['cc', *flags, '-c', str(lvgl / 'src' / name), '-o', str(obj)], check=True, capture_output=True)
            objects.append(obj)
        binary = folder / 'prefetch'
        subprocess.run(['c++', '-std=c++17', '-pthread', '-DUSE_ESP32', *flags,
                        '-I'+str(ROOT / 'styles/glass-v2'), '-I'+str(ROOT / 'components/panel_debug'), '-I'+str(ROOT / 'bench'),
                        str(ROOT / 'tests/native/packed_prefetch.cpp'),
                        *map(str, objects), str(libraries[0]), '-o', str(binary)], check=True, capture_output=True)
        pack = atlas.build_packed(folder / 'orb.lz4', version=atlas.PANEL_VERSION)
        result = subprocess.run([str(binary), str(pack)], check=True, capture_output=True, text=True, timeout=30)
        data = json.loads(result.stdout.splitlines()[-1])
    data.update(ok=data['orb_prefetch_hits'] > 100 and data['orb_prefetch_errors'] == 0,
                actual_native_lvgl=True, physical_hardware=False,
                sources={str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest() for p in (
                    ROOT / 'styles/glass-v2/packed_prefetch.h', ROOT / 'styles/glass-v2/prefetch_slots.h',
                    ROOT / 'styles/glass-v2/virtual_frames.h', ROOT / 'styles/glass-v2/orb_director.h', ROOT / 'styles/glass-v2/voice_wave.h', ROOT / 'styles/glass-v2/packed_orb.h', ROOT / 'bench/vsync_clock.h',
                    ROOT / 'components/panel_debug/masked_blend.h',
                    ROOT / 'tests/native/packed_prefetch.cpp', Path(__file__).resolve())})
    return data


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    try:
        result = run()
    except subprocess.CalledProcessError as error:
        print((error.stderr or b'').decode() if isinstance(error.stderr, bytes) else error.stderr, file=sys.stderr)
        raise
    args.out.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
    args.out.write_text(json.dumps(result, indent=2)+'\n')
    args.out.chmod(0o600)
    print(json.dumps(result, indent=2))
    raise SystemExit(0 if result['ok'] else 1)
