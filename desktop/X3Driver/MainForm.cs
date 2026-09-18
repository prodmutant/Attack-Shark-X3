using System.Runtime.InteropServices;
using System.Text.Json;
using Microsoft.Web.WebView2.Core;
using Microsoft.Web.WebView2.WinForms;

namespace X3Driver;

/// <summary>
/// The window. Everything inside it is the same interface the web UI serves,
/// rendered by the same engine, so the two cannot look different from each
/// other - there is one front end, shown two ways.
///
/// The frame is drawn by the page, not by Windows: a title bar in the system
/// colours above a near-black interface looks like two programs stuck
/// together. Dropping the frame means taking back the three things it did -
/// moving, resizing, and the caption buttons - which is what WndProc and the
/// web messages below are for. Both hand the work to Windows rather than
/// imitate it, so snapping, edge cursors, double-click-to-maximise and the
/// keyboard move/size commands all still behave normally.
/// </summary>
internal sealed class MainForm : Form
{
    /// <summary>Width of the invisible resize border, in pixels.</summary>
    private const int Grip = 6;

    private readonly Backend _backend = new();
    private readonly WebView2 _web = new() { Dock = DockStyle.Fill };
    private readonly Label _splash;

    // the palette's --bg, so the window never flashes white before the page
    private static readonly Color Ink = Color.FromArgb(0x05, 0x05, 0x06);

    public MainForm()
    {
        Text = "PRODMUTANT — X3 Driver";
        FormBorderStyle = FormBorderStyle.None;
        BackColor = Ink;
        // The page sits inside this margin. The strip that shows is the resize
        // border, painted the background colour so it reads as nothing.
        Padding = new Padding(Grip);
        MinimumSize = new Size(940, 660);
        ClientSize = new Size(1320, 860);
        StartPosition = FormStartPosition.CenterScreen;
        DoubleBuffered = true;
        try { Icon = new Icon(Path.Combine(AppContext.BaseDirectory, "app.ico")); }
        catch (Exception) { /* running from a build tree without the icon */ }

        _splash = new Label
        {
            Dock = DockStyle.Fill,
            BackColor = Ink,
            ForeColor = Color.FromArgb(0x87, 0x7e, 0x86),
            TextAlign = ContentAlignment.MiddleCenter,
            Font = new Font("Segoe UI", 9f),
            Text = "starting the driver service…",
        };
        Controls.Add(_splash);
        Controls.Add(_web);
        _web.Visible = false;

        Load += OnLoad;
        FormClosed += (_, _) => _backend.Dispose();
    }

    protected override void OnHandleCreated(EventArgs e)
    {
        base.OnHandleCreated(e);
        ClampMaximised();
    }

    protected override void OnResize(EventArgs e)
    {
        base.OnResize(e);
        // A maximised window has no edges to grab, and the margin would only
        // be a band of dead space around the page.
        Padding = WindowState == FormWindowState.Maximized
            ? new Padding(0) : new Padding(Grip);
        Post("state", WindowState == FormWindowState.Maximized ? "max" : "normal");
    }

    protected override void OnLocationChanged(EventArgs e)
    {
        base.OnLocationChanged(e);
        ClampMaximised();
    }

    /// <summary>
    /// A borderless window maximises over the taskbar unless it is told the
    /// working area of the screen it is actually on.
    /// </summary>
    private void ClampMaximised()
    {
        if (!IsHandleCreated) return;
        try { MaximizedBounds = Screen.FromHandle(Handle).WorkingArea; }
        catch (Exception) { /* screen went away mid-move */ }
    }

    /// <summary>
    /// Hit testing for the invisible border. Returning the edge codes hands
    /// the drag to Windows, so the cursors, the snapping and the keyboard
    /// size commands are the real ones rather than an imitation.
    /// </summary>
    protected override void WndProc(ref Message m)
    {
        const int WM_NCHITTEST = 0x0084;
        const int HTCLIENT = 1, HTLEFT = 10, HTRIGHT = 11, HTTOP = 12,
                  HTTOPLEFT = 13, HTTOPRIGHT = 14, HTBOTTOM = 15,
                  HTBOTTOMLEFT = 16, HTBOTTOMRIGHT = 17;

        if (m.Msg == WM_NCHITTEST && WindowState == FormWindowState.Normal)
        {
            // LParam packs two signed shorts; a monitor to the left of the
            // primary makes them negative, so they must not be read unsigned
            int x = unchecked((short)(long)m.LParam);
            int y = unchecked((short)((long)m.LParam >> 16));
            var p = PointToClient(new Point(x, y));

            bool left = p.X < Grip, right = p.X >= ClientSize.Width - Grip;
            bool top = p.Y < Grip, bottom = p.Y >= ClientSize.Height - Grip;

            int hit = HTCLIENT;
            if (top && left) hit = HTTOPLEFT;
            else if (top && right) hit = HTTOPRIGHT;
            else if (bottom && left) hit = HTBOTTOMLEFT;
            else if (bottom && right) hit = HTBOTTOMRIGHT;
            else if (left) hit = HTLEFT;
            else if (right) hit = HTRIGHT;
            else if (top) hit = HTTOP;
            else if (bottom) hit = HTBOTTOM;

            if (hit != HTCLIENT) { m.Result = hit; return; }
        }
        base.WndProc(ref m);
    }

    private async void OnLoad(object? sender, EventArgs e)
    {
        var problem = _backend.Start();
        if (problem is not null) { Fail(problem); return; }

        // keep the browser profile beside the app, not in the user's Temp
        var dataDir = Path.Combine(
            Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),
            "PRODMUTANT X3 Driver", "WebView2");
        Directory.CreateDirectory(dataDir);

        try
        {
            var env = await CoreWebView2Environment.CreateAsync(null, dataDir);
            await _web.EnsureCoreWebView2Async(env);
        }
        catch (Exception ex)
        {
            Fail("WebView2 could not start: " + ex.Message
                 + "\n\nInstall the Microsoft Edge WebView2 Runtime and try again.");
            return;
        }

        var s = _web.CoreWebView2.Settings;
        s.AreDefaultContextMenusEnabled = false;   // it is an app, not a page
        s.IsStatusBarEnabled = false;
        s.AreBrowserAcceleratorKeysEnabled = false;
        s.IsSwipeNavigationEnabled = false;
        _web.CoreWebView2.NewWindowRequested += (_, a) => a.Handled = true;
        _web.CoreWebView2.WebMessageReceived += OnWebMessage;
        _web.DefaultBackgroundColor = Ink;

        if (!await _backend.WaitReady(TimeSpan.FromSeconds(25)))
        {
            var detail = _backend.ReadError();
            Fail("The driver service did not start."
                 + (string.IsNullOrWhiteSpace(detail) ? "" : "\n\n" + detail));
            return;
        }

        _web.CoreWebView2.Navigate(_backend.Url);
        _web.NavigationCompleted += (_, _) =>
        {
            _splash.Visible = false;
            _web.Visible = true;
            _web.Focus();
            Post("state", WindowState == FormWindowState.Maximized ? "max" : "normal");
        };
    }

    /// <summary>The page asking for something only the window can do.</summary>
    private void OnWebMessage(object? sender, CoreWebView2WebMessageReceivedEventArgs e)
    {
        string cmd;
        try
        {
            using var doc = JsonDocument.Parse(e.WebMessageAsJson);
            cmd = doc.RootElement.TryGetProperty("cmd", out var c)
                ? (c.GetString() ?? "") : "";
        }
        catch (JsonException) { return; }

        switch (cmd)
        {
            case "drag":
                // Hand the move to Windows rather than chasing the cursor:
                // this is what gives Aero Snap, the monitor edges, and the
                // restore-then-drag when a maximised window is pulled down.
                ReleaseCapture();
                SendMessage(Handle, WM_NCLBUTTONDOWN, (IntPtr)HTCAPTION, IntPtr.Zero);
                break;
            case "min":
                WindowState = FormWindowState.Minimized;
                break;
            case "max":
                WindowState = WindowState == FormWindowState.Maximized
                    ? FormWindowState.Normal : FormWindowState.Maximized;
                break;
            case "close":
                Close();
                break;
        }
    }

    private void Post(string key, string value)
    {
        if (_web.CoreWebView2 is null) return;
        try
        {
            _web.CoreWebView2.PostWebMessageAsJson(
                JsonSerializer.Serialize(new Dictionary<string, string> { [key] = value }));
        }
        catch (Exception) { /* page not up yet */ }
    }

    private void Fail(string message)
    {
        _splash.Text = message;
        _splash.Padding = new Padding(40);
        _splash.Visible = true;
        _web.Visible = false;
    }

    private const int WM_NCLBUTTONDOWN = 0x00A1, HTCAPTION = 2;

    [DllImport("user32.dll")]
    [return: MarshalAs(UnmanagedType.Bool)]
    private static extern bool ReleaseCapture();

    [DllImport("user32.dll")]
    private static extern IntPtr SendMessage(IntPtr hwnd, int msg, IntPtr wp, IntPtr lp);
}
