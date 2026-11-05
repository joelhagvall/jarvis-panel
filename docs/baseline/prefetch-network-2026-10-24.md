# Lower decode work and network latency, 2026-10-24

The installed candidate passed full physical CI and a repeated fixed responsiveness
workload. Orb refresh-work p95 fell from 17.547 ms to 12.792–12.883 ms, approximately
27%. Status-request p95 during the same workload fell from 704.315 ms to
117.563–124.936 ms. Frame cadence remains approximately 25 redraws/s. These changes
provide more rendering headroom; they do not establish 60 fps or optical touch latency.

## Candidate and method

- Running build: `52c24f86916081bc78ff9ecf7830595d`.
- OTA SHA256: `c84d3893a1f6ea887f0388ed45b3cd13b3bb7131a8f983aa8597fdc1207d6edb`.
- Full successful CI: `.panel-ci/run-20261024-185134-302063/`.
- Before: `/tmp/hallpanel-next-before/responsiveness.json`, previous verified 5 ms firmware.
- Repeat: `/tmp/hallpanel-next-after-repeat/responsiveness.json`, same new firmware and boot.
- Protocol remains `tabs-20-orb-10s-v2`; policy and acceptance tolerances are unchanged.
- All 401 free tests, ESPHome config/build, checked OTA, boot/identity/log review,
  physical navigation, synthetic faults, 14 visual scenes, memory/performance and
  responsiveness gates passed. Subsequent host diagnostics received a targeted regression
  and another physical responsiveness pass; they did not alter firmware inputs.
- Final `.venv/bin/python tools/check.py` passed all **402 free tests** and ESPHome
  configuration validation. The final fresh screenshot/status inspection showed the
  normal dashboard, healthy idle firmware, the same boot and zero decode errors.

The generator disables Wi-Fi modem sleep on this wall-powered panel. The HTTP server
sets TCP_NODELAY for accepted sockets. These two changes were tested together; the
experiment does not isolate their individual contributions. A passive 24-pair probe
before the change showed that reopening connections alone did not resolve the delay.

The orb keeps its compressed HPA3 assets. A core-0 worker predicts and LZ4-decodes
the next RGB565A8 image into one of three fixed buffers. LVGL pins a buffer during
drawing; atomic ownership prevents concurrent overwrite. A prediction miss immediately
uses the original LVGL decoder. Image order, pixels, animation duration and asynchronous
display buffering remain unchanged. A full 64-frame decoded listen cache would need
9,292,800 bytes; the accepted bounded cache uses 435,600 bytes.

## Fixed workload distributions

All values below are milliseconds, written as **p50 / p95 / p99 / maximum**.
Navigation has 20 press and 20 release samples per run. With n=20, p99 is the maximum.

| Measurement | Before | Full CI | Repeat |
|---|---|---|---|
| Orb refresh work | 16.779 / 17.547 / 23.685 / 27.503 | 12.281 / 12.792 / 18.121 / 20.933 | 12.279 / 12.883 / 19.059 / 19.758 |
| Orb refresh interval | 42.419 / 43.827 / 44.262 / 45.308 | 39.563 / 43.282 / 43.608 / 43.784 | 39.548 / 43.494 / 43.826 / 43.938 |
| Injected press to redraw | 10.015 / 18.047 / 18.158 / 18.158 | 17.575 / 18.150 / 18.163 / 18.163 | 17.382 / 17.908 / 18.064 / 18.064 |
| Injected release to redraw | 78.942 / 83.006 / 83.409 / 83.409 | 77.234 / 80.020 / 80.036 / 80.036 | 79.354 / 84.375 / 84.654 / 84.654 |
| Command HTTP, including 60 ms hold | 85.650 / 142.461 / 151.742 / 151.742 | 80.401 / 92.589 / 125.693 / 125.693 | 78.863 / 98.302 / 140.455 / 140.455 |
| Status HTTP | 254.287 / 704.315 / 730.215 / 730.215 | 56.124 / 117.563 / 162.876 / 162.876 | 55.275 / 124.936 / 161.983 / 161.983 |
| Command through status acknowledgment | 245.079 / 794.590 / 803.451 / 803.451 | 193.994 / 211.032 / 246.409 / 246.409 | 199.554 / 215.702 / 246.733 / 246.733 |

There were 255 orb intervals before, 253 in full CI and 253 in the repeat. Both new
runs measured approximately 25.01 redraws/s and **zero of 506 intervals over 50 ms**.
Each new run retained 254 fresh refresh-work samples. This is finite observation,
not a guarantee against all future stalls. Press/release latency has not materially
improved in this experiment; its observed distributions are retained above.

The repeat measured 254 prefetch hits, zero fallbacks and zero decode errors during
the orb interval. Lifetime decode errors also remained zero after the visual/fault
tests. Full CI had the same 254-hit/zero-fallback result. The worker completed 250
new decodes per run; a cached image can serve multiple opens. CPU work and cached
opens are separate quantities.

UI-task status snapshots remained small: repeat p50/p95/p99/max were
242 / 314 / 315 / 315 microseconds. Radio RPC p50/p95/p99/max remained
30.147 / 30.559 / 30.807 / 30.807 ms on core 0. The repeat's HTTP phase p95 values
were 0.35 ms to submit, 121.45 ms until headers, and 5.70 ms to read the status body.
The remaining network tail is not attributed to UI rendering.

## Independent connection probe and screenshot tail

The probe alternates persistent and new connections, reverses their order on every
pair and pauses 100 ms between requests. Each mode has 24 samples. It runs on the
idle panel, separately from the animation workload.

| Mode | Before p50 / p95 / p99 / max (ms) | After p50 / p95 / p99 / max (ms) |
|---|---|---|
| Persistent | 406.2 / 788.0 / 878.1 / 878.1 | 44.08 / 69.95 / 70.71 / 70.71 |
| Fresh connection | 404.7 / 920.5 / 1061.4 / 1061.4 | 49.59 / 72.63 / 398.05 / 398.05 |

Raw evidence: `/tmp/hallpanel-http-probe/transport.json` and
`/tmp/hallpanel-http-probe-after/transport.json`. New connections still had one
398 ms outlier; no sample was removed.

Full CI's five JPEG captures had median 151.3 ms and p95/max 1702.1 ms, including
two slow body deliveries of 1606.18 and 1256.31 ms. Copying the framebuffer remained
approximately 25–27 ms. A same-workload five-capture repeat had median 133.7 ms and
p95/max 153.0 ms. Both sets are retained. This does not establish that large transfers
have no network stalls; see [the existing transport diagnosis](../screenshot-transport.md).
The unchanged screenshot gate passed against its original measured reference.

## Memory, pixels and future comparisons

Idle minimum free PSRAM was 18,872,568 bytes versus 19,314,216 in the prior full CI.
The 441,648-byte difference includes the fixed 435,600-byte decode storage and other
runtime allocations. Minimum free internal memory was 303,064 versus 306,824 bytes.
Both absolute floors and relative regressions passed. Power consumption was not measured;
disabling modem sleep increases radio power in exchange for lower receive latency.

The optional native diagnostic passed 719 byte-for-byte comparisons against the
actual LVGL binary decoder, with ASan/UBSan and zero decode errors. It covers all
143 source frames, sequential modes, transitions and unpredictable frame orders.
Its report is `.panel-ci/prefetch-native-next.json`. The mandatory slot-ownership
test also stresses 100,000 producer iterations against concurrent reads. Neither
native test is represented as physical display verification.

The responsiveness collector now retains separate HTTP phases and fresh prefetch
counter deltas. Partial/invalid counters, resets, changing residency or decode errors
fail the check. Older firmware without the feature is explicitly unsupported for
these extra counters. Existing baseline/workload comparison semantics stay intact.

The successful full-CI measurement was recorded as
`.panel-ci/baselines/responsiveness-prefetch-20261024.json`. The independent repeat
also passed against that stricter measured reference. The default selector now uses
it; the previous passing reference is preserved as
`.panel-ci/baselines/responsiveness-before-prefetch-20261024.json`.
No failed reference, threshold, golden pixel set or slow sample was overwritten.

Software redraw completion measures refresh submission after input sampling. It
does not prove GT911 scan latency, optical finger-to-photon latency, physical flicker,
acoustic quality or a measured comparison with Tesla.
