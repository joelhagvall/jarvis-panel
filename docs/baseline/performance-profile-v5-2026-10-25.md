# Reviewed reference for the existing orb-v5 production profile

The October 24 idle/animation references remain unchanged at
`.panel-ci/baselines/performance.json` and `animation.json`. Their PSRAM medians
are 19,352,524 and 19,325,112 bytes. They precede the current production artwork
and buffers. They are not valid as a before/after memory comparison for the
already-installed October 25 orb-v5 profile.

Before this session's firmware work, the saved physical status reports 16,914,100
free PSRAM bytes, 199 original orb frames, 679 virtual frames and 857,072 decoded
prefetch-buffer bytes. The current unchanged orb-v5 compressed file is 5,776,072
bytes; v3 is 4,305,012. The pre-existing larger resident artwork/buffers explain
the profile change. The boot/status candidate's failing legacy-reference run
measured 16,917,388 bytes: **3288 bytes more free than the saved pre-change
status**, not a newly introduced 2.4 MB loss. Page/sampling differences prevent
claiming that small difference as an optimization.

The failed run and its comparison remain at
`.panel-ci/run-20261025-223009-623511/`. Its normal software boot, stable identity,
physical smoke, synthetic modes and all visual scenes passed before the
incompatible memory comparison stopped it. It is not reported as a complete
release pass. No reference or threshold was changed to relabel that run.

## Separate measured profile

New captures explicitly use the existing installed orb-v5 firmware and the same
measurement protocol: Hemmet idle with five physical captures, then the bounded
silent animation with restoration. Activity, installed identity, boot health and
restored page were checked. All absolute limits passed before reference creation.
Raw captures, statuses, timings and checks are retained under
`.panel-ci/boot-fast-20261025/profile-v5-calibration/`.

| New reference | PSRAM min/median/max, bytes |
| --- | ---: |
| `performance-orb-v5.json` | 16,918,356 / 16,918,356 / 16,918,356 |
| `animation-orb-v5.json` | 16,918,536 / 16,918,536 / 16,918,536 |

The recorder created these distinct files with exclusive creation; it refused
failed measurements and existing paths. The old references were not overwritten.
Absolute memory floors, drop limits, all regression percentages, visual,
responsiveness, production 60 fps and 120-second soak gates are unchanged.
The CI now accepts explicit reviewed idle/animation paths and records their exact
paths and SHA-256 in the report. Missing or invalid explicit references fail;
there is no fallback or automatic baseline promotion. Defaults still select the
original reference files. Four host regressions cover these properties.

For the current orb-v5 profile, use both explicit references:

```
tools/panel ci --visual-baseline .panel-visual/reference-calendar-v4 \
  --performance-baseline .panel-ci/baselines/performance-orb-v5.json \
  --animation-baseline .panel-ci/baselines/animation-orb-v5.json
```

The references describe the existing production artwork; future asset/buffer
changes still require inspection and an explicit profile decision. A failed
measurement is never automatically made into a reference. A complete physical
run against these compatible references is still required for installation
verification and quarantine resolution.

Offline lossless temporal/spatial atlas compression experiments were also kept
as negative results. Their best savings did not justify the extra decoder work
or meet the legacy-memory difference after required workspace; no atlas format,
orb pixel, frame count, prefetch buffer or image-quality change is retained.
Raw sizes: `atlas-delta-sizes.json` and `atlas-spatial-sizes.json` beside the boot
evidence. The new fade-in work remains separate from this profile calibration.
