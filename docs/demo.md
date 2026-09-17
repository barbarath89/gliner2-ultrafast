# Demo measurements

Goal: `Find a one-way ticket from New York to San Francisco on October 9, 2026.`

The recording shows the agent operating real Google Flights. Independent post-run checks verify one-way travel, origin, destination, departure date including year, and visible flight options. No ticket is selected or booked.

| Measurement | Recorded value |
|---|---:|
| Visible-results frame | 12.201 seconds |
| Complete action loop | 13.785 seconds |
| Text-helper API usage | $0.00010623 across three calls |
| Published MP4 duration | 14.5 seconds |

The clock starts after model loading, goal extraction/parsing, initial navigation and first page observation. The action-loop measurement includes its local matching, text calls, browser actions, stale decisions and waits. Independent final verification is outside that clock.

The published cut ends at visible results before the loop's additional waits and scrolls. It has a 1.2-second opening hold and roughly a 1.1-second results hold. The verification label is added after verification; it does not influence execution. The README GIF uses fewer frames per second, preserving the same elapsed-time playback.

GLiNER inference is local and incurs no API charge. Reported dollars are API usage only, not hardware, electricity or browser-hosting costs. This recording is a demonstration, not a controlled performance comparison or general reliability benchmark.

## Reproducibility

- Local model: `fastino/gliner2-multi-v1`.
- Recorded cached revision: `edd4f6efd8611a4c29632fa8034b181ee8da9ebc`.
- Text model: `inception/mercury-2.5` via OpenRouter, reasoning disabled.
- Dependencies: `uv.lock`.
- Capture: `scripts/record_flights_demo.py` uses continuous browser screencast timestamps.

The default model loader follows the model repository; upstream changes, website changes, browser state and network timing can affect reproduction. Use a future date and update the example verifier consistently. Keep the model/controller independent of that verifier.

The public code retains the recorded policy and executor behavior. Publication cleanup changes comments, documentation, inspector labels, serialization and packaging; it does not introduce a prepared route or click sequence.
