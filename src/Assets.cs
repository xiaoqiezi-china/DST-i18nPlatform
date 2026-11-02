using System.Reflection;
using System.Text;

namespace DstMcI18n;

/// <summary>Serves the single-page UI from embedded resources so the exe is self-contained.</summary>
public sealed class AssetBundle
{
    public string Html { get; set; } = "";
    public string Favicon { get; set; } = "";
    public Dictionary<string, (string Content, string ContentType)> Files { get; } = new(StringComparer.OrdinalIgnoreCase);
}

public static class EmbeddedAssets
{
    private const string Placeholder = "__DST_MC_I18N_MISSING_ASSET__";

    public static AssetBundle Load()
    {
        var bundle = new AssetBundle();
        var assembly = Assembly.GetExecutingAssembly();

        foreach (var name in assembly.GetManifestResourceNames())
        {
            if (!name.StartsWith("wwwroot/", StringComparison.OrdinalIgnoreCase)) continue;
            var relative = name["wwwroot/".Length..].Replace('\\', '/');
            var content = ReadText(assembly, name);

            switch (relative.ToLowerInvariant())
            {
                case "index.html":
                    bundle.Html = content;
                    break;
                case "favicon.svg":
                    bundle.Favicon = content;
                    break;
                default:
                    bundle.Files[relative] = (content, ContentTypeOf(relative));
                    break;
            }
        }

        if (string.IsNullOrEmpty(bundle.Html))
            bundle.Html = "<!doctype html><meta charset=\"utf-8\"><title>DST MC I18n Tool</title>" +
                          "<h1>UI 资源缺失</h1><p>发布时 wwwroot 未打包进程序。</p>";
        if (string.IsNullOrEmpty(bundle.Favicon)) bundle.Favicon = DefaultFavicon;
        return bundle;
    }

    private static string ReadText(Assembly assembly, string name)
    {
        using var stream = assembly.GetManifestResourceStream(name);
        if (stream is null) return Placeholder;
        using var reader = new StreamReader(stream, Encoding.UTF8);
        return reader.ReadToEnd();
    }

    private static string ContentTypeOf(string path) => Path.GetExtension(path).ToLowerInvariant() switch
    {
        ".html" => "text/html; charset=utf-8",
        ".css" => "text/css; charset=utf-8",
        ".js" => "text/javascript; charset=utf-8",
        ".json" => "application/json; charset=utf-8",
        ".svg" => "image/svg+xml",
        ".png" => "image/png",
        ".ico" => "image/x-icon",
        ".woff2" => "font/woff2",
        _ => "application/octet-stream"
    };

    private const string DefaultFavicon =
        "<svg xmlns=\"http://www.w3.org/2000/svg\" viewBox=\"0 0 64 64\">" +
        "<rect width=\"64\" height=\"64\" rx=\"14\" fill=\"#0d1b2a\"/>" +
        "<path d=\"M18 40V24h6l8 10 8-10h6v16h-5V31l-9 11-9-11v9z\" fill=\"#5cc8ff\"/></svg>";
}

/// <summary>Minimal Windows folder picker (no WinForms dependency).</summary>
public static class FolderPicker
{
    private static string FirstLine(string text)
    {
        var line = text.Split('\n').FirstOrDefault(l => !string.IsNullOrWhiteSpace(l)) ?? "";
        return line.Trim();
    }

    public static string? Pick(string? startPath)
    {
        if (!OperatingSystem.IsWindows()) return null;
        if (!Environment.UserInteractive) throw new InvalidOperationException("当前会话没有桌面环境，无法显示系统文件夹选择框");

        // Write the script to a temp .ps1 instead of passing it via -Command so that
        // paths containing quotes, spaces or non-ASCII characters stay intact.
        var scriptPath = Path.Combine(Path.GetTempPath(), "dstmc-pick-" + Guid.NewGuid().ToString("N") + ".ps1");
        var lines = new List<string>
        {
            "Add-Type -AssemblyName System.Windows.Forms | Out-Null",
            "$dlg = New-Object System.Windows.Forms.FolderBrowserDialog",
            "$dlg.Description = '选择 Minecraft 整合包 / .minecraft 目录'",
            "$dlg.ShowNewFolderButton = $false"
        };
        if (!string.IsNullOrWhiteSpace(startPath))
        {
            var escaped = startPath!.Replace("'", "''");
            lines.Add("if (Test-Path -LiteralPath '" + escaped + "') { $dlg.SelectedPath = '" + escaped + "' }");
        }
        lines.Add("if ($dlg.ShowDialog() -eq [System.Windows.Forms.DialogResult]::OK) { [Console]::Out.Write($dlg.SelectedPath) }");
        File.WriteAllText(scriptPath, string.Join("\r\n", lines), new UTF8Encoding(true));

        try
        {
            var psi = new System.Diagnostics.ProcessStartInfo
            {
                FileName = "powershell.exe",
                Arguments = "-NoProfile -STA -ExecutionPolicy Bypass -File \"" + scriptPath + "\"",
                RedirectStandardOutput = true,
                RedirectStandardError = true,
                UseShellExecute = false,
                CreateNoWindow = true
            };

            using var process = System.Diagnostics.Process.Start(psi)
                                ?? throw new InvalidOperationException("无法启动文件夹选择器");

            // Read asynchronously so a hung/headless PowerShell can never block forever.
            var readTask = process.StandardOutput.ReadToEndAsync();
            var errorTask = process.StandardError.ReadToEndAsync();
            if (!process.WaitForExit(TimeSpan.FromSeconds(45)))
            {
                try { process.Kill(entireProcessTree: true); } catch { /* already gone */ }
                throw new TimeoutException("文件夹选择框没有响应");
            }

            var output = readTask.GetAwaiter().GetResult();
            var error = errorTask.GetAwaiter().GetResult();
            var path = output.Trim();
            if (string.IsNullOrWhiteSpace(path))
            {
                // PowerShell failed to build/show the dialog -> let the caller fall back.
                if (process.ExitCode != 0 || error.Contains("Exception", StringComparison.OrdinalIgnoreCase))
                    throw new InvalidOperationException("无法显示系统文件夹选择框: " + FirstLine(error));
                return null;
            }
            return path;
        }
        finally
        {
            try { File.Delete(scriptPath); } catch { /* best effort */ }
        }
    }
}