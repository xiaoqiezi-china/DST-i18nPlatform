using System.IO.Compression;
using System.Runtime.CompilerServices;
using System.Text;
using System.Text.Json.Nodes;
using DstMcI18n.Core;
using DstMcI18n.Services;

namespace DstMcI18n.ExportHarness;

/// <summary>
/// Console test harness for Exporters. Links the real src/Core and src/Services/Exporters.cs
/// sources, produces artifacts under tests/ExportHarness/out and prints PASS/FAIL per check.
/// </summary>
internal static class Program
{
    private static readonly List<string> Failures = new();
    private static int _passed;
    private static string _outDir = "";

    private static int Main()
    {
        _outDir = Path.Combine(ProjectDir(), "out");
        Directory.CreateDirectory(_outDir);
        Console.OutputEncoding = Encoding.UTF8;

        Console.WriteLine("ExportHarness - Exporters.cs self test");
        Console.WriteLine("out dir: " + _outDir);
        Console.WriteLine();

        TestBuildLangJson();
        TestResolvePackFormat();
        TestResourcePack();
        TestOverlayZip();
        TestSafeFileName();

        Console.WriteLine();
        if (Failures.Count == 0)
        {
            Console.WriteLine("ALL PASS (" + _passed + " checks)");
            return 0;
        }

        Console.WriteLine("FAILED (" + Failures.Count + " of " + (_passed + Failures.Count) + " checks)");
        foreach (var failure in Failures) Console.WriteLine("  - " + failure);
        return 1;
    }

    // --------------------------------------------------------------- lang json

    private static void TestBuildLangJson()
    {
        var entries = new List<LangEntry>
        {
            new() { Key = "item.create.cogwheel", Value = "齿轮" },
            new() { Key = "block.create.shaft", Value = "传动轴" },
            new() { Key = "plain", Value = "纯文本" },
            new() { Key = "with.quote", Value = "他说 \"你好\"" },
            new() { Key = "with.newline", Value = "第一行\n第二行" },
            new() { Key = "number.like", Value = "42" }
        };

        Check("BuildLangJson: parses as JSON object with flat keys", () =>
        {
            var bytes = Exporters.BuildLangJson(entries);
            var text = Encoding.UTF8.GetString(bytes);
            var node = JsonNode.Parse(text) as JsonObject;
            Assert(node is not null, "root is not a JSON object");
            AssertEqual(6, node!.Count, "property count");
            // Dotted keys must stay one flat property, never nested.
            Assert(node.ContainsKey("item.create.cogwheel"), "dotted key was expanded");
            AssertEqual("齿轮", (string?)node["item.create.cogwheel"], "value");
            AssertEqual("他说 \"你好\"", (string?)node["with.quote"], "escaped quote round trip");
            AssertEqual("第一行\n第二行", (string?)node["with.newline"], "newline round trip");
            Assert(node["item"] is null, "unexpected nested object 'item'");
        });

        Check("BuildLangJson: UTF-8 without BOM, Chinese not escaped", () =>
        {
            var bytes = Exporters.BuildLangJson(entries);
            Assert(!(bytes.Length >= 3 && bytes[0] == 0xEF && bytes[1] == 0xBB && bytes[2] == 0xBF), "output has a UTF-8 BOM");
            var text = Encoding.UTF8.GetString(bytes);
            Assert(text.Contains("齿轮"), "Chinese value was escaped: " + text);
            Assert(!text.Contains("\\u"), "output contains \\u escapes");
        });

        Check("BuildLangJson: indented flag controls formatting", () =>
        {
            var indented = Encoding.UTF8.GetString(Exporters.BuildLangJson(entries, true));
            var compact = Encoding.UTF8.GetString(Exporters.BuildLangJson(entries, false));
            Assert(indented.Contains("\n"), "indented output has no newlines");
            Assert(!compact.Contains("\n"), "compact output has newlines");
            Assert(indented.Length > compact.Length, "indented output not longer than compact");
            AssertEqual(6, (JsonNode.Parse(compact) as JsonObject)!.Count, "compact property count");
        });

        Check("BuildLangJson: empty and duplicate inputs", () =>
        {
            var empty = JsonNode.Parse(Encoding.UTF8.GetString(Exporters.BuildLangJson(Array.Empty<LangEntry>()))) as JsonObject;
            AssertEqual(0, empty!.Count, "empty object property count");

            var dup = Exporters.BuildLangJson(new[]
            {
                new LangEntry { Key = "k", Value = "first" },
                new LangEntry { Key = "k", Value = "second" }
            });
            var dupNode = JsonNode.Parse(Encoding.UTF8.GetString(dup)) as JsonObject;
            AssertEqual(1, dupNode!.Count, "duplicate keys should collapse to one property");
            AssertEqual("second", (string?)dupNode["k"], "last value wins");

            var nullEntries = JsonNode.Parse(Encoding.UTF8.GetString(Exporters.BuildLangJson(null!))) as JsonObject;
            AssertEqual(0, nullEntries!.Count, "null input should produce {}");
        });

        Check("BuildLangJson: unusual keys survive a round trip", () =>
        {
            var bytes = Exporters.BuildLangJson(new[]
            {
                new LangEntry { Key = "", Value = "empty key" },
                new LangEntry { Key = "中文键", Value = "值" },
                new LangEntry { Key = "a.b.c", Value = "flat" },
                new LangEntry { Key = "quote\"key", Value = "v" },
                new LangEntry { Key = "k", Value = "" }
            });
            var text = Encoding.UTF8.GetString(bytes);
            var node = JsonNode.Parse(text) as JsonObject;
            Assert(node is not null, "unparseable: " + text);
            AssertEqual(5, node!.Count, "property count");
            AssertEqual("empty key", (string?)node[""], "empty key");
            AssertEqual("值", (string?)node["中文键"], "unicode key");
            AssertEqual("flat", (string?)node["a.b.c"], "dotted key");
            AssertEqual("v", (string?)node["quote\"key"], "quoted key");
            AssertEqual("", (string?)node["k"], "empty value");
        });

        Check("BuildLangJson: stable output for the same input", () =>
        {
            var a = Exporters.BuildLangJson(entries);
            var b = Exporters.BuildLangJson(entries);
            Assert(a.AsSpan().SequenceEqual(b), "same input produced different bytes");
        });
    }

    // ------------------------------------------------------------- pack format

    private static void TestResolvePackFormat()
    {
        var cases = new (string? Version, int Expected)[]
        {
            ("1.16.5", 6),
            ("1.17", 7), ("1.17.1", 7),
            ("1.18", 8), ("1.18.2", 8),
            ("1.19", 9), ("1.19.2", 9),
            ("1.19.3", 12),
            ("1.19.4", 13),
            ("1.20", 15), ("1.20.1", 15),
            ("1.20.2", 18),
            ("1.20.3", 22), ("1.20.4", 22),
            ("1.20.5", 32), ("1.20.6", 32),
            ("1.21", 34), ("1.21.1", 34),
            ("1.21.2", 42), ("1.21.3", 42),
            ("1.21.4", 46),
            ("1.21.5", 55)
        };

        Check("ResolvePackFormat: documented version table", () =>
        {
            foreach (var (version, expected) in cases)
                AssertEqual(expected, Exporters.ResolvePackFormat(version, null), "pack_format for " + version);
        });

        Check("ResolvePackFormat: tolerant input forms", () =>
        {
            AssertEqual(15, Exporters.ResolvePackFormat(" 1.20.1 ", null), "padded version");
            AssertEqual(15, Exporters.ResolvePackFormat("1.20.1-forge", null), "loader suffix");
            AssertEqual(15, Exporters.ResolvePackFormat("MC 1.20.1", null), "prefix");
            AssertEqual(9, Exporters.ResolvePackFormat("1.19.2-pre1", null), "pre-release suffix");
            AssertEqual(15, Exporters.ResolvePackFormat("1.20.1", null), "exact");
        });

        Check("ResolvePackFormat: fallback and explicit override", () =>
        {
            AssertEqual(15, Exporters.ResolvePackFormat(null, null), "null version fallback");
            AssertEqual(15, Exporters.ResolvePackFormat("", null), "empty version fallback");
            AssertEqual(15, Exporters.ResolvePackFormat("not-a-version", null), "garbage fallback");
            AssertEqual(15, Exporters.ResolvePackFormat("1.20.99", null), "unknown patch fallback");
            AssertEqual(99, Exporters.ResolvePackFormat("1.20.1", 99), "explicit format should win");
            AssertEqual(6, Exporters.ResolvePackFormat(null, 6), "explicit format without version");
        });
    }

    // ----------------------------------------------------------- resource pack

    private static void TestResourcePack()
    {
        var entries = new List<LangEntry>
        {
            new() { Key = "item.create.cogwheel", Value = "齿轮" },
            new() { Key = "item.create.shaft", Value = "传动轴" },
            new() { Key = "gui.create.title", Value = "机械动力" }
        };
        var options = new PackOptions
        {
            PackName = "Create 汉化",
            PackVersion = "1.2.3",
            McVersion = "1.20.1",
            TargetLanguage = "zh_cn",
            Description = "机械动力 中文 \"测试\" 包"
        };

        byte[] pack = Exporters.BuildResourcePack("Create Mod!", entries, options);
        File.WriteAllBytes(Path.Combine(_outDir, "resourcepack.zip"), pack);

        Check("BuildResourcePack: zip opens and has exactly the expected entries", () =>
        {
            using var zip = new ZipArchive(new MemoryStream(pack), ZipArchiveMode.Read);
            var names = zip.Entries.Select(e => e.FullName).ToArray();
            AssertEqual(3, names.Length, "entry count: " + string.Join(", ", names));
            AssertEqual("pack.mcmeta", names[0], "entry 0");
            AssertEqual("pack.png", names[1], "entry 1");
            AssertEqual("assets/create_mod/lang/zh_cn.json", names[2], "entry 2 (namespace sanitized)");
            AssertEqual(names.Length, names.Distinct(StringComparer.Ordinal).Count(), "duplicate entries");
            foreach (var name in names) Assert(!name.EndsWith("/"), "unexpected directory entry: " + name);
            foreach (var name in names) Assert(!name.StartsWith("/"), "leading slash in " + name);
        });

        Check("BuildResourcePack: fixed timestamps make exports byte-identical", () =>
        {
            using var zip = new ZipArchive(new MemoryStream(pack), ZipArchiveMode.Read);
            AssertSameTimestamp(zip);
            var again = Exporters.BuildResourcePack("Create Mod!", entries, options);
            Assert(pack.AsSpan().SequenceEqual(again), "two exports of the same input differ");
        });

        Check("BuildResourcePack: pack.mcmeta is valid and carries pack_format + Chinese description", () =>
        {
            using var zip = new ZipArchive(new MemoryStream(pack), ZipArchiveMode.Read);
            var metaText = ReadEntryText(zip, "pack.mcmeta");
            var meta = JsonNode.Parse(metaText) as JsonObject;
            Assert(meta is not null, "pack.mcmeta is not a JSON object");
            var packNode = meta!["pack"] as JsonObject;
            Assert(packNode is not null, "missing pack object");
            AssertEqual(15, (int?)packNode!["pack_format"], "pack_format");
            AssertEqual("机械动力 中文 \"测试\" 包", (string?)packNode["description"], "description");
            Assert(metaText.Contains("机械动力"), "Chinese description was escaped in pack.mcmeta");
        });

        Check("BuildResourcePack: lang json parses with the right entry count", () =>
        {
            using var zip = new ZipArchive(new MemoryStream(pack), ZipArchiveMode.Read);
            var langText = ReadEntryText(zip, "assets/create_mod/lang/zh_cn.json");
            var lang = JsonNode.Parse(langText) as JsonObject;
            Assert(lang is not null, "lang json is not an object");
            AssertEqual(3, lang!.Count, "lang entry count");
            AssertEqual("齿轮", (string?)lang["item.create.cogwheel"], "lang value");
            AssertEqual("机械动力", (string?)lang["gui.create.title"], "lang value 2");
            var bytes = ReadEntryBytes(zip, "assets/create_mod/lang/zh_cn.json");
            Assert(!(bytes.Length >= 3 && bytes[0] == 0xEF && bytes[1] == 0xBB && bytes[2] == 0xBF), "lang json has BOM");
        });

        Check("BuildResourcePack: pack.png is a valid 64x64 RGBA PNG", () =>
        {
            using var zip = new ZipArchive(new MemoryStream(pack), ZipArchiveMode.Read);
            ValidatePng(ReadEntryBytes(zip, "pack.png"));
        });

        Check("BuildResourcePack: pack_format from explicit option and version fallback", () =>
        {
            using (var zip = new ZipArchive(new MemoryStream(
                       Exporters.BuildResourcePack("x", entries, new PackOptions { PackFormat = 99 })), ZipArchiveMode.Read))
            {
                var meta = JsonNode.Parse(ReadEntryText(zip, "pack.mcmeta")) as JsonObject;
                AssertEqual(99, (int?)meta!["pack"]!["pack_format"], "explicit pack_format");
            }

            using (var zip = new ZipArchive(new MemoryStream(
                       Exporters.BuildResourcePack("x", entries, new PackOptions { McVersion = "1.21.4" })), ZipArchiveMode.Read))
            {
                var meta = JsonNode.Parse(ReadEntryText(zip, "pack.mcmeta")) as JsonObject;
                AssertEqual(46, (int?)meta!["pack"]!["pack_format"], "derived pack_format");
            }

            using (var zip = new ZipArchive(new MemoryStream(
                       Exporters.BuildResourcePack("x", entries, new PackOptions())), ZipArchiveMode.Read))
            {
                var meta = JsonNode.Parse(ReadEntryText(zip, "pack.mcmeta")) as JsonObject;
                AssertEqual(15, (int?)meta!["pack"]!["pack_format"], "default pack_format");
                AssertEqual("汉化资源包 1.0.0", (string?)meta["pack"]!["description"], "default description");
            }
        });

        Check("BuildResourcePack: namespace sanitizing", () =>
        {
            var cases = new (string? Input, string Expected)[]
            {
                ("Create", "create"),
                ("Create Mod!", "create_mod"),
                ("My.Mod-1", "my.mod-1"),
                ("  ", "unknown"),
                ("", "unknown"),
                (null, "unknown"),
                ("中文名", "unknown")
            };

            foreach (var (input, expected) in cases)
            {
                using var zip = new ZipArchive(new MemoryStream(Exporters.BuildResourcePack(input!, entries, new PackOptions())), ZipArchiveMode.Read);
                var langEntry = zip.Entries.Single(e => e.FullName.StartsWith("assets/") && e.FullName.EndsWith(".json"));
                AssertEqual("assets/" + expected + "/lang/zh_cn.json", langEntry.FullName, "namespace for '" + input + "'");
            }
        });

        Check("BuildResourcePack: target language and empty entry list", () =>
        {
            using (var zip = new ZipArchive(new MemoryStream(
                       Exporters.BuildResourcePack("mod", entries, new PackOptions { TargetLanguage = "zh_TW" })), ZipArchiveMode.Read))
            {
                Assert(zip.Entries.Any(e => e.FullName == "assets/mod/lang/zh_tw.json"),
                    "lowercased language file missing: " + string.Join(", ", zip.Entries.Select(e => e.FullName)));
            }

            using (var zip = new ZipArchive(new MemoryStream(
                       Exporters.BuildResourcePack("mod", Array.Empty<LangEntry>(), new PackOptions())), ZipArchiveMode.Read))
            {
                var lang = JsonNode.Parse(ReadEntryText(zip, "assets/mod/lang/zh_cn.json")) as JsonObject;
                AssertEqual(0, lang!.Count, "empty lang file should be {}");
            }
        });
    }

    // --------------------------------------------------------------- overlay

    private static void TestOverlayZip()
    {
        var files = new List<OverlayFile>
        {
            new() { ZipPath = "kubejs/assets/create/lang/zh_cn.json", Content = "{\"a\":\"齿轮\"}" },
            new() { ZipPath = "config/ftbquests/quests/lang/zh_cn.snbt", Content = "{\n\ttitle: \"任务\"\n}" },
            new() { ZipPath = "\\kubejs//data/./extra.json", Content = "{}" }
        };

        var options = new PackOptions
        {
            PackName = "整合包汉化",
            PackVersion = "2.0.0",
            TargetLanguage = "zh_cn",
            Description = "KubeJS 与 FTB 任务覆盖包"
        };
        var zipBytes = Exporters.BuildOverlayZip(files, options);
        File.WriteAllBytes(Path.Combine(_outDir, "kubejs-overlay.zip"), zipBytes);

        Check("BuildOverlayZip: entries written in order with no wrapper folder", () =>
        {
            using var zip = new ZipArchive(new MemoryStream(zipBytes), ZipArchiveMode.Read);
            var names = zip.Entries.Select(e => e.FullName).ToArray();
            AssertEqual(4, names.Length, "entry count: " + string.Join(", ", names));
            AssertEqual("kubejs/assets/create/lang/zh_cn.json", names[0], "entry 0");
            AssertEqual("config/ftbquests/quests/lang/zh_cn.snbt", names[1], "entry 1");
            AssertEqual("kubejs/data/extra.json", names[2], "entry 2 (path normalized)");
            AssertEqual("README.txt", names[3], "README at zip root");
            foreach (var name in names) Assert(!name.StartsWith("/"), "leading slash in " + name);
            foreach (var name in names) Assert(!name.EndsWith("/"), "unexpected directory entry: " + name);
        });

        Check("BuildOverlayZip: file contents round trip as UTF-8", () =>
        {
            using var zip = new ZipArchive(new MemoryStream(zipBytes), ZipArchiveMode.Read);
            AssertEqual("{\"a\":\"齿轮\"}", ReadEntryText(zip, "kubejs/assets/create/lang/zh_cn.json"), "kubejs json content");
            AssertEqual("{\n\ttitle: \"任务\"\n}", ReadEntryText(zip, "config/ftbquests/quests/lang/zh_cn.snbt"), "snbt content");
        });

        Check("BuildOverlayZip: README.txt explains how to apply the overlay", () =>
        {
            using var zip = new ZipArchive(new MemoryStream(zipBytes), ZipArchiveMode.Read);
            var readme = ReadEntryText(zip, "README.txt");
            Assert(readme.Contains("解压"), "README does not mention 解压");
            Assert(readme.Contains(".minecraft"), "README does not mention .minecraft");
            Assert(readme.Contains("覆盖"), "README does not mention 覆盖");
            Assert(readme.Contains("整合包"), "README does not mention 整合包");
            Assert(readme.Contains("整合包汉化 2.0.0"), "README missing pack name/version");
            Assert(readme.Contains("KubeJS 与 FTB 任务覆盖包"), "README should include the pack description");
        });

        Check("BuildOverlayZip: empty input throws ArgumentException", () =>
        {
            AssertThrows<ArgumentException>(() => Exporters.BuildOverlayZip(Array.Empty<OverlayFile>(), options), "empty list");
            AssertThrows<ArgumentException>(() => Exporters.BuildOverlayZip(null!, options), "null list");
        });

        Check("BuildOverlayZip: duplicate paths are written once", () =>
        {
            var dup = new List<OverlayFile>
            {
                new() { ZipPath = "kubejs/a.json", Content = "first" },
                new() { ZipPath = "KUBEJS/A.json", Content = "second" }
            };
            using var zip = new ZipArchive(new MemoryStream(Exporters.BuildOverlayZip(dup, options)), ZipArchiveMode.Read);
            AssertEqual(2, zip.Entries.Count, "duplicate path entries: " + string.Join(", ", zip.Entries.Select(e => e.FullName)));
            AssertEqual("first", ReadEntryText(zip, "kubejs/a.json"), "first content wins");
        });

        Check("BuildOverlayZip: caller-supplied README.txt is never duplicated", () =>
        {
            var withReadme = new List<OverlayFile>
            {
                new() { ZipPath = "README.txt", Content = "自定义说明" },
                new() { ZipPath = "kubejs/a.json", Content = "{}" }
            };
            using var zip = new ZipArchive(new MemoryStream(Exporters.BuildOverlayZip(withReadme, options)), ZipArchiveMode.Read);
            AssertEqual(2, zip.Entries.Count, "entry count: " + string.Join(", ", zip.Entries.Select(e => e.FullName)));
            var readme = ReadEntryText(zip, "README.txt");
            Assert(readme.Contains("解压"), "generated README should win over the caller file");
        });

        Check("BuildOverlayZip: deterministic bytes and fixed timestamps", () =>
        {
            var again = Exporters.BuildOverlayZip(files, options);
            Assert(zipBytes.AsSpan().SequenceEqual(again), "two exports of the same input differ");
            using var zip = new ZipArchive(new MemoryStream(zipBytes), ZipArchiveMode.Read);
            AssertSameTimestamp(zip);
        });
    }

    // ------------------------------------------------------------ safe names

    private static void TestSafeFileName()
    {
        Check("SafeFileName: strips Windows illegal characters", () =>
        {
            AssertEqual("Create 1.20.1 zh_cn.zip", Exporters.SafeFileName("Create: 1.20.1 / zh_cn.zip"), "colon+slash");
            AssertEqual("abcdefg.zip", Exporters.SafeFileName("a<b>c\"d|e?f*g.zip"), "many illegal chars");
            AssertEqual("ab", Exporters.SafeFileName("a\\b"), "backslash");
        });

        Check("SafeFileName: removes control characters and collapses whitespace", () =>
        {
            AssertEqual("ab c", Exporters.SafeFileName("a\tb\u0007  c"), "control chars");
            AssertEqual("a b c", Exporters.SafeFileName("a   b\u00A0c"), "whitespace runs");
        });

        Check("SafeFileName: trims leading/trailing dots and spaces", () =>
        {
            AssertEqual("name", Exporters.SafeFileName("  ...name...  "), "dots and spaces");
            AssertEqual("name.zip", Exporters.SafeFileName(".name.zip"), "leading dot");
        });

        Check("SafeFileName: caps length at 100 characters", () =>
        {
            var longName = new string('a', 150);
            var result = Exporters.SafeFileName(longName);
            AssertEqual(100, result.Length, "length cap");

            var withExtension = Exporters.SafeFileName(longName + ".zip");
            AssertEqual(100, withExtension.Length, "length cap with extension");
            Assert(withExtension.EndsWith(".zip"), "extension was chopped off: " + withExtension);
        });

        Check("SafeFileName: fallback when empty", () =>
        {
            AssertEqual("download", Exporters.SafeFileName(""), "empty input");
            AssertEqual("download", Exporters.SafeFileName(null!), "null input");
            AssertEqual("download", Exporters.SafeFileName("   "), "whitespace input");
            AssertEqual("download", Exporters.SafeFileName("<>:\"/\\|?*"), "only illegal chars");
            AssertEqual("fallback", Exporters.SafeFileName("", "fallback"), "custom fallback");
            AssertEqual("download", Exporters.SafeFileName("", ""), "empty fallback falls back to default");
        });

        Check("SafeFileName: keeps Chinese text", () =>
        {
            AssertEqual("机械动力-1.20.1-汉化.zip", Exporters.SafeFileName("机械动力-1.20.1-汉化.zip"), "Chinese name");
        });

        Check("SafeFileName: avoids reserved device names", () =>
        {
            AssertEqual("_CON", Exporters.SafeFileName("CON"), "CON");
            AssertEqual("_nul.txt", Exporters.SafeFileName("nul.txt"), "nul.txt");
            AssertEqual("CONSOLE", Exporters.SafeFileName("CONSOLE"), "CONSOLE is fine");
        });
    }

    // --------------------------------------------------------------- helpers

    /// <summary>All entries must carry one identical, non-"now" timestamp.</summary>
    private static void AssertSameTimestamp(ZipArchive zip)
    {
        Assert(zip.Entries.Count > 0, "zip has no entries");
        var first = zip.Entries[0].LastWriteTime;
        foreach (var entry in zip.Entries)
            AssertEqual(first, entry.LastWriteTime, "timestamp of " + entry.FullName);

        // The DOS timestamp is stored as local time, so compare the date loosely but
        // make sure it is a fixed past date rather than "now".
        AssertEqual(2020, first.Year, "fixed timestamp year");
        Assert(Math.Abs((DateTimeOffset.UtcNow - first).TotalDays) > 365, "timestamp looks like the current time");
    }

    private static void ValidatePng(byte[] png)
    {
        byte[] signature = { 0x89, 0x50, 0x4E, 0x47, 0x0D, 0x0A, 0x1A, 0x0A };
        Assert(png.Length > 8, "png is too short");
        for (var i = 0; i < signature.Length; i++) Assert(png[i] == signature[i], "bad PNG signature");

        var pos = 8;
        var width = 0;
        var height = 0;
        var sawIhdr = false;
        var sawIend = false;
        var idat = new MemoryStream();

        while (pos + 12 <= png.Length)
        {
            var length = ReadInt32BigEndian(png, pos);
            Assert(length >= 0 && pos + 12 + length <= png.Length, "truncated chunk at " + pos);
            var type = Encoding.ASCII.GetString(png, pos + 4, 4);
            var storedCrc = (uint)ReadInt32BigEndian(png, pos + 8 + length);
            var actualCrc = Crc32(png, pos + 4, length + 4);
            AssertEqual(storedCrc, actualCrc, "CRC of chunk " + type);

            switch (type)
            {
                case "IHDR":
                    sawIhdr = true;
                    width = ReadInt32BigEndian(png, pos + 8);
                    height = ReadInt32BigEndian(png, pos + 12);
                    AssertEqual(8, (int)png[pos + 16], "bit depth");
                    AssertEqual(6, (int)png[pos + 17], "color type (RGBA)");
                    AssertEqual(0, (int)png[pos + 20], "interlace");
                    break;
                case "IDAT":
                    idat.Write(png, pos + 8, length);
                    break;
                case "IEND":
                    sawIend = true;
                    break;
            }

            if (sawIend) break;
            pos += 12 + length;
        }

        Assert(sawIhdr, "missing IHDR chunk");
        Assert(sawIend, "missing IEND chunk");
        AssertEqual(64, width, "png width");
        AssertEqual(64, height, "png height");

        idat.Position = 0;
        using var inflate = new ZLibStream(idat, CompressionMode.Decompress);
        using var rawStream = new MemoryStream();
        inflate.CopyTo(rawStream);
        var raw = rawStream.ToArray();
        AssertEqual(64 * (64 * 4 + 1), raw.Length, "decompressed pixel data length");
        for (var y = 0; y < 64; y++) AssertEqual(0, (int)raw[y * (64 * 4 + 1)], "row filter at row " + y);
    }

    private static string ReadEntryText(ZipArchive zip, string name)
    {
        var bytes = ReadEntryBytes(zip, name);
        return Encoding.UTF8.GetString(bytes);
    }

    private static byte[] ReadEntryBytes(ZipArchive zip, string name)
    {
        var entry = zip.GetEntry(name) ?? throw new Exception("zip entry not found: " + name);
        using var stream = entry.Open();
        using var buffer = new MemoryStream();
        stream.CopyTo(buffer);
        return buffer.ToArray();
    }

    private static int ReadInt32BigEndian(byte[] data, int offset)
        => (data[offset] << 24) | (data[offset + 1] << 16) | (data[offset + 2] << 8) | data[offset + 3];

    private static uint Crc32(byte[] data, int offset, int count)
    {
        var crc = 0xFFFFFFFFu;
        for (var i = 0; i < count; i++)
        {
            crc ^= data[offset + i];
            for (var bit = 0; bit < 8; bit++)
                crc = (crc & 1) != 0 ? 0xEDB88320u ^ (crc >> 1) : crc >> 1;
        }

        return crc ^ 0xFFFFFFFFu;
    }

    private static void AssertThrows<T>(Action action, string what) where T : Exception
    {
        try
        {
            action();
        }
        catch (T)
        {
            return;
        }
        catch (Exception ex)
        {
            throw new Exception(what + ": expected " + typeof(T).Name + " but got " + ex.GetType().Name);
        }

        throw new Exception(what + ": expected " + typeof(T).Name + " but nothing was thrown");
    }

    private static void Check(string name, Action body)
    {
        try
        {
            body();
            _passed++;
            Console.WriteLine("PASS " + name);
        }
        catch (Exception ex)
        {
            Failures.Add(name + " :: " + ex.Message);
            Console.WriteLine("FAIL " + name + " :: " + ex.Message);
        }
    }

    private static void Assert(bool condition, string message)
    {
        if (!condition) throw new Exception(message);
    }

    private static void AssertEqual(object? expected, object? actual, string what)
    {
        if (Equals(expected, actual)) return;
        throw new Exception(what + ": expected <" + Format(expected) + "> but was <" + Format(actual) + ">");
    }

    private static string Format(object? value) => value switch
    {
        null => "null",
        string s => s,
        _ => value.ToString() ?? "null"
    };

    private static string ProjectDir([CallerFilePath] string path = "")
        => Path.GetDirectoryName(path) ?? Directory.GetCurrentDirectory();
}
