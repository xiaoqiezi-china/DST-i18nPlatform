using System.IO.Compression;
using System.Text;
using System.Text.Json;
using System.Text.Json.Nodes;
using DstMcI18n.Core;
using DstMcI18n.Services;

namespace DstMcI18n.OverlayHarness;

/// <summary>
/// Self test for the v1.1 overlay export (ExportService.BuildKubejsOverlay / BuildFtbOverlay):
///   - an empty "files" list exports every kubejs / ftbquests language file of the workspace;
///   - every exported file is a complete table (source text + on-disk zh_cn + UI translations);
///   - zip paths are kubejs/... and config/ftbquests/... with the language name swapped;
///   - no packName / packVersion is required;
///   - an explicit file list still works (backwards compatible).
/// It builds a throw-away workspace under the system temp dir, so it never touches tests/fixtures.
/// </summary>
internal static class Program
{
    private static readonly List<string> Failures = new();
    private static int _passed;
    private static string _tmp = "";

    private static int Main()
    {
        Console.OutputEncoding = Encoding.UTF8;
        _tmp = Path.Combine(Path.GetTempPath(), "i18n-overlay-harness");
        if (Directory.Exists(_tmp)) Directory.Delete(_tmp, true);
        Directory.CreateDirectory(_tmp);

        Console.WriteLine("OverlayHarness - ExportService overlay self test");
        Console.WriteLine("work dir: " + _tmp);
        Console.WriteLine();

        var workspace = BuildWorkspace();
        var session = new WorkspaceSession();
        session.Open(workspace);
        var service = new ExportService(session, new DocumentService(session));

        TestKubejsAllFiles(service);
        TestKubejsCompleteTable(service);
        TestFtbAllFiles(service);
        TestFtbCompleteTable(service);
        TestNoPackVersion(service);
        TestEmptyFilesField(service);
        TestExplicitFilesStillWork(service);
        TestTargetLanguage(service);
        TestErrorBranches(session);

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

    // ------------------------------------------------------------- workspace

    /// <summary>Writes a minimal fake instance: two kubejs namespaces plus two ftbquests files.</summary>
    private static string BuildWorkspace()
    {
        var root = Path.Combine(_tmp, "instance");
        WriteFile(root, "kubejs/assets/custom_ns/lang/en_us.json", JsonText(new JsonObject
        {
            ["item.custom_ns.magic_wand"] = "Magic Wand",
            ["block.custom_ns.rune_stone"] = "Rune Stone",
            ["custom_ns.tooltip.energy"] = "Energy: %d FE",
            ["custom_ns.jei.category"] = "Rune Infusion"
        }));
        WriteFile(root, "kubejs/assets/custom_ns/lang/zh_cn.json", JsonText(new JsonObject
        {
            ["item.custom_ns.magic_wand"] = "魔法杖",
            ["block.custom_ns.rune_stone"] = "符文石"
        }));
        WriteFile(root, "kubejs/assets/kubejs/lang/en_us.json", JsonText(new JsonObject
        {
            ["item.kubejs.custom_sword"] = "Custom Sword",
            ["kubejs.ui.welcome"] = "Welcome to KubeJS"
        }));
        WriteFile(root, "config/ftbquests/quests/lang/en_us.snbt",
            "{\n\t\"quest.1.title\": \"Hello\",\n\t\"quest.2.title\": \"Gather Wood\",\n\t\"chapter.2.subtitle\": \"Automate everything\"\n}\n");
        WriteFile(root, "config/ftbquests/quests/lang/ja_jp.snbt",
            "{\n\t\"quest.1.title\": \"こんにちは\",\n\t\"quest.2.title\": \"木を集める\",\n\t\"chapter.2.subtitle\": \"すべてを自動化\"\n}\n");
        // Quest data must never end up inside an overlay pack.
        WriteFile(root, "config/ftbquests/quests/chapters/chapter1.snbt", "{ id: \"chapter1\", title: \"Getting Started\" }\n");
        WriteFile(root, "version.json", JsonText(new JsonObject { ["id"] = "1.20.1-forge-47.2.0" }));
        return root;
    }

    private static void WriteFile(string root, string relative, string content)
    {
        var full = Path.Combine(root, relative.Replace('/', Path.DirectorySeparatorChar));
        Directory.CreateDirectory(Path.GetDirectoryName(full)!);
        File.WriteAllText(full, content, new UTF8Encoding(false));
    }

    private static string JsonText(JsonObject node)
        => node.ToJsonString(JsonUtil.OutOptions) + "\n";

    // ----------------------------------------------------------------- tests

    private static void TestKubejsAllFiles(ExportService service)
    {
        Check("kubejs overlay: empty files list exports every kubejs language file", () =>
        {
            var (bytes, name) = service.BuildKubejsOverlay(new ExportOverlayRequest());
            var names = ZipNames(bytes);
            var langFiles = names.Where(n => n != "README.txt").ToList();

            AssertEqual(2, langFiles.Count, "language file count: " + string.Join(", ", names));
            foreach (var file in langFiles)
                Assert(file.StartsWith("kubejs/", StringComparison.Ordinal), "path not under kubejs/: " + file);
            Assert(names.Contains("kubejs/assets/custom_ns/lang/zh_cn.json"), "missing custom_ns: " + string.Join(", ", names));
            Assert(names.Contains("kubejs/assets/kubejs/lang/zh_cn.json"), "missing kubejs: " + string.Join(", ", names));
            AssertEqual("instance-kubejs-zh_cn.zip", name, "download file name");
        });
    }

    private static void TestKubejsCompleteTable(ExportService service)
    {
        Check("kubejs overlay: each file merges source + on-disk zh_cn + UI translations", () =>
        {
            var (bytes, _) = service.BuildKubejsOverlay(new ExportOverlayRequest());
            var table = ReadJson(bytes, "kubejs/assets/custom_ns/lang/zh_cn.json");

            AssertEqual(4, table.Count, "entry count");
            AssertEqual("魔法杖", (string?)table["item.custom_ns.magic_wand"], "on-disk zh_cn kept");
            AssertEqual("符文石", (string?)table["block.custom_ns.rune_stone"], "on-disk zh_cn kept 2");
            AssertEqual("Rune Infusion", (string?)table["custom_ns.jei.category"], "untranslated keeps source text");
        });

        Check("kubejs overlay: UI translations win and UI-only keys are appended", () =>
        {
            var (bytes, _) = service.BuildKubejsOverlay(new ExportOverlayRequest
            {
                Files = new List<OverlayFileRequest>
                {
                    new()
                    {
                        Path = "assets/custom_ns/lang/en_us.json",
                        Entries = new List<LangEntry>
                        {
                            new() { Key = "custom_ns.tooltip.energy", Value = "能量：%d FE" },
                            new() { Key = "custom_ns.only.in.ui", Value = "仅前端" }
                        }
                    }
                }
            });
            var table = ReadJson(bytes, "kubejs/assets/custom_ns/lang/zh_cn.json");
            AssertEqual(5, table.Count, "entry count (4 source + 1 ui-only)");
            AssertEqual("能量：%d FE", (string?)table["custom_ns.tooltip.energy"], "UI translation wins");
            AssertEqual("仅前端", (string?)table["custom_ns.only.in.ui"], "ui-only key appended");
            AssertEqual("魔法杖", (string?)table["item.custom_ns.magic_wand"], "on-disk zh_cn still merged");
        });
    }

    private static void TestFtbAllFiles(ExportService service)
    {
        Check("ftb overlay: empty files list exports every ftbquests language file", () =>
        {
            var (bytes, name) = service.BuildFtbOverlay(new ExportOverlayRequest());
            var names = ZipNames(bytes);
            var langFiles = names.Where(n => n != "README.txt").ToList();

            // en_us + ja_jp both map onto quests/lang/zh_cn.snbt, so exactly one entry survives.
            AssertEqual(1, langFiles.Count, "language file count: " + string.Join(", ", names));
            AssertEqual("config/ftbquests/quests/lang/zh_cn.snbt", langFiles[0], "zip path");
            Assert(!names.Any(n => n.Contains("chapters")), "quest data file leaked into the pack: " + string.Join(", ", names));
            AssertEqual("instance-ftbquests-zh_cn.zip", name, "download file name");
        });

        Check("ftb overlay: snbt keeps the full table and applies translations", () =>
        {
            var (bytes, _) = service.BuildFtbOverlay(new ExportOverlayRequest
            {
                Files = new List<OverlayFileRequest>
                {
                    new()
                    {
                        Path = "quests/lang/en_us.snbt",
                        Entries = new List<LangEntry>
                        {
                            new() { Key = "quest.1.title", Value = "你好" },
                            new() { Key = "quest.2.title", Value = "收集木材" }
                        }
                    }
                }
            });
            var text = Encoding.UTF8.GetString(ZipRead(bytes, "config/ftbquests/quests/lang/zh_cn.snbt"));
            Assert(text.Contains("你好"), "translation missing: " + text);
            Assert(text.Contains("收集木材"), "translation 2 missing: " + text);
            Assert(text.Contains("chapter.2.subtitle"), "full table not preserved: " + text);
            Assert(text.Contains("Automate everything"), "untranslated key should keep source text: " + text);
        });
    }

    private static void TestFtbCompleteTable(ExportService service)
    {
        Check("ftb overlay: source-language file wins when several map onto one zh_cn path", () =>
        {
            var (bytes, _) = service.BuildFtbOverlay(new ExportOverlayRequest());
            var text = Encoding.UTF8.GetString(ZipRead(bytes, "config/ftbquests/quests/lang/zh_cn.snbt"));
            // en_us must be the base document; ja_jp must not overwrite it.
            Assert(text.Contains("Hello"), "en_us base text missing: " + text);
            Assert(!text.Contains("こんにちは"), "ja_jp file overwrote the source table: " + text);
        });
    }

    private static void TestNoPackVersion(ExportService service)
    {
        Check("overlay: no packName / packVersion needed, README carries no version", () =>
        {
            var (kubejs, _) = service.BuildKubejsOverlay(new ExportOverlayRequest { TargetLanguage = "zh_cn" });
            var (ftb, _) = service.BuildFtbOverlay(new ExportOverlayRequest { TargetLanguage = "zh_cn" });
            Assert(kubejs.Length > 0 && ftb.Length > 0, "empty zip");

            var readme = Encoding.UTF8.GetString(ZipRead(kubejs, "README.txt"));
            Assert(!readme.Contains("1.0.0"), "README should not invent a version: " + readme);
            Assert(readme.Contains("覆盖"), "README should still explain how to apply the pack");
            Assert(readme.Contains("zh_cn"), "README should mention the target language");
        });
    }

    private static void TestEmptyFilesField(ExportService service)
    {
        Check("overlay: JSON body without a files field exports everything", () =>
        {
            var body = JsonSerializer.Deserialize<ExportOverlayRequest>(
                "{\"targetLanguage\":\"zh_cn\"}",
                new JsonSerializerOptions
                {
                    PropertyNameCaseInsensitive = true,
                    PropertyNamingPolicy = JsonNamingPolicy.CamelCase
                });
            Assert(body is not null, "request body did not deserialize");
            AssertEqual(0, body!.Files.Count, "Files should default to an empty list");

            var (bytes, _) = service.BuildKubejsOverlay(body);
            AssertEqual(3, ZipNames(bytes).Count, "2 language files + README expected");
        });
    }

    private static void TestExplicitFilesStillWork(ExportService service)
    {
        Check("overlay: explicit file list is still honoured (backwards compatible)", () =>
        {
            var (bytes, _) = service.BuildKubejsOverlay(new ExportOverlayRequest
            {
                Files = new List<OverlayFileRequest> { new() { Path = "assets/kubejs/lang/en_us.json" } }
            });
            var names = ZipNames(bytes);
            AssertEqual(2, names.Count, "1 language file + README expected: " + string.Join(", ", names));
            AssertEqual("kubejs/assets/kubejs/lang/zh_cn.json", names[0], "zip path");
        });
    }

    private static void TestTargetLanguage(ExportService service)
    {
        Check("overlay: a non-default target language swaps the file name and the zip paths", () =>
        {
            var (bytes, name) = service.BuildKubejsOverlay(new ExportOverlayRequest { TargetLanguage = "zh_tw" });
            var names = ZipNames(bytes);
            Assert(names.Contains("kubejs/assets/custom_ns/lang/zh_tw.json"), "zh_tw path missing: " + string.Join(", ", names));
            AssertEqual("instance-kubejs-zh_tw.zip", name, "download file name");
            AssertEqual(4, ReadJson(bytes, "kubejs/assets/custom_ns/lang/zh_tw.json").Count, "table stays complete");
        });
    }

    private static void TestErrorBranches(WorkspaceSession session)
    {
        Check("overlay: unknown explicit path reports a clear error", () =>
        {
            var service = new ExportService(session, new DocumentService(session));
            var error = AssertThrows<ArgumentException>(() => service.BuildKubejsOverlay(new ExportOverlayRequest
            {
                Files = new List<OverlayFileRequest> { new() { Path = "assets/nope/lang/en_us.json" } }
            }), "missing file");
            Assert(error.Message.Contains("不存在"), "message should explain the file is missing: " + error.Message);
        });

        Check("overlay: workspace without language files reports a clear error", () =>
        {
            var empty = Path.Combine(_tmp, "empty-instance");
            Directory.CreateDirectory(Path.Combine(empty, "kubejs"));
            Directory.CreateDirectory(Path.Combine(empty, "config", "ftbquests"));
            var emptySession = new WorkspaceSession();
            emptySession.Open(empty);
            var service = new ExportService(emptySession, new DocumentService(emptySession));

            var kubejs = AssertThrows<ArgumentException>(() => service.BuildKubejsOverlay(new ExportOverlayRequest()), "empty kubejs");
            Assert(kubejs.Message.Contains("没有可导出"), "message: " + kubejs.Message);
            var ftb = AssertThrows<ArgumentException>(() => service.BuildFtbOverlay(new ExportOverlayRequest()), "empty ftb");
            Assert(ftb.Message.Contains("没有可导出"), "message: " + ftb.Message);
        });

        Check("overlay: exporting without an opened workspace fails cleanly", () =>
        {
            var service = new ExportService(new WorkspaceSession(), new DocumentService(new WorkspaceSession()));
            AssertThrows<InvalidOperationException>(() => service.BuildKubejsOverlay(new ExportOverlayRequest()), "no workspace");
        });
    }

    // --------------------------------------------------------------- helpers

    private static List<string> ZipNames(byte[] zip)
    {
        using var archive = new ZipArchive(new MemoryStream(zip), ZipArchiveMode.Read);
        return archive.Entries.Select(e => e.FullName).Where(n => !n.EndsWith("/", StringComparison.Ordinal)).ToList();
    }

    private static byte[] ZipRead(byte[] zip, string name)
    {
        using var archive = new ZipArchive(new MemoryStream(zip), ZipArchiveMode.Read);
        var entry = archive.GetEntry(name) ?? throw new Exception("zip entry not found: " + name +
            " (have: " + string.Join(", ", archive.Entries.Select(e => e.FullName)) + ")");
        using var stream = entry.Open();
        using var buffer = new MemoryStream();
        stream.CopyTo(buffer);
        return buffer.ToArray();
    }

    private static JsonObject ReadJson(byte[] zip, string name)
    {
        var text = Encoding.UTF8.GetString(ZipRead(zip, name));
        return JsonNode.Parse(text) as JsonObject ?? throw new Exception(name + " is not a JSON object");
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
        throw new Exception(what + ": expected <" + expected + "> but was <" + actual + ">");
    }

    private static T AssertThrows<T>(Action action, string what) where T : Exception
    {
        try
        {
            action();
        }
        catch (T error)
        {
            return error;
        }
        catch (Exception ex)
        {
            throw new Exception(what + ": expected " + typeof(T).Name + " but got " + ex.GetType().Name + " (" + ex.Message + ")");
        }

        throw new Exception(what + ": expected " + typeof(T).Name + " but nothing was thrown");
    }
}
