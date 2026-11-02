using System.Diagnostics;
using System.Text;
using System.Text.Json;
using System.Text.Json.Nodes;
using DstMcI18n.Core;
using DstMcI18n.Services;

namespace DstMcI18n;

public static class Program
{
    public const string AppName = "DST MC I18n Tool";
    public const string Version = "1.1.0";

    public static async Task<int> Main(string[] args)
    {
        try
        {
            Console.OutputEncoding = new UTF8Encoding(false);
        }
        catch
        {
            // redirected output may not support changing the encoding
        }

        try
        {
            Console.SetOut(new StreamWriter(Console.OpenStandardOutput(), new UTF8Encoding(false)) { AutoFlush = true });
        }
        catch
        {
            // keep the default writer
        }
        var options = AppOptions.Parse(args);
        if (options.ShowHelp)
        {
            Console.WriteLine(AppOptions.HelpText);
            return 0;
        }

        var settings = new SettingsStore();
        var session = new WorkspaceSession();
        var documents = new DocumentService(session);
        var exports = new ExportService(session, documents);

        var http = new HttpClient(new SocketsHttpHandler
        {
            PooledConnectionLifetime = TimeSpan.FromMinutes(5),
            AutomaticDecompression = System.Net.DecompressionMethods.All
        })
        {
            Timeout = Timeout.InfiniteTimeSpan
        };

        var llm = new LlmClient(http);
        var mt = new MachineTranslator(http);
        var engine = new TranslationEngine(llm, mt);

        var builder = WebApplication.CreateSlimBuilder(new WebApplicationOptions
        {
            Args = args,
            ContentRootPath = AppContext.BaseDirectory
        });

        builder.Logging.ClearProviders();
        builder.WebHost.UseKestrel(kestrel =>
        {
            // ListenLocalhost() rejects port 0, so bind the loopback address explicitly to
            // support "random free port" mode.
            kestrel.Listen(System.Net.IPAddress.Loopback, options.Port ?? 0);
            kestrel.AddServerHeader = false;
        });

        builder.Services.ConfigureHttpJsonOptions(o =>
        {
            o.SerializerOptions.PropertyNamingPolicy = JsonNamingPolicy.CamelCase;
            o.SerializerOptions.Encoder = System.Text.Encodings.Web.JavaScriptEncoder.UnsafeRelaxedJsonEscaping;
            o.SerializerOptions.DefaultIgnoreCondition = System.Text.Json.Serialization.JsonIgnoreCondition.Never;
        });
        builder.Services.AddSingleton(settings);
        builder.Services.AddSingleton(session);
        builder.Services.AddSingleton(documents);
        builder.Services.AddSingleton(exports);
        builder.Services.AddSingleton(engine);
        builder.Services.AddSingleton(llm);
        builder.Services.AddSingleton(mt);

        var app = builder.Build();
        Api.Map(app);

        await app.StartAsync().ConfigureAwait(false);

        // 目录扫描放到服务启动之后异步执行：大整合包扫描可能要几十秒，
        // 不能让 HTTP 端口一直不监听（否则浏览器打不开，看起来像程序卡死）。
        var startupPath = options.WorkspacePath;
        if (string.IsNullOrWhiteSpace(startupPath) &&
            !string.IsNullOrWhiteSpace(settings.Current.LastWorkspace) &&
            Directory.Exists(settings.Current.LastWorkspace))
        {
            startupPath = settings.Current.LastWorkspace;
        }

        if (!string.IsNullOrWhiteSpace(startupPath))
        {
            try
            {
                var state = session.StartScan(startupPath!);
                settings.SetLastWorkspace(state.Progress.Path);
                Console.WriteLine("  正在后台解析: " + state.Progress.Path + "（可随时在网页里取消）");
            }
            catch (Exception ex)
            {
                Console.Error.WriteLine("[warn] 无法打开工作目录: " + ex.Message);
            }
        }

        var addresses = app.Urls.ToList();
        var url = addresses.FirstOrDefault() ?? "http://127.0.0.1:5000";
        url = url.Replace("[::]", "127.0.0.1").Replace("0.0.0.0", "127.0.0.1");

        Console.WriteLine();
        Console.WriteLine("  " + AppName + " v" + Version);
        Console.WriteLine("  ------------------------------------------");
        Console.WriteLine("  服务地址: " + url);
        var workspaceLabel = session.CurrentPath
            ?? (session.ActiveScanId is not null ? "解析中…" : "(未打开，请在网页中选择)");
        Console.WriteLine("  工作目录: " + workspaceLabel);
        Console.WriteLine("  按 Ctrl+C 退出");
        Console.WriteLine();

        if (!options.NoBrowser) TryOpenBrowser(url);

        var waitForShutdown = new TaskCompletionSource();
        Console.CancelKeyPress += (_, e) =>
        {
            e.Cancel = true;
            waitForShutdown.TrySetResult();
        };
        AppDomain.CurrentDomain.ProcessExit += (_, _) => waitForShutdown.TrySetResult();

        _ = Task.Run(async () =>
        {
            while (true)
            {
                await Task.Delay(TimeSpan.FromMinutes(10)).ConfigureAwait(false);
                engine.Cleanup(TimeSpan.FromHours(6));
            }
        });

        await waitForShutdown.Task.ConfigureAwait(false);
        await app.StopAsync().ConfigureAwait(false);
        return 0;
    }

    private static void TryOpenBrowser(string url)
    {
        try
        {
            Process.Start(new ProcessStartInfo
            {
                FileName = url,
                UseShellExecute = true
            });
        }
        catch (Exception ex)
        {
            Console.Error.WriteLine("[warn] 无法自动打开浏览器: " + ex.Message);
        }
    }
}
