# Prism Monitor: hardware statistics widget for Windows 11

A compact, translucent desktop widget showing live CPU, GPU, memory and game FPS readings, with optional network, disk, fan and clock tiles. It runs as a standard user. Sensors that need administrator rights are read by a separate helper process, which you start yourself.

## Run it

1. Run `PrismMonitor-Setup-<version>.exe`. It installs for your account only, without an administrator prompt, unless you choose all users in the first dialog. It adds a Start-menu shortcut and an entry under **Settings → Apps** for uninstalling. Windows SmartScreen may warn because the installer is not code-signed: choose **More info → Run anyway** only if you got it from this repository's releases. For a portable copy instead, extract `PrismMonitor-windows.zip`, keep the folder together and run `PrismMonitor\PrismMonitor.exe`.
2. Start **Prism Monitor** from the Start menu. The widget appears at the top right of your main screen, and a tray icon (four bars) appears in the notification area.
3. CPU temperature, CPU package power, measured core clocks and motherboard fans need the sensor helper. Set it up once:
   * Keep **Set up CPU temperature… sensors** ticked in the installer, or
   * click the tray notification on first start, or
   * right-click the widget or tray icon and choose **Set up automatic CPU sensors (one-time)…**.

   Windows asks for administrator approval once. After that the helper starts automatically with no prompt whenever Prism Monitor runs. Without the setup, **Start sensor helper (administrator)…** starts it for one session, with a prompt each time.

From source: `.venv\Scripts\python monitor_main.py`. Build the release with `.venv\Scripts\python build_monitor.py`. It produces `dist\PrismMonitor\`, `dist\PrismMonitor-windows.zip` and, when Inno Setup 6 is installed (`winget install JRSoftware.InnoSetup`), `dist\PrismMonitor-Setup-<version>.exe` from `installer\PrismMonitor.iss`. Uninstalling removes the program and its launch-at-login entry but keeps your settings in `%APPDATA%\PrismMonitor`.

### Using the widget

| Action | How |
|---|---|
| Move | Drag the widget. The position is saved. Lock it from the tray menu or **Behaviour**. |
| Settings | Double-click the widget, double-click the tray icon, or choose **Settings…** from the tray menu. |
| Menu | Right-click the widget or the tray icon: show/hide, settings, lock position, click-through, always on top, sensor helper, reset position, quit. |
| Click-through | Clicks pass through the widget to the windows beneath it. Turn it off from the **tray icon menu**, which stays clickable. |
| Lost widget | Choose **Reset widget position** from the tray menu. If a monitor is unplugged or rearranged, the widget also moves itself back onto a visible screen. |

Settings are saved to `%APPDATA%\PrismMonitor\settings.json`. **Launch at login** is off by default. When enabled, it adds a per-user `HKCU\…\Run` entry and needs no administrator rights.

## Metrics and where they come from

| Metric | Source | Needs helper |
|---|---|---|
| CPU load | Windows PDH `% Processor Utility` (the Task Manager figure) | No |
| CPU clock | LibreHardwareMonitor `Cores (Average)` when the helper runs; otherwise a Task Manager-style estimate (base clock × `% Processor Performance`), labelled as an estimate in its tooltip | Measured value: yes |
| CPU temperature | LibreHardwareMonitor `Core (Tctl/Tdie)` by default; CCD max, CCD average, CCD1 or CCD2 can be chosen in **Settings → Metrics** | Yes |
| CPU package power | LibreHardwareMonitor `Package` | Yes |
| GPU temp, hotspot, load, clock, board power, fan | AMD driver ADL PMLog sensors (same sensor IDs as LibreHardwareMonitor) | No |
| VRAM used / total | ADL dedicated VRAM usage / display adapter registry size | No |
| RAM used / total | `GlobalMemoryStatusEx` | No |
| FPS, frame time | Intel PresentMon, for the selected process | No* |
| Network up/down | PDH `Network Interface` (virtual/loopback adapters excluded) | No |
| Disk active, read, write | PDH `PhysicalDisk(_Total)` | No |
| Motherboard fans | LibreHardwareMonitor Super I/O (IT8696E on this board) | Yes |

\* PresentMon needs administrator rights or membership of the Windows **Performance Log Users** group. Without either, the widget asks the running sensor helper to run PresentMon and relay its output.

**Which CPU temperature?** Ryzen 9000 CPUs report several temperatures. Tctl/Tdie is AMD's control temperature: Ryzen Master, HWiNFO and the BIOS show it as "CPU temperature", and the cooler responds to it. The CCD values are the individual core dies, and an idle die can read 30 °C cooler than Tctl. On the test PC, Tctl and the motherboard's CPU socket sensor both read 68 °C while CCD2 read 37 °C. The CPU tile shows which sensor it uses (for example "Temp · Tctl"), and hovering it lists them all.

A reading that is missing shows as *n/a*. One that has not updated for 2.5 polling intervals (at least 3 s) shows as *stale*. Neither is ever drawn as zero, and history graphs show a gap instead. Hover any value to see its source or why it is unavailable. A fan reporting 0 rpm is a real reading (stopped or unconnected header), so zero is shown for those. Unconnected headers are hidden by default; pick fans under **Settings → Metrics**.

## Game FPS

PresentMon reads the present events Windows already records through Event Tracing for Windows (ETW). Each event carries the presenting process ID and the time since that process's previous present. The widget averages the last second of `MsBetweenPresents` for the chosen process: FPS = 1000 / mean frame time. When a process has several swap chains, it uses the busiest one. The widget's own drawing and the monitor's refresh rate play no part.

**Process selection** is under **Settings → Game FPS**. **Automatic** follows the application in focus, ignoring shell processes. Alternatively, pick a process from the list of those currently presenting, or type an executable name. The widget never takes focus, so it does not interfere with automatic selection.

**Compatibility and limits:**

* Works with Direct3D 9/10/11/12, Vulkan and OpenGL games in windowed, borderless and fullscreen modes.
* No injection, no overlay hooks, and no changes to anti-cheat or Windows security settings.
* Driver-generated frames (AMD Fluid Motion Frames) are not visible to PresentMon. In-game frame generation (FSR 3, DLSS FG) is counted because the game presents those frames.
* Not measured: streamed or cloud games, and apps that do not present through DXGI/D3D/the Windows compositor.
* If the game is not in focus and no specific process is selected, FPS shows *n/a*.

## Privileges and safety

* `PrismMonitor.exe` never needs administrator rights.
* `vendor\sensorhost\PrismSensorHost.exe` is a small C# helper built from `monitor\sensorhost\PrismSensorHost.cs` against LibreHardwareMonitorLib 0.9.6:
  * It starts only when you ask, through the standard Windows prompt.
  * It only reads sensors. CPU, motherboard and GPU groups are enabled; storage, USB/HID controllers, PSU and network groups are left off so it does not poll devices other programs control.
  * It streams JSON over a randomly named pipe that only your account can open (network access denied).
  * It exits when the widget exits or disconnects.
* **One-time automatic setup** (`PrismSensorHost.exe --install`, run once through the Windows prompt):
  * It copies the helper, LibreHardwareMonitorLib and PresentMon to `C:\Program Files\Prism Monitor Sensor Helper\`, which only administrators can modify. That way other software cannot replace the binary that runs elevated.
  * It registers the Task Scheduler task **Prism Monitor Sensor Helper**. The task runs that protected copy with highest privileges for your account only. It has no triggers: it runs only when the widget starts it.
  * The widget connects only if the pipe is served by that exact Program Files executable.
  * **Settings → Sensors → Remove automatic helper…** and the uninstaller remove the task and the folder. Both need administrator approval.
  * The helper has no window. If it stops for any reason, the widget restarts it silently after 3, 10, 30, then 60 seconds.
  * After updating Prism Monitor, the protected copy must be refreshed once. The installer does this, or the widget shows a notification: click it, or choose **Update CPU sensor helper…** in the tray menu, and approve the prompt. Prompts are started from a click because Windows only flashes a taskbar button for prompts requested by background apps.
* LibreHardwareMonitor uses the **PawnIO** driver for ring-0 access. Prism Monitor never installs drivers. If PawnIO is missing, the helper still runs, but CPU temperature, power and fan values stay *n/a*. Install PawnIO yourself from <https://pawnio.eu/> if you want them.
* PresentMon runs in a kill-on-close job, so it cannot outlive the widget. Its ETW session is stopped on exit and cleared on the next start after a crash.

## Resource use

Polling defaults to 1 s and can be set from 0.25 to 10 s. All sensor I/O runs on a background thread. The widget repaints once per poll and has no animations.

## Why this stack

* **Python 3.13 + PySide6 (Qt 6.10):** already used and pinned in this repository. It provides a frameless per-pixel-translucent window, tray icon, high-DPI and multi-monitor handling, and a PyInstaller release path, with no new runtime for the user.
* **LibreHardwareMonitorLib 0.9.6** (MPL-2.0, released 2026-02-14): the maintained open-source sensor library. It supports Ryzen 9000 (Zen 5) Tctl/CCD temperatures, RAPL package power and ITE IT8696E Super I/O, and uses the signed PawnIO driver instead of the deprecated WinRing0. Its .NET Framework 4.7.2 build runs on the .NET Framework that ships with Windows 11, so the helper is compiled with Windows' built-in C# compiler and needs no .NET SDK or runtime install.
* **Intel PresentMon 2.6.0** (MIT, released 2026-09-21): the reference ETW frame-timing tool and the basis of most FPS overlays. It works without injection.
* **AMD ADL** (in the installed Radeon driver): GPU sensors without administrator rights.

## Verified on the development machine (Ryzen 9 9900X, RX 9070 XT, B850 AORUS ELITE WIFI7 ICE)

Verified with the release build: CPU Tctl/Tdie temperature, package power, measured core clocks and load; GPU edge and hotspot temperature, load, clock, board power, fan and VRAM; RAM; network; disk; motherboard fans #1, #2 and #5 (IT8696E). FPS was checked against a Direct3D 11 test window set to 30, 40 and 48 fps (`tools/fps_test_presenter.py`) on a 60 Hz display: 30.5, 40.0 and 40.0 were measured, while other apps presented at 60. Both the direct PresentMon path and the helper-relay path were tested.

The one-time automatic setup was tested on this PC: one prompt, then the packaged widget started the helper with no prompt and read CPU temperature and power. Removing the automatic helper (`--uninstall`) has not been run here.

Not verified here: FPS in an actual game (no game was run), PresentMon without Performance Log Users membership (this account is a member, so the relay path was forced for testing), the secondary GIGABYTE IT87952E chip (LibreHardwareMonitor does not expose it on this board), and behaviour after physically unplugging a monitor (the on-screen recovery logic is in place but untested).
