using System.IO.Compression;
using System.Text;

namespace DstMcI18n.Core;

/// <summary>Reads entries out of a .jar/.zip without extracting it.</summary>
public static class JarReader
{
    public static readonly Encoding Utf8 = new UTF8Encoding(false);

    public static List<string> ListEntries(string path, string prefix)
    {
        var result = new List<string>();
        using var fs = File.Open(path, FileMode.Open, FileAccess.Read, FileShare.ReadWrite);
        using var zip = new ZipArchive(fs, ZipArchiveMode.Read);
        foreach (var e in zip.Entries)
        {
            var name = e.FullName.Replace('\\', '/');
            if (name.StartsWith(prefix, StringComparison.OrdinalIgnoreCase) && !name.EndsWith('/'))
                result.Add(name);
        }
        return result;
    }

    public static string? ReadText(string path, string entryName)
    {
        using var fs = File.Open(path, FileMode.Open, FileAccess.Read, FileShare.ReadWrite);
        using var zip = new ZipArchive(fs, ZipArchiveMode.Read);
        var entry = zip.Entries.FirstOrDefault(e =>
            e.FullName.Replace('\\', '/').Equals(entryName, StringComparison.OrdinalIgnoreCase));
        if (entry is null) return null;
        using var stream = entry.Open();
        using var reader = new StreamReader(stream, Utf8, detectEncodingFromByteOrderMarks: true);
        return reader.ReadToEnd();
    }

    public static byte[]? ReadBytes(string path, string entryName)
    {
        using var fs = File.Open(path, FileMode.Open, FileAccess.Read, FileShare.ReadWrite);
        using var zip = new ZipArchive(fs, ZipArchiveMode.Read);
        var entry = zip.Entries.FirstOrDefault(e =>
            e.FullName.Replace('\\', '/').Equals(entryName, StringComparison.OrdinalIgnoreCase));
        if (entry is null) return null;
        using var stream = entry.Open();
        using var ms = new MemoryStream();
        stream.CopyTo(ms);
        return ms.ToArray();
    }

    /// <summary>Locates the mod id from fabric.mod.json / META-INF/mods.toml / quilt.mod.json.</summary>
    public static (string modId, string name, string version, string loader) ReadModMeta(string path)
    {
        string modId = "", name = "", version = "", loader = "";
        try
        {
            var entries = ListEntries(path, "");
            var fabric = entries.FirstOrDefault(e => e.EndsWith("fabric.mod.json", StringComparison.OrdinalIgnoreCase));
            if (fabric is not null)
            {
                loader = "fabric";
                var text = ReadText(path, fabric) ?? "";
                var node = JsonUtil.Parse(text) as System.Text.Json.Nodes.JsonObject;
                if (node is not null)
                {
                    modId = node["id"]?.GetValue<string>() ?? "";
                    name = node["name"]?.GetValue<string>() ?? "";
                    version = node["version"]?.GetValue<string>() ?? "";
                }
            }

            var quilt = entries.FirstOrDefault(e => e.EndsWith("quilt.mod.json", StringComparison.OrdinalIgnoreCase));
            if (quilt is not null && loader.Length == 0)
            {
                loader = "quilt";
                var text = ReadText(path, quilt) ?? "";
                var node = JsonUtil.Parse(text) as System.Text.Json.Nodes.JsonObject;
                var ql = node?["quilt_loader"] as System.Text.Json.Nodes.JsonObject;
                if (ql is not null)
                {
                    modId = ql["id"]?.GetValue<string>() ?? "";
                    version = ql["version"]?.GetValue<string>() ?? "";
                    var meta = ql["metadata"] as System.Text.Json.Nodes.JsonObject;
                    name = meta?["name"]?.GetValue<string>() ?? "";
                }
            }

            var toml = entries.FirstOrDefault(e => e.Equals("META-INF/mods.toml", StringComparison.OrdinalIgnoreCase))
                       ?? entries.FirstOrDefault(e => e.EndsWith("/META-INF/mods.toml", StringComparison.OrdinalIgnoreCase))
                       ?? entries.FirstOrDefault(e => e.EndsWith("META-INF/neoforge.mods.toml", StringComparison.OrdinalIgnoreCase));
            if (toml is not null)
            {
                if (loader.Length == 0)
                    loader = toml.EndsWith("neoforge.mods.toml", StringComparison.OrdinalIgnoreCase) ? "neoforge" : "forge";
                var text = ReadText(path, toml) ?? "";
                var meta = TomlLite.ParseModsToml(text);
                if (modId.Length == 0) modId = meta.modId;
                if (name.Length == 0) name = meta.displayName;
                if (version.Length == 0) version = meta.version;
            }

            if (loader.Length == 0)
            {
                if (entries.Any(e => e.StartsWith("META-INF/services/net.minecraftforge", StringComparison.OrdinalIgnoreCase)))
                    loader = "forge";
            }
        }
        catch
        {
            // unreadable jar: fall back to file name
        }

        return (modId, name, version, loader);
    }
}

/// <summary>Tiny TOML helper that only extracts mod metadata fields.</summary>
public static class TomlLite
{
    public static (string modId, string displayName, string version) ParseModsToml(string text)
    {
        string modId = "", displayName = "", version = "";
        var inMods = false;
        foreach (var rawLine in text.Split('\n'))
        {
            var line = rawLine.Trim();
            if (line.Length == 0 || line.StartsWith('#')) continue;
            if (line.StartsWith("[[", StringComparison.Ordinal))
            {
                inMods = line.StartsWith("[[mods]]", StringComparison.OrdinalIgnoreCase);
                continue;
            }
            if (line.StartsWith("[", StringComparison.Ordinal)) { inMods = false; continue; }
            if (!inMods) continue;
            var idx = line.IndexOf('=');
            if (idx <= 0) continue;
            var key = line[..idx].Trim();
            var value = Unquote(line[(idx + 1)..].Trim());
            switch (key)
            {
                case "modId" when modId.Length == 0: modId = value; break;
                case "displayName" when displayName.Length == 0: displayName = value; break;
                case "version" when version.Length == 0: version = value; break;
            }
        }
        return (modId, displayName, version);
    }

    private static string Unquote(string value)
    {
        if (value.Length >= 2 && value[0] == '"' && value[^1] == '"')
            return value[1..^1].Replace("\\\"", "\"").Replace("\\\\", "\\");
        if (value.Length >= 2 && value[0] == '\'' && value[^1] == '\'')
            return value[1..^1];
        return value;
    }
}
