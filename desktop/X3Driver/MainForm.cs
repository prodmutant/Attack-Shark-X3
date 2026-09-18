using System.Runtime.InteropServices;
using Microsoft.Web.WebView2.Core;
using Microsoft.Web.WebView2.WinForms;

namespace X3Driver;

/// <summary>
/// The window. Everything inside it is the same interface the web UI serves,
/// rendered by the same engine, so the two cannot look different from each
/// other - there is one front end, shown two ways.
///
/// What makes this an application rather than a browser tab: no address bar,
/// no tabs, no browser chrome, its own taskbar entry and icon, a dark title
/// bar, its own profile directory, and a service that lives and dies with the
/// window on a port nothing else knows about.
/// </summary>
internal sealed class MainForm : Form
{
    private readonly Backend _backend = new();
    private readonly WebView2 _web = new() { Dock = DockStyle.Fill };
    private readonly Label _splash;

    // the palette's --bg, so the window never flashes white before the page
    private static readonly Color Ink = Color.FromArgb(0x05, 0x05, 0x06);

    public MainForm()
    {
        Text = "PRODMUTANT — X3 Driver";
        BackColor = Ink;
        MinimumSize = new Size(940, 660);
        ClientSize = new Size(1320, 860);
        StartPosition = FormStartPosition.CenterScreen;
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
        UseDarkTitleBar(Handle);
    }

    protected override void OnShown(EventArgs e)
    {
        base.OnShown(e);
        // Setting it before the window is mapped is not enough on every build:
        // the frame is drawn once on show and keeps the light colours unless
        // it is told again and asked to repaint.
        UseDarkTitleBar(Handle);
        RedrawFrame(Handle);
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
        };
    }

    private void Fail(string message)
    {
        _splash.Text = message;
        _splash.Padding = new Padding(40);
        _splash.Visible = true;
        _web.Visible = false;
    }

    /// <summary>
    /// A light title bar over a near-black page looks broken. DWM has taken
    /// this attribute since Windows 10 2004; on anything older the call fails
    /// harmlessly and the bar stays light.
    /// </summary>
    private static void UseDarkTitleBar(IntPtr hwnd)
    {
        // 20 since Windows 10 2004; 19 on the builds before it
        int on = 1;
        try
        {
            if (DwmSetWindowAttribute(hwnd, 20, ref on, sizeof(int)) != 0)
                DwmSetWindowAttribute(hwnd, 19, ref on, sizeof(int));
        }
        catch (DllNotFoundException) { }
    }

    /// <summary>
    /// Ask for the non-client area to be drawn again. Resizing the window
    /// would also do it, but it resizes the WebView2 with it and the page can
    /// come back unpainted.
    /// </summary>
    private static void RedrawFrame(IntPtr hwnd)
    {
        const uint SWP_NOSIZE = 0x0001, SWP_NOMOVE = 0x0002;
        const uint SWP_NOZORDER = 0x0004, SWP_FRAMECHANGED = 0x0020;
        try
        {
            SetWindowPos(hwnd, IntPtr.Zero, 0, 0, 0, 0,
                         SWP_NOSIZE | SWP_NOMOVE | SWP_NOZORDER | SWP_FRAMECHANGED);
        }
        catch (DllNotFoundException) { }
    }

    [DllImport("dwmapi.dll", PreserveSig = true)]
    private static extern int DwmSetWindowAttribute(IntPtr hwnd, int attr,
                                                    ref int value, int size);

    [DllImport("user32.dll", SetLastError = true)]
    [return: MarshalAs(UnmanagedType.Bool)]
    private static extern bool SetWindowPos(IntPtr hwnd, IntPtr after,
                                            int x, int y, int cx, int cy,
                                            uint flags);
}
