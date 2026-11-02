namespace DstMcI18n.Core;

/// <summary>Command line options.</summary>
public sealed class AppOptions
{
    public string? WorkspacePath { get; set; }
    public int? Port { get; set; }
    public bool NoBrowser { get; set; }
    public bool ShowHelp { get; set; }

    public static AppOptions Parse(string[] args)
    {
        var o = new AppOptions();
        for (var i = 0; i < args.Length; i++)
        {
            var a = args[i];
            switch (a)
            {
                case "-h":
                case "--help":
                case "/?":
                    o.ShowHelp = true;
                    break;
                case "--no-browser":
                    o.NoBrowser = true;
                    break;
                case "--port":
                    if (i + 1 < args.Length && int.TryParse(args[++i], out var p) && p is > 0 and <= 65535)
                        o.Port = p;
                    break;
                case "--path":
                    if (i + 1 < args.Length)
                        o.WorkspacePath = args[++i];
                    break;
                default:
                    if (a.StartsWith("--port=", StringComparison.OrdinalIgnoreCase) &&
                        int.TryParse(a[7..], out var p2) && p2 is > 0 and <= 65535)
                        o.Port = p2;
                    else if (a.StartsWith("--path=", StringComparison.OrdinalIgnoreCase))
                        o.WorkspacePath = a[7..];
                    break;
            }
        }

        return o;
    }

    public static string HelpText =>
        "DST MC I18n Tool v1.1.0 — Minecraft 模组本地化批处理工具\n" +
        "\n" +
        "用法:\n" +
        "  DST-MC-I18n-Tool.exe [--path <整合包目录>] [--port <端口>] [--no-browser]\n" +
        "\n" +
        "参数:\n" +
        "  --path <dir>    工作目录（.minecraft / 版本隔离目录 / 整合包根目录）。\n" +
        "                  也可以启动后在网页里点“打开文件夹”选择。\n" +
        "  --port <port>   监听端口。省略时自动使用一个随机空闲端口。\n" +
        "  --no-browser    启动后不自动打开浏览器。\n" +
        "  -h, --help      显示本帮助。\n" +
        "\n" +
        "网页界面地址: http://127.0.0.1:<port>/ ，仅本机可访问。\n";
}
