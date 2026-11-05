# Screenshot transport stalls

## Observed failure, 2026-10-24

Physical CI stopped at `performance-regression` in
`.panel-ci/run-20261024-114750-236556/`: screenshot p95 was 3568.3 ms against
the measured 1499.1 ms baseline (35% relative tolerance unchanged). A second
measurement on the same Hemmet page reached 3573.5 ms. Boot, memory, the
framebuffer copy, physical integration and all 14 visual scenes had passed.

Timing the existing HTTP transaction separated rendering from delivery:
headers arrived in approximately 80–100 ms, with framebuffer copy around
24–26 ms, while a slow response body alone took 1111.91 ms or 1447.85 ms.
Read-only TCP response metadata showed late/out-of-order 1440-byte segments,
including arrivals up to 65534 bytes behind the highest received sequence.
These are actual transport stalls, not evidence of slow LVGL rendering.

Jarvis, using eth0 and the same shared harness against the same firmware,
measured 12 screenshots at 116.5–233.8 ms. Its mDNS lookup failed; specifying
the verified panel IPv4 address worked. This is separate from body delivery.

## Correction

The shared `PanelClient` bounds its socket receive buffer to **16 KiB** before
sending requests. TCP flow control limits the sender's flight size instead
of allowing the large bursts observed on the Mac Wi-Fi path. Both persistent
HTTP/HTTPS and one-off urllib clients use the same policy, including reconnects.
TLS validation, token handling, redirect refusal, device leases, capture rate
limits and the rule against retrying ambiguous input are unchanged.

This is a host transport change. It does not change the panel's framebuffer,
JPEG quality, firmware Wi-Fi configuration, alarms, or animation. It controls
bursts in the failing path; the packet trace does not identify which AP/radio/
SDIO component originally lost or delayed a packet. Do not claim RF repair.

The fixed-window diagnostic comparison used the same dashboard, firmware and
image without OTA, comparing 30 captures per variant:

| Receiver | Median | p95 | Maximum |
|---|---:|---:|---:|
| Mac OS default window | 146.45 ms | 465.4 ms | 2014.2 ms |
| Mac 16 KiB | 157.95 ms | 470.9 ms | 530.7 ms |
| Mac 8 KiB | 161.25 ms | 531.1 ms | 534.6 ms |

An earlier 15-capture 16 KiB run measured 131.9–565.1 ms. The change reduced
the severe observed outliers; the 30-capture p95 itself was essentially equal
to control. This is not a guarantee against arbitrary Wi-Fi congestion.
Full CI must still pass its original measured baseline and absolute limits.
No baseline or tolerance was replaced to make these results green.

## Reusable diagnosis and physical QA

```sh
tools/panel metrics collect --out /tmp/NEW-DIRECTORY --samples 30 --interval 0.6
tools/panel metrics collect --out /tmp/NEW-CONTROL --samples 30 --interval 0.6 --receive-buffer 0
tools/panel metrics collect --out /tmp/NEW-16K --samples 30 --interval 0.6 --receive-buffer 16384
tools/panel ci --brightness-drag --visual-baseline REVIEWED-DIRECTORY
```

`--receive-buffer 0` explicitly selects the OS default for diagnosis. Other
values must be 4096–65536 bytes. CI uses the fixed default; its performance
gates cannot be disabled by this diagnostic flag. Persistent capture metadata
now includes request-send, response-header and response-body durations without
headers or credentials. The full elapsed measurement still includes reconnects
and capture rate-limit delays; no time is subtracted from the gate.

`--brightness-drag` opts CI into the shared settings detent checks before its
performance gates. It tests raw 48% and 52% positions, both of which must snap
to 50%, and restores the original brightness. See [slider-detents.md](slider-detents.md).

Evidence is retained privately in `.panel-ci/transport-diagnosis-20261024/`.
The packet capture is bounded to 35 seconds and panel responses from port 8080;
request Authorization headers and unrelated network traffic are excluded.
Golden references, the original failed run and the quarantine remain intact
until the approved recovery plan completes every required physical run.
