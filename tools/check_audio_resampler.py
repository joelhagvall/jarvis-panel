#!/usr/bin/env python3
"""Compile the exact device microphone conversion with its managed ESPHome audio library."""
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]


def build(destination):
    source = ROOT / 'styles/glass-v2/.esphome/build/hallpanel/managed_components/esphome__esp-audio-libs'
    if not (source / 'include/resampler.h').is_file():
        raise RuntimeError('ESPHome audio dependency missing; resolve the panel build dependencies before release checks')
    destination = Path(destination)
    subprocess.run(['c++', '-std=c++17', '-O2', '-Wall', '-Wextra', '-I', str(ROOT),
        '-I', str(source/'include'), str(ROOT/'tests/native/audio_resampler.cpp'),
        *(str(source/'src'/name) for name in ('resample/resampler.cpp', 'resample/art_resampler.cpp',
            'resample/art_biquad.cpp', 'quantization_utils.cpp', 'dsp/dsps_dotprod_f32_ansi.c')),
        '-o', str(destination)], check=True, capture_output=True)
    return destination
