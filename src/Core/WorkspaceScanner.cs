using System.Text.Json.Nodes;
using System.Text.RegularExpressions;

namespace DstMcI18n.Core;

/// <summary>Callback used by <see cref="WorkspaceScanner"/> to report scan progress.</summary>
public sealed class ScanReporter
{
    private readonly Action<ScanProgress>? _onUpdate;

    public ScanReporter(Action<ScanProgress>? onUpdate, CancellationToken cancellationToken)
    {
        _onUpdate = onUpdate;
        CancellationToken = cancellationToken;
        Progress = new ScanProgress();
    }

    public ScanProgress Progress { get; }
    public CancellationToken CancellationToken { get; }

    public void Phase(string phase, string label, int total = 0)
    {
        Progress.Phase = phase;
        Progress.PhaseLabel = label;
        Progress.Total = total;
        Progress.Current = 0;
        Progress.Percent = 0;
        Push();
    }

    public void Step(string item, int? current = null, int? total = null)
    {
        if (current.HasValue) Progress.Current = current.Value;
        else Progress.Current++;
        if (total.HasValue) Progress.Total = total.Value;
        Progress.CurrentItem = item ?? "";
        Progress.Percent = Progress.Total <= 0
            ? 0
            : (int)Math.Clamp(Progress.Current * 100.0 / Progress.Total, 0, 100);
        Push();
    }

    public void Push()
    {
        if (_onUpdate is null) return;
        try { _onUpdate(Progress); } catch { /* progress reporting must never break a scan */ }
    }

    public void ThrowIfCancelled() => CancellationToken.ThrowIfCancellationRequested();
}

/// <summary>Discovers mods / kubejs / config language files inside a Minecraft instance.</summary>
public sealed class WorkspaceScanner
{
    private static readonly Regex LangPathRegex = new(
        @"^(?:(?<root>.+?)/)?assets/(?<ns>[a-zA-Z0-9_.-]+)/lang/(?<lang>[a-zA-Z0-9_-]+)\.json$",
        RegexOptions.Compiled | RegexOptions.IgnoreCase);

    private static readonly string[] SkipJarSuffixes = { ".jar.disabled", ".jar.bak", ".zip" };

    /// <summary>Quick structural check: does this folder look like a Minecraft instance?</summary>
    public static (bool valid, List<string> candidates) Validate(string path)
    {
        var candidates = new List<string>();
        if (string.IsNullOrWhiteSpace(path) || !Directory.Exists(path))
            return (false, candidates);

        var hasMods = Directory.Exists(System.IO.Path.Combine(path, "mods"));
        var hasKubejs = Directory.Exists(System.IO.Path.Combine(path, "kubejs"));
        var hasConfig = Directory.Exists(System.IO.Path.Combine(path, "config"));
        var hasMinecraft = Directory.Exists(System.IO.Path.Combine(path, ".minecraft"));
        if (hasMods || hasKubejs || hasConfig || hasMinecraft) return (true, candidates);

        candidates.AddRange(FindCandidates(path));
        return (candidates.Count > 0, candidates);
    }

    public WorkspaceInfo Scan(string path) => Scan(path, null);

    public WorkspaceInfo Scan(string path, ScanReporter? reporter)
    {
        var info = new WorkspaceInfo
        {
            Path = path,
            Exists = Directory.Exists(path),
            ScannedAt = DateTimeOffset.UtcNow.ToUnixTimeMilliseconds()
        };
        if (!info.Exists)
        {
            info.Warnings.Add("工作目录不存在: " + path);
            return info;
        }

        reporter?.Phase("prepare", "正在识别目录结构…");
        info.Name = new DirectoryInfo(path.TrimEnd('\\', '/')).Name;
        if (string.IsNullOrEmpty(info.Name)) info.Name = path;
        DetectGameVersion(info);

        info.ModsDir = FindDir(info, "mods");
        info.KubejsDir = FindDir(info, "kubejs");
        info.ConfigDir = FindDir(info, "config");

        if (info.ModsDir is null && info.KubejsDir is null && info.ConfigDir is null)
        {
            info.Candidates.AddRange(FindCandidates(path));
            if (info.Candidates.Count > 0)
                info.Warnings.Add("当前目录下没有直接找到 mods/kubejs/config，可能选择了启动器目录。请在下方选择具体的版本隔离目录。");
            else
                info.Warnings.Add("没有找到 mods、kubejs 或 config 目录，请确认选择的是整合包 / .minecraft 目录。");
        }

        reporter?.ThrowIfCancelled();
        info.Mods = ScanMods(info, reporter);

        reporter?.ThrowIfCancelled();
        reporter?.Phase("kubejs", "正在解析 KubeJS…");
        info.Kubejs = ScanKubejs(info);

        reporter?.ThrowIfCancelled();
        reporter?.Phase("ftb", "正在解析 FTB Quests…");
        info.Ftb = ScanFtbQuest(info);

        reporter?.Phase("finish", "扫描完成", 1);
        reporter?.Step("", 1, 1);
        return info;
    }

    private static void DetectGameVersion(WorkspaceInfo info)
    {
        try
        {
            var list = Directory.GetFiles(info.Path, "*.json", SearchOption.TopDirectoryOnly)
                .Where(f =>
                {
                    var n = System.IO.Path.GetFileName(f);
                    return n.Equals("version.json", StringComparison.OrdinalIgnoreCase) ||
                           n.Equals("instance.json", StringComparison.OrdinalIgnoreCase) ||
                           n.Equals("mmc-pack.json", StringComparison.OrdinalIgnoreCase);
                })
                .ToList();

            foreach (var file in list)
            {
                var node = JsonUtil.Parse(File.ReadAllText(file)) as JsonObject;
                if (node is null) continue;
                foreach (var key in new[] { "name", "id", "version", "gameVersion" })
                {
                    if (node[key] is JsonValue v && v.TryGetValue<string>(out var s) && s.Length > 0)
                    {
                        info.GameVersion ??= s;
                        break;
                    }
                }
                if (node["components"] is JsonArray comps)
                {
                    foreach (var c in comps.OfType<JsonObject>())
                    {
                        var uid = c["uid"]?.GetValue<string>();
                        if (uid is "net.minecraft") info.GameVersion ??= c["version"]?.GetValue<string>();
                        if (uid is "net.fabricmc.fabric-loader") info.Loader = "fabric";
                        if (uid is "net.minecraftforge") info.Loader = "forge";
                        if (uid is "net.neoforged") info.Loader = "neoforge";
                        if (uid is "org.quiltmc.quilt-loader") info.Loader = "quilt";
                    }
                }
                if (node["libraries"] is JsonArray libs)
                {
                    foreach (var l in libs.OfType<JsonObject>())
                    {
                        var n = l["name"]?.GetValue<string>() ?? "";
                        if (n.Contains("fabric-loader", StringComparison.OrdinalIgnoreCase)) info.Loader = "fabric";
                        if (n.Contains("forge", StringComparison.OrdinalIgnoreCase)) info.Loader ??= "forge";
                        if (n.Contains("neoforge", StringComparison.OrdinalIgnoreCase)) info.Loader = "neoforge";
                    }
                }
            }
        }
        catch
        {
            // version detection is best effort
        }
    }

    private static string? FindDir(WorkspaceInfo info, string name)
    {
        var direct = System.IO.Path.Combine(info.Path, name);
        if (Directory.Exists(direct)) return direct;

        foreach (var sub in new[] { ".minecraft", "minecraft" })
        {
            var nested = System.IO.Path.Combine(info.Path, sub, name);
            if (Directory.Exists(nested)) return nested;
        }
        return null;
    }

    /// <summary>
    /// Looks for plausible instance folders below <paramref name="root"/>.
    /// Bounded to a few levels and a hard time budget: launcher roots can contain
    /// tens of thousands of files, and a validation step must stay instant.
    /// </summary>
    private static List<string> FindCandidates(string root)
    {
        var result = new List<string>();
        var deadline = DateTime.UtcNow.AddSeconds(2);
        var queue = new Queue<(string dir, int depth)>();
        queue.Enqueue((root, 0));

        while (queue.Count > 0 && DateTime.UtcNow < deadline && result.Count < 20)
        {
            var (dir, depth) = queue.Dequeue();
            if (depth >= 3) continue;

            IEnumerable<string> children;
            try
            {
                children = Directory.EnumerateDirectories(dir);
            }
            catch
            {
                continue;
            }

            foreach (var child in children)
            {
                if (DateTime.UtcNow >= deadline || result.Count >= 20) break;
                var name = System.IO.Path.GetFileName(child);
                if (name.StartsWith('.')) continue;

                var hasMods = Directory.Exists(System.IO.Path.Combine(child, "mods"));
                var hasKubejs = Directory.Exists(System.IO.Path.Combine(child, "kubejs"));
                var hasMinecraft = Directory.Exists(System.IO.Path.Combine(child, ".minecraft"));
                if (hasMods || hasKubejs || hasMinecraft)
                {
                    if (!result.Contains(child)) result.Add(child);
                    continue;
                }
                queue.Enqueue((child, depth + 1));
            }
        }

        return result.OrderBy(x => x.Length).Take(20).ToList();
    }

    private static ModsScan ScanMods(WorkspaceInfo info, ScanReporter? reporter)
    {
        var scan = new ModsScan { Found = info.ModsDir is not null, Root = info.ModsDir };
        if (info.ModsDir is null) return scan;

        List<string> jarFiles;
        List<string> folders;
        try
        {
            jarFiles = Directory.EnumerateFiles(info.ModsDir, "*", SearchOption.TopDirectoryOnly)
                .Where(f =>
                {
                    var name = System.IO.Path.GetFileName(f);
                    var ext = System.IO.Path.GetExtension(name).ToLowerInvariant();
                    if (ext is not (".jar" or ".zip")) return false;
                    if (name.EndsWith(".disabled", StringComparison.OrdinalIgnoreCase)) return false;
                    return !SkipJarSuffixes.Any(s => name.EndsWith(s, StringComparison.OrdinalIgnoreCase));
                })
                .OrderBy(f => f, StringComparer.OrdinalIgnoreCase)
                .ToList();
            folders = Directory.EnumerateDirectories(info.ModsDir, "*", SearchOption.TopDirectoryOnly)
                .OrderBy(f => f, StringComparer.OrdinalIgnoreCase)
                .ToList();
        }
        catch (Exception ex)
        {
            scan.Warnings.Add("无法读取 mods 目录: " + ex.Message);
            return scan;
        }

        var total = jarFiles.Count + folders.Count;
        reporter?.Phase("mods", "正在解析模组…", total);
        var index = 0;

        // Mods unpacked into a folder (common with some launchers / dev setups).
        foreach (var folder in folders)
        {
            reporter?.ThrowIfCancelled();
            var folderName = System.IO.Path.GetFileName(folder);
            index++;
            reporter?.Step(folderName, index, total);
            try
            {
                var assetsDir = System.IO.Path.Combine(folder, "assets");
                if (!Directory.Exists(assetsDir)) continue;
                var mod = new ModInfo
                {
                    Id = folderName,
                    FileName = folderName,
                    Path = folder,
                    Name = folderName,
                    ModId = folderName,
                    IsFolder = true
                };

                foreach (var langFile in Directory.EnumerateFiles(assetsDir, "*.json", SearchOption.AllDirectories))
                {
                    reporter?.ThrowIfCancelled();
                    var rel = System.IO.Path.GetRelativePath(folder, langFile).Replace('\\', '/');
                    var m = LangPathRegex.Match(rel);
                    if (!m.Success) continue;
                    mod.LangFiles.Add(new ModLangFile
                    {
                        Namespace = m.Groups["ns"].Value,
                        Lang = m.Groups["lang"].Value.ToLowerInvariant(),
                        ArchivePath = rel,
                        EntryCount = CountFolderEntries(langFile)
                    });
                }

                if (mod.LangFiles.Count == 0) continue;
                var hasZh = mod.LangFiles.Any(l => l.Lang is "zh_cn");
                foreach (var lf in mod.LangFiles) lf.HasZhCn = hasZh;
                mod.EntryCount = mod.LangFiles.Where(l => l.Lang is "en_us").Sum(l => l.EntryCount);
                scan.Mods.Add(mod);
                if (reporter is not null) reporter.Progress.ModsFound = scan.Mods.Count;
            }
            catch (Exception ex)
            {
                scan.Warnings.Add(folderName + ": " + ex.Message);
            }
        }

        foreach (var file in jarFiles)
        {
            reporter?.ThrowIfCancelled();
            var name = System.IO.Path.GetFileName(file);
            index++;
            reporter?.Step(name, index, total);

            try
            {
                var entries = JarReader.ListEntries(file, "");
                var langEntries = entries
                    .Select(e => (entry: e, match: LangPathRegex.Match(e.Replace('\\', '/'))))
                    .Where(t => t.match.Success)
                    .ToList();

                var meta = JarReader.ReadModMeta(file);
                var mod = new ModInfo
                {
                    Id = System.IO.Path.GetFileNameWithoutExtension(name),
                    FileName = name,
                    Path = file,
                    Name = meta.name.Length > 0 ? meta.name : System.IO.Path.GetFileNameWithoutExtension(name),
                    ModId = meta.modId.Length > 0 ? meta.modId : System.IO.Path.GetFileNameWithoutExtension(name),
                    Version = meta.version,
                    Loader = meta.loader,
                    IsFolder = false
                };

                foreach (var (entry, match) in langEntries)
                {
                    reporter?.ThrowIfCancelled();
                    mod.LangFiles.Add(new ModLangFile
                    {
                        Namespace = match.Groups["ns"].Value,
                        Lang = match.Groups["lang"].Value.ToLowerInvariant(),
                        ArchivePath = entry
                    });
                }

                if (mod.LangFiles.Count == 0) continue;
                var zh = mod.LangFiles.Any(l => l.Lang is "zh_cn");
                foreach (var lf in mod.LangFiles)
                {
                    reporter?.ThrowIfCancelled();
                    lf.HasZhCn = zh;
                    lf.EntryCount = CountEntries(file, lf.ArchivePath);
                }
                mod.EntryCount = mod.LangFiles.Where(l => l.Lang is "en_us").Sum(l => l.EntryCount);
                scan.Mods.Add(mod);
                if (reporter is not null) reporter.Progress.ModsFound = scan.Mods.Count;
            }
            catch (Exception ex)
            {
                scan.Warnings.Add(name + ": " + ex.Message);
            }
        }

        return scan;
    }

    private static int CountFolderEntries(string path)
    {
        try
        {
            return JsonUtil.Flatten(JsonUtil.Parse(File.ReadAllText(path))).Count;
        }
        catch
        {
            return 0;
        }
    }

    private static int CountEntries(string jar, string entry)
    {
        try
        {
            var text = JarReader.ReadText(jar, entry);
            if (text is null) return 0;
            var node = JsonUtil.Parse(text);
            return JsonUtil.Flatten(node).Count;
        }
        catch
        {
            return 0;
        }
    }

    private static KubejsInfo ScanKubejs(WorkspaceInfo info)
    {
        var result = new KubejsInfo { Found = info.KubejsDir is not null, Root = info.KubejsDir };
        if (info.KubejsDir is null) return result;

        try
        {
            var assetDirs = new List<string>();
            var directAssets = System.IO.Path.Combine(info.KubejsDir, "assets");
            if (Directory.Exists(directAssets)) assetDirs.Add(directAssets);

            // kubejs/assets/<namespace>/lang/*.json and also any nested assets folders.
            foreach (var dir in Directory.EnumerateDirectories(info.KubejsDir, "assets", SearchOption.AllDirectories))
            {
                if (!assetDirs.Contains(dir)) assetDirs.Add(dir);
            }

            if (assetDirs.Count == 0)
            {
                result.Warnings.Add("kubejs 目录下没有找到 assets 文件夹。");
                return result;
            }

            foreach (var assets in assetDirs)
            {
                foreach (var nsDir in Directory.EnumerateDirectories(assets))
                {
                    var ns = System.IO.Path.GetFileName(nsDir);
                    var langDir = System.IO.Path.Combine(nsDir, "lang");
                    if (!Directory.Exists(langDir)) continue;
                    if (!result.Namespaces.Contains(ns)) result.Namespaces.Add(ns);
                    var files = Directory.EnumerateFiles(langDir, "*.json", SearchOption.TopDirectoryOnly).ToList();
                    var hasZh = files.Any(f =>
                        System.IO.Path.GetFileNameWithoutExtension(f).Equals("zh_cn", StringComparison.OrdinalIgnoreCase));
                    foreach (var file in files)
                    {
                        var lang = System.IO.Path.GetFileNameWithoutExtension(file).ToLowerInvariant();
                        var entry = new KubejsLangFile
                        {
                            Namespace = ns,
                            Lang = lang,
                            RelativePath = System.IO.Path.GetRelativePath(info.KubejsDir!, file).Replace('\\', '/'),
                            HasZhCn = hasZh
                        };
                        try
                        {
                            entry.EntryCount = JsonUtil.Flatten(JsonUtil.Parse(File.ReadAllText(file))).Count;
                        }
                        catch
                        {
                            entry.EntryCount = 0;
                        }
                        result.Files.Add(entry);
                    }
                }
            }

            result.Files = result.Files
                .OrderBy(f => f.Namespace, StringComparer.OrdinalIgnoreCase)
                .ThenBy(f => f.Lang, StringComparer.OrdinalIgnoreCase)
                .ToList();
        }
        catch (Exception ex)
        {
            result.Warnings.Add("扫描 kubejs 失败: " + ex.Message);
        }
        return result;
    }

    private static FtbInfo ScanFtbQuest(WorkspaceInfo info)
    {
        var result = new FtbInfo();
        var roots = new List<string>();
        if (info.ConfigDir is not null)
        {
            var ftb = System.IO.Path.Combine(info.ConfigDir, "ftbquests");
            if (Directory.Exists(ftb)) roots.Add(ftb);
        }
        if (info.KubejsDir is not null)
        {
            var alt = System.IO.Path.Combine(info.KubejsDir, "config", "ftbquests");
            if (Directory.Exists(alt) && !roots.Contains(alt)) roots.Add(alt);
        }

        if (roots.Count == 0)
        {
            result.Warnings.Add("没有找到 config/ftbquests 目录。");
            return result;
        }

        result.Found = true;
        result.Root = roots[0];
        foreach (var root in roots)
        {
            try
            {
                // FTB Quests keeps its localized text in <quests>/lang/<locale>.snbt.
                // Only fall back to every .snbt file when no lang folder exists.
                var all = Directory.EnumerateFiles(root, "*.snbt", SearchOption.AllDirectories).ToList();
                var langFiles = all.Where(f =>
                {
                    var rel = System.IO.Path.GetRelativePath(root, f).Replace('\\', '/');
                    return rel.Split('/').Any(seg => seg.Equals("lang", StringComparison.OrdinalIgnoreCase));
                }).ToList();
                var selected = langFiles.Count > 0 ? langFiles : all;

                foreach (var file in selected)
                {
                    var rel = System.IO.Path.GetRelativePath(root, file).Replace('\\', '/');
                    var lang = GuessFtbLang(rel);
                    var entry = new FtbLangFile
                    {
                        Lang = lang,
                        RelativePath = rel
                    };
                    try
                    {
                        var text = File.ReadAllText(file);
                        if (Snbt.TryParse(text, out var node, out var raw, out _))
                        {
                            entry.EntryCount = JsonUtil.Flatten(node).Count;
                        }
                    }
                    catch
                    {
                        entry.EntryCount = 0;
                    }
                    result.Files.Add(entry);
                    if (!result.Languages.Contains(lang)) result.Languages.Add(lang);
                }
            }
            catch (Exception ex)
            {
                result.Warnings.Add("扫描 ftbquests 失败: " + ex.Message);
            }
        }

        var zh = result.Files.Any(f => f.Lang is "zh_cn");
        foreach (var f in result.Files) f.HasZhCn = zh;
        result.Files = result.Files.OrderBy(f => f.RelativePath, StringComparer.OrdinalIgnoreCase).ToList();
        return result;
    }

    private static string GuessFtbLang(string relativePath)
    {
        // FTB Quests stores localized files under lang/<locale>/ or lang/<locale>.snbt
        var parts = relativePath.Split('/');
        for (var i = 0; i < parts.Length - 1; i++)
        {
            if (parts[i].Equals("lang", StringComparison.OrdinalIgnoreCase) && i + 1 < parts.Length)
                return System.IO.Path.GetFileNameWithoutExtension(parts[i + 1]).ToLowerInvariant();
        }
        var fileName = System.IO.Path.GetFileNameWithoutExtension(relativePath).ToLowerInvariant();
        if (Regex.IsMatch(fileName, @"^[a-z]{2}_[a-z]{2}$")) return fileName;
        return "en_us";
    }
}
