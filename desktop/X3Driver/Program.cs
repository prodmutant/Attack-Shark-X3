using System.Threading;

namespace X3Driver;

internal static class Program
{
    [STAThread]
    private static void Main()
    {
        // one window per machine: a second instance would start a second
        // driver service, and two of those racing on the state file is how
        // settings get lost
        using var single = new Mutex(true, "PRODMUTANT.X3Driver.Instance", out var first);
        if (!first)
        {
            MessageBox.Show("PRODMUTANT X3 Driver is already running.",
                            "X3 Driver", MessageBoxButtons.OK, MessageBoxIcon.Information);
            return;
        }

        ApplicationConfiguration.Initialize();
        Application.Run(new MainForm());
    }
}
