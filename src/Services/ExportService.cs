using System.Text.Json.Nodes;
using DstMcI18n.Core;

namespace DstMcI18n.Services;

/// <summary>Everything the export endpoints need: entry application, pack building, disk writes.</summary>
public sealed class ExportService
{
    private readonly WorkspaceSession _session;
    private readonly DocumentService _documents;

    public ExportService(WorkspaceSession session, DocumentService documents)
    {
        _session = session;
        _documents = documents;
    }

    /// <summary>Serializes a document with the given translations applied, honouring its format.</summary>
    public byte[] BuildFileContent(LoadedDocument doc, IEnumerable<LangEntry> entries)
    {
        var list = entries.ToList();
        if (doc.Dto.Format == "snbt")
        {
            var tree = ApplyEntries(doc, list);
            var text = Snbt.Serialize(tree, doc.RawNumbers);
            return new System.Text.UTF8Encoding(false).GetBytes(text);
        }

        var json = ApplyEntries(doc, list);
        var jsonText = JsonUtil.Serialize(json, indented: true) + "\n";
        return new System.Text.UTF8Encoding(false).GetBytes(jsonText);
    }

    /// <summary>Applies translations onto a clone of the source tree.</summary>
    public static JsonNode? ApplyEntries(LoadedDocument doc, IReadOnlyList<LangEntry> entries)
    {
        var tree = doc.Tree?.DeepClone();
        if (tree is null) return BuildFlat(entries);

        var isFlat = tree is JsonObject obj &&
                     obj.All(kv => kv.Value is JsonValue v && v.TryGetValue<string>(out _));

        if (isFlat)
        {
            var target = (JsonObject)tree;
            foreach (var entry in entries) target[entry.Key] = JsonValue.Create(entry.Value);
            return target;
        }

        foreach (var entry in entries)
        {
            var segments = doc.KeySegments.TryGetValue(entry.Key, out var s) ? s : entry.Key.Split('.');
            JsonUtil.Apply(tree, new[] { new LangEntry { Key = entry.Key, Value = entry.Value, Segments = segments } });
        }
        return tree;
    }

    /// <summary>
    /// Builds one complete language table out of three layers, so an exported pack never ships
    /// a partial language file. Priority (high to low): entries sent by the UI, translations
    /// already on disk (zh_cn), source text. Keys only present in a lower layer are appended,
    /// so nothing is silently dropped.
    /// </summary>
    public static List<LangEntry> MergeEntries(LoadedDocument doc, IReadOnlyList<LangEntry>? translated)
    {
        var existing = new Dictionary<string, string>(StringComparer.Ordinal);
        foreach (var entry in doc.Dto.Existing)
        {
            if (!string.IsNullOrWhiteSpace(entry.Key)) existing[entry.Key] = entry.Value ?? "";
        }

        var map = new Dictionary<string, string>(StringComparer.Ordinal);
        if (translated is not null)
        {
            foreach (var entry in translated)
            {
                if (entry is null || string.IsNullOrWhiteSpace(entry.Key)) continue;
                map[entry.Key] = entry.Value ?? "";
            }
        }

        var result = new List<LangEntry>(doc.Dto.Entries.Count + map.Count + existing.Count);
        var seen = new HashSet<string>(StringComparer.Ordinal);
        foreach (var entry in doc.Dto.Entries)
        {
            seen.Add(entry.Key);
            var value = entry.Value;
            if (existing.TryGetValue(entry.Key, out var zh)) value = zh;
            if (map.TryGetValue(entry.Key, out var translatedValue)) value = translatedValue;
            result.Add(new LangEntry { Key = entry.Key, Value = value, Segments = entry.Segments });
        }

        foreach (var kv in existing)
        {
            if (!seen.Add(kv.Key)) continue;
            result.Add(new LangEntry { Key = kv.Key, Value = kv.Value });
        }

        foreach (var kv in map)
        {
            if (!seen.Add(kv.Key)) continue;
            result.Add(new LangEntry { Key = kv.Key, Value = kv.Value });
        }
        return result;
    }

    private static JsonNode BuildFlat(IEnumerable<LangEntry> entries)
    {
        var obj = new JsonObject();
        foreach (var entry in entries) obj[entry.Key] = JsonValue.Create(entry.Value);
        return obj;
    }

    // ---------------- resource pack (mod) ----------------

    public (byte[] bytes, string fileName) BuildResourcePack(ExportPackRequest request)
    {
        var doc = _documents.Load(new DocumentQuery
        {
            Kind = "mod",
            File = request.File,
            Namespace = request.Namespace,
            Lang = request.Lang
        });

        var targetLang = NormalizeLang(request.TargetLanguage);
        var ns = SanitizeNamespace(doc.Dto.NamespaceName ?? "minecraft");

        var options = new PackOptions
        {
            PackName = string.IsNullOrWhiteSpace(request.PackName) ? "汉化资源包" : request.PackName!.Trim(),
            PackVersion = string.IsNullOrWhiteSpace(request.PackVersion) ? "1.0.0" : request.PackVersion!.Trim(),
            McVersion = request.McVersion,
            PackFormat = request.PackFormat,
            TargetLanguage = targetLang,
            Description = string.IsNullOrWhiteSpace(request.Description)
                ? doc.Dto.DisplayName + " 汉化资源包 v" + (request.PackVersion ?? "1.0.0")
                : request.Description!
        };

        var bytes = Exporters.BuildResourcePack(ns, MergeEntries(doc, request.Entries), options);
        var name = Exporters.SafeFileName(
            (string.IsNullOrWhiteSpace(request.PackName) ? "汉化资源包" : request.PackName) +
            "-" + options.PackVersion + "-" + ns + ".zip", "resourcepack.zip");
        return (bytes, name);
    }

    // ---------------- overlay packs ----------------

    public (byte[] bytes, string fileName) BuildKubejsOverlay(ExportOverlayRequest request)
    {
        var info = _session.Require();
        if (info.Kubejs.Root is null) throw new ArgumentException("没有找到 kubejs 目录");
        var targetLang = NormalizeLang(request.TargetLanguage);
        var workspaceName = WorkspaceName(info);

        // No explicit file list -> the pack covers every kubejs language file of the workspace.
        var plan = BuildOverlayPlan(request, info.Kubejs.Files.Select(f => (f.RelativePath, (IReadOnlyList<LangEntry>?)null)));
        var files = BuildOverlayFiles("kubejs", "kubejs", targetLang, plan);
        if (files.Count == 0) throw new ArgumentException("没有可导出的 kubejs 语言文件");

        var options = BuildOverlayOptions(workspaceName + " KubeJS 覆盖包", targetLang,
            "KubeJS 汉化覆盖包，解压到整合包根目录覆盖 kubejs 文件夹即可。");
        var bytes = Exporters.BuildOverlayZip(files, options);
        return (bytes, Exporters.SafeFileName(workspaceName + "-kubejs-" + targetLang + ".zip", "kubejs-overlay.zip"));
    }

    public (byte[] bytes, string fileName) BuildFtbOverlay(ExportOverlayRequest request)
    {
        var info = _session.Require();
        if (info.Ftb.Root is null) throw new ArgumentException("没有找到 config/ftbquests 目录");
        var targetLang = NormalizeLang(request.TargetLanguage);
        var workspaceName = WorkspaceName(info);

        // No explicit file list -> the pack covers every ftbquests language file of the workspace.
        var plan = BuildOverlayPlan(request, info.Ftb.Files.Select(f => (f.RelativePath, (IReadOnlyList<LangEntry>?)null)));
        var files = BuildOverlayFiles("ftb", "config/ftbquests", targetLang, plan);
        if (files.Count == 0) throw new ArgumentException("没有可导出的 ftbquests 语言文件");

        var options = BuildOverlayOptions(workspaceName + " FTB Quests 覆盖包", targetLang,
            "FTB Quests 汉化覆盖包，解压到整合包根目录覆盖 config/ftbquests 文件夹即可。");
        var bytes = Exporters.BuildOverlayZip(files, options);
        return (bytes, Exporters.SafeFileName(workspaceName + "-ftbquests-" + targetLang + ".zip", "ftbquests-overlay.zip"));
    }

    /// <summary>
    /// Chooses which files an overlay covers: the caller list when present, otherwise every
    /// language file the last scan found. Source-language files are ordered first so that,
    /// when several files map onto the same target language path, the source one wins.
    /// </summary>
    private static List<(string Path, IReadOnlyList<LangEntry>? Entries)> BuildOverlayPlan(
        ExportOverlayRequest request,
        IEnumerable<(string Path, IReadOnlyList<LangEntry>? Entries)> scanned)
    {
        var plan = new List<(string Path, IReadOnlyList<LangEntry>? Entries)>();
        var requested = request.Files ?? new List<OverlayFileRequest>();
        if (requested.Count > 0)
        {
            foreach (var file in requested)
            {
                if (file is null || string.IsNullOrWhiteSpace(file.Path)) continue;
                plan.Add((file.Path, file.Entries));
            }
            return plan;
        }

        var targetLang = NormalizeLang(request.TargetLanguage);
        foreach (var file in scanned)
        {
            if (string.IsNullOrWhiteSpace(file.Path)) continue;
            // Only files whose language name can be swapped are real language files; quest
            // data files (.snbt chapters etc.) must never be copied into an overlay pack.
            if (DocumentService.SwapLang(file.Path.Replace('\\', '/'), targetLang) is null) continue;
            plan.Add((file.Path, file.Entries));
        }

        // Files already in the target language go last, so the source-language document
        // (which carries the full table) wins when several map onto the same zip path.
        return plan
            .OrderBy(item => IsLanguagePath(item.Path, targetLang) ? 1 : 0)
            .ToList();
    }

    /// <summary>Loads every planned document and renders it as one complete overlay file.</summary>
    private List<OverlayFile> BuildOverlayFiles(
        string kind,
        string zipPrefix,
        string targetLang,
        IReadOnlyList<(string Path, IReadOnlyList<LangEntry>? Entries)> plan)
    {
        var files = new List<OverlayFile>(plan.Count);
        var seen = new HashSet<string>(StringComparer.OrdinalIgnoreCase);

        foreach (var (rawPath, entries) in plan)
        {
            var relative = rawPath.Replace('\\', '/').TrimStart('/');
            if (relative.Length == 0) continue;

            // Language name swapped to the target, extension kept (zh_cn.json / zh_cn.snbt).
            var targetPath = DocumentService.SwapLang(relative, targetLang) ?? relative;
            var zipPath = zipPrefix + "/" + targetPath.TrimStart('/');
            if (!seen.Add(zipPath)) continue;

            var doc = _documents.Load(new DocumentQuery { Kind = kind, Path = relative });
            files.Add(new OverlayFile
            {
                ZipPath = zipPath,
                // Merge source + on-disk zh_cn + UI translations: never ship a partial table.
                Content = System.Text.Encoding.UTF8.GetString(BuildFileContent(doc, MergeEntries(doc, entries)))
            });
        }

        return files;
    }

    private static bool IsLanguagePath(string relativePath, string targetLang)
    {
        var path = relativePath.Replace('\\', '/');
        var parts = path.Split('/');
        for (var i = 0; i < parts.Length - 1; i++)
        {
            if (parts[i].Equals("lang", StringComparison.OrdinalIgnoreCase) &&
                i + 1 < parts.Length &&
                Path.GetFileNameWithoutExtension(parts[i + 1]).Equals(targetLang, StringComparison.OrdinalIgnoreCase))
                return true;
        }

        return Path.GetFileNameWithoutExtension(path).Equals(targetLang, StringComparison.OrdinalIgnoreCase);
    }

    private static string WorkspaceName(WorkspaceInfo info)
    {
        if (!string.IsNullOrWhiteSpace(info.Name)) return info.Name.Trim();
        var trimmed = info.Path.TrimEnd(Path.DirectorySeparatorChar, Path.AltDirectorySeparatorChar);
        var name = Path.GetFileName(trimmed);
        return string.IsNullOrWhiteSpace(name) ? "整合包" : name;
    }

    // ---------------- single lang file / write back ----------------

    public (byte[] bytes, string fileName) BuildLangFile(ExportLangRequest request)
    {
        var doc = _documents.Load(new DocumentQuery
        {
            Kind = request.Kind,
            File = request.File,
            Namespace = request.Namespace,
            Lang = request.Lang,
            Path = request.Path
        });

        var targetLang = NormalizeLang(request.TargetLanguage);
        var bytes = BuildFileContent(doc, request.Entries);
        var fileName = string.IsNullOrWhiteSpace(request.FileName)
            ? targetLang + (doc.Dto.Format == "snbt" ? ".snbt" : ".json")
            : request.FileName!;
        return (bytes, Exporters.SafeFileName(fileName, targetLang + ".json"));
    }

    public List<string> WriteBack(ExportWriteRequest request)
    {
        var info = _session.Require();
        var doc = _documents.Load(new DocumentQuery
        {
            Kind = request.Kind,
            File = request.File,
            Namespace = request.Namespace,
            Lang = request.Lang,
            Path = request.Path
        });

        var targetLang = NormalizeLang(request.TargetLanguage);
        var bytes = BuildFileContent(doc, request.Entries);
        var written = new List<string>();

        if (request.Kind.Equals("mod", StringComparison.OrdinalIgnoreCase))
        {
            // mods live inside jars: write a resource pack style folder instead of touching the jar
            var outDir = string.IsNullOrWhiteSpace(request.OutputDirectory)
                ? Path.Combine(info.Path, "dst_i18n_output", "assets",
                    SanitizeNamespace(doc.Dto.NamespaceName ?? "minecraft"), "lang")
                : request.OutputDirectory!;
            Directory.CreateDirectory(outDir);
            var target = Path.Combine(outDir, targetLang + ".json");
            File.WriteAllBytes(target, bytes);
            written.Add(target);
            return written;
        }

        var sourcePath = doc.SourcePath ?? throw new ArgumentException("无法确定源文件路径");
        var targetPath = DocumentService.SwapLang(sourcePath.Replace('\\', '/'), targetLang);
        if (targetPath is null) throw new ArgumentException("无法推断目标文件名");
        targetPath = targetPath.Replace('/', Path.DirectorySeparatorChar);

        if (File.Exists(targetPath) && !request.Overwrite)
            throw new InvalidOperationException("目标文件已存在: " + targetPath);

        var dir = Path.GetDirectoryName(targetPath);
        if (!string.IsNullOrEmpty(dir)) Directory.CreateDirectory(dir);
        File.WriteAllBytes(targetPath, bytes);
        written.Add(targetPath);
        return written;
    }

    /// <summary>
    /// Overlay packs no longer carry a version number: PackName is only used in the README text.
    /// </summary>
    private static PackOptions BuildOverlayOptions(string name, string targetLang, string description)
        => new()
        {
            PackName = string.IsNullOrWhiteSpace(name) ? "汉化覆盖包" : name.Trim(),
            PackVersion = string.Empty,
            TargetLanguage = targetLang,
            Description = description
        };

    public static string NormalizeLang(string? lang)
    {
        var value = string.IsNullOrWhiteSpace(lang) ? "zh_cn" : lang!.Trim().ToLowerInvariant();
        if (!System.Text.RegularExpressions.Regex.IsMatch(value, "^[a-z]{2,3}_[a-z]{2,4}$")) return "zh_cn";
        return value;
    }

    public static string SanitizeNamespace(string ns)
    {
        var clean = new string(ns.ToLowerInvariant().Where(c => char.IsLetterOrDigit(c) || c is '_' or '.' or '-').ToArray());
        return clean.Length == 0 ? "minecraft" : clean;
    }
}
