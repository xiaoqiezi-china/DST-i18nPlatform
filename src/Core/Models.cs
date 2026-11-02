using System.Text.Json.Serialization;

namespace DstMcI18n.Core;

/// <summary>One translatable key/value pair.</summary>
public sealed class LangEntry
{
    public string Key { get; set; } = "";
    public string Value { get; set; } = "";
    /// <summary>Path segments used to write the value back into the source tree.</summary>
    [JsonIgnore] public string[]? Segments { get; set; }
}

/// <summary>A parsed language document (one lang file / snbt file).</summary>
public sealed class LangDocument
{
    public string Id { get; set; } = "";
    public string Kind { get; set; } = "";          // mod | kubejs | ftbquests
    public string OwnerId { get; set; } = "";       // mod id / namespace / ftbquests
    public string DisplayName { get; set; } = "";
    public string RelativePath { get; set; } = "";  // relative to workspace root (or archive path for mods)
    public string Format { get; set; } = "json";    // json | snbt
    public string SourceLanguage { get; set; } = "en_us";
    public string TargetLanguage { get; set; } = "zh_cn";
    public List<LangEntry> Entries { get; set; } = new();
    [JsonIgnore] public object? Tree { get; set; }
    [JsonIgnore] public string? RawContent { get; set; }
}

/// <summary>One language file inside a mod jar / mod folder.</summary>
public sealed class ModLangFile
{
    public string Namespace { get; set; } = "";
    public string Lang { get; set; } = "en_us";
    public string ArchivePath { get; set; } = "";
    public int EntryCount { get; set; }
    public bool HasZhCn { get; set; }
}

public sealed class ModInfo
{
    public string Id { get; set; } = "";
    public string Name { get; set; } = "";
    public string FileName { get; set; } = "";
    public string ModId { get; set; } = "";
    public string Version { get; set; } = "";
    public string Loader { get; set; } = "";
    public string Path { get; set; } = "";
    public bool IsFolder { get; set; }
    public bool Enabled { get; set; } = true;
    public List<ModLangFile> LangFiles { get; set; } = new();
    public int EntryCount { get; set; }
    public int TranslatedCount { get; set; }
    public string? Error { get; set; }
}

public sealed class KubejsLangFile
{
    public string Namespace { get; set; } = "";
    public string Lang { get; set; } = "en_us";
    public string RelativePath { get; set; } = "";
    public int EntryCount { get; set; }
    public bool HasZhCn { get; set; }
}

public sealed class KubejsInfo
{
    public bool Found { get; set; }
    public string? Root { get; set; }
    public List<string> Namespaces { get; set; } = new();
    public List<KubejsLangFile> Files { get; set; } = new();
    public List<string> Warnings { get; set; } = new();
}

public sealed class FtbLangFile
{
    public string Lang { get; set; } = "en_us";
    public string RelativePath { get; set; } = "";
    public int EntryCount { get; set; }
    public bool HasZhCn { get; set; }
}

public sealed class FtbInfo
{
    public bool Found { get; set; }
    public string? Root { get; set; }
    public List<string> Languages { get; set; } = new();
    public List<FtbLangFile> Files { get; set; } = new();
    public List<string> Warnings { get; set; } = new();
}

/// <summary>Result of scanning a Minecraft instance folder.</summary>
public sealed class WorkspaceInfo
{
    public string Path { get; set; } = "";
    public bool Exists { get; set; }
    public string Name { get; set; } = "";
    public string? ModsDir { get; set; }
    public string? KubejsDir { get; set; }
    public string? ConfigDir { get; set; }
    public string? GameVersion { get; set; }
    public string? Loader { get; set; }
    public List<string> Warnings { get; set; } = new();
    public List<string> Candidates { get; set; } = new();
    public ModsScan Mods { get; set; } = new();
    public KubejsInfo Kubejs { get; set; } = new();
    public FtbInfo Ftb { get; set; } = new();
    public long ScannedAt { get; set; }
}

public sealed class ModsScan
{
    public bool Found { get; set; }
    public string? Root { get; set; }
    public List<ModInfo> Mods { get; set; } = new();
    public List<string> Warnings { get; set; } = new();
}

/// <summary>Progress of an asynchronous workspace scan.</summary>
public sealed class ScanProgress
{
    public string ScanId { get; set; } = "";
    public string Path { get; set; } = "";
    public string Name { get; set; } = "";
    /// <summary>running | done | cancelled | error</summary>
    public string Status { get; set; } = "running";
    /// <summary>prepare | mods | kubejs | ftb | finish</summary>
    public string Phase { get; set; } = "prepare";
    public string PhaseLabel { get; set; } = "";
    public int Current { get; set; }
    public int Total { get; set; }
    public int Percent { get; set; }
    public string CurrentItem { get; set; } = "";
    public int ModsFound { get; set; }
    public long ElapsedMs { get; set; }
    public string? Error { get; set; }
    public List<string> Warnings { get; set; } = new();
}

/// <summary>Application settings persisted globally (per user).</summary>
public sealed class AppSettings
{
    /// <summary>Default model id; older DeepSeek ids (deepseek-chat) are migrated on load.</summary>
    public const string DefaultModel = "deepseek-flash";

    public static readonly string[] SuggestedModels = { "deepseek-flash", "deepseek-v4-pro" };

    public string LlmBaseUrl { get; set; } = "https://api.deepseek.com/v1";
    public string LlmApiKey { get; set; } = "";
    public string LlmModel { get; set; } = DefaultModel;
    /// <summary>User-editable system prompt. Empty means "use the built-in default".</summary>
    public string LlmSystemPrompt { get; set; } = "";
    public double LlmTemperature { get; set; } = 1.0;
    public int LlmMaxTokens { get; set; } = 4096;
    public string MtProvider { get; set; } = "auto";
    public string MtEndpoint { get; set; } = "";
    public string MtApiKey { get; set; } = "";
    public string TargetLanguage { get; set; } = "zh_cn";
    public bool AskUserEnabled { get; set; } = true;
    public string LastWorkspace { get; set; } = "";
    public int RequestTimeoutSeconds { get; set; } = 120;
    public int BatchSize { get; set; } = 24;
}

/// <summary>Request to start a batch translation job.</summary>
public sealed class TranslationRequest
{
    public string DocumentId { get; set; } = "";
    public string Kind { get; set; } = "mod";
    public string DisplayName { get; set; } = "";
    public string SourceLanguage { get; set; } = "en_us";
    public string TargetLanguage { get; set; } = "zh_cn";
    public string Mode { get; set; } = "llm";     // llm | mt
    public string? Provider { get; set; }
    public string? Context { get; set; }
    /// <summary>Optional user-supplied system prompt override.</summary>
    public string? SystemPrompt { get; set; }
    public List<LangEntry> Entries { get; set; } = new();
    public Dictionary<string, string>? Glossary { get; set; }
    public bool AskUserEnabled { get; set; } = true;
    public int BatchSize { get; set; } = 24;
    public bool SkipExisting { get; set; } = true;
    public Dictionary<string, string>? Existing { get; set; }
}

public sealed class TranslationItem
{
    public string Key { get; set; } = "";
    public string Source { get; set; } = "";
    public string Target { get; set; } = "";
    public string Status { get; set; } = "pending"; // pending | running | done | error | skipped
    public string? Error { get; set; }
}

public sealed class TranslationQuestion
{
    public string Id { get; set; } = "";
    public string Question { get; set; } = "";
    public List<string> Terms { get; set; } = new();
    public List<string> Options { get; set; } = new();
    public string Status { get; set; } = "pending"; // pending | answered | expired
    public string? Answer { get; set; }
    public long AskedAt { get; set; }
}

public sealed class TranslationJob
{
    public string Id { get; set; } = "";
    public string DocumentId { get; set; } = "";
    public string DisplayName { get; set; } = "";
    public string Mode { get; set; } = "llm";
    public string? Provider { get; set; }
    public string Status { get; set; } = "queued"; // queued | running | waiting_user | done | cancelled | error
    public int Total { get; set; }
    public int Done { get; set; }
    public int Failed { get; set; }
    public int Skipped { get; set; }
    public string? Error { get; set; }
    public long StartedAt { get; set; }
    public long FinishedAt { get; set; }
    public List<TranslationItem> Items { get; set; } = new();
    public List<TranslationQuestion> Questions { get; set; } = new();
    public string? LogTail { get; set; }
}
