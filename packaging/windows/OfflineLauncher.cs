using System;
using System.Diagnostics;
using System.IO;
using System.Reflection;
using System.Text;
using System.Windows.Forms;

[assembly: AssemblyTitle("AutoKaraoke 中文离线版")]
[assembly: AssemblyDescription("AutoKaraoke by GiacoBot and contributors; Chinese fork by Dhz8678")]
[assembly: AssemblyVersion("1.0.0.0")]

internal static class OfflineLauncher
{
    private static string Quote(string value)
    {
        StringBuilder result = new StringBuilder("\"");
        int slashes = 0;
        foreach (char c in value)
        {
            if (c == '\\') { slashes++; continue; }
            if (c == '"') result.Append('\\', slashes * 2 + 1);
            else result.Append('\\', slashes);
            result.Append(c);
            slashes = 0;
        }
        result.Append('\\', slashes * 2);
        return result.Append('"').ToString();
    }

    [STAThread]
    private static int Main(string[] args)
    {
        try
        {
            string root = AppDomain.CurrentDomain.BaseDirectory;
            string python = Path.Combine(root, "runtime", "python");
            string state = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "AutoKaraoke-Offline");
            Directory.CreateDirectory(state);
            bool check = args.Length > 0 && args[0] == "--self-test";
            ProcessStartInfo start = new ProcessStartInfo();
            start.FileName = Path.Combine(python, check ? "python.exe" : "pythonw.exe");
            start.WorkingDirectory = state;
            start.UseShellExecute = false;
            start.CreateNoWindow = true;
            start.EnvironmentVariables["PYTHONHOME"] = python;
            start.EnvironmentVariables["PYTHONPATH"] = Path.Combine(root, "app", "src");
            start.EnvironmentVariables["PYTHONNOUSERSITE"] = "1";
            start.EnvironmentVariables["PYTHONUTF8"] = "1";
            start.EnvironmentVariables["PYTHONDONTWRITEBYTECODE"] = "1";
            start.EnvironmentVariables["HF_HOME"] = Path.Combine(root, "models", "huggingface");
            start.EnvironmentVariables["HF_HUB_CACHE"] = Path.Combine(root, "models", "huggingface", "hub");
            start.EnvironmentVariables["HUGGINGFACE_HUB_CACHE"] = start.EnvironmentVariables["HF_HUB_CACHE"];
            start.EnvironmentVariables["HF_HUB_OFFLINE"] = "1";
            start.EnvironmentVariables["TRANSFORMERS_OFFLINE"] = "1";
            start.EnvironmentVariables["HF_HUB_DISABLE_TELEMETRY"] = "1";
            start.EnvironmentVariables["AUTOKARAOKE_MODELS"] = Path.Combine(root, "models", "separation");
            start.EnvironmentVariables["AUTOKARAOKE_OFFLINE_ROOT"] = root;
            start.EnvironmentVariables.Remove("AUTOKARAOKE_UV");
            start.EnvironmentVariables.Remove("AUTOKARAOKE_APP");
            start.EnvironmentVariables.Remove("UV_PROJECT_ENVIRONMENT");
            start.EnvironmentVariables["NUMBA_CACHE_DIR"] = Path.Combine(state, "cache", "numba");
            start.EnvironmentVariables["TORCH_HOME"] = Path.Combine(state, "cache", "torch");
            start.EnvironmentVariables["PATH"] = Path.Combine(root, "bin") + ";" + python + ";" + Environment.GetEnvironmentVariable("PATH");
            if (String.IsNullOrEmpty(Environment.GetEnvironmentVariable("AUTOKARAOKE_PROJECTS")))
                start.EnvironmentVariables["AUTOKARAOKE_PROJECTS"] = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.MyDocuments), "AutoKaraoke");
            if (check)
            {
                start.Arguments = "-B " + Quote(Path.Combine(root, "app", "offline_check.py"));
                for (int i = 1; i < args.Length; i++) start.Arguments += " " + Quote(args[i]);
            }
            else
            {
                start.Arguments = "-B -m autokaraoke.cli gui";
                foreach (string value in args) start.Arguments += " " + Quote(value);
            }
            Process process = Process.Start(start);
            if (check) { process.WaitForExit(); return process.ExitCode; }
            return 0;
        }
        catch (Exception error)
        {
            MessageBox.Show("启动失败：" + error.Message + "\n请使用离线安装程序重新安装。", "AutoKaraoke", MessageBoxButtons.OK, MessageBoxIcon.Error);
            return 1;
        }
    }
}
