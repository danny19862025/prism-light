# Studio validation — 13 November 2026

Environment: Linux x86-64, Python 3.14.9, PySide6/Qt 6.10.1, PyInstaller 6.17.0. Desktop checks use Qt's offscreen platform. Earlier foundation-only evidence is retained in FOUNDATION-VALIDATION.md and does not establish Windows validation of Studio.

## Verified locally

- 32 automated tests: SDK negotiation/framing, fragmented responses, timeout/refusal/disconnect, invalid/old packets, parsing, device-list change rejection, unsupported-device isolation, independent LED bytes and Direct-mode initialization once per session.
- Frame validation rejects wrong lengths, invalid colors and unknown device identities before lighting writes.
- All 37 effects return bounded RGB output; zero brightness renders black and screen-grid mapping is checked.
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

## Bundled hardware engine

The managed engine is tested with a real child-process SDK fixture: automatic private-port negotiation, missing/escaping executable rejection, readiness cancellation, environment isolation and child cleanup. Studio integration is tested through the managed discovery entry point with no port controls. Source startup against the actual pinned Linux OpenRGB 1.1 runtime completed SDK negotiation with zero devices visible in the sandbox and shut down the child cleanly. Zero visible devices is not a hardware-compatibility result.

The portable engine includes checksum-verified official assets, its original license and the exact matching OpenRGB source archive. Windows asset integrity and archive layout are verified locally; Windows execution remains unverified. `--hardware-smoke-test` exercises actual engine startup and shutdown in the portable Linux build without LED writes. Demo startup never launches the hardware engine.

## Twenty additional effects

All 37 renderers are checked for frame length, bounded integer RGB channels and blackout at zero brightness. Each new renderer is additionally checked for nonblack spatial output, animation over sampled times where appropriate, and identical results when one desk-space frame is rendered in separate device slices. Audio bars/rings remain black without audio input. Scale/density ranges and older-scene defaults are validated. Gallery counts and category filters derive from the catalog instead of fixed effect counts.
