using System.Text.Json;
using System.Text.Json.Nodes;

namespace DstMcI18n.Core;

/// <summary>Loads/saves application settings in %APPDATA%\DstMcI18nTool\settings.json.</summary>
public sealed class SettingsStore
{
    private readonly object _gate = new();
    private AppSettings _settings;

    public SettingsStore()
    {
        FilePath = ResolvePath();
        _settings = Load();
    }

    public string FilePath { get; }

    public AppSettings Current
    {
        get { lock (_gate) return _settings; }
    }

    private static string ResolvePath()
    {
        try
        {
            var dir = System.IO.Path.Combine(
                Environment.GetFolderPath(Environment.SpecialFolder.ApplicationData),
                "DstMcI18nTool");
            Directory.CreateDirectory(dir);
            return System.IO.Path.Combine(dir, "settings.json");
        }
        catch
        {
            var fallback = System.IO.Path.Combine(AppContext.BaseDirectory, "settings.json");
            return fallback;
        }
    }

    private AppSettings Load()
    {
        try
        {
            if (File.Exists(FilePath))
            {
                var json = File.ReadAllText(FilePath);
                var loaded = JsonSerializer.Deserialize<AppSettings>(json);
                if (loaded is not null)
                {
                    MigrateLegacyModel(loaded);
                    return loaded;
                }
            }
        }
        catch
        {
            // corrupted settings fall back to defaults
        }
        return new AppSettings();
    }

    /// <summary>Older builds defaulted to model ids that no longer exist on the DeepSeek API.</summary>
    private static void MigrateLegacyModel(AppSettings settings)
    {
        var model = (settings.LlmModel ?? "").Trim();
        if (model.Length == 0 ||
            model.Equals("deepseek-chat", StringComparison.OrdinalIgnoreCase) ||
            model.Equals("deepseek-reasoner", StringComparison.OrdinalIgnoreCase) ||
            model.Equals("deepseek-coder", StringComparison.OrdinalIgnoreCase))
        {
            settings.LlmModel = AppSettings.DefaultModel;
        }
    }

    public AppSettings Save(AppSettings incoming)
    {
        lock (_gate)
        {
            var s = _settings;
            s.LlmBaseUrl = string.IsNullOrWhiteSpace(incoming.LlmBaseUrl) ? s.LlmBaseUrl : incoming.LlmBaseUrl.Trim();
            if (!string.IsNullOrWhiteSpace(incoming.LlmApiKey)) s.LlmApiKey = incoming.LlmApiKey.Trim();
            s.LlmModel = string.IsNullOrWhiteSpace(incoming.LlmModel) ? s.LlmModel : incoming.LlmModel.Trim();
            s.LlmSystemPrompt = incoming.LlmSystemPrompt ?? "";
            s.LlmTemperature = Math.Clamp(incoming.LlmTemperature, 0, 2);
            s.LlmMaxTokens = Math.Clamp(incoming.LlmMaxTokens, 256, 32768);
            s.MtProvider = string.IsNullOrWhiteSpace(incoming.MtProvider) ? s.MtProvider : incoming.MtProvider.Trim();
            s.MtEndpoint = incoming.MtEndpoint ?? "";
            if (!string.IsNullOrWhiteSpace(incoming.MtApiKey)) s.MtApiKey = incoming.MtApiKey.Trim();
            s.TargetLanguage = string.IsNullOrWhiteSpace(incoming.TargetLanguage) ? s.TargetLanguage : incoming.TargetLanguage.Trim();
            s.AskUserEnabled = incoming.AskUserEnabled;
            s.RequestTimeoutSeconds = Math.Clamp(incoming.RequestTimeoutSeconds, 10, 600);
            s.BatchSize = Math.Clamp(incoming.BatchSize, 1, 200);
            if (!string.IsNullOrWhiteSpace(incoming.LastWorkspace)) s.LastWorkspace = incoming.LastWorkspace;
            Persist();
            return s;
        }
    }

    public void ResetSystemPrompt()
    {
        lock (_gate)
        {
            _settings.LlmSystemPrompt = "";
            Persist();
        }
    }

    public void SetLastWorkspace(string path)
    {
        lock (_gate)
        {
            _settings.LastWorkspace = path;
            Persist();
        }
    }

    private void Persist()
    {
        try
        {
            var json = JsonSerializer.Serialize(_settings, JsonUtil.OutOptions);
            var dir = System.IO.Path.GetDirectoryName(FilePath);
            if (!string.IsNullOrEmpty(dir)) Directory.CreateDirectory(dir);
            File.WriteAllText(FilePath, json);
        }
        catch
        {
            // settings persistence is best effort
        }
    }

    /// <summary>Settings shape sent to the browser (never exposes the API key).</summary>
    public object ToDto()
    {
        var s = Current;
        return new
        {
            llmBaseUrl = s.LlmBaseUrl,
            llmApiKey = "",
            llmApiKeySet = !string.IsNullOrEmpty(s.LlmApiKey),
            llmModel = s.LlmModel,
            suggestedModels = AppSettings.SuggestedModels,
            llmSystemPrompt = s.LlmSystemPrompt,
            llmSystemPromptDefault = PromptDefaults.DefaultSystemPrompt,
            llmTemperature = s.LlmTemperature,
            llmMaxTokens = s.LlmMaxTokens,
            mtProvider = s.MtProvider,
            mtEndpoint = s.MtEndpoint,
            mtApiKey = "",
            mtApiKeySet = !string.IsNullOrEmpty(s.MtApiKey),
            targetLanguage = s.TargetLanguage,
            askUserEnabled = s.AskUserEnabled,
            requestTimeoutSeconds = s.RequestTimeoutSeconds,
            batchSize = s.BatchSize,
            lastWorkspace = s.LastWorkspace,
            settingsPath = FilePath
        };
    }
}
