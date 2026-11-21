// SPDX-License-Identifier: GPL-3.0-only
// Prism Monitor sensor helper. The only part of Prism Monitor that runs with
// administrator rights. It reads CPU and motherboard sensors through
// LibreHardwareMonitorLib (MPL-2.0, which uses the PawnIO driver) and streams
// them as JSON lines over a named pipe that only the requesting user can open.
// It never writes to hardware, never installs drivers and exits when the
// widget exits or disconnects.
//
// Usage: PrismSensorHost.exe --pipe <name> --sid <user SID> [--parent <pid>]
//        PrismSensorHost.exe --dump      (print one JSON snapshot and exit)
//        PrismSensorHost.exe --install --sid <user SID>   (elevated, one time)
//        PrismSensorHost.exe --uninstall                  (elevated)
//
// --install copies this helper, LibreHardwareMonitorLib and PresentMon into
// %ProgramFiles%\Prism Monitor Sensor Helper (writable only by
// administrators) and registers an on-demand Task Scheduler task that runs
// that protected copy with highest privileges for the given user. The widget
// then starts the task without a consent prompt. The task has no triggers:
// it runs only when the widget asks, and the helper exits when the widget
// disconnects.
using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Globalization;
using System.IO;
using System.IO.Pipes;
using System.Security.AccessControl;
using System.Security.Principal;
using System.Text;
using System.Threading;
using LibreHardwareMonitor.Hardware;

internal static class PrismSensorHost
{
    private static volatile int intervalMs = 1000;
    private static volatile bool running = true;
    private static readonly object writeLock = new object();
    private static StreamWriter writer;
    private static Process presentMon;

    private static int Main(string[] args)
    {
        string pipe = null, sid = null;
        int parent = 0;
        bool dump = false, install = false, uninstall = false;
        for (int i = 0; i < args.Length; i++)
        {
            if (args[i] == "--install") install = true;
            else if (args[i] == "--uninstall") uninstall = true;
            else
            if (args[i] == "--pipe" && i + 1 < args.Length) pipe = args[++i];
            else if (args[i] == "--sid" && i + 1 < args.Length) sid = args[++i];
            else if (args[i] == "--parent" && i + 1 < args.Length) int.TryParse(args[++i], out parent);
            else if (args[i] == "--dump") dump = true;
        }
        if (install || uninstall)
        {
            try
            {
                if (uninstall) return Uninstall();
                if (string.IsNullOrEmpty(sid)) return 64;
                new SecurityIdentifier(sid);   // validates the SID format
                return Install(sid);
            }
            catch (Exception e)
            {
                Console.Error.WriteLine(e.Message);
                return 5;
            }
        }
        Computer computer = new Computer();
        computer.IsCpuEnabled = true;
        computer.IsMotherboardEnabled = true;
        computer.IsGpuEnabled = true;
        // Storage, controllers, PSU, network and memory stay disabled: they are
        // not needed and some poll USB/HID devices other programs may control.
        try
        {
            computer.Open();
        }
        catch (Exception e)
        {
            Console.Error.WriteLine("open failed: " + e.Message);
            return 2;
        }
        try
        {
            if (dump)
            {
                Console.WriteLine(Snapshot(computer));
                return 0;
            }
            if (string.IsNullOrEmpty(pipe) || string.IsNullOrEmpty(sid)) return 64;
            if (pipe.IndexOfAny(new[] { '\\', '/', ':' }) >= 0 || pipe.Length > 100) return 64;
            return Serve(computer, pipe, sid, parent);
        }
        finally
        {
            computer.Close();
        }
    }

    private static int Serve(Computer computer, string pipeName, string sid, int parentPid)
    {
        Process parent = null;
        if (parentPid > 0)
        {
            try { parent = Process.GetProcessById(parentPid); } catch (ArgumentException) { return 3; }
        }
        PipeSecurity security = new PipeSecurity();
        SecurityIdentifier user = new SecurityIdentifier(sid);
        security.AddAccessRule(new PipeAccessRule(user, PipeAccessRights.ReadWrite, AccessControlType.Allow));
        security.AddAccessRule(new PipeAccessRule(WindowsIdentity.GetCurrent().User, PipeAccessRights.FullControl, AccessControlType.Allow));
        security.AddAccessRule(new PipeAccessRule(new SecurityIdentifier(WellKnownSidType.NetworkSid, null), PipeAccessRights.FullControl, AccessControlType.Deny));
        using (NamedPipeServerStream server = new NamedPipeServerStream(pipeName, PipeDirection.InOut, 1,
            PipeTransmissionMode.Byte, PipeOptions.Asynchronous, 4096, 65536, security))
        {
            IAsyncResult wait = server.BeginWaitForConnection(null, null);
            DateTime deadline = DateTime.UtcNow.AddSeconds(60);
            while (!wait.AsyncWaitHandle.WaitOne(500))
            {
                if (DateTime.UtcNow > deadline || (parent != null && parent.HasExited)) return 4;
            }
            server.EndWaitForConnection(wait);
            writer = new StreamWriter(server, new UTF8Encoding(false));
            writer.AutoFlush = true;
            Thread reader = new Thread(delegate () { ReadCommands(server); });
            reader.IsBackground = true;
            reader.Start();
            Send("{\"t\":\"hello\",\"version\":1,\"lhm\":\"" +
                typeof(Computer).Assembly.GetName().Version + "\",\"elevated\":" +
                (IsElevated() ? "true" : "false") + "}");
            while (running && server.IsConnected)
            {
                if (parent != null && parent.HasExited) break;
                if (!Send(Snapshot(computer))) break;
                int slept = 0;
                while (running && slept < intervalMs)
                {
                    Thread.Sleep(100);
                    slept += 100;
                }
            }
        }
        StopPresentMon();
        return 0;
    }

    internal const string TaskName = "Prism Monitor Sensor Helper";

    private static string InstallRoot()
    {
        return Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.ProgramFiles), "Prism Monitor Sensor Helper");
    }

    private static int RunTool(string exe, string arguments)
    {
        ProcessStartInfo info = new ProcessStartInfo(Path.Combine(Environment.SystemDirectory, exe), arguments);
        info.UseShellExecute = false;
        info.CreateNoWindow = true;
        info.RedirectStandardOutput = true;
        info.RedirectStandardError = true;
        using (Process p = Process.Start(info))
        {
            string output = p.StandardOutput.ReadToEnd() + p.StandardError.ReadToEnd();
            p.WaitForExit(30000);
            if (p.ExitCode != 0) Console.Error.WriteLine(output.Trim());
            return p.ExitCode;
        }
    }

    private static void StopInstalledHelpers(string root)
    {
        RunTool("schtasks.exe", "/End /TN \"" + TaskName + "\"");
        foreach (Process p in Process.GetProcessesByName("PrismSensorHost"))
        {
            try
            {
                if (p.Id != Process.GetCurrentProcess().Id &&
                    p.MainModule.FileName.StartsWith(root, StringComparison.OrdinalIgnoreCase))
                {
                    p.Kill();
                    p.WaitForExit(5000);
                }
            }
            catch (Exception) { }
        }
    }

    private static string Xml(string s)
    {
        return System.Security.SecurityElement.Escape(s);
    }

    private static int Install(string sid)
    {
        string source = Path.GetDirectoryName(typeof(PrismSensorHost).Assembly.Location);
        string root = InstallRoot();
        string hostDir = Path.Combine(root, "sensorhost");
        if (source.StartsWith(root, StringComparison.OrdinalIgnoreCase))
            throw new InvalidOperationException("Run --install from the Prism Monitor folder, not the installed copy.");
        string presentMon = Path.GetFullPath(Path.Combine(source, Path.Combine("..", Path.Combine("presentmon", "PresentMon.exe"))));
        StopInstalledHelpers(root);
        Directory.CreateDirectory(hostDir);
        foreach (string file in Directory.GetFiles(source))
        {
            string ext = Path.GetExtension(file).ToLowerInvariant();
            if (ext == ".dll" || ext == ".exe" || ext == ".config" || ext == ".txt")
                File.Copy(file, Path.Combine(hostDir, Path.GetFileName(file)), true);
        }
        if (File.Exists(presentMon))
        {
            Directory.CreateDirectory(Path.Combine(root, "presentmon"));
            File.Copy(presentMon, Path.Combine(root, Path.Combine("presentmon", "PresentMon.exe")), true);
        }
        string exe = Path.Combine(hostDir, "PrismSensorHost.exe");
        string pipe = "PrismMonitorHelper-" + sid;
        string xml =
            "<?xml version=\"1.0\" encoding=\"UTF-16\"?>\r\n" +
            "<Task version=\"1.2\" xmlns=\"http://schemas.microsoft.com/windows/2004/02/mit/task\">\r\n" +
            "  <RegistrationInfo><Description>Reads CPU and motherboard sensors for Prism Monitor (read-only). " +
            "Started on demand by the widget; exits when the widget closes.</Description></RegistrationInfo>\r\n" +
            "  <Principals><Principal id=\"Author\"><UserId>" + Xml(sid) + "</UserId><LogonType>InteractiveToken</LogonType>" +
            "<RunLevel>HighestAvailable</RunLevel></Principal></Principals>\r\n" +
            "  <Settings><MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>" +
            "<DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries><StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>" +
            "<AllowHardTerminate>true</AllowHardTerminate><StartWhenAvailable>false</StartWhenAvailable>" +
            "<AllowStartOnDemand>true</AllowStartOnDemand><Enabled>true</Enabled><Hidden>false</Hidden>" +
            "<ExecutionTimeLimit>PT0S</ExecutionTimeLimit><Priority>7</Priority></Settings>\r\n" +
            "  <Actions Context=\"Author\"><Exec><Command>" + Xml(exe) + "</Command>" +
            "<Arguments>--pipe " + Xml(pipe) + " --sid " + Xml(sid) + "</Arguments>" +
            "<WorkingDirectory>" + Xml(hostDir) + "</WorkingDirectory></Exec></Actions>\r\n" +
            "</Task>\r\n";
        string temp = Path.Combine(Path.GetTempPath(), "PrismMonitorHelperTask-" + Guid.NewGuid().ToString("N") + ".xml");
        File.WriteAllText(temp, xml, Encoding.Unicode);
        try
        {
            return RunTool("schtasks.exe", "/Create /TN \"" + TaskName + "\" /XML \"" + temp + "\" /F") == 0 ? 0 : 6;
        }
        finally
        {
            File.Delete(temp);
        }
    }

    private static int Uninstall()
    {
        string root = InstallRoot();
        StopInstalledHelpers(root);
        RunTool("schtasks.exe", "/Delete /TN \"" + TaskName + "\" /F");
        if (Directory.Exists(root)) Directory.Delete(root, true);
        return 0;
    }

    private static bool Send(string line)
    {
        lock (writeLock)
        {
            try
            {
                writer.WriteLine(line);
                return true;
            }
            catch (IOException) { return false; }
            catch (ObjectDisposedException) { return false; }
        }
    }

    // FPS relay for accounts that cannot open an ETW session themselves.
    // Runs only the PresentMon build bundled next to this helper, with fixed
    // arguments, and forwards its CSV lines unchanged.
    private static void StartPresentMon()
    {
        if (presentMon != null && !presentMon.HasExited) return;
        StopPresentMon();
        string dir = Path.GetDirectoryName(typeof(PrismSensorHost).Assembly.Location);
        string exe = Path.GetFullPath(Path.Combine(dir, Path.Combine("..", Path.Combine("presentmon", "PresentMon.exe"))));
        if (!File.Exists(exe))
        {
            Send("{\"t\":\"pmerror\",\"msg\":\"PresentMon.exe is missing\"}");
            return;
        }
        ProcessStartInfo info = new ProcessStartInfo(exe,
            "--output_stdout --no_console_stats --v1_metrics --session_name PrismMonitorFPSHost --stop_existing_session " +
            "--no_track_input --no_track_gpu --no_track_display --exclude dwm.exe");
        info.UseShellExecute = false;
        info.RedirectStandardOutput = true;
        info.RedirectStandardError = true;
        info.CreateNoWindow = true;
        Process p = new Process();
        p.StartInfo = info;
        p.OutputDataReceived += delegate (object sender, DataReceivedEventArgs e)
        {
            if (e.Data != null) Send("{\"t\":\"pm\",\"l\":" + Quote(e.Data) + "}");
        };
        p.ErrorDataReceived += delegate (object sender, DataReceivedEventArgs e) { };
        p.EnableRaisingEvents = true;
        p.Exited += delegate (object sender, EventArgs e) { Send("{\"t\":\"pmexit\"}"); };
        p.Start();
        p.BeginOutputReadLine();
        p.BeginErrorReadLine();
        presentMon = p;
    }

    private static void StopPresentMon()
    {
        Process p = presentMon;
        presentMon = null;
        if (p == null) return;
        try { if (!p.HasExited) { p.Kill(); p.WaitForExit(3000); } } catch (Exception) { }
        // A killed PresentMon cannot close its ETW session; stop it so the next start receives events.
        try
        {
            ProcessStartInfo stop = new ProcessStartInfo(Path.Combine(Environment.SystemDirectory, "logman.exe"),
                "stop PrismMonitorFPSHost -ets");
            stop.UseShellExecute = false;
            stop.CreateNoWindow = true;
            using (Process q = Process.Start(stop)) { q.WaitForExit(5000); }
        }
        catch (Exception) { }
    }

    private static void ReadCommands(Stream stream)
    {
        try
        {
            StreamReader reader = new StreamReader(stream, Encoding.UTF8);
            string line;
            while ((line = reader.ReadLine()) != null)
            {
                string[] parts = line.Trim().Split(' ');
                int ms;
                if (parts.Length == 2 && parts[0] == "interval" && int.TryParse(parts[1], out ms))
                    intervalMs = Math.Max(250, Math.Min(10000, ms));
                else if (parts.Length == 2 && parts[0] == "presentmon" && parts[1] == "on")
                    StartPresentMon();
                else if (parts.Length == 2 && parts[0] == "presentmon" && parts[1] == "off")
                    StopPresentMon();
                else if (parts[0] == "quit")
                    break;
            }
        }
        catch (IOException) { }
        catch (ObjectDisposedException) { }
        running = false;
    }

    private static bool IsElevated()
    {
        return new WindowsPrincipal(WindowsIdentity.GetCurrent()).IsInRole(WindowsBuiltInRole.Administrator);
    }

    private static string Snapshot(Computer computer)
    {
        StringBuilder sb = new StringBuilder(16384);
        sb.Append("{\"t\":\"sensors\",\"items\":[");
        bool first = true;
        foreach (IHardware hardware in computer.Hardware)
            Append(sb, hardware, ref first);
        sb.Append("]}");
        return sb.ToString();
    }

    private static void Append(StringBuilder sb, IHardware hardware, ref bool first)
    {
        try { hardware.Update(); } catch (Exception) { return; }
        foreach (ISensor sensor in hardware.Sensors)
        {
            if (!first) sb.Append(',');
            first = false;
            sb.Append("{\"hw\":").Append(Quote(hardware.Name))
              .Append(",\"hwType\":").Append(Quote(hardware.HardwareType.ToString()))
              .Append(",\"id\":").Append(Quote(sensor.Identifier.ToString()))
              .Append(",\"type\":").Append(Quote(sensor.SensorType.ToString()))
              .Append(",\"name\":").Append(Quote(sensor.Name))
              .Append(",\"value\":");
            float? value = sensor.Value;
            if (value.HasValue && !float.IsNaN(value.Value) && !float.IsInfinity(value.Value))
                sb.Append(value.Value.ToString("R", CultureInfo.InvariantCulture));
            else
                sb.Append("null");
            sb.Append('}');
        }
        foreach (IHardware sub in hardware.SubHardware)
            Append(sb, sub, ref first);
    }

    private static string Quote(string s)
    {
        StringBuilder sb = new StringBuilder(s.Length + 2);
        sb.Append('"');
        foreach (char c in s ?? "")
        {
            if (c == '"' || c == '\\') sb.Append('\\').Append(c);
            else if (c < 0x20) sb.AppendFormat("\\u{0:x4}", (int)c);
            else sb.Append(c);
        }
        sb.Append('"');
        return sb.ToString();
    }
}
