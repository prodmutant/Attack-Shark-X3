using System.Diagnostics;
using System.Net;
using System.Net.Http;
using System.Net.Sockets;

namespace X3Driver;

/// <summary>
/// Owns the Python side. The device logic - the HID protocol, the macro
/// engine, the state file - already exists and is tested; this app is a window
/// around it, not a reimplementation of it.
///
/// The server binds a port chosen at runtime rather than the fixed 7332, so
/// launching the desktop app never collides with a web UI the user already has
/// open, and closing the window takes the server with it. Loopback only.
/// </summary>
internal sealed class Backend : IDisposable
{
    private Process? _proc;

    public int Port { get; private set; }
    public string Url => $"http://127.0.0.1:{Port}/";

    /// <summary>Repo root: the directory holding attackshark\__main__.py.</summary>
    public static string? FindRoot()
    {
        var seeds = new List<string>();
        var exeDir = Path.GetDirectoryName(Environment.ProcessPath);
        if (exeDir is not null) seeds.Add(exeDir);
        seeds.Add(Directory.GetCurrentDirectory());

        foreach (var seed in seeds)
        {
            var dir = new DirectoryInfo(seed);
            while (dir is not null)
            {
                if (File.Exists(Path.Combine(dir.FullName, "attackshark", "__main__.py")))
                    return dir.FullName;
                dir = dir.Parent;
            }
        }
        return null;
    }

    /// <summary>pythonw keeps a console window from flashing up behind us.</summary>
    private static string? FindPython()
    {
        var names = new[] { "pythonw.exe", "python.exe" };
        var paths = (Environment.GetEnvironmentVariable("PATH") ?? "")
                    .Split(Path.PathSeparator, StringSplitOptions.RemoveEmptyEntries);
        foreach (var name in names)
            foreach (var p in paths)
            {
                try
                {
                    var full = Path.Combine(p.Trim('"'), name);
                    if (File.Exists(full)) return full;
                }
                catch (ArgumentException) { /* a malformed PATH entry */ }
            }
        return null;
    }

    private static int FreePort()
    {
        var l = new TcpListener(IPAddress.Loopback, 0);
        l.Start();
        var port = ((IPEndPoint)l.LocalEndpoint).Port;
        l.Stop();
        return port;
    }

    public string? Start()
    {
        var root = FindRoot();
        if (root is null)
            return "Could not find the attackshark package. Run this from the "
                 + "project folder, or keep the executable inside it.";

        var python = FindPython();
        if (python is null)
            return "Python was not found on PATH. The interface is a window "
                 + "around the existing driver, which needs Python 3.8+.";

        Port = FreePort();
        var psi = new ProcessStartInfo(python)
        {
            WorkingDirectory = root,
            UseShellExecute = false,
            CreateNoWindow = true,
            RedirectStandardOutput = true,
            RedirectStandardError = true,
        };
        psi.ArgumentList.Add("-m");
        psi.ArgumentList.Add("attackshark");
        psi.ArgumentList.Add("gui");
        psi.ArgumentList.Add("--port");
        psi.ArgumentList.Add(Port.ToString());
        psi.ArgumentList.Add("--no-browser");

        try { _proc = Process.Start(psi); }
        catch (Exception ex) { return "Could not start the driver service: " + ex.Message; }

        return _proc is null ? "Could not start the driver service." : null;
    }

    /// <summary>Wait for the service to answer before showing anything.</summary>
    public async Task<bool> WaitReady(TimeSpan timeout)
    {
        using var http = new HttpClient { Timeout = TimeSpan.FromSeconds(2) };
        var until = DateTime.UtcNow + timeout;
        while (DateTime.UtcNow < until)
        {
            if (_proc is { HasExited: true }) return false;
            try
            {
                var r = await http.GetAsync(Url + "api/state");
                if (r.IsSuccessStatusCode) return true;
            }
            catch (HttpRequestException) { /* not up yet */ }
            catch (TaskCanceledException) { /* not up yet */ }
            await Task.Delay(120);
        }
        return false;
    }

    public string ReadError()
    {
        if (_proc is null) return "";
        try
        {
            if (!_proc.HasExited) return "";
            var err = _proc.StandardError.ReadToEnd();
            var outp = _proc.StandardOutput.ReadToEnd();
            return (err + "\n" + outp).Trim();
        }
        catch (InvalidOperationException) { return ""; }
    }

    public void Dispose()
    {
        try
        {
            if (_proc is { HasExited: false })
            {
                _proc.Kill(entireProcessTree: true);
                _proc.WaitForExit(3000);
            }
        }
        catch (Exception) { /* already gone */ }
        _proc?.Dispose();
        _proc = null;
    }
}
