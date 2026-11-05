# Detail-page update and speaker-feedback verification, 2026-11-02

Installed build: `00b26eda36a5b3c6fb60f519023481a1`. Checked flash ended `FLASH OK`; visual, responsiveness, production 60 fps, turntable, 120-second soak and crash/rollback checks passed. The release ran 1,057 free local tests. The separate checked Jarvis deployment passed 123 HA-runtime tests. Nothing was committed or pushed.

All table values are **p50 / p95 / p99 / maximum in ms**. Each detail update population has 10 fresh redraws over six seconds. Each navigation population has 20 real remote-pointer taps (10 exits, 10 entries), with synthetic updates and offscreen cover preparation active. With these small samples p99 is the observed maximum; these are measured samples, not a population guarantee.

| Page | Before: update work | After: update work | After: press → redraw | After: release → redraw |
| --- | --- | --- | --- | --- |
| weather | 64.218 / 66.073 / 66.073 / 66.073 | 31.987 / 32.744 / 32.744 / 32.744 | 16.142 / 30.019 / 34.397 / 34.397 | 41.888 / 61.206 / 61.225 / 61.225 |
| calendar | 50.744 / 51.690 / 51.690 / 51.690 | 29.986 / 32.454 / 32.454 / 32.454 | 16.293 / 24.917 / 25.154 / 25.154 | 41.741 / 45.225 / 45.267 / 45.267 |
| ai | 36.983 / 40.595 / 40.595 / 40.595 | 16.331 / 19.152 / 19.152 / 19.152 | 3.851 / 19.041 / 19.317 / 19.317 | 35.849 / 42.353 / 43.339 / 43.339 |

All three cases passed the unchanged detail protocol. No clipped text was detected. The fixed-workload updates do not represent model calls, downloads or every possible real data payload.

Host/observer measurements are separate from the on-device redraw samples. Command round trips include the requested 60 ms pointer hold and device processing; they are not network-only latency. `/status` round trips include network and device scheduling.

| Page | Command round trip | Status round trip | UI diagnostic snapshot cost |
| --- | --- | --- | --- |
| weather | 78.425 / 103.527 / 105.947 / 105.947 | 25.184 / 91.101 / 98.074 / 98.074 | 0.506 / 0.564 / 0.575 / 0.575 |
| calendar | 69.735 / 85.106 / 87.316 / 87.316 | 22.778 / 54.582 / 68.774 / 68.774 | 0.514 / 0.588 / 0.619 / 0.619 |
| ai | 68.326 / 75.132 / 80.912 / 80.912 | 22.700 / 43.244 / 80.520 / 80.520 | 0.524 / 0.598 / 0.609 / 0.609 |

The standard responsiveness gate compared the same protocol/policy with the existing measured baseline. Its ordinary navigation results did not show a general speed increase; the material improvements above concern the detail update workload.

| Standard navigation | Before | After |
| --- | --- | --- |
| navigation.remote_press_ms | 5.351 / 6.426 / 6.441 / 6.441 | 5.329 / 6.286 / 6.312 / 6.312 |
| navigation.remote_release_ms | 31.783 / 35.774 / 36.812 / 36.812 | 31.267 / 33.723 / 33.969 / 33.969 |
| status_roundtrip_ms | 28.497 / 54.618 / 59.683 / 59.683 | 26.204 / 47.290 / 59.390 / 59.390 |

Production listening-orb throughput: 60.397 fps, 0.000% jank, 0 repeated frames. Presented-frame intervals: 16.563 / 16.566 / 16.569 / 16.575 ms. Host status round trip: 23.398 / 65.611 / 95.527 / 95.527 ms. This is the listening orb, not all screens or optical latency.

Speaker UI checks used real settings controls and actual HA actions. They showed waiting, then genuine Bluetooth failures for JBL selection/reconnect. After correcting the HA service-error type, the failures reached the panel after about 20/46 seconds of observation instead of waiting for a false-success timer. Re-selecting the original Panelen output showed success on the first poll. Observation clocks started after the remote tap returned, so no precise input-to-acknowledgement latency is claimed. JBL did not reconnect; sound recovery was not established.

Original output Panelen, daytime volume 65%, brightness 35% and night brightness 15% were preserved. No paid model/voice call was used.

Point 4 is prepared in [optical-latency.md](../optical-latency.md), with an empty CSV template and uncertainty-aware analyser. No external recording exists and no physical finger-to-photon result is claimed.

Raw evidence remains private under `.panel-ci/`:

- `detail-pages-first-20261102/`, `detail-pages-optimized-20261102/`, `detail-pages-final-20261102/`: all failed intermediate runs retained.
- `detail-pages-batched-20261102/`: accepted detail workload, raw samples, UI trees and lossless pictures.
- `output-live-ui-20261102/`: speaker waiting/failure/success observations and captures.
- `weather-batch-install-console.log`, `output-ha-deploy-console.log`, `last-flash.json`: install/test/build evidence.
- `responsiveness/run-20261102-094509-549060/`, `throughput/run-20261102-094532-866198/`: standard measured release gates.
