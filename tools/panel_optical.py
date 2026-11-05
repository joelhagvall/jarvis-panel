#!/usr/bin/env python3
"""Analyse manually bracketed contact/visible-response frames from original external high-speed video.

This reads annotations only. It never measures optical latency from panel telemetry or guesses capture fps.
"""
import argparse
import csv
import hashlib
import json
import math
from pathlib import Path

from panel_metrics import private_write
from panel_responsiveness import stats

FIELDS=('sample','condition','action','status','contact_before','contact_after','response_before','response_after','observation_end')

def frame(row,key):
    value=row.get(key,'')
    if not str(value).isdigit():raise ValueError(f"Sample {row.get('sample')}: {key} must be a nonnegative frame index")
    return int(value)

def analyse(rows,fps):
    if not math.isfinite(fps) or fps<=0:raise ValueError('Capture fps must be finite and positive')
    if not rows:raise ValueError('No annotated samples; no physical measurement exists yet')
    period=1000/fps;result=[];groups={};seen=set()
    for raw in rows:
        row=dict(raw)
        name=row.get('sample','')
        if not name or name in seen:raise ValueError('Every sample needs a unique identifier')
        seen.add(name)
        if not row.get('condition') or not row.get('action'):raise ValueError('Condition and action are required')
        cb,ca=frame(row,'contact_before'),frame(row,'contact_after')
        if cb>=ca:raise ValueError(f'{name}: contact bracket is reversed/empty')
        status=row.get('status')
        if status=='complete':
            rb,ra=frame(row,'response_before'),frame(row,'response_after')
            if rb>=ra:raise ValueError(f'{name}: response bracket is reversed/empty')
            lower,upper=(rb-ca)*period,(ra-cb)*period
            row.update(lower_ms=lower,upper_ms=upper,midpoint_ms=(lower+upper)/2,
                       needs_review=lower<0,uncertainty_width_ms=upper-lower)
        elif status=='no_response':
            end=frame(row,'observation_end')
            if end<ca:raise ValueError(f'{name}: observation ended before contact')
            row.update(lower_bound_ms=(end-ca)*period,needs_review=True)
        else:raise ValueError(f'{name}: status must be complete or no_response')
        result.append(row)
        groups.setdefault((row['condition'],row['action']),[]).append(row)
    summary=[]
    for (condition,action),samples in groups.items():
        complete=[r for r in samples if r['status']=='complete']
        summary.append({'condition':condition,'action':action,'total':len(samples),'complete':len(complete),
            'no_response':len(samples)-len(complete),'needs_review':sum(r['needs_review'] for r in samples),
            'completed_only_midpoint_ms':stats([r['midpoint_ms'] for r in complete]) if complete else None,
            'completed_only_lower_ms':stats([r['lower_ms'] for r in complete]) if complete else None,
            'completed_only_upper_ms':stats([r['upper_ms'] for r in complete]) if complete else None})
    return {'method':'manually bracketed external high-speed video','capture_fps_declared':fps,'frame_period_ms':period,
        'samples':result,'summary':summary,'limitations':[
            'Visible contact is manually estimated; it does not locate the internal capacitive trigger.',
            'Capture rate is supplied by the operator; playback/slow-motion fps is not capture fps.',
            'Rolling shutter, exposure and source-video authenticity are not calibrated by this tool.',
            'Unanswered taps remain separate censored outcomes; completed-only percentiles are not overall percentiles.',
            'Negative/overlapping intervals remain in the report for review; no slow or ambiguous sample is dropped.']}

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--template',type=Path,help='Create an empty annotation CSV; never fake a measured row')
    parser.add_argument('--annotations',type=Path)
    parser.add_argument('--capture-fps',type=float)
    parser.add_argument('--video',type=Path,help='Original source video, kept locally and hashed for provenance')
    parser.add_argument('--out',type=Path)
    args=parser.parse_args()
    if args.template:
        with args.template.open('x',newline='') as file:csv.writer(file).writerow(FIELDS)
        args.template.chmod(0o600);print(args.template.resolve());return 0
    if args.annotations is None or args.capture_fps is None or args.video is None or args.out is None:
        parser.error('--annotations, --capture-fps, --video and --out are required for analysis')
    if args.out.exists():raise ValueError('Output already exists; keep earlier measurements')
    digest=hashlib.sha256()
    with args.video.open('rb') as file:
        while chunk:=file.read(1024*1024):digest.update(chunk)
    with args.annotations.open(newline='') as file:report=analyse(list(csv.DictReader(file)),args.capture_fps)
    report['video']={'path':str(args.video.resolve()),'sha256':digest.hexdigest()}
    report['annotations']=str(args.annotations.resolve())
    private_write(args.out,report)
    print(json.dumps(report['summary'],ensure_ascii=False,indent=2))
    return 0

if __name__=='__main__':raise SystemExit(main())
