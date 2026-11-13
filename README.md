# Prism Light

An original personal RGB lighting desktop app. Free software, local only: no subscriptions, accounts, telemetry or cloud service. This first version uses solid colour with software brightness. It does not control fans or pumps. Donations can be added as a voluntary external link when the maintainer chooses a destination; no payment flow or donation URL has been invented.

![Explicit demo mode](docs/demo.png)

## Stack and integration

Python 3.12+ and Qt Widgets through PySide6 give us native desktop windows, accessible standard controls, a polished dark interface and a Windows packaging route. The UI and preset storage are separate from a small original SDK transport. Socket work runs on a serial worker thread so a failed connection does not freeze the interface. No browser runtime is needed.

OpenRGB is a **separate program**, connected only over documented TCP SDK packets on `127.0.0.1`. Hardware detection and device drivers remain in OpenRGB. This project neither links nor copies its controller code and does not bundle OpenRGB. The client requests SDK protocol **3** (OpenRGB 0.7+); newer servers negotiate down. Older servers fail with a helpful error. Current protocol 6 features, segments, zone effects and mode-specific-only devices are outside this foundation.

Official source and SDK docs inspected before implementation:

- https://gitlab.com/CalcProgrammer1/OpenRGB
- https://gitlab.com/CalcProgrammer1/OpenRGB/-/blob/master/Documentation/OpenRGBSDK.md
- Inspected revision: `4e7aa49d68aca6c959eb2a9b48a6d6fc35f744a6`
- Local reference checkout: `../openrgb-upstream` (not needed to run).
- License and integration notes: [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).

## Windows setup and run

Install 64-bit Python 3.12 or newer from https://www.python.org/downloads/windows/. Open PowerShell in this project folder:

```powershell
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe main.py --demo
```

For normal startup, omit `--demo`. Demo is also available from the connection bar. It is always explicitly labelled and never contacts hardware. It includes two controllable simulated devices and one unsupported simulated device.

Linux development:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python main.py --demo
```

No internet is required at runtime. Initial dependency and OpenRGB downloads require internet.

## Connect OpenRGB

1. Install OpenRGB from https://openrgb.org and follow its Windows device/driver setup instructions. Open it and check which devices and zones it actually detects. This app cannot add support for hardware that OpenRGB does not detect.
2. Open OpenRGB's **SDK Server** tab. Start the server on port **6742**. Keep OpenRGB running. You may instead launch OpenRGB with `--server --server-port 6742`.
3. In Prism Light, click **Connect OpenRGB**. The address is intentionally fixed to IPv4 localhost. Change the port if necessary. If OpenRGB has an address-binding option, use `127.0.0.1`; remote access is unnecessary.
4. Select a single device's checkbox to apply to only that device; select multiple to apply to them together. Choose a colour or enter `#RRGGBB`, set brightness, then click **Apply to selected devices**. Changes are explicit, not streamed while dragging.
5. A device must advertise a mode named **Direct** or **Custom** with per-LED colour support and at least one LED/colour slot. Others remain listed with disabled controls and a reason. Mode names outside this conservative allowlist are currently unsupported even if similar hardware might be controllable.
6. Brightness is explicitly **software dimming**: multiply each RGB channel by the percentage. Native brightness is set to the Direct mode maximum when advertised, to avoid double dimming. This is lighting intensity, not electrical power or thermal control. At 0%, RGB is black.

Zones are listed with LED counts; this first version colours the whole selected device. No LED count changes, zone resizing, firmware writes or profile-save commands are sent. Applying a colour switches supported devices to their Direct mode. On disconnect or exit, the last lighting may remain; previous hardware effects are not automatically restored.

The UI checks the SDK connection every four seconds and before applying. A device-list notification or count change invalidates control and requires reconnecting. Do not rescan or hotplug while applying: protocol 3 uses positional device IDs and cannot make topology changes atomic with a write. Device lighting writes have no hardware acknowledgement in protocol 3: **sent** means bytes were sent, not that a physical LED has been verified.

## Named presets

Select devices, choose colour and brightness, then **Save as…**. This saves the editor settings and selected device identities. **Restore** loads the colour, brightness and selection; **Apply** sends them. Missing, unsupported and ambiguous devices are skipped. Presets match name, vendor, serial and location rather than a device's temporary list index; a changed USB location may require resaving. Identical devices without unique identities are disabled to avoid restoring to the wrong device.

- Windows: `%APPDATA%\PrismLight\presets.json`
- Linux: `$XDG_CONFIG_HOME/PrismLight/presets.json`, falling back to `~/.config/PrismLight/presets.json`.

Writes use a temporary file, flush and atomic replacement. Demo and hardware presets have separate scopes in this file. Names are unique across scopes; replacing a name requires confirmation. Corrupt or unknown-format files are preserved and an actionable error is displayed. Back up or rename a corrupt file to start fresh. This version assumes one running instance when saving presets.

## Build a Windows app

Run on Windows, in the project folder:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-build.txt
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\.venv\Scripts\python.exe build.py
.\dist\PrismLight\PrismLight.exe --demo
```

The result is a portable **folder** at `dist/PrismLight/`. Keep the entire folder together. Python need not be installed on the destination machine. Build on Windows for Windows; PyInstaller does not cross-compile Windows executables on Linux. The optional GitHub Actions workflow builds and tests on Windows once this project is pushed to a repository; it has not been run here. Public distribution must retain the included notices, licenses and corresponding source; see third-party notices. This prototype is unsigned.

## Checks

```sh
.venv/bin/python -m unittest discover -s tests -v
```

14 tests passed on Linux with Qt's offscreen platform:

- Real localhost TCP framing against a fake SDK server, protocol negotiation to v3, device and zone decoding, and RGB/update-mode encoding.
- Connection refused, timeout, server disconnect, invalid magic, old protocol and truncated data.
- Device-list notification blocks stale indexed writes.
- Unsupported devices produce no lighting packets; demo performs no network access.
- Named preset persistence across store instances, overwrite, scope, invalid values and corrupt-file preservation.
- Qt demo interaction, colour/brightness restore, unsupported-device UI, empty inventory, mock live connection and connection failure messaging.

A visual capture was inspected and the layout corrected. `docs/demo.png` is the actual Qt demo interface, not a design mockup. `--smoke-test` launches the labelled demo and exits after verifying basic UI startup, useful for packaged builds.

**Requires your Windows PC:** run the packaged executable; check scaling at 100%, 150% and 200%, keyboard navigation and colour dialog; connect to your actual OpenRGB server; verify device/zone names, colour order, dimming from 0–100%, single and multi-device application, disconnect/reconnect, restarting the app and restoring presets, and behaviour on unplug/rescan. No physical hardware or Windows compatibility has been verified here. The fake server is a protocol test fixture, not OpenRGB itself.

## Troubleshooting

- **Connection refused:** start OpenRGB's SDK server and check its port matches. OpenRGB must accept IPv4 localhost connections.
- **Timeout or invalid packet:** verify the port belongs to OpenRGB, not another service. SDK v3+ is required.
- **No devices:** inspect OpenRGB detection, permissions and supported-device documentation; reconnect after detection completes.
- **Unavailable device:** v1 has no suitable Direct mode for that device. Do not interpret a detected device as verified compatibility.
- **Colour sent but no visible change:** verify Direct mode in OpenRGB, brightness and active competing RGB applications. Test using OpenRGB first. Do not run multiple effect controllers on the same device.

## License

Original Prism Light code: GPL-3.0-only. Copyright (C) 2026 Prism Light contributors. See [LICENSE](LICENSE). OpenRGB remains independently GPLv2 licensed. This is an independent project and does not use SignalRGB code, graphics or branding. The name Prism Light is a working project name; no trademark clearance is asserted.
