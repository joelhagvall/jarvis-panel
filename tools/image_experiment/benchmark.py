#!/usr/bin/env python3
"""Cold image preparation in separate processes; never publishes or changes live state.

The coordinator runs bake+frame, voice frosting and three RGB565 conversions in
the same order as a new uploaded background. Imports, PNG/QOI encoding and child
startup are included. Network/download, cache hits and publication are excluded.
Linux ru_maxrss is the largest child RSS, not a sum of sequential process peaks.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import resource
import subprocess
import sys
import time


def rust_sampler(library):
    import ctypes
    import numpy as np
    lib = ctypes.CDLL(str(Path(library).resolve()))
    call = lib.hallpanel_sample
    f32 = np.ctypeslib.ndpointer(dtype=np.float32, flags='C_CONTIGUOUS')
    f64 = np.ctypeslib.ndpointer(dtype=np.float64, flags='C_CONTIGUOUS')
    call.argtypes = [f32, ctypes.c_size_t, ctypes.c_size_t, f64, f64, ctypes.c_size_t, f64]
    call.restype = ctypes.c_int

    def sample(image, sx, sy):
        image = np.ascontiguousarray(image, dtype=np.float32)
        sx, sy = np.ascontiguousarray(sx, dtype=np.float64), np.ascontiguousarray(sy, dtype=np.float64)
        if image.ndim != 3 or image.shape[2] != 3 or sx.shape != sy.shape:
            raise ValueError('invalid sampling buffers')
        output = np.empty((*sx.shape, 3), dtype=np.float64)
        status = call(image, image.shape[1], image.shape[0], sx, sy, sx.size, output)
        if status:
            raise ValueError(f'rust sampler rejected buffers: {status}')
        return output
    return sample


def load_bake(path, gen_dir, library=None):
    import importlib.util
    sys.path.insert(0, str(gen_dir))
    spec = importlib.util.spec_from_file_location('experiment_bake', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    if library:
        module.sample = rust_sampler(library)
    return module


def worker(args):
    sys.path.insert(0, str(args.root / 'server'))
    if args.worker == 'bake':
        bake = load_bake(args.bake, args.root / 'styles/glass-v2', args.library)
        bake.bake(str(args.input), str(args.out / 'bg.png'))
        bake.frame(str(args.input), str(args.out / 'art.png'))
    elif args.worker == 'voice':
        import voice_background
        voice_background.make(args.out / 'art.png', args.out / 'voice_bg.png')
    else:
        import panel_image
        name = args.worker
        panel_image.convert(args.out / f'{name}.png', args.out / f'{name}_565.png')


def coordinator(args):
    args.out.mkdir(parents=True, exist_ok=False)
    start = time.perf_counter()
    stages = []
    for stage in ('bake', 'voice', 'bg', 'art', 'voice_bg'):
        before = resource.getrusage(resource.RUSAGE_CHILDREN)
        stamp = time.perf_counter()
        command = [sys.executable, str(Path(__file__).resolve()), '--worker', stage,
                   '--root', str(args.root), '--bake', str(args.bake), '--input', str(args.input),
                   '--out', str(args.out)]
        if args.library:
            command.extend(['--library', str(args.library)])
        result = subprocess.run(command, stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=120)
        after = resource.getrusage(resource.RUSAGE_CHILDREN)
        stages.append({'stage': stage, 'wall_s': time.perf_counter() - stamp,
                       'cpu_s': after.ru_utime + after.ru_stime - before.ru_utime - before.ru_stime,
                       'stdout': result.stdout, 'stderr': result.stderr, 'returncode': result.returncode})
        if result.returncode:
            (args.out / 'failed.json').write_text(json.dumps(stages, indent=2))
            raise RuntimeError(f'{stage} failed: {result.stderr}')
    elapsed = time.perf_counter() - start
    usage = resource.getrusage(resource.RUSAGE_CHILDREN)
    # Image comparisons happen in another process, after this measured process
    # has exited. Its allocations and CPU cannot contaminate the timings.
    report = {'variant': args.variant, 'input': str(args.input), 'wall_s': elapsed,
              'cpu_s': usage.ru_utime + usage.ru_stime,
              'peak_rss_kib': usage.ru_maxrss / (1024 if sys.platform == 'darwin' else 1),
              'stages': stages, 'input_sha256': hashlib.sha256(args.input.read_bytes()).hexdigest(),
              'bake_sha256': hashlib.sha256(args.bake.read_bytes()).hexdigest(),
              'python': sys.version, 'platform': sys.platform,
              'threads': {k: os.environ.get(k) for k in ('OPENBLAS_NUM_THREADS', 'OMP_NUM_THREADS')}}
    (args.out / 'timing.json').write_text(json.dumps(report, indent=2))
    print(json.dumps({k: v for k, v in report.items() if k != 'stages'}))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--bake', type=Path, required=True)
    parser.add_argument('--input', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--variant', default='baseline')
    parser.add_argument('--library', type=Path)
    parser.add_argument('--worker', choices=('bake', 'voice', 'bg', 'art', 'voice_bg'))
    args = parser.parse_args()
    worker(args) if args.worker else coordinator(args)


if __name__ == '__main__':
    main()
