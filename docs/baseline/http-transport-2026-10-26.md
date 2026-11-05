# HTTP transport interruption, 26 October 2026

The UI repair candidate `7313ee6f4b885d09de66c6c0fd6f1834` passed boot identity,
clean logs, physical navigation/chat scrolling, fourteen visual scenes, idle and
animation gates. CI stopped at responsiveness: only `status_roundtrip_ms` failed,
p95 310.107 ms versus the unchanged measured-reference allowance 120.850 ms.
The same problem preceded installation (p95 336.728 and 388.634 ms). No failed
sample or reference was removed.

## Evidence and limits

Private evidence is in `.panel-ci/http-verification-20261026/` and
`.panel-ci/ui-repair-20261026/`. It includes raw request timing, panel queue/main/
worker time, bounded TCP-header traces, before/after identity and negative A/Bs.
One 846 ms request was retransmitted at about 252 and 555 ms before the peer
acknowledged it. The reply followed promptly; its panel queue/worker times were
0.527/2.194 ms. This establishes transport delivery loss/delay rather than an
846 ms UI computation. It does not identify a router, interference source or
Wi-Fi driver defect. No router or system Wi-Fi settings were changed.

A 30-sample trace initially looked better (p95 41.655 ms). Repetition with and
without promiscuous capture did not reproduce a consistent observer benefit:
p95 375–634 ms across those phases. Capturing is not a remedy. A later four-phase
best-effort/Apple Responsive Data A/B gave p95 24.755–38.540 ms in both classes;
there was no demonstrated classification benefit, so no QoS code was retained.
These separate diagnostics are not substitutes for the full release workload.

Use header-only IPv4 capture on the observed EN10MB interface: 54 bytes covers
Ethernet plus the minimum IPv4/TCP headers without HTTP payload or credentials.
Retain raw local evidence privately. Never record an Authorization payload.

## Bounded completion

Joel explicitly requested fixing/completing verification after the failed run.
Use the existing checked local CI and candidate/artifact/quarantine-bound recovery
plan with `--installed-report` for one attempt. Runtime code remains identical;
this diagnosis is documentation only. That attempt must repeat all physical,
visual, responsiveness, production 60 fps, 120-second soak and log gates. It may
not replace a baseline, skip a failure or loop until green. A failed attempt
retains quarantine and its ledger. Optical flicker remains a separate measurement;
RGB565 rendering and software boot markers do not establish it.
