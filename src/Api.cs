using System.Text.Json;
using System.Text.Json.Nodes;
using DstMcI18n.Core;
using DstMcI18n.Services;

namespace DstMcI18n;

/// <summary>All HTTP endpoints. Every handler returns a typed object or an IResult.</summary>
public static class Api
{
    private static readonly JsonSerializerOptions JsonOut = new()
    {
        PropertyNamingPolicy = JsonNamingPolicy.CamelCase,
        Encoder = System.Text.Encodings.Web.JavaScriptEncoder.UnsafeRelaxedJsonEscaping
    };

    public static void Map(WebApplication app)
    {
        MapStatic(app);
        MapSettings(app);
        MapWorkspace(app);
        MapDocuments(app);
        MapTranslation(app);
        MapExport(app);
    }

    // ---------------- static ----------------

    private static void MapStatic(WebApplication app)
    {
        var assets = EmbeddedAssets.Load();

        app.MapGet("/", () => Results.Content(assets.Html, "text/html; charset=utf-8"));
        app.MapGet("/index.html", () => Results.Content(assets.Html, "text/html; charset=utf-8"));
        app.MapGet("/favicon.svg", () => Results.Content(assets.Favicon, "image/svg+xml"));
        app.MapGet("/favicon.ico", () => Results.Content(assets.Favicon, "image/svg+xml"));

        foreach (var (path, asset) in assets.Files)
        {
            var captured = asset;
            app.MapGet("/" + path, () => Results.Content(captured.Content, captured.ContentType));
        }

        app.MapFallback((HttpContext ctx) =>
        {
            if (ctx.Request.Path.StartsWithSegments("/api"))
                return Results.Json(new { error = "接口不存在: " + ctx.Request.Path }, JsonOut, statusCode: 404);
            return Results.Content(assets.Html, "text/html; charset=utf-8");
        });
    }

    // ---------------- settings ----------------

    private static void MapSettings(WebApplication app)
    {
        app.MapGet("/api/health", () => Results.Json(new
        {
            ok = true,
            name = Program.AppName,
            version = Program.Version,
            pid = Environment.ProcessId,
            startedAt = StartupTime
        }, JsonOut));

        app.MapGet("/api/settings", (SettingsStore store) => Results.Json(store.ToDto(), JsonOut));

        app.MapPost("/api/settings", async (HttpRequest request, SettingsStore store) =>
        {
            var incoming = await ReadJsonAsync<AppSettings>(request);
            if (incoming is null) return BadRequest("请求体不是合法的设置对象");
            store.Save(incoming);
            return Results.Json(store.ToDto(), JsonOut);
        });

        app.MapPost("/api/settings/reset-prompt", (SettingsStore store) =>
        {
            store.ResetSystemPrompt();
            return Results.Json(store.ToDto(), JsonOut);
        });

        app.MapPost("/api/settings/test-llm", async (SettingsStore store, LlmClient llm) =>
        {
            var (ok, message) = await llm.TestAsync(store.Current, CancellationToken.None);
            return Results.Json(new { ok, model = store.Current.LlmModel, message }, JsonOut);
        });

        app.MapPost("/api/settings/test-mt", async (HttpRequest request, SettingsStore store, MachineTranslator mt) =>
        {
            string? provider = null;
            try
            {
                var body = await ReadJsonAsync<JsonObject>(request);
                provider = body?["provider"]?.GetValue<string>();
            }
            catch
            {
                // provider is optional
            }
            var (ok, resolved, message) = await mt.TestAsync(store.Current, provider, CancellationToken.None);
            return Results.Json(new { ok, provider = resolved, message }, JsonOut);
        });
    }

    // ---------------- workspace ----------------

    private static void MapWorkspace(WebApplication app)
    {
        app.MapGet("/api/workspace", (WorkspaceSession session) =>
        {
            var info = session.Current;
            var scanning = session.ActiveScanId;
            if (info is null)
            {
                return Results.Json(new
                {
                    exists = false,
                    path = session.CurrentPath,
                    opened = false,
                    scanning = scanning is not null,
                    scanId = scanning
                }, JsonOut);
            }

            var wire = ToWireDictionary(info);
            wire["scanning"] = scanning is not null;
            wire["scanId"] = scanning;
            return Results.Json(wire, JsonOut);
        });

        // ---- asynchronous scan ----

        app.MapPost("/api/workspace/scan", async (HttpRequest request, WorkspaceSession session, SettingsStore store) =>
        {
            var body = await ReadJsonAsync<JsonObject>(request);
            var path = body?["path"]?.GetValue<string>();
            if (string.IsNullOrWhiteSpace(path)) return BadRequest("缺少 path");

            try
            {
                var state = session.StartScan(path!);
                store.SetLastWorkspace(state.Progress.Path);
                var info = new DirectoryInfo(state.Progress.Path.TrimEnd('\\', '/'));
                return Results.Json(new
                {
                    scanId = state.Progress.ScanId,
                    path = state.Progress.Path,
                    name = state.Progress.Name,
                    modsDir = Directory.Exists(Path.Combine(state.Progress.Path, "mods"))
                        ? Path.Combine(state.Progress.Path, "mods") : null,
                    kubejsDir = Directory.Exists(Path.Combine(state.Progress.Path, "kubejs"))
                        ? Path.Combine(state.Progress.Path, "kubejs") : null,
                    configDir = Directory.Exists(Path.Combine(state.Progress.Path, "config"))
                        ? Path.Combine(state.Progress.Path, "config") : null,
                    candidates = Array.Empty<string>()
                }, JsonOut);
            }
            catch (DirectoryNotFoundException ex)
            {
                return Results.Json(new { error = ex.Message, valid = false, candidates = Array.Empty<string>() }, JsonOut, statusCode: 400);
            }
            catch (InvalidOperationException ex) when (ex.Data.Contains("candidates"))
            {
                return Results.Json(new
                {
                    error = ex.Message,
                    valid = false,
                    candidates = (List<string>)(ex.Data["candidates"] ?? new List<string>())
                }, JsonOut, statusCode: 400);
            }
            catch (InvalidOperationException ex)
            {
                return Results.Json(new { error = ex.Message, valid = true }, JsonOut, statusCode: 409);
            }
            catch (Exception ex)
            {
                return Results.Json(new { error = ex.Message, valid = false, candidates = Array.Empty<string>() }, JsonOut, statusCode: 400);
            }
        });

        app.MapGet("/api/workspace/scan/{scanId}", (string scanId, WorkspaceSession session) =>
        {
            var progress = session.GetScan(scanId);
            return progress is null
                ? Results.Json(new { error = "扫描任务不存在或已过期" }, JsonOut, statusCode: 404)
                : Results.Json(progress, JsonOut);
        });

        app.MapPost("/api/workspace/scan/{scanId}/cancel", (string scanId, WorkspaceSession session) =>
            Results.Json(new { ok = session.CancelScan(scanId) }, JsonOut));

        app.MapPost("/api/workspace/open", async (HttpRequest request, WorkspaceSession session, SettingsStore store) =>
        {
            var body = await ReadJsonAsync<JsonObject>(request);
            var path = body?["path"]?.GetValue<string>();
            if (string.IsNullOrWhiteSpace(path)) return BadRequest("缺少 path");
            var info = session.Open(path!);
            if (info.Exists) store.SetLastWorkspace(info.Path);
            return Results.Json(ToWire(info), JsonOut);
        });

        app.MapPost("/api/workspace/refresh", (WorkspaceSession session) =>
        {
            try
            {
                return Results.Json(ToWire(session.Refresh()), JsonOut);
            }
            catch (Exception ex)
            {
                return BadRequest(ex.Message);
            }
        });

        app.MapGet("/api/workspace/browse", (string? path) => Results.Json(Browse(path), JsonOut));

        app.MapPost("/api/workspace/pick", async (WorkspaceSession session) =>
        {
            // The native dialog runs on its own thread; if it cannot appear (headless /
            // service session) we give up quickly and the UI falls back to its built-in browser.
            var work = Task.Run(() => FolderPicker.Pick(session.CurrentPath));
            var finished = await Task.WhenAny(work, Task.Delay(TimeSpan.FromSeconds(60))).ConfigureAwait(false);
            if (finished != work)
                return Results.Json(new { path = (string?)null, error = "系统文件夹选择框没有响应" }, JsonOut);

            try
            {
                var picked = await work.ConfigureAwait(false);
                return Results.Json(new { path = picked }, JsonOut);
            }
            catch (Exception ex)
            {
                return Results.Json(new { path = (string?)null, error = ex.Message }, JsonOut);
            }
        });

        app.MapPost("/api/workspace/reveal", async (HttpRequest request) =>
        {
            var body = await ReadJsonAsync<JsonObject>(request);
            var path = body?["path"]?.GetValue<string>();
            if (string.IsNullOrWhiteSpace(path)) return BadRequest("缺少 path");
            try
            {
                System.Diagnostics.Process.Start(new System.Diagnostics.ProcessStartInfo
                {
                    FileName = "explorer.exe",
                    Arguments = "\"" + path + "\"",
                    UseShellExecute = true
                });
                return Results.Json(new { ok = true }, JsonOut);
            }
            catch (Exception ex)
            {
                return Results.Json(new { ok = false, error = ex.Message }, JsonOut);
            }
        });
    }

    private static object Browse(string? path)
    {
        var drives = new List<object>();
        foreach (var drive in DriveInfo.GetDrives())
        {
            try
            {
                if (!drive.IsReady) continue;
                drives.Add(new { name = drive.Name.TrimEnd('\\'), path = drive.RootDirectory.FullName });
            }
            catch
            {
                // skip unreadable drives
            }
        }

        if (string.IsNullOrWhiteSpace(path))
        {
            return new
            {
                path = (string?)null,
                parent = (string?)null,
                isRoot = true,
                drives,
                dirs = Array.Empty<object>(),
                suggestions = Array.Empty<object>()
            };
        }

        string full;
        try
        {
            full = Path.GetFullPath(path!.Trim().Trim('"'));
        }
        catch
        {
            full = path!;
        }

        var dirsOut = new List<object>();
        if (Directory.Exists(full))
        {
            try
            {
                foreach (var dir in Directory.EnumerateDirectories(full).OrderBy(d => d, StringComparer.OrdinalIgnoreCase))
                {
                    var name = Path.GetFileName(dir);
                    if (name.StartsWith('.')) continue;
                    var hasMods = Directory.Exists(Path.Combine(dir, "mods"));
                    var hasKubejs = Directory.Exists(Path.Combine(dir, "kubejs"));
                    var hasConfig = Directory.Exists(Path.Combine(dir, "config"));
                    var hasMc = Directory.Exists(Path.Combine(dir, ".minecraft"));
                    if (hasMods || hasKubejs || hasConfig || hasMc)
                    {
                        dirsOut.Insert(0, new { name, path = dir, hasMods, hasKubejs, hasConfig, hasMinecraft = hasMc });
                    }
                    else
                    {
                        dirsOut.Add(new { name, path = dir, hasMods, hasKubejs, hasConfig, hasMinecraft = hasMc });
                    }
                }
            }
            catch (Exception ex)
            {
                return new { path = full, parent = (string?)null, isRoot = false, drives, dirs = Array.Empty<object>(), error = ex.Message, suggestions = Array.Empty<object>() };
            }
        }

        var parent = Directory.Exists(full) ? Path.GetDirectoryName(full) : Path.GetDirectoryName(full + Path.DirectorySeparatorChar);
        var suggestions = BuildSuggestions(full);

        return new
        {
            path = full,
            parent,
            isRoot = string.IsNullOrEmpty(parent),
            drives,
            dirs = dirsOut,
            suggestions
        };
    }

    private static List<object> BuildSuggestions(string current)
    {
        var result = new List<object>();
        var roots = new List<string>();
        var dir = new DirectoryInfo(current);
        for (var i = 0; i < 3 && dir?.Parent is not null; i++)
        {
            dir = dir.Parent;
            if (dir is not null) roots.Add(dir.FullName);
        }

        foreach (var root in roots)
        {
            try
            {
                foreach (var candidate in Directory.EnumerateDirectories(root, "*", SearchOption.TopDirectoryOnly))
                {
                    var name = Path.GetFileName(candidate);
                    if (name is "mods" or "kubejs" or "config") continue;
                    var hasMods = Directory.Exists(Path.Combine(candidate, "mods"));
                    var hasKubejs = Directory.Exists(Path.Combine(candidate, "kubejs"));
                    var hasMc = Directory.Exists(Path.Combine(candidate, ".minecraft"));
                    if (hasMods || hasKubejs || hasMc)
                        result.Add(new { label = candidate, path = candidate, reason = "包含 mods/kubejs 目录" });
                    if (result.Count >= 8) break;
                }
            }
            catch
            {
                // ignore unreadable roots
            }
            if (result.Count >= 8) break;
        }
        return result;
    }

    // ---------------- documents ----------------

    private static void MapDocuments(WebApplication app)
    {
        app.MapGet("/api/document", (string kind, string? file, string? ns, string? @namespace, string? lang, string? path, DocumentService documents) =>
        {
            try
            {
                var doc = documents.Load(new DocumentQuery
                {
                    Kind = kind,
                    File = file,
                    Namespace = string.IsNullOrWhiteSpace(ns) ? @namespace : ns,
                    Lang = lang,
                    Path = path
                });
                return Results.Json(doc.Dto, JsonOut);
            }
            catch (Exception ex)
            {
                return BadRequest(ex.Message);
            }
        });

        app.MapPost("/api/document", async (HttpRequest request, DocumentService documents) =>
        {
            var query = await ReadJsonAsync<DocumentQuery>(request);
            if (query is null) return BadRequest("请求体不是合法的文档查询");
            try
            {
                return Results.Json(documents.Load(query).Dto, JsonOut);
            }
            catch (Exception ex)
            {
                return BadRequest(ex.Message);
            }
        });
    }

    // ---------------- translation ----------------

    private static void MapTranslation(WebApplication app)
    {
        app.MapPost("/api/translate", async (HttpRequest request, DocumentService documents, TranslationEngine engine, SettingsStore store) =>
        {
            var body = await ReadJsonAsync<TranslateBody>(request);
            if (body is null) return BadRequest("请求体不是合法的翻译请求");

            try
            {
                var doc = documents.Load(new DocumentQuery
                {
                    Kind = body.Kind,
                    File = body.File,
                    Namespace = body.Namespace,
                    Lang = body.Lang,
                    Path = body.Path
                });

                var entries = body.Entries is { Count: > 0 }
                    ? body.Entries
                    : doc.Dto.Entries;

                var translationRequest = new TranslationRequest
                {
                    DocumentId = doc.Dto.Id,
                    Kind = doc.Dto.Kind,
                    DisplayName = doc.Dto.DisplayName,
                    SourceLanguage = doc.Dto.SourceLanguage,
                    TargetLanguage = string.IsNullOrWhiteSpace(body.TargetLanguage) ? doc.Dto.TargetLanguage : body.TargetLanguage!,
                    Mode = string.IsNullOrWhiteSpace(body.Mode) ? "llm" : body.Mode!,
                    Provider = body.Provider,
                    Context = body.Context ?? doc.Dto.DisplayName,
                    SystemPrompt = string.IsNullOrWhiteSpace(body.SystemPrompt)
                        ? store.Current.LlmSystemPrompt
                        : body.SystemPrompt,
                    Entries = entries,
                    Glossary = body.Glossary,
                    AskUserEnabled = body.AskUser ?? store.Current.AskUserEnabled,
                    BatchSize = body.BatchSize ?? store.Current.BatchSize,
                    SkipExisting = body.SkipExisting ?? true,
                    Existing = doc.Dto.Existing
                };

                var job = engine.Start(translationRequest, store.Current);
                return Results.Json(new { jobId = job.Id }, JsonOut);
            }
            catch (Exception ex)
            {
                return BadRequest(ex.Message);
            }
        });

        app.MapGet("/api/translate/{jobId}", (string jobId, TranslationEngine engine) =>
        {
            var job = engine.Get(jobId);
            return job is null ? NotFound("任务不存在或已过期") : Results.Json(job, JsonOut);
        });

        app.MapPost("/api/translate/{jobId}/cancel", (string jobId, TranslationEngine engine) =>
            Results.Json(new { ok = engine.Cancel(jobId) }, JsonOut));

        app.MapPost("/api/translate/{jobId}/answer", async (string jobId, HttpRequest request, TranslationEngine engine) =>
        {
            var body = await ReadJsonAsync<AnswerRequest>(request);
            if (body is null || string.IsNullOrWhiteSpace(body.QuestionId)) return BadRequest("缺少 questionId");
            var ok = engine.Answer(jobId, body.QuestionId, body.Answer ?? "");
            return Results.Json(new { ok }, JsonOut);
        });
    }

    // ---------------- export ----------------

    private static void MapExport(WebApplication app)
    {
        app.MapPost("/api/export/lang", async (HttpRequest request, ExportService exports) =>
        {
            var body = await ReadJsonAsync<ExportLangRequest>(request);
            if (body is null) return BadRequest("请求体不合法");
            try
            {
                var (bytes, name) = exports.BuildLangFile(body);
                return Download(bytes, name);
            }
            catch (Exception ex)
            {
                return BadRequest(ex.Message);
            }
        });

        app.MapPost("/api/export/resourcepack", async (HttpRequest request, ExportService exports) =>
        {
            var body = await ReadJsonAsync<ExportPackRequest>(request);
            if (body is null) return BadRequest("请求体不合法");
            try
            {
                var (bytes, name) = exports.BuildResourcePack(body);
                return Download(bytes, name);
            }
            catch (Exception ex)
            {
                return BadRequest(ex.Message);
            }
        });

        app.MapPost("/api/export/kubejs-overlay", async (HttpRequest request, ExportService exports) =>
        {
            var body = await ReadJsonAsync<ExportOverlayRequest>(request);
            if (body is null) return BadRequest("请求体不合法");
            try
            {
                var (bytes, name) = exports.BuildKubejsOverlay(body);
                return Download(bytes, name);
            }
            catch (Exception ex)
            {
                return BadRequest(ex.Message);
            }
        });

        app.MapPost("/api/export/ftb-overlay", async (HttpRequest request, ExportService exports) =>
        {
            var body = await ReadJsonAsync<ExportOverlayRequest>(request);
            if (body is null) return BadRequest("请求体不合法");
            try
            {
                var (bytes, name) = exports.BuildFtbOverlay(body);
                return Download(bytes, name);
            }
            catch (Exception ex)
            {
                return BadRequest(ex.Message);
            }
        });

        app.MapPost("/api/export/write", async (HttpRequest request, ExportService exports) =>
        {
            var body = await ReadJsonAsync<ExportWriteRequest>(request);
            if (body is null) return BadRequest("请求体不合法");
            try
            {
                return Results.Json(new { written = exports.WriteBack(body) }, JsonOut);
            }
            catch (Exception ex)
            {
                return BadRequest(ex.Message);
            }
        });
    }

    // ---------------- helpers ----------------

    private static readonly long StartupTime = DateTimeOffset.UtcNow.ToUnixTimeMilliseconds();

    private static IResult Download(byte[] bytes, string fileName)
    {
        var safe = fileName.Replace("\"", "");
        return Results.File(bytes, "application/octet-stream", safe,
            enableRangeProcessing: false,
            lastModified: null,
            entityTag: null);
    }

    private static IResult BadRequest(string message)
        => Results.Json(new { error = message }, JsonOut, statusCode: 400);

    private static IResult NotFound(string message)
        => Results.Json(new { error = message }, JsonOut, statusCode: 404);

    private static async Task<T?> ReadJsonAsync<T>(HttpRequest request) where T : class
    {
        try
        {
            using var reader = new StreamReader(request.Body, System.Text.Encoding.UTF8);
            var text = await reader.ReadToEndAsync().ConfigureAwait(false);
            if (string.IsNullOrWhiteSpace(text)) return null;
            return JsonSerializer.Deserialize<T>(text, new JsonSerializerOptions
            {
                PropertyNameCaseInsensitive = true,
                PropertyNamingPolicy = JsonNamingPolicy.CamelCase,
                NumberHandling = System.Text.Json.Serialization.JsonNumberHandling.AllowReadingFromString
            });
        }
        catch
        {
            return null;
        }
    }

    /// <summary>Projects the workspace onto the shape documented in docs/API.md.</summary>
    private static object ToWire(WorkspaceInfo info) => ToWireDictionary(info);

    /// <summary>Same projection but as a mutable dictionary (so extra flags can be added).</summary>
    private static Dictionary<string, object?> ToWireDictionary(WorkspaceInfo info) => new(StringComparer.Ordinal)
    {
        ["path"] = info.Path,
        ["exists"] = info.Exists,
        ["opened"] = true,
        ["name"] = info.Name,
        ["modsDir"] = info.ModsDir,
        ["kubejsDir"] = info.KubejsDir,
        ["configDir"] = info.ConfigDir,
        ["gameVersion"] = info.GameVersion,
        ["loader"] = info.Loader,
        ["warnings"] = info.Warnings,
        ["candidates"] = info.Candidates,
        ["mods"] = new
        {
            found = info.Mods.Found,
            root = info.Mods.Root,
            warnings = info.Mods.Warnings,
            mods = info.Mods.Mods.Select(m => new
            {
                id = m.Id,
                fileName = m.FileName,
                name = m.Name,
                modId = m.ModId,
                version = m.Version,
                loader = m.Loader,
                path = m.Path,
                isFolder = m.IsFolder,
                enabled = m.Enabled,
                entryCount = m.EntryCount,
                translatedCount = m.TranslatedCount,
                error = m.Error,
                langFiles = m.LangFiles.Select(f => new
                {
                    @namespace = f.Namespace,
                    lang = f.Lang,
                    archivePath = f.ArchivePath,
                    entryCount = f.EntryCount,
                    hasZhCn = f.HasZhCn
                })
            })
        },
        ["kubejs"] = new
        {
            found = info.Kubejs.Found,
            root = info.Kubejs.Root,
            namespaces = info.Kubejs.Namespaces,
            warnings = info.Kubejs.Warnings,
            files = info.Kubejs.Files.Select(f => new
            {
                @namespace = f.Namespace,
                lang = f.Lang,
                relativePath = f.RelativePath,
                entryCount = f.EntryCount,
                hasZhCn = f.HasZhCn
            })
        },
        ["ftb"] = new
        {
            found = info.Ftb.Found,
            root = info.Ftb.Root,
            languages = info.Ftb.Languages,
            warnings = info.Ftb.Warnings,
            files = info.Ftb.Files.Select(f => new
            {
                lang = f.Lang,
                relativePath = f.RelativePath,
                entryCount = f.EntryCount,
                hasZhCn = f.HasZhCn
            })
        },
        ["scannedAt"] = info.ScannedAt
    };
}

/// <summary>Request body for POST /api/translate.</summary>
public sealed class TranslateBody
{
    public string Kind { get; set; } = "mod";
    public string? File { get; set; }
    public string? Namespace { get; set; }
    public string? Lang { get; set; }
    public string? Path { get; set; }
    public string? Mode { get; set; }
    public string? Provider { get; set; }
    public string? Context { get; set; }
    public string? SystemPrompt { get; set; }
    public List<LangEntry>? Entries { get; set; }
    public Dictionary<string, string>? Glossary { get; set; }
    public bool? AskUser { get; set; }
    public bool? SkipExisting { get; set; }
    public int? BatchSize { get; set; }
    public string? TargetLanguage { get; set; }
}
