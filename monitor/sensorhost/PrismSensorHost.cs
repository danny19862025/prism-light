// SPDX-License-Identifier: GPL-3.0-only
// Prism Monitor sensor helper. The only part of Prism Monitor that runs with
// administrator rights. It reads CPU and motherboard sensors through
// LibreHardwareMonitorLib (MPL-2.0, which uses the PawnIO driver) and streams
// them as JSON lines over a named pipe that only the requesting user can open.
// It never writes to hardware, never installs drivers and exits when the
// widget exits or disconnects.
//
// Usage: PrismSensorHost.exe --pipe <name> --sid <user SID> --parent <pid>
//        PrismSensorHost.exe --dump      (print one JSON snapshot and exit)
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
        bool dump = false;
        for (int i = 0; i < args.Length; i++)
        {
            if (args[i] == "--pipe" && i + 1 < args.Length) pipe = args[++i];
            else if (args[i] == "--sid" && i + 1 < args.Length) sid = args[++i];
            else if (args[i] == "--parent" && i + 1 < args.Length) int.TryParse(args[++i], out parent);
            else if (args[i] == "--dump") dump = true;
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
