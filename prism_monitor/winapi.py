# SPDX-License-Identifier: GPL-3.0-only
"""Small ctypes wrappers for the unprivileged Windows APIs Prism Monitor uses.

Everything here works as a standard user: PDH performance counters,
GlobalMemoryStatusEx, the registry, process names and ShellExecuteEx (the
"runas" verb asks Windows to show its own consent prompt; nothing is elevated
silently).
"""
import ctypes
import os
import sys
from ctypes import wintypes

WINDOWS = sys.platform == 'win32'
LINUX = sys.platform.startswith('linux')
if LINUX:
    from . import linuxapi

if WINDOWS:
    kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
    user32 = ctypes.WinDLL('user32', use_last_error=True)
    advapi32 = ctypes.WinDLL('advapi32', use_last_error=True)
    shell32 = ctypes.WinDLL('shell32', use_last_error=True)
    try:
        pdh = ctypes.WinDLL('pdh')
    except OSError:  # pragma: no cover - PDH ships with every Windows
        pdh = None

PDH_FMT_DOUBLE = 0x00000200
PDH_FMT_NOCAP100 = 0x00008000
PDH_MORE_DATA = 0x800007D2
PDH_CSTATUS_VALID_DATA = 0x0
PDH_CSTATUS_NEW_DATA = 0x1


class _PdhValue(ctypes.Structure):
    _fields_ = [('CStatus', wintypes.DWORD), ('doubleValue', ctypes.c_double)]


class _PdhItem(ctypes.Structure):
    _fields_ = [('szName', wintypes.LPWSTR), ('FmtValue', _PdhValue)]


class Pdh:
    """One PDH query with English counter paths. Values are None until valid."""

    def __init__(self):
        if not WINDOWS or pdh is None:
            raise OSError('PDH is only available on Windows.')
        self.query = wintypes.HANDLE()
        status = pdh.PdhOpenQueryW(None, 0, ctypes.byref(self.query))
        if status:
            raise OSError(f'PdhOpenQuery failed (0x{status & 0xffffffff:08x})')
        self.counters = {}

    def add(self, key, path):
        handle = wintypes.HANDLE()
        status = pdh.PdhAddEnglishCounterW(self.query, path, 0, ctypes.byref(handle))
        if status:
            return False
        self.counters[key] = handle
        return True

    def collect(self):
        return pdh.PdhCollectQueryData(self.query) == 0

    def value(self, key, nocap=False):
        handle = self.counters.get(key)
        if handle is None:
            return None
        out = _PdhValue()
        flags = PDH_FMT_DOUBLE | (PDH_FMT_NOCAP100 if nocap else 0)
        status = pdh.PdhGetFormattedCounterValue(handle, flags, None, ctypes.byref(out))
        if status or out.CStatus not in (PDH_CSTATUS_VALID_DATA, PDH_CSTATUS_NEW_DATA):
            return None
        return out.doubleValue

    def array(self, key):
        """{instance: value} for a wildcard counter, or None if not valid yet."""
        handle = self.counters.get(key)
        if handle is None:
            return None
        size, count = wintypes.DWORD(0), wintypes.DWORD(0)
        status = pdh.PdhGetFormattedCounterArrayW(handle, PDH_FMT_DOUBLE, ctypes.byref(size), ctypes.byref(count), None)
        if (status & 0xffffffff) != PDH_MORE_DATA:
            return None
        buffer = ctypes.create_string_buffer(size.value)
        status = pdh.PdhGetFormattedCounterArrayW(handle, PDH_FMT_DOUBLE, ctypes.byref(size), ctypes.byref(count), buffer)
        if status:
            return None
        items = ctypes.cast(buffer, ctypes.POINTER(_PdhItem))
        result = {}
        for i in range(count.value):
            item = items[i]
            if item.FmtValue.CStatus in (PDH_CSTATUS_VALID_DATA, PDH_CSTATUS_NEW_DATA):
                result[item.szName] = item.FmtValue.doubleValue
        return result

    def close(self):
        if self.query:
            pdh.PdhCloseQuery(self.query)
            self.query = None


class _MemoryStatus(ctypes.Structure):
    _fields_ = [('dwLength', wintypes.DWORD), ('dwMemoryLoad', wintypes.DWORD),
                ('ullTotalPhys', ctypes.c_ulonglong), ('ullAvailPhys', ctypes.c_ulonglong),
                ('ullTotalPageFile', ctypes.c_ulonglong), ('ullAvailPageFile', ctypes.c_ulonglong),
                ('ullTotalVirtual', ctypes.c_ulonglong), ('ullAvailVirtual', ctypes.c_ulonglong),
                ('ullAvailExtendedVirtual', ctypes.c_ulonglong)]


def memory_status():
    """(used_bytes, total_bytes) of physical memory, or None."""
    status = _MemoryStatus()
    status.dwLength = ctypes.sizeof(status)
    if not kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
        return None
    return status.ullTotalPhys - status.ullAvailPhys, status.ullTotalPhys


def page_file_size():
    """Total page file bytes (commit limit minus physical memory, as Task Manager derives it), or None."""
    status = _MemoryStatus()
    status.dwLength = ctypes.sizeof(status)
    if not kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
        return None
    return max(0, status.ullTotalPageFile - status.ullTotalPhys) or None


def read_registry(root, path, name):
    import winreg
    try:
        with winreg.OpenKey(root, path) as key:
            return winreg.QueryValueEx(key, name)[0]
    except OSError:
        return None


def cpu_name_and_base_mhz():
    import winreg
    path = r'HARDWARE\DESCRIPTION\System\CentralProcessor\0'
    name = read_registry(winreg.HKEY_LOCAL_MACHINE, path, 'ProcessorNameString')
    mhz = read_registry(winreg.HKEY_LOCAL_MACHINE, path, '~MHz')
    return (name or '').strip() or None, (int(mhz) if mhz else None)


def gpu_memory_bytes(adapter_name):
    """Dedicated VRAM size from the display-class registry key (unprivileged)."""
    import winreg
    base = r'SYSTEM\CurrentControlSet\Control\Class\{4d36e968-e325-11ce-bfc1-08002be10318}'
    try:
        root = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, base)
    except OSError:
        return None
    with root:
        for i in range(64):
            try:
                sub = winreg.EnumKey(root, i)
            except OSError:
                break
            if not sub.isdigit():
                continue
            try:
                with winreg.OpenKey(root, sub) as key:
                    desc = winreg.QueryValueEx(key, 'DriverDesc')[0]
                    if desc.strip() != adapter_name.strip():
                        continue
                    size = winreg.QueryValueEx(key, 'HardwareInformation.qwMemorySize')[0]
                    if isinstance(size, bytes):
                        size = int.from_bytes(size, 'little')
                    return int(size) or None
            except OSError:
                continue
    return None


PROCESS_QUERY_LIMITED_INFORMATION = 0x1000


def process_image_name(pid):
    """Executable file name for a PID, or None (no special rights needed for own-session processes)."""
    if LINUX and pid:
        return linuxapi.process_name(pid)
    if not WINDOWS or not pid:
        return None
    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid))
    if not handle:
        return None
    try:
        buffer = ctypes.create_unicode_buffer(1024)
        size = wintypes.DWORD(len(buffer))
        if not kernel32.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(size)):
            return None
        return os.path.basename(buffer.value)
    finally:
        kernel32.CloseHandle(handle)


def foreground_pid():
    if LINUX:
        return linuxapi.foreground_pid()
    if not WINDOWS:
        return None
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(user32.GetForegroundWindow(), ctypes.byref(pid))
    return pid.value or None


def is_elevated():
    if not WINDOWS:
        return False
    try:
        return bool(shell32.IsUserAnAdmin())
    except Exception:
        return False


def current_user_sid():
    """String SID of the user running this process."""
    TOKEN_QUERY, TokenUser = 0x0008, 1
    token = wintypes.HANDLE()
    if not advapi32.OpenProcessToken(kernel32.GetCurrentProcess(), TOKEN_QUERY, ctypes.byref(token)):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        size = wintypes.DWORD()
        advapi32.GetTokenInformation(token, TokenUser, None, 0, ctypes.byref(size))
        buffer = ctypes.create_string_buffer(size.value)
        if not advapi32.GetTokenInformation(token, TokenUser, buffer, size, ctypes.byref(size)):
            raise ctypes.WinError(ctypes.get_last_error())
        sid_pointer = ctypes.cast(buffer, ctypes.POINTER(ctypes.c_void_p))[0]
        string = wintypes.LPWSTR()
        if not advapi32.ConvertSidToStringSidW(ctypes.c_void_p(sid_pointer), ctypes.byref(string)):
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            return string.value
        finally:
            kernel32.LocalFree(string)
    finally:
        kernel32.CloseHandle(token)


class _ShellExecuteInfo(ctypes.Structure):
    _fields_ = [('cbSize', wintypes.DWORD), ('fMask', ctypes.c_ulong), ('hwnd', wintypes.HWND),
                ('lpVerb', wintypes.LPCWSTR), ('lpFile', wintypes.LPCWSTR), ('lpParameters', wintypes.LPCWSTR),
                ('lpDirectory', wintypes.LPCWSTR), ('nShow', ctypes.c_int), ('hInstApp', wintypes.HINSTANCE),
                ('lpIDList', ctypes.c_void_p), ('lpClass', wintypes.LPCWSTR), ('hkeyClass', wintypes.HKEY),
                ('dwHotKey', wintypes.DWORD), ('hIconOrMonitor', wintypes.HANDLE), ('hProcess', wintypes.HANDLE)]


ERROR_CANCELLED = 1223


def run_elevated(executable, parameters, directory=None, hwnd=None):
    """Start a process through Windows' consent (UAC) prompt.

    Blocks until the user answers. Returns a process handle; raises
    PermissionError if the prompt was declined.
    """
    SEE_MASK_NOCLOSEPROCESS, SEE_MASK_NOASYNC, SW_HIDE = 0x40, 0x100, 0
    info = _ShellExecuteInfo()
    info.cbSize = ctypes.sizeof(info)
    info.fMask = SEE_MASK_NOCLOSEPROCESS | SEE_MASK_NOASYNC
    info.lpVerb = 'runas'
    info.lpFile = str(executable)
    info.lpParameters = parameters
    info.lpDirectory = str(directory) if directory else None
    info.nShow = SW_HIDE
    # Owning the prompt with one of our windows lets Windows bring it to the front
    # instead of only flashing a taskbar button (the widget itself never takes focus).
    info.hwnd = hwnd
    if not shell32.ShellExecuteExW(ctypes.byref(info)):
        error = ctypes.get_last_error()
        if error == ERROR_CANCELLED:
            raise PermissionError('The Windows administrator prompt was declined.')
        raise ctypes.WinError(error)
    return info.hProcess


def process_exited(handle):
    if not handle:
        return True
    return kernel32.WaitForSingleObject(handle, 0) == 0


def close_handle(handle):
    if handle:
        kernel32.CloseHandle(handle)


def service_running(name):
    """True/False for an installed service, None if it is not installed."""
    SC_MANAGER_CONNECT, SERVICE_QUERY_STATUS = 0x0001, 0x0004

    class Status(ctypes.Structure):
        _fields_ = [('type', wintypes.DWORD), ('state', wintypes.DWORD), ('accepted', wintypes.DWORD),
                    ('exit', wintypes.DWORD), ('specific', wintypes.DWORD), ('checkpoint', wintypes.DWORD),
                    ('wait', wintypes.DWORD)]
    manager = advapi32.OpenSCManagerW(None, None, SC_MANAGER_CONNECT)
    if not manager:
        return None
    try:
        service = advapi32.OpenServiceW(manager, name, SERVICE_QUERY_STATUS)
        if not service:
            return None
        try:
            status = Status()
            if not advapi32.QueryServiceStatus(service, ctypes.byref(status)):
                return None
            return status.state == 4  # SERVICE_RUNNING
        finally:
            advapi32.CloseServiceHandle(service)
    finally:
        advapi32.CloseServiceHandle(manager)


if WINDOWS:
    advapi32.OpenSCManagerW.restype = wintypes.HANDLE
    advapi32.OpenServiceW.restype = wintypes.HANDLE
    advapi32.OpenSCManagerW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD]
    advapi32.OpenServiceW.argtypes = [wintypes.HANDLE, wintypes.LPCWSTR, wintypes.DWORD]
    advapi32.CloseServiceHandle.argtypes = [wintypes.HANDLE]
    advapi32.QueryServiceStatus.argtypes = [wintypes.HANDLE, ctypes.c_void_p]
    advapi32.OpenProcessToken.argtypes = [wintypes.HANDLE, wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE)]
    advapi32.GetTokenInformation.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD,
                                             ctypes.POINTER(wintypes.DWORD)]
    advapi32.ConvertSidToStringSidW.argtypes = [ctypes.c_void_p, ctypes.POINTER(wintypes.LPWSTR)]
    kernel32.GetCurrentProcess.restype = wintypes.HANDLE
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR,
                                                    ctypes.POINTER(wintypes.DWORD)]
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    kernel32.GlobalMemoryStatusEx.argtypes = [ctypes.c_void_p]
    user32.GetForegroundWindow.restype = wintypes.HWND
    user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    shell32.ShellExecuteExW.argtypes = [ctypes.POINTER(_ShellExecuteInfo)]
    if pdh is not None:
        pdh.PdhOpenQueryW.argtypes = [wintypes.LPCWSTR, ctypes.c_size_t, ctypes.POINTER(wintypes.HANDLE)]
        pdh.PdhAddEnglishCounterW.argtypes = [wintypes.HANDLE, wintypes.LPCWSTR, ctypes.c_size_t,
                                              ctypes.POINTER(wintypes.HANDLE)]
        pdh.PdhCollectQueryData.argtypes = [wintypes.HANDLE]
        pdh.PdhGetFormattedCounterValue.argtypes = [wintypes.HANDLE, wintypes.DWORD, ctypes.c_void_p,
                                                    ctypes.POINTER(_PdhValue)]
        pdh.PdhGetFormattedCounterArrayW.argtypes = [wintypes.HANDLE, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD),
                                                     ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p]
        pdh.PdhCloseQuery.argtypes = [wintypes.HANDLE]
        for fn in (pdh.PdhOpenQueryW, pdh.PdhAddEnglishCounterW, pdh.PdhCollectQueryData,
                   pdh.PdhGetFormattedCounterValue, pdh.PdhGetFormattedCounterArrayW, pdh.PdhCloseQuery):
            fn.restype = wintypes.DWORD


def stop_trace_session(name):
    """Stop an ETW session by name (ControlTraceW, EVENT_TRACE_CONTROL_STOP).

    PresentMon cannot close its trace session when it is terminated rather
    than stopped with Ctrl+C, and an orphaned session keeps the next capture
    with the same name from receiving events. Returns the Win32 status.
    """
    if not WINDOWS:
        return 0
    properties_size = 120          # sizeof(EVENT_TRACE_PROPERTIES) on x64
    buffer = ctypes.create_string_buffer(properties_size + 2 * 1024 * 2)
    ctypes.c_ulong.from_buffer(buffer, 0).value = len(buffer)          # Wnode.BufferSize
    ctypes.c_ulong.from_buffer(buffer, 116).value = properties_size    # LoggerNameOffset
    ctypes.c_ulong.from_buffer(buffer, 112).value = properties_size + 2048  # LogFileNameOffset
    advapi32.ControlTraceW.restype = wintypes.ULONG
    advapi32.ControlTraceW.argtypes = [ctypes.c_uint64, wintypes.LPCWSTR, ctypes.c_void_p, wintypes.ULONG]
    return advapi32.ControlTraceW(0, name, buffer, 1)


_job = None


def tie_to_this_process(pid):
    """Put a child process in a kill-on-close job so it ends with this process, even after a crash."""
    global _job
    if not WINDOWS:
        return False

    class Basic(ctypes.Structure):
        _fields_ = [('PerProcessUserTimeLimit', ctypes.c_int64), ('PerJobUserTimeLimit', ctypes.c_int64),
                    ('LimitFlags', wintypes.DWORD), ('MinimumWorkingSetSize', ctypes.c_size_t),
                    ('MaximumWorkingSetSize', ctypes.c_size_t), ('ActiveProcessLimit', wintypes.DWORD),
                    ('Affinity', ctypes.c_size_t), ('PriorityClass', wintypes.DWORD), ('SchedulingClass', wintypes.DWORD)]

    class Extended(ctypes.Structure):
        _fields_ = [('Basic', Basic), ('Io', ctypes.c_uint64 * 6), ('ProcessMemoryLimit', ctypes.c_size_t),
                    ('JobMemoryLimit', ctypes.c_size_t), ('PeakProcessMemoryUsed', ctypes.c_size_t),
                    ('PeakJobMemoryUsed', ctypes.c_size_t)]
    kernel32.CreateJobObjectW.restype = wintypes.HANDLE
    kernel32.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
    kernel32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    if _job is None:
        job = kernel32.CreateJobObjectW(None, None)
        if not job:
            return False
        info = Extended()
        info.Basic.LimitFlags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not kernel32.SetInformationJobObject(job, 9, ctypes.byref(info), ctypes.sizeof(info)):
            kernel32.CloseHandle(job)
            return False
        _job = job
    PROCESS_SET_QUOTA, PROCESS_TERMINATE = 0x0100, 0x0001
    handle = kernel32.OpenProcess(PROCESS_SET_QUOTA | PROCESS_TERMINATE, False, int(pid))
    if not handle:
        return False
    try:
        return bool(kernel32.AssignProcessToJobObject(_job, handle))
    finally:
        kernel32.CloseHandle(handle)


def wait_exit_code(handle, timeout_ms):
    """Wait for a process handle; returns its exit code, or None on timeout."""
    if kernel32.WaitForSingleObject(handle, int(timeout_ms)) != 0:
        return None
    code = wintypes.DWORD()
    kernel32.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    if not kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
        return None
    return code.value


def pipe_server_image(pipe_handle):
    """Full executable path of the process serving a connected named pipe, or None."""
    pid = wintypes.ULONG()
    kernel32.GetNamedPipeServerProcessId.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.ULONG)]
    if not kernel32.GetNamedPipeServerProcessId(wintypes.HANDLE(pipe_handle), ctypes.byref(pid)):
        return None
    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid.value)
    if not handle:
        return None
    try:
        buffer = ctypes.create_unicode_buffer(1024)
        size = wintypes.DWORD(len(buffer))
        if not kernel32.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(size)):
            return None
        return buffer.value
    finally:
        kernel32.CloseHandle(handle)


# -- keep awake / power ---------------------------------------------------------
ES_CONTINUOUS = 0x80000000
ES_SYSTEM_REQUIRED = 0x00000001
ES_DISPLAY_REQUIRED = 0x00000002
ES_AWAYMODE_REQUIRED = 0x00000040


def set_execution_state(system, display=False):
    """Hold the PC (and optionally the display) awake from the calling thread, or release it.

    Same mechanism as macOS `caffeinate -i/-d`: an assertion Windows drops when this
    thread or process ends, so a crash can never leave the PC unable to sleep.
    """
    if LINUX:
        return linuxapi.set_execution_state(system, display)
    if not WINDOWS:
        return False
    flags = ES_CONTINUOUS
    if system:
        flags |= ES_SYSTEM_REQUIRED | (ES_DISPLAY_REQUIRED if display else 0)
    kernel32.SetThreadExecutionState.restype = wintypes.DWORD
    kernel32.SetThreadExecutionState.argtypes = [wintypes.DWORD]
    return bool(kernel32.SetThreadExecutionState(flags))


class _ProcessEntry(ctypes.Structure):
    _fields_ = [('dwSize', wintypes.DWORD), ('cntUsage', wintypes.DWORD), ('th32ProcessID', wintypes.DWORD),
                ('th32DefaultHeapID', ctypes.c_size_t), ('th32ModuleID', wintypes.DWORD),
                ('cntThreads', wintypes.DWORD), ('th32ParentProcessID', wintypes.DWORD),
                ('pcPriClassBase', ctypes.c_long), ('dwFlags', wintypes.DWORD), ('szExeFile', ctypes.c_wchar * 260)]


TH32CS_SNAPPROCESS = 0x2
INVALID_HANDLE = ctypes.c_void_p(-1).value


def running_processes():
    """{pid: exe name} for every process (names only; no special rights needed)."""
    if LINUX:
        return linuxapi.running_processes()
    if not WINDOWS:
        return {}
    kernel32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    snap = kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    if not snap or snap == INVALID_HANDLE:
        return {}
    out = {}
    try:
        entry = _ProcessEntry()
        entry.dwSize = ctypes.sizeof(entry)
        ok = kernel32.Process32FirstW(snap, ctypes.byref(entry))
        while ok:
            out[entry.th32ProcessID] = entry.szExeFile
            ok = kernel32.Process32NextW(snap, ctypes.byref(entry))
    finally:
        kernel32.CloseHandle(snap)
    return out


def windowed_pids():
    """PIDs owning a visible, titled top-level window: the apps a user thinks of as "open"."""
    if LINUX:
        return linuxapi.windowed_pids()
    if not WINDOWS:
        return set()
    pids = set()
    proc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    def visit(hwnd, _):
        if user32.IsWindowVisible(hwnd) and user32.GetWindowTextLengthW(hwnd) > 0 and not user32.GetWindow(hwnd, 4):
            pid = wintypes.DWORD()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            pids.add(pid.value)
        return True
    user32.EnumWindows(proc(visit), 0)
    return pids


def downloads_folder():
    """The user's Downloads folder (honours a relocated folder)."""
    path = None
    if LINUX:
        return linuxapi.downloads_folder()
    if WINDOWS:
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                                r'Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders') as key:
                path = os.path.expandvars(winreg.QueryValueEx(key, '{374DE290-123F-4565-9164-39C4925E467B}')[0])
        except OSError:
            path = None
    return path or os.path.join(os.path.expanduser('~'), 'Downloads')


def sleep_now(hibernate=False):
    """Sleep or hibernate (no administrator rights needed)."""
    if LINUX:
        return linuxapi.sleep_now(hibernate)
    if not WINDOWS:
        return False
    powrprof = ctypes.WinDLL('powrprof')
    return bool(powrprof.SetSuspendState(bool(hibernate), False, False))


def lock_now():
    if LINUX:
        return linuxapi.lock_now()
    return bool(WINDOWS and user32.LockWorkStation())


def shutdown_now():
    """Normal Windows shutdown (apps may still ask to save their work)."""
    if LINUX:
        return linuxapi.shutdown_now()
    if not WINDOWS:
        return False
    import subprocess
    exe = os.path.join(os.environ.get('SystemRoot', r'C:\Windows'), 'System32', 'shutdown.exe')
    subprocess.Popen([exe, '/s', '/t', '0'], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                     stderr=subprocess.DEVNULL, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    return True


WM_WINDOWPOSCHANGING = 0x0046
SWP_NOZORDER = 0x0004
HWND_BOTTOM = 1


def keep_at_bottom(message):
    """In a native message hook: rewrite a z-order change of this window to HWND_BOTTOM."""
    import ctypes
    from ctypes import wintypes

    class MSG(ctypes.Structure):
        _fields_ = [('hwnd', wintypes.HWND), ('message', wintypes.UINT), ('wParam', wintypes.WPARAM),
                    ('lParam', wintypes.LPARAM), ('time', wintypes.DWORD), ('pt', wintypes.POINT)]

    class WINDOWPOS(ctypes.Structure):
        _fields_ = [('hwnd', wintypes.HWND), ('hwndInsertAfter', wintypes.HWND), ('x', ctypes.c_int),
                    ('y', ctypes.c_int), ('cx', ctypes.c_int), ('cy', ctypes.c_int), ('flags', wintypes.UINT)]

    try:
        msg = MSG.from_address(int(message))
        if msg.message != WM_WINDOWPOSCHANGING or not msg.lParam:
            return
        pos = WINDOWPOS.from_address(msg.lParam)
        if not pos.flags & SWP_NOZORDER:
            pos.hwndInsertAfter = HWND_BOTTOM
    except (ValueError, OSError):
        pass
