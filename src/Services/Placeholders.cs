using System.Text;
using System.Text.RegularExpressions;

namespace DstMcI18n.Services;

/// <summary>
/// Protects Minecraft formatting tokens (placeholders, color codes, newlines)
/// so a translator cannot destroy them.
/// </summary>
public static class Placeholders
{
    private static readonly Regex TokenRegex = new(
        @"%(\d+\$)?[-+ 0#]*(\d+)?(\.\d+)?[sdfxXoObBcC%]|\{\d+\}|§[0-9a-fk-orA-FK-OR]|\\n|\$\{[^}]+\}",
        RegexOptions.Compiled);

    public static (string text, List<string> tokens) Extract(string input)
    {
        var tokens = new List<string>();
        var text = TokenRegex.Replace(input, m =>
        {
            tokens.Add(m.Value);
            return "\u3010" + (tokens.Count - 1) + "\u3011";
        });
        return (text, tokens);
    }

    public static string Restore(string translated, List<string> tokens)
    {
        if (tokens.Count == 0) return translated;
        var sb = new StringBuilder(translated);
        for (var i = 0; i < tokens.Count; i++)
        {
            var marker = "\u3010" + i + "\u3011";
            var alt = "[[" + i + "]]";
            if (sb.ToString().Contains(marker, StringComparison.Ordinal))
            {
                sb.Replace(marker, tokens[i]);
            }
            else if (sb.ToString().Contains(alt, StringComparison.Ordinal))
            {
                sb.Replace(alt, tokens[i]);
            }
            else if (sb.ToString().Contains("\u3010" + i + " \u3011", StringComparison.Ordinal))
            {
                sb.Replace("\u3010" + i + " \u3011", tokens[i]);
            }
            else
            {
                // translator dropped the marker: append so information is not silently lost
                sb.Append(' ').Append(tokens[i]);
            }
        }
        return sb.ToString();
    }

    /// <summary>True when the value has nothing translatable (only tokens / punctuation).</summary>
    public static bool IsTrivial(string value)
    {
        if (string.IsNullOrWhiteSpace(value)) return true;
        var stripped = TokenRegex.Replace(value, "");
        return stripped.Trim().Length == 0;
    }
}
