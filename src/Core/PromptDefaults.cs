using System.Text;

namespace DstMcI18n.Core;

/// <summary>
/// The built-in translation system prompt. Users can override it from the settings page;
/// the placeholders {{targetLanguage}}, {{glossary}} and {{askUser}} are substituted at run time.
/// </summary>
public static class PromptDefaults
{
    public const string TargetLanguageToken = "{{targetLanguage}}";
    public const string GlossaryToken = "{{glossary}}";
    public const string AskUserToken = "{{askUser}}";

    public const string DefaultSystemPrompt =
        "You are a professional game localization engine for Minecraft mods.\n" +
        "Translate the given JSON object values from English into " + TargetLanguageToken + " (Simplified Chinese).\n" +
        "\n" +
        "Rules:\n" +
        "1. Return ONLY a JSON object. Keys must be exactly the same as the input. No markdown, no comments.\n" +
        "2. Translate the VALUES only. Never translate, remove or reorder placeholder tokens such as %s, %1$s, {0}, or curly-brace placeholders, and keep formatting codes (section sign followed by a letter) untouched.\n" +
        "3. Keep escaped sequences such as \n intact.\n" +
        "4. Use the terminology Chinese players actually use (Redstone -> 红石, Nether -> 下界, Crafting Table -> 工作台).\n" +
        "5. Keep translations concise; UI space is limited.\n" +
        "6. If a value is already Chinese or is a pure token/number, return it unchanged.\n" +
        "7. If a key/value pair defines an item ID, or references another component or other non-text content (for example a registry id, a tag, a translation key reference, a resource location, a command, a config path), do not translate or modify it — return the original value unchanged.\n" +
        GlossaryToken + "\n" +
        AskUserToken;

    /// <summary>Renders the prompt, falling back to the default when the user prompt is empty.</summary>
    public static string Render(string? userPrompt, string targetLanguage, IReadOnlyDictionary<string, string>? glossary, bool askUser)
    {
        var template = string.IsNullOrWhiteSpace(userPrompt) ? DefaultSystemPrompt : userPrompt!;
        var text = template.Replace(TargetLanguageToken, targetLanguage, StringComparison.Ordinal);

        var glossaryText = BuildGlossary(glossary);
        if (text.Contains(GlossaryToken, StringComparison.Ordinal))
            text = text.Replace(GlossaryToken, glossaryText, StringComparison.Ordinal);
        else if (glossaryText.Length > 0)
            text += "\n" + glossaryText;

        var askText = askUser ? AskUserSection : "";
        if (text.Contains(AskUserToken, StringComparison.Ordinal))
            text = text.Replace(AskUserToken, askText, StringComparison.Ordinal);
        else if (askText.Length > 0)
            text += "\n" + askText;

        return text.TrimEnd() + "\n";
    }

    private static string BuildGlossary(IReadOnlyDictionary<string, string>? glossary)
    {
        if (glossary is null || glossary.Count == 0) return "";
        var sb = new StringBuilder();
        sb.AppendLine("Glossary (must be followed exactly):");
        foreach (var kv in glossary) sb.AppendLine("- " + kv.Key + " => " + kv.Value);
        return sb.ToString().TrimEnd();
    }

    private const string AskUserSection =
        "If a term is a highly specialized domain word whose translation you cannot determine with confidence,\n" +
        "you may instead return: {\"__ask__\": {\"question\": \"...\", \"terms\": [\"...\"], \"options\": [\"...\", \"...\"]}}\n" +
        "and the user will answer. Use this sparingly (at most once per batch).";
}
