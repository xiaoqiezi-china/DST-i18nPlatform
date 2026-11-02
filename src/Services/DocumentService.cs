using System.Text.Json.Nodes;
using DstMcI18n.Core;

namespace DstMcI18n.Services;

/// <summary>A document plus the parsed source tree needed for lossless export.</summary>
public sealed class LoadedDocument
{
    public DocumentResult Dto { get; set; } = new();
    public JsonNode? Tree { get; set; }
    public Dictionary<string, string> RawNumbers { get; set; } = new(StringComparer.Ordinal);
    public Dictionary<string, string[]> KeySegments { get; set; } = new(StringComparer.Ordinal);
    public string? SourcePath { get; set; }
}

/// <summary>Builds key/value tables (and keeps the source tree) for the opened workspace.</summary>
public sealed class DocumentService
{
    private readonly WorkspaceSession _session;

    public DocumentService(WorkspaceSession session)
    {
        _session = session;
    }

    public LoadedDocument Load(DocumentQuery query)
    {
        var info = _session.Require();
        return query.Kind.ToLowerInvariant() switch
        {
            "mod" => LoadMod(info, query),
            "kubejs" => LoadKubejs(info, query),
            "ftb" or "ftbquests" => LoadFtb(info, query),
            _ => throw new ArgumentException("未知的文档类型: " + query.Kind)
        };
    }

    private static LoadedDocument LoadMod(WorkspaceInfo info, DocumentQuery query)
    {
        if (string.IsNullOrWhiteSpace(query.File)) throw new ArgumentException("缺少 mod 文件名");
        var mod = info.Mods.Mods.FirstOrDefault(m => m.FileName.Equals(query.File, StringComparison.OrdinalIgnoreCase))
                  ?? throw new ArgumentException("未找到该 mod: " + query.File);

        var sourceLang = string.IsNullOrWhiteSpace(query.Lang) ? "en_us" : query.Lang!.ToLowerInvariant();
        var candidates = mod.LangFiles.Where(f => f.Lang == sourceLang).ToList();
        if (candidates.Count == 0) candidates = mod.LangFiles.Where(f => f.Lang is "en_us").ToList();
        if (candidates.Count == 0) throw new ArgumentException("该 mod 没有可用的源语言文件");

        var source = string.IsNullOrWhiteSpace(query.Namespace)
            ? candidates[0]
            : candidates.FirstOrDefault(c => c.Namespace.Equals(query.Namespace, StringComparison.OrdinalIgnoreCase))
              ?? throw new ArgumentException("该 mod 的命名空间 " + query.Namespace + " 没有 " + sourceLang + " 语言文件");

        var text = ReadModText(mod, source.ArchivePath)
                   ?? throw new ArgumentException("无法读取语言文件: " + source.ArchivePath);
        var tree = JsonUtil.Parse(text);
        var entries = JsonUtil.Flatten(tree);

        var existing = new Dictionary<string, string>(StringComparer.Ordinal);
        var zhFile = mod.LangFiles.FirstOrDefault(f =>
            f.Namespace.Equals(source.Namespace, StringComparison.OrdinalIgnoreCase) && f.Lang is "zh_cn");
        if (zhFile is not null)
        {
            var zhText = ReadModText(mod, zhFile.ArchivePath);
            if (zhText is not null)
            {
                try
                {
                    foreach (var e in JsonUtil.Flatten(JsonUtil.Parse(zhText))) existing[e.Key] = e.Value;
                }
                catch
                {
                    // malformed existing translations are ignored
                }
            }
        }

        var displayName = string.IsNullOrWhiteSpace(mod.Name) ? mod.FileName : mod.Name;
        if (!string.IsNullOrWhiteSpace(mod.Version)) displayName += " " + mod.Version;

        return new LoadedDocument
        {
            Tree = tree,
            KeySegments = BuildSegments(entries),
            Dto = new DocumentResult
            {
                Id = "mod|" + mod.FileName + "|" + source.Namespace + "|" + source.Lang,
                Kind = "mod",
                OwnerId = mod.ModId,
                DisplayName = displayName,
                RelativePath = source.ArchivePath,
                Format = "json",
                SourceLanguage = source.Lang,
                TargetLanguage = "zh_cn",
                Entries = entries,
                Existing = existing,
                HasExisting = existing.Count > 0,
                ModId = mod.ModId,
                NamespaceName = source.Namespace,
                ModFileName = mod.FileName
            }
        };
    }

    /// <summary>Reads a language file from a jar or from an unpacked mod folder.</summary>
    private static string? ReadModText(ModInfo mod, string archivePath)
        => mod.IsFolder
            ? (File.Exists(Path.Combine(mod.Path, archivePath.Replace('/', Path.DirectorySeparatorChar)))
                ? File.ReadAllText(Path.Combine(mod.Path, archivePath.Replace('/', Path.DirectorySeparatorChar)))
                : null)
            : JarReader.ReadText(mod.Path, archivePath);

    private static LoadedDocument LoadKubejs(WorkspaceInfo info, DocumentQuery query)
    {
        if (string.IsNullOrWhiteSpace(query.Path)) throw new ArgumentException("缺少 kubejs 语言文件路径");
        var root = info.Kubejs.Root ?? throw new ArgumentException("没有找到 kubejs 目录");
        var full = SafeJoin(root, query.Path!);
        if (!File.Exists(full)) throw new ArgumentException("文件不存在: " + query.Path);

        var tree = JsonUtil.Parse(File.ReadAllText(full));
        var entries = JsonUtil.Flatten(tree);
        var sourceLang = Path.GetFileNameWithoutExtension(full).ToLowerInvariant();

        var existing = new Dictionary<string, string>(StringComparer.Ordinal);
        var zhPath = Path.Combine(Path.GetDirectoryName(full)!, "zh_cn.json");
        if (File.Exists(zhPath) && !string.Equals(zhPath, full, StringComparison.OrdinalIgnoreCase))
        {
            try
            {
                foreach (var e in JsonUtil.Flatten(JsonUtil.Parse(File.ReadAllText(zhPath)))) existing[e.Key] = e.Value;
            }
            catch
            {
                // ignore
            }
        }

        var ns = GuessNamespace(root, full);
        var rel = query.Path!.Replace('\\', '/');
        return new LoadedDocument
        {
            Tree = tree,
            KeySegments = BuildSegments(entries),
            SourcePath = full,
            Dto = new DocumentResult
            {
                Id = "kubejs|" + rel,
                Kind = "kubejs",
                OwnerId = ns,
                DisplayName = "KubeJS · " + ns + " · " + sourceLang,
                RelativePath = rel,
                Format = "json",
                SourceLanguage = sourceLang,
                TargetLanguage = "zh_cn",
                Entries = entries,
                Existing = existing,
                HasExisting = existing.Count > 0,
                NamespaceName = ns
            }
        };
    }

    private static LoadedDocument LoadFtb(WorkspaceInfo info, DocumentQuery query)
    {
        if (string.IsNullOrWhiteSpace(query.Path)) throw new ArgumentException("缺少 ftbquests 文件路径");
        var root = info.Ftb.Root ?? throw new ArgumentException("没有找到 config/ftbquests 目录");
        var full = SafeJoin(root, query.Path!);
        if (!File.Exists(full)) throw new ArgumentException("文件不存在: " + query.Path);

        if (!Snbt.TryParse(File.ReadAllText(full), out var tree, out var rawNumbers, out var error))
            throw new ArgumentException("SNBT 解析失败: " + error);

        var entries = JsonUtil.Flatten(tree);
        var rel = query.Path!.Replace('\\', '/');
        var sourceLang = GuessLang(rel);

        var existing = new Dictionary<string, string>(StringComparer.Ordinal);
        var zhRel = SwapLang(rel, "zh_cn");
        if (zhRel is not null && !zhRel.Equals(rel, StringComparison.OrdinalIgnoreCase))
        {
            var zhFull = SafeJoin(root, zhRel);
            if (File.Exists(zhFull))
            {
                try
                {
                    if (Snbt.TryParse(File.ReadAllText(zhFull), out var zhTree, out _, out _))
                        foreach (var e in JsonUtil.Flatten(zhTree)) existing[e.Key] = e.Value;
                }
                catch
                {
                    // ignore
                }
            }
        }

        return new LoadedDocument
        {
            Tree = tree,
            KeySegments = BuildSegments(entries),
            RawNumbers = rawNumbers,
            SourcePath = full,
            Dto = new DocumentResult
            {
                Id = "ftb|" + rel,
                Kind = "ftb",
                OwnerId = "ftbquests",
                DisplayName = "FTB Quests · " + rel,
                RelativePath = rel,
                Format = "snbt",
                SourceLanguage = sourceLang,
                TargetLanguage = "zh_cn",
                Entries = entries,
                Existing = existing,
                HasExisting = existing.Count > 0
            }
        };
    }

    private static Dictionary<string, string[]> BuildSegments(IEnumerable<LangEntry> entries)
    {
        var map = new Dictionary<string, string[]>(StringComparer.Ordinal);
        foreach (var entry in entries)
        {
            if (entry.Segments is { Length: > 0 }) map[entry.Key] = entry.Segments;
        }
        return map;
    }

    public static string GuessLang(string relativePath)
    {
        var parts = relativePath.Split('/');
        for (var i = 0; i < parts.Length - 1; i++)
        {
            if (parts[i].Equals("lang", StringComparison.OrdinalIgnoreCase)) return parts[i + 1].ToLowerInvariant();
        }
        var fileName = Path.GetFileNameWithoutExtension(relativePath).ToLowerInvariant();
        return System.Text.RegularExpressions.Regex.IsMatch(fileName, @"^[a-z]{2}_[a-z]{2}$") ? fileName : "en_us";
    }

    public static string? SwapLang(string relativePath, string target)
    {
        var parts = relativePath.Split('/');
        for (var i = 0; i < parts.Length - 1; i++)
        {
            if (parts[i].Equals("lang", StringComparison.OrdinalIgnoreCase))
            {
                var ext = Path.GetExtension(parts[i + 1]);
                parts[i + 1] = target + ext;
                return string.Join('/', parts);
            }
        }
        var fileName = Path.GetFileNameWithoutExtension(relativePath);
        if (System.Text.RegularExpressions.Regex.IsMatch(fileName, @"^[a-z]{2}_[a-z]{2}$"))
        {
            parts[^1] = target + Path.GetExtension(relativePath);
            return string.Join('/', parts);
        }
        return null;
    }

    public static string SafeJoin(string root, string relative)
    {
        var rel = relative.Replace('\\', '/').TrimStart('/');
        var full = Path.GetFullPath(Path.Combine(root, rel.Replace('/', Path.DirectorySeparatorChar)));
        var rootFull = Path.GetFullPath(root);
        if (!full.StartsWith(rootFull, StringComparison.OrdinalIgnoreCase))
            throw new ArgumentException("非法的相对路径: " + relative);
        return full;
    }

    private static string GuessNamespace(string kubejsRoot, string fullPath)
    {
        var rel = Path.GetRelativePath(kubejsRoot, fullPath).Replace('\\', '/');
        var parts = rel.Split('/');
        for (var i = 0; i < parts.Length - 1; i++)
        {
            if (parts[i].Equals("assets", StringComparison.OrdinalIgnoreCase) && i + 1 < parts.Length) return parts[i + 1];
        }
        return "kubejs";
    }
}
