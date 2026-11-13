# Studio validation — 13 November 2026

Environment: Linux x86-64, Python 3.14.9, PySide6/Qt 6.10.1, PyInstaller 6.17.0. Desktop checks use Qt's offscreen platform. Earlier foundation-only evidence is retained in FOUNDATION-VALIDATION.md and does not establish Windows validation of Studio.

## Verified locally

- 25 automated tests: SDK negotiation/framing, fragmented responses, timeout/refusal/disconnect, invalid/old packets, parsing, device-list change rejection, unsupported-device isolation, independent LED bytes and Direct-mode initialization once per session.
- Frame validation rejects wrong lengths, invalid colors and unknown device identities before lighting writes.
- All 17 effects return bounded RGB output; zero brightness renders black and screen-grid mapping is checked.
- Scene round-trip, invalid parameter rejection and corrupt-file preservation.
- Inert plugin catalog import; the supplied recovered archive indexes 478 JS plugin files. No code is evaluated.
- Local HTTP endpoint rejects absent authentication, accepts an authorized color and closes on stop.
- Studio GUI demo: per-LED streaming, spatial color variation, layout/paint scene restore, demo/hardware scope isolation, automation edge transitions, stopped effects and queued blackout while a frame is in flight.
- Existing preset storage and original desktop interaction tests continue to pass.
- Seven tabs instantiated and exercised. Screenshots of lighting, layout, sources and coverage inspected. Source controls were placed in a scroll container to retain readable controls at constrained heights.
- Source and packaged Linux offscreen startup smoke checks.

## Remaining verification

- Current Studio Windows source launch, EXE packaging, interactive desktop appearance and display scaling. CI definitions for Windows/Ubuntu are supplied and run on pushes; consult GitHub Actions for results after publication.
- Real OpenRGB device detection, physical colors, transport throughput, reconnect/hotplug timing and coexistence with other RGB controllers. Wire tests use an independent localhost fake SDK server, not physical RGB hardware.
- Real audio input permission, microphone/monitor behavior and capture latency. Qt devices are enumerated, but no user audio was recorded during validation.
- Real monitor capture permissions and behavior on Windows/X11; no Wayland portal capture implementation is included.
- LCD hardware, fan/pump writes and game-specific integrations are not implemented and are labelled as unavailable rather than validated features.

No hardware compatibility list, exact SignalRGB parity or public-release certification is claimed. The Linux binary is built for the current environment; it has not been tested on other distributions. Qt/PyInstaller warnings about optional system libraries are recorded in the build log; packaged smoke startup is the relevant local executable check.

## Gallery redesign

The dark sidebar, effect gallery, spotlight carousel and persistent playback bar use original Qt-painted artwork. A GUI test verifies category/search filtering, effect-card navigation, playback and brightness synchronization. Existing device, scene, streaming and blackout tests remain in place. Source and packaged startup are checked after rebuilding.
