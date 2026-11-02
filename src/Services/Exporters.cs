using System.IO.Compression;
using System.Text;
using System.Text.Json.Nodes;
using System.Text.RegularExpressions;
using DstMcI18n.Core;

namespace DstMcI18n.Services;

/// <summary>Options shared by all packaging exports.</summary>
public sealed class PackOptions
{
    public string PackName { get; set; } = "汉化资源包";
    public string PackVersion { get; set; } = "1.0.0";
    /// <summary>Minecraft version such as "1.20.1" (used to derive pack_format).</summary>
    public string? McVersion { get; set; }
    /// <summary>Explicit pack_format; wins over McVersion when set.</summary>
    public int? PackFormat { get; set; }
    public string Description { get; set; } = "";
    public string TargetLanguage { get; set; } = "zh_cn";
}

/// <summary>One file that will be placed inside an overlay zip.</summary>
public sealed class OverlayFile
{
    /// <summary>Path inside the zip, using forward slashes, no leading slash.</summary>
    public string ZipPath { get; set; } = "";
    public string Content { get; set; } = "";
}

/// <summary>
/// Builds downloadable artifacts: lang json, resource packs and overlay zips.
/// All methods are pure (no IO) so they can be unit tested.
/// </summary>
public static class Exporters
{
    /// <summary>pack_format used when the Minecraft version cannot be parsed.</summary>
    private const int DefaultPackFormat = 15;

    /// <summary>Namespace used when the supplied namespace contains no usable characters.</summary>
    private const string UnknownNamespace = "unknown";

    /// <summary>Language code used when none was supplied.</summary>
    private const string DefaultLanguage = "zh_cn";

    /// <summary>Maximum length of a generated download file name.</summary>
    private const int MaxFileNameLength = 100;

    /// <summary>Longest trailing suffix treated as a file extension when truncating.</summary>
    private const int MaxExtensionLength = 10;

    /// <summary>Root-level readme written into every overlay zip.</summary>
    private const string ReadmeEntryName = "README.txt";

    /// <summary>Edge length of the generated pack.png icon.</summary>
    private const int IconSize = 64;

    /// <summary>
    /// Fixed zip entry timestamp so repeated exports are byte-identical.
    /// Midday keeps the stored date stable whatever the local UTC offset is.
    /// </summary>
    private static readonly DateTimeOffset FixedZipTimestamp = new(2020, 7, 1, 12, 0, 0, TimeSpan.Zero);

    private static readonly byte[] PngSignature = { 0x89, 0x50, 0x4E, 0x47, 0x0D, 0x0A, 0x1A, 0x0A };

    private static readonly Regex VersionRegex = new(
        @"(?<!\d)(?<major>\d{1,2})\.(?<minor>\d{1,2})(?:\.(?<patch>\d{1,2}))?(?!\d)",
        RegexOptions.Compiled | RegexOptions.CultureInvariant);

    private static readonly Regex WhitespaceRegex = new(@"\s+", RegexOptions.Compiled);

    private static readonly Regex UnderscoreRunRegex = new(@"_{2,}", RegexOptions.Compiled);

    private static readonly char[] IllegalFileNameChars = { '<', '>', ':', '"', '/', '\\', '|', '?', '*' };

    private static readonly HashSet<string> ReservedFileNames = new(StringComparer.OrdinalIgnoreCase)
    {
        "CON", "PRN", "AUX", "NUL",
        "COM1", "COM2", "COM3", "COM4", "COM5", "COM6", "COM7", "COM8", "COM9",
        "LPT1", "LPT2", "LPT3", "LPT4", "LPT5", "LPT6", "LPT7", "LPT8", "LPT9"
    };

    /// <summary>Exact Minecraft version to resource pack format mapping.</summary>
    private static readonly Dictionary<string, int> PackFormatByVersion = new(StringComparer.OrdinalIgnoreCase)
    {
        ["1.16.5"] = 6,
        ["1.17"] = 7,
        ["1.18"] = 8,
        ["1.19"] = 9,
        ["1.19.1"] = 9,
        ["1.19.2"] = 9,
        ["1.19.3"] = 12,
        ["1.19.4"] = 13,
        ["1.20"] = 15,
        ["1.20.1"] = 15,
        ["1.20.2"] = 18,
        ["1.20.3"] = 22,
        ["1.20.4"] = 22,
        ["1.20.5"] = 32,
        ["1.20.6"] = 32,
        ["1.21"] = 34,
        ["1.21.1"] = 34,
        ["1.21.2"] = 42,
        ["1.21.3"] = 42,
        ["1.21.4"] = 46,
        ["1.21.5"] = 55
    };

    /// <summary>Whole minor lines that share one pack format (documented as "x").</summary>
    private static readonly Dictionary<string, int> PackFormatByMinor = new(StringComparer.OrdinalIgnoreCase)
    {
        ["1.17"] = 7,
        ["1.18"] = 8
    };

    private static readonly uint[] Crc32Table = BuildCrc32Table();

    /// <summary>Serializes entries into a flat JSON object (UTF-8, no BOM).</summary>
    public static byte[] BuildLangJson(IEnumerable<LangEntry> entries, bool indented = true)
    {
        var root = new JsonObject();
        if (entries is not null)
        {
            foreach (var entry in entries)
            {
                if (entry?.Key is null) continue;
                // Keys are written verbatim: dotted keys stay one flat property, never nested.
                root[entry.Key] = entry.Value ?? string.Empty;
            }
        }

        var json = root.ToJsonString(indented ? JsonUtil.OutOptions : JsonUtil.CompactOptions);
        return Encoding.UTF8.GetBytes(json);
    }

    /// <summary>Maps a Minecraft version to the resource pack format number.</summary>
    public static int ResolvePackFormat(string? mcVersion, int? explicitFormat)
    {
        if (explicitFormat is > 0) return explicitFormat.Value;

        var version = NormalizeVersion(mcVersion);
        if (version.Length == 0) return DefaultPackFormat;
        if (PackFormatByVersion.TryGetValue(version, out var format)) return format;

        var dot = version.IndexOf('.');
        if (dot > 0)
        {
            var minor = version[..dot];
            var rest = version[(dot + 1)..];
            var dot2 = rest.IndexOf('.');
            if (dot2 > 0) minor += "." + rest[..dot2];
            if (PackFormatByMinor.TryGetValue(minor, out var minorFormat)) return minorFormat;
        }

        return DefaultPackFormat;
    }

    /// <summary>
    /// Builds a client resource pack zip:
    /// pack.mcmeta + pack.png(optional) + assets/&lt;namespace&gt;/lang/&lt;lang&gt;.json
    /// </summary>
    public static byte[] BuildResourcePack(string namespaceName, IEnumerable<LangEntry> entries, PackOptions options)
    {
        options ??= new PackOptions();
        var ns = SanitizeNamespace(namespaceName);
        var lang = SanitizeLanguage(options.TargetLanguage);
        var packFormat = ResolvePackFormat(options.McVersion, options.PackFormat);
        var description = ResolveDescription(options);

        var meta = new JsonObject
        {
            ["pack"] = new JsonObject
            {
                ["pack_format"] = packFormat,
                ["description"] = description
            }
        };

        var metaBytes = Encoding.UTF8.GetBytes(meta.ToJsonString(JsonUtil.OutOptions));
        var langBytes = BuildLangJson(entries);

        using var buffer = new MemoryStream();
        using (var zip = new ZipArchive(buffer, ZipArchiveMode.Create, leaveOpen: true))
        {
            AddEntry(zip, "pack.mcmeta", metaBytes);
            AddEntry(zip, "pack.png", BuildPackIcon());
            AddEntry(zip, "assets/" + ns + "/lang/" + lang + ".json", langBytes);
        }

        return buffer.ToArray();
    }

    /// <summary>Builds a generic overlay zip (kubejs / ftbquests) from explicit file list.</summary>
    public static byte[] BuildOverlayZip(IReadOnlyList<OverlayFile> files, PackOptions options)
    {
        if (files is null || files.Count == 0)
            throw new ArgumentException("覆盖包至少需要一个文件。", nameof(files));

        options ??= new PackOptions();

        using var buffer = new MemoryStream();
        using (var zip = new ZipArchive(buffer, ZipArchiveMode.Create, leaveOpen: true))
        {
            var seen = new HashSet<string>(StringComparer.OrdinalIgnoreCase) { ReadmeEntryName };
            foreach (var file in files)
            {
                if (file is null) continue;
                var path = NormalizeZipPath(file.ZipPath);
                if (path.Length == 0) continue;
                if (!seen.Add(path)) continue; // never write the same path twice
                AddEntry(zip, path, Encoding.UTF8.GetBytes(file.Content ?? string.Empty));
            }

            // README lives at the zip root so users know what to do with the archive.
            // It is always generated by us, so a caller file with the same name is skipped.
            AddEntry(zip, ReadmeEntryName, BuildOverlayReadme(options));
        }

        return buffer.ToArray();
    }

    /// <summary>Safe file name for downloads, e.g. "Create-1.20.1-zh_cn.zip".</summary>
    public static string SafeFileName(string raw, string fallback = "download")
    {
        var fallbackName = CleanFileName(fallback);
        if (fallbackName.Length == 0) fallbackName = "download";

        if (string.IsNullOrWhiteSpace(raw)) return fallbackName;
        var cleaned = CleanFileName(raw);
        return cleaned.Length == 0 ? fallbackName : cleaned;
    }

    // ---------------------------------------------------------------- helpers

    private static void AddEntry(ZipArchive zip, string path, byte[] content)
    {
        var entry = zip.CreateEntry(path, CompressionLevel.Optimal);
        entry.LastWriteTime = FixedZipTimestamp;
        using var stream = entry.Open();
        stream.Write(content, 0, content.Length);
    }

    private static string ResolveDescription(PackOptions options)
    {
        if (!string.IsNullOrWhiteSpace(options.Description)) return options.Description.Trim();

        var name = string.IsNullOrWhiteSpace(options.PackName) ? "汉化资源包" : options.PackName.Trim();
        var version = options.PackVersion?.Trim();
        return string.IsNullOrEmpty(version) ? name : name + " " + version;
    }

    /// <summary>Normalizes a zip path: forward slashes, no leading slash, no "." / ".." segments.</summary>
    private static string NormalizeZipPath(string? path)
    {
        if (string.IsNullOrWhiteSpace(path)) return string.Empty;

        var segments = new List<string>();
        foreach (var raw in path.Replace('\\', '/').Trim().Split('/'))
        {
            var segment = raw.Trim();
            if (segment.Length == 0 || segment == ".") continue;
            if (segment == "..")
            {
                if (segments.Count > 0) segments.RemoveAt(segments.Count - 1);
                continue;
            }

            segments.Add(segment);
        }

        return string.Join("/", segments);
    }

    private static byte[] BuildOverlayReadme(PackOptions options)
    {
        var name = string.IsNullOrWhiteSpace(options.PackName) ? "汉化覆盖包" : options.PackName.Trim();
        var version = string.IsNullOrWhiteSpace(options.PackVersion) ? string.Empty : " " + options.PackVersion.Trim();
        var lang = SanitizeLanguage(options.TargetLanguage);

        var sb = new StringBuilder();
        sb.Append(name).Append(version).Append("（目标语言：").Append(lang).Append("）\n");
        if (!string.IsNullOrWhiteSpace(options.Description))
            sb.Append(options.Description.Trim()).Append('\n');
        sb.Append('\n');
        sb.Append("使用说明：\n");
        sb.Append("1. 关闭游戏。\n");
        sb.Append("2. 将本压缩包内的所有文件解压到 .minecraft 目录（或整合包根目录），目录结构与游戏内保持一致。\n");
        sb.Append("3. 提示文件已存在时选择“覆盖”。\n");
        sb.Append("4. 重新启动游戏即可看到中文文本。\n");
        sb.Append('\n');
        sb.Append("建议在覆盖前备份原文件；如需还原，请恢复备份。\n");
        sb.Append('\n');
        sb.Append("由 DST MC I18n Tool 生成。\n");
        return Encoding.UTF8.GetBytes(sb.ToString());
    }

    /// <summary>Keeps only [a-z0-9_.-]; everything else becomes "_", runs collapsed, edges trimmed.</summary>
    private static string SanitizeNamespace(string? raw)
    {
        if (string.IsNullOrWhiteSpace(raw)) return UnknownNamespace;

        var sb = new StringBuilder(raw.Length);
        foreach (var ch in raw.Trim().ToLowerInvariant())
        {
            var allowed = (ch >= 'a' && ch <= 'z') || (ch >= '0' && ch <= '9') || ch == '_' || ch == '.' || ch == '-';
            sb.Append(allowed ? ch : '_');
        }

        var result = UnderscoreRunRegex.Replace(sb.ToString(), "_").Trim('_');
        return result.Length == 0 ? UnknownNamespace : result;
    }

    /// <summary>Keeps only [a-z0-9_-] for the lang folder/file name.</summary>
    private static string SanitizeLanguage(string? raw)
    {
        if (string.IsNullOrWhiteSpace(raw)) return DefaultLanguage;

        var sb = new StringBuilder(raw.Length);
        foreach (var ch in raw.Trim().ToLowerInvariant())
        {
            var allowed = (ch >= 'a' && ch <= 'z') || (ch >= '0' && ch <= '9') || ch == '_' || ch == '-';
            sb.Append(allowed ? ch : '_');
        }

        var result = UnderscoreRunRegex.Replace(sb.ToString(), "_").Trim('_');
        return result.Length == 0 ? DefaultLanguage : result;
    }

    /// <summary>Strips illegal characters, collapses whitespace, trims dots/spaces, caps length.</summary>
    private static string CleanFileName(string? raw)
    {
        if (string.IsNullOrEmpty(raw)) return string.Empty;

        var sb = new StringBuilder(raw.Length);
        foreach (var ch in raw)
        {
            if (ch < 0x20 || ch == 0x7F) continue;              // control characters
            if (Array.IndexOf(IllegalFileNameChars, ch) >= 0) continue; // < > : " / \ | ? *
            sb.Append(ch);
        }

        var text = WhitespaceRegex.Replace(sb.ToString(), " ").Trim(' ', '.');
        if (text.Length > MaxFileNameLength)
        {
            // Keep a short trailing extension (".zip") instead of chopping it off.
            var extensionDot = text.LastIndexOf('.');
            var extension = extensionDot > 0 && text.Length - extensionDot <= MaxExtensionLength
                ? text[extensionDot..]
                : string.Empty;
            if (extension.Length > 0)
            {
                var truncatedStem = Truncate(text[..extensionDot], MaxFileNameLength - extension.Length);
                text = truncatedStem.Length == 0 ? Truncate(text, MaxFileNameLength) : truncatedStem + extension;
            }
            else
            {
                text = Truncate(text, MaxFileNameLength);
            }
        }

        if (text.Length == 0) return string.Empty;

        var dot = text.IndexOf('.');
        var stem = dot >= 0 ? text[..dot] : text;
        if (ReservedFileNames.Contains(stem)) text = "_" + text;

        return text;
    }

    /// <summary>Truncates to at most <paramref name="max"/> chars without splitting a surrogate pair.</summary>
    private static string Truncate(string value, int max)
    {
        if (value.Length <= max) return value.TrimEnd(' ', '.');

        var cut = Math.Max(1, max);
        if (cut < value.Length && char.IsHighSurrogate(value[cut - 1])) cut--;
        return value[..cut].TrimEnd(' ', '.');
    }

    private static string NormalizeVersion(string? mcVersion)
    {
        if (string.IsNullOrWhiteSpace(mcVersion)) return string.Empty;

        var match = VersionRegex.Match(mcVersion.Trim());
        if (!match.Success) return string.Empty;

        var major = int.Parse(match.Groups["major"].Value, System.Globalization.CultureInfo.InvariantCulture);
        var minor = int.Parse(match.Groups["minor"].Value, System.Globalization.CultureInfo.InvariantCulture);
        if (match.Groups["patch"].Success)
        {
            var patch = int.Parse(match.Groups["patch"].Value, System.Globalization.CultureInfo.InvariantCulture);
            return major.ToString(System.Globalization.CultureInfo.InvariantCulture) + "." +
                   minor.ToString(System.Globalization.CultureInfo.InvariantCulture) + "." +
                   patch.ToString(System.Globalization.CultureInfo.InvariantCulture);
        }

        return major.ToString(System.Globalization.CultureInfo.InvariantCulture) + "." +
               minor.ToString(System.Globalization.CultureInfo.InvariantCulture);
    }

    // ------------------------------------------------------------- pack icon

    /// <summary>Generates a deterministic 64x64 PNG icon (RGBA, zlib-compressed) without external files.</summary>
    private static byte[] BuildPackIcon()
    {
        var stride = IconSize * 4;
        var raw = new byte[(stride + 1) * IconSize];

        for (var y = 0; y < IconSize; y++)
        {
            var rowStart = y * (stride + 1);
            raw[rowStart] = 0; // PNG filter type: none

            var t = y / (double)(IconSize - 1);
            var bgR = (byte)Math.Round(24 + 46 * t);
            var bgG = (byte)Math.Round(48 + 78 * t);
            var bgB = (byte)Math.Round(92 + 96 * t);

            for (var x = 0; x < IconSize; x++)
            {
                var i = rowStart + 1 + x * 4;
                var border = x < 3 || y < 3 || x >= IconSize - 3 || y >= IconSize - 3;
                if (border)
                {
                    raw[i] = 160;
                    raw[i + 1] = 205;
                    raw[i + 2] = 255;
                }
                else
                {
                    raw[i] = bgR;
                    raw[i + 1] = bgG;
                    raw[i + 2] = bgB;
                }

                raw[i + 3] = 255;
            }
        }

        // Simple "文" glyph so the pack is recognizable in the resource pack list.
        FillRect(raw, 30, 13, 4, 4, 255, 255, 255);
        FillRect(raw, 16, 23, 32, 4, 255, 255, 255);
        DrawLine(raw, 30, 29, 18, 49, 2.6, 255, 255, 255);
        DrawLine(raw, 34, 29, 46, 49, 2.6, 255, 255, 255);

        byte[] compressed;
        using (var rawStream = new MemoryStream())
        {
            using (var zlib = new ZLibStream(rawStream, CompressionLevel.Optimal, leaveOpen: true))
                zlib.Write(raw, 0, raw.Length);
            compressed = rawStream.ToArray();
        }

        using var png = new MemoryStream();
        png.Write(PngSignature, 0, PngSignature.Length);

        var ihdr = new byte[13];
        WriteInt32BigEndian(ihdr, 0, IconSize);
        WriteInt32BigEndian(ihdr, 4, IconSize);
        ihdr[8] = 8;  // bit depth
        ihdr[9] = 6;  // color type: RGBA
        ihdr[10] = 0; // compression
        ihdr[11] = 0; // filter
        ihdr[12] = 0; // interlace
        WritePngChunk(png, "IHDR", ihdr);
        WritePngChunk(png, "IDAT", compressed);
        WritePngChunk(png, "IEND", Array.Empty<byte>());

        return png.ToArray();
    }

    private static void FillRect(byte[] raw, int x0, int y0, int width, int height, byte r, byte g, byte b)
    {
        for (var y = y0; y < y0 + height; y++)
        for (var x = x0; x < x0 + width; x++)
            SetPixel(raw, x, y, r, g, b);
    }

    private static void DrawLine(byte[] raw, int x0, int y0, int x1, int y1, double thickness, byte r, byte g, byte b)
    {
        var dx = x1 - x0;
        var dy = y1 - y0;
        var lengthSquared = (double)dx * dx + (double)dy * dy;
        var radius = thickness / 2.0;
        var minX = (int)Math.Floor(Math.Min(x0, x1) - radius);
        var maxX = (int)Math.Ceiling(Math.Max(x0, x1) + radius);
        var minY = (int)Math.Floor(Math.Min(y0, y1) - radius);
        var maxY = (int)Math.Ceiling(Math.Max(y0, y1) + radius);

        for (var y = minY; y <= maxY; y++)
        for (var x = minX; x <= maxX; x++)
        {
            double t = 0;
            if (lengthSquared > 0)
            {
                t = ((x - x0) * (double)dx + (y - y0) * (double)dy) / lengthSquared;
                t = Math.Clamp(t, 0.0, 1.0);
            }

            var px = x0 + t * dx;
            var py = y0 + t * dy;
            var distance = Math.Sqrt((x - px) * (x - px) + (y - py) * (y - py));
            if (distance <= radius) SetPixel(raw, x, y, r, g, b);
        }
    }

    private static void SetPixel(byte[] raw, int x, int y, byte r, byte g, byte b)
    {
        if ((uint)x >= (uint)IconSize || (uint)y >= (uint)IconSize) return;
        var i = y * (IconSize * 4 + 1) + 1 + x * 4;
        raw[i] = r;
        raw[i + 1] = g;
        raw[i + 2] = b;
        raw[i + 3] = 255;
    }

    private static void WritePngChunk(Stream stream, string type, byte[] data)
    {
        var lengthBytes = new byte[4];
        WriteInt32BigEndian(lengthBytes, 0, data.Length);
        stream.Write(lengthBytes, 0, 4);

        var typeBytes = Encoding.ASCII.GetBytes(type);
        stream.Write(typeBytes, 0, typeBytes.Length);
        stream.Write(data, 0, data.Length);

        var crcBytes = new byte[4];
        WriteInt32BigEndian(crcBytes, 0, unchecked((int)Crc32(typeBytes, data)));
        stream.Write(crcBytes, 0, 4);
    }

    private static void WriteInt32BigEndian(byte[] buffer, int offset, int value)
    {
        buffer[offset] = (byte)(value >> 24);
        buffer[offset + 1] = (byte)(value >> 16);
        buffer[offset + 2] = (byte)(value >> 8);
        buffer[offset + 3] = (byte)value;
    }

    private static uint Crc32(byte[] first, byte[] second)
    {
        var crc = 0xFFFFFFFFu;
        foreach (var b in first) crc = Crc32Table[(crc ^ b) & 0xFF] ^ (crc >> 8);
        foreach (var b in second) crc = Crc32Table[(crc ^ b) & 0xFF] ^ (crc >> 8);
        return crc ^ 0xFFFFFFFFu;
    }

    private static uint[] BuildCrc32Table()
    {
        var table = new uint[256];
        for (uint n = 0; n < 256; n++)
        {
            var c = n;
            for (var k = 0; k < 8; k++)
                c = (c & 1) != 0 ? 0xEDB88320u ^ (c >> 1) : c >> 1;
            table[n] = c;
        }

        return table;
    }
}
