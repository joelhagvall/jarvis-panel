#!/usr/bin/env python3
"""Validate observed shared-clock microphone cadence from bounded native API logs."""
import argparse
import json
from pathlib import Path
import re

PATTERN = re.compile(r'Audio 48000->16000: elapsed_ms=(\d+) input=(\d+) output=(\d+) max_us=(\d+) clipped=(\d+) errors=(\d+)')


def evaluate(path):
    samples=[]
    text=Path(path).read_text()
    for match in PATTERN.finditer(text):
        elapsed, raw, voice, processing, clipped, errors = map(int, match.groups())
        if elapsed <= 0:
            raise RuntimeError('Invalid audio measurement interval')
        samples.append({'elapsed_ms':elapsed, 'raw_hz':raw*1000/elapsed, 'voice_hz':voice*1000/elapsed,
                        'max_processing_us':processing, 'clipped_total':clipped, 'errors':errors})
    failures=[]
    if len(samples)<2:
        failures.append('At least two observed audio cadence windows are required')
    if any(abs(s['raw_hz']-48000)>480 or abs(s['voice_hz']-16000)>160 for s in samples):
        failures.append('Actual microphone cadence differs from the 48 kHz/16 kHz contract by over 1%')
    if any(s['errors'] for s in samples):
        failures.append('Microphone resampler reported a format/progress error')
    if 'Microphone conversion stopped' in text or 'Cannot initialize filtered' in text:
        failures.append('Microphone adapter failed during the run')
    result={'ok':not failures,'failures':failures,'samples':samples,
            'measurement':'Actual frame counts, not acoustic quality or wake-word recognition accuracy'}
    if failures:
        raise RuntimeError('; '.join(failures))
    return result


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('log',type=Path)
    args=parser.parse_args()
    print(json.dumps(evaluate(args.log),indent=2))
