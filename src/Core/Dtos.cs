namespace DstMcI18n.Core;

/// <summary>Wire format for a key/value table shown in the browser.</summary>
public sealed class DocumentResult
{
    public string Id { get; set; } = "";
    public string Kind { get; set; } = "";
    public string OwnerId { get; set; } = "";
    public string DisplayName { get; set; } = "";
    public string RelativePath { get; set; } = "";
    public string Format { get; set; } = "json";
    public string SourceLanguage { get; set; } = "en_us";
    public string TargetLanguage { get; set; } = "zh_cn";
    public List<LangEntry> Entries { get; set; } = new();
    public Dictionary<string, string> Existing { get; set; } = new(StringComparer.Ordinal);
    public bool HasExisting { get; set; }
    /// <summary>True when the mod/kubejs/ftb already ships a zh_cn file that can be restored.</summary>
    public bool CanRestoreToExisting { get; set; }
    public string? ModId { get; set; }
    public string? NamespaceName { get; set; }
    public string? ModFileName { get; set; }
}

public sealed class DocumentQuery
{
    public string Kind { get; set; } = "mod";
    public string? File { get; set; }
    public string? Namespace { get; set; }
    public string? Lang { get; set; }
    public string? Path { get; set; }
}

public sealed class ExportLangRequest
{
    public string Kind { get; set; } = "mod";
    public string? File { get; set; }
    public string? Namespace { get; set; }
    public string? Lang { get; set; }
    public string? Path { get; set; }
    public List<LangEntry> Entries { get; set; } = new();
    public string? TargetLanguage { get; set; }
    public string? FileName { get; set; }
}

public sealed class ExportPackRequest
{
    public string Kind { get; set; } = "mod";
    public string File { get; set; } = "";
    public string? Namespace { get; set; }
    public string? Lang { get; set; }
    public List<LangEntry> Entries { get; set; } = new();
    public string? TargetLanguage { get; set; }
    public string? PackName { get; set; }
    public string? PackVersion { get; set; }
    public string? McVersion { get; set; }
    public int? PackFormat { get; set; }
    public string? Description { get; set; }
}

public sealed class OverlayFileRequest
{
    public string Path { get; set; } = "";
    public List<LangEntry> Entries { get; set; } = new();
}

public sealed class ExportOverlayRequest
{
    public List<OverlayFileRequest> Files { get; set; } = new();
    public string? TargetLanguage { get; set; }
    public string? PackName { get; set; }
    public string? PackVersion { get; set; }
}

public sealed class ExportWriteRequest
{
    public string Kind { get; set; } = "mod";
    public string? File { get; set; }
    public string? Namespace { get; set; }
    public string? Lang { get; set; }
    public string? Path { get; set; }
    public List<LangEntry> Entries { get; set; } = new();
    public string? TargetLanguage { get; set; }
    public bool Overwrite { get; set; }
    public string? OutputDirectory { get; set; }
}

public sealed class AnswerRequest
{
    public string QuestionId { get; set; } = "";
    public string Answer { get; set; } = "";
}
