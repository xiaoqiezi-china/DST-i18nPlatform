using System.Text.Encodings.Web;
using System.Text.Json;
using System.Text.Json.Nodes;

namespace DstMcI18n.Core;

public static class JsonUtil
{
    public static readonly JsonDocumentOptions DocOptions = new()
    {
        CommentHandling = JsonCommentHandling.Skip,
        AllowTrailingCommas = true
    };

    public static readonly JsonSerializerOptions OutOptions = new()
    {
        WriteIndented = true,
        Encoder = JavaScriptEncoder.UnsafeRelaxedJsonEscaping
    };

    public static readonly JsonSerializerOptions CompactOptions = new()
    {
        WriteIndented = false,
        Encoder = JavaScriptEncoder.UnsafeRelaxedJsonEscaping
    };

    public static JsonNode? Parse(string text)
    {
        if (string.IsNullOrWhiteSpace(text)) return null;
        return JsonNode.Parse(text, documentOptions: DocOptions);
    }

    public static bool TryParse(string text, out JsonNode? node, out string? error)
    {
        try
        {
            node = Parse(text);
            error = null;
            return true;
        }
        catch (Exception ex)
        {
            node = null;
            error = ex.Message;
            return false;
        }
    }

    public static string Serialize(JsonNode? node, bool indented = true)
        => node is null ? "{}" : node.ToJsonString(indented ? OutOptions : CompactOptions);

    /// <summary>Flattens all string leaves into entries with segment paths.</summary>
    public static List<LangEntry> Flatten(JsonNode? root)
    {
        var list = new List<LangEntry>();
        Walk(root, new List<string>(), list);
        return list;
    }

    private static void Walk(JsonNode? node, List<string> segments, List<LangEntry> list)
    {
        switch (node)
        {
            case JsonObject obj:
                foreach (var kv in obj)
                {
                    segments.Add(kv.Key);
                    Walk(kv.Value, segments, list);
                    segments.RemoveAt(segments.Count - 1);
                }
                break;
            case JsonArray arr:
                for (var i = 0; i < arr.Count; i++)
                {
                    segments.Add("[" + i + "]");
                    Walk(arr[i], segments, list);
                    segments.RemoveAt(segments.Count - 1);
                }
                break;
            case JsonValue value when value.TryGetValue<string>(out var s):
                list.Add(new LangEntry
                {
                    Key = string.Join(".", segments),
                    Value = s,
                    Segments = segments.ToArray()
                });
                break;
        }
    }

    /// <summary>Writes translated values back into a parsed tree.</summary>
    public static void Apply(JsonNode? root, IEnumerable<LangEntry> entries)
    {
        foreach (var entry in entries)
        {
            if (entry.Segments is { Length: > 0 })
                SetPath(root, entry.Segments, entry.Value);
            else if (!string.IsNullOrEmpty(entry.Key))
                SetPath(root, entry.Key.Split('.'), entry.Value);
        }
    }

    private static void SetPath(JsonNode? node, string[] segments, string value)
    {
        if (node is null || segments.Length == 0) return;
        JsonNode? current = node;
        for (var i = 0; i < segments.Length; i++)
        {
            var seg = segments[i];
            var last = i == segments.Length - 1;
            if (current is JsonObject obj)
            {
                if (last)
                {
                    obj[seg] = JsonValue.Create(value);
                    return;
                }
                current = obj[seg];
            }
            else if (current is JsonArray arr)
            {
                var idx = ParseIndex(seg);
                if (idx < 0 || idx >= arr.Count) return;
                if (last)
                {
                    arr[idx] = JsonValue.Create(value);
                    return;
                }
                current = arr[idx];
            }
            else
            {
                return;
            }
        }
    }

    private static int ParseIndex(string seg)
    {
        if (seg.Length > 2 && seg[0] == '[' && seg[^1] == ']' && int.TryParse(seg[1..^1], out var idx))
            return idx;
        return -1;
    }

    public static JsonNode? Clone(JsonNode? node) => node?.DeepClone();
}
