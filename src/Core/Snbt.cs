using System.Globalization;
using System.Text;
using System.Text.Json.Nodes;

namespace DstMcI18n.Core;

/// <summary>
/// Minimal SNBT (Minecraft NBT text form) reader/writer.
/// Non-string values keep their raw text so a round-trip is lossless.
/// </summary>
public static class Snbt
{
    public static bool TryParse(string text, out JsonNode? root, out Dictionary<string, string> raw, out string? error)
    {
        raw = new Dictionary<string, string>(StringComparer.Ordinal);
        try
        {
            var parser = new Parser(text);
            root = parser.ParseDocument(raw);
            error = null;
            return true;
        }
        catch (Exception ex)
        {
            root = null;
            error = ex.Message;
            return false;
        }
    }

    public static string Serialize(JsonNode? root, Dictionary<string, string>? raw)
    {
        var sb = new StringBuilder();
        Write(root, raw ?? new Dictionary<string, string>(), sb, 0, "");
        return sb.ToString();
    }

    private static void Write(JsonNode? node, Dictionary<string, string> raw, StringBuilder sb, int depth, string path)
    {
        switch (node)
        {
            case JsonObject obj:
            {
                if (obj.Count == 0) { sb.Append("{}"); return; }
                sb.Append('{');
                var first = true;
                foreach (var kv in obj)
                {
                    if (!first) sb.Append(',');
                    first = false;
                    sb.Append('\n');
                    Indent(sb, depth + 1);
                    sb.Append(QuoteKey(kv.Key)).Append(": ");
                    Write(kv.Value, raw, sb, depth + 1, path.Length == 0 ? kv.Key : path + "." + kv.Key);
                }
                sb.Append('\n');
                Indent(sb, depth);
                sb.Append('}');
                return;
            }
            case JsonArray arr:
            {
                if (arr.Count == 0) { sb.Append("[]"); return; }
                sb.Append('[');
                for (var i = 0; i < arr.Count; i++)
                {
                    if (i > 0) sb.Append(',');
                    sb.Append('\n');
                    Indent(sb, depth + 1);
                    Write(arr[i], raw, sb, depth + 1, path + "." + i.ToString(CultureInfo.InvariantCulture));
                }
                sb.Append('\n');
                Indent(sb, depth);
                sb.Append(']');
                return;
            }
            case JsonValue value:
            {
                if (value.TryGetValue<string>(out var s))
                {
                    sb.Append(Quote(s));
                    return;
                }
                if (raw.TryGetValue(path, out var rawText))
                {
                    sb.Append(rawText);
                    return;
                }
                if (value.TryGetValue<bool>(out var b)) { sb.Append(b ? "1b" : "0b"); return; }
                if (value.TryGetValue<long>(out var l)) { sb.Append(l.ToString(CultureInfo.InvariantCulture)).Append('L'); return; }
                if (value.TryGetValue<double>(out var d)) { sb.Append(d.ToString("R", CultureInfo.InvariantCulture)).Append('d'); return; }
                sb.Append(value.ToJsonString());
                return;
            }
            default:
                sb.Append("{}");
                return;
        }
    }

    private static void Indent(StringBuilder sb, int depth)
    {
        for (var i = 0; i < depth; i++) sb.Append('\t');
    }

    private static string QuoteKey(string key) => Quote(key);

    public static string Quote(string value)
    {
        var sb = new StringBuilder(value.Length + 2);
        sb.Append('"');
        foreach (var ch in value)
        {
            switch (ch)
            {
                case '"': sb.Append("\\\""); break;
                case '\\': sb.Append("\\\\"); break;
                case '\n': sb.Append("\\n"); break;
                case '\r': sb.Append("\\r"); break;
                case '\t': sb.Append("\\t"); break;
                case '\b': sb.Append("\\b"); break;
                case '\f': sb.Append("\\f"); break;
                default:
                    if (ch < 0x20) sb.Append("\\u").Append(((int)ch).ToString("x4", CultureInfo.InvariantCulture));
                    else sb.Append(ch);
                    break;
            }
        }
        sb.Append('"');
        return sb.ToString();
    }

    private sealed class Parser
    {
        private readonly string _text;
        private int _pos;

        public Parser(string text)
        {
            _text = text ?? "";
        }

        private char Peek()
        {
            SkipWs();
            return _pos < _text.Length ? _text[_pos] : '\0';
        }

        private void SkipWs()
        {
            while (_pos < _text.Length)
            {
                var c = _text[_pos];
                if (c is ' ' or '\t' or '\r' or '\n') { _pos++; continue; }
                if (c == '/' && _pos + 1 < _text.Length && _text[_pos + 1] == '/')
                {
                    while (_pos < _text.Length && _text[_pos] != '\n') _pos++;
                    continue;
                }
                break;
            }
        }

        public JsonNode? ParseDocument(Dictionary<string, string> raw)
        {
            var node = ParseValue(raw, "");
            SkipWs();
            return node;
        }

        private JsonNode? ParseValue(Dictionary<string, string> raw, string path)
        {
            var c = Peek();
            return c switch
            {
                '{' => ParseCompound(raw, path),
                '[' => ParseList(raw, path),
                '"' or '\'' => JsonValue.Create(ParseQuoted()),
                '\0' => throw new FormatException("Unexpected end of SNBT input at offset " + _pos),
                _ => ParseBare(raw, path)
            };
        }

        private JsonObject ParseCompound(Dictionary<string, string> raw, string path)
        {
            Expect('{');
            var obj = new JsonObject();
            while (true)
            {
                var c = Peek();
                if (c == '}') { _pos++; break; }
                if (c == '\0') throw new FormatException("Unterminated compound");
                string key = (c is '"' or '\'') ? ParseQuoted() : ParseBareKey();
                SkipWs();
                Expect(':');
                SkipWs();
                var childPath = path.Length == 0 ? key : path + "." + key;
                var value = ParseValue(raw, childPath);
                obj[key] = value;
                SkipWs();
                if (_pos < _text.Length && _text[_pos] == ',') { _pos++; continue; }
                if (_pos < _text.Length && _text[_pos] == '}') { _pos++; break; }
                if (_pos >= _text.Length) throw new FormatException("Unterminated compound");
            }
            return obj;
        }

        private JsonArray ParseList(Dictionary<string, string> raw, string path)
        {
            Expect('[');
            // typed array prefix: B; I; L;
            var save = _pos;
            SkipWs();
            if (_pos + 1 < _text.Length && _text[_pos] is 'B' or 'I' or 'L' && _text[_pos + 1] == ';')
            {
                _pos += 2;
                var arr = new JsonArray();
                while (true)
                {
                    SkipWs();
                    if (_pos < _text.Length && _text[_pos] == ']') { _pos++; break; }
                    var rawValue = ReadBareToken();
                    var num = StripNumberSuffix(rawValue, out _);
                    var idx = arr.Count;
                    var childPath = path + "." + idx.ToString(CultureInfo.InvariantCulture);
                    arr.Add(JsonValue.Create(num));
                    raw[childPath] = rawValue;
                    SkipWs();
                    if (_pos < _text.Length && _text[_pos] == ',') { _pos++; continue; }
                }
                return arr;
            }
            _pos = save;
            var list = new JsonArray();
            while (true)
            {
                SkipWs();
                if (_pos < _text.Length && _text[_pos] == ']') { _pos++; break; }
                var idx = list.Count;
                var childPath = path + "." + idx.ToString(CultureInfo.InvariantCulture);
                list.Add(ParseValue(raw, childPath));
                SkipWs();
                if (_pos < _text.Length && _text[_pos] == ',') { _pos++; continue; }
                if (_pos < _text.Length && _text[_pos] == ']') { _pos++; break; }
                if (_pos >= _text.Length) throw new FormatException("Unterminated list");
            }
            return list;
        }

        private string ParseBareKey()
        {
            SkipWs();
            var start = _pos;
            while (_pos < _text.Length && _text[_pos] != ':' && _text[_pos] != '{' && _text[_pos] != '}')
            {
                if (_text[_pos] is ' ' or '\t' or '\r' or '\n' or ',') break;
                _pos++;
            }
            if (_pos == start) throw new FormatException("Expected compound key at offset " + _pos);
            return _text[start.._pos].Trim();
        }

        private JsonNode? ParseBare(Dictionary<string, string> raw, string path)
        {
            var token = ReadBareToken();
            if (token.Length == 0) throw new FormatException("Unexpected token at offset " + _pos);
            if (token.Equals("true", StringComparison.OrdinalIgnoreCase)) return JsonValue.Create(true);
            if (token.Equals("false", StringComparison.OrdinalIgnoreCase)) return JsonValue.Create(false);
            var numText = StripNumberSuffix(token, out var isFloat);
            if (isFloat)
            {
                if (double.TryParse(numText, NumberStyles.Float, CultureInfo.InvariantCulture, out var d))
                {
                    raw[path] = token;
                    return JsonValue.Create(d);
                }
            }
            else if (long.TryParse(numText, NumberStyles.Integer, CultureInfo.InvariantCulture, out var l))
            {
                raw[path] = token;
                return JsonValue.Create(l);
            }
            // Unquoted string (legacy SNBT allows it).
            return JsonValue.Create(token);
        }

        private string ReadBareToken()
        {
            SkipWs();
            var start = _pos;
            while (_pos < _text.Length)
            {
                var c = _text[_pos];
                if (c is ' ' or '\t' or '\r' or '\n' or ',' or ']' or '}' or ':' or '{' or '[') break;
                _pos++;
            }
            return _text[start.._pos];
        }

        private static string StripNumberSuffix(string token, out bool isFloat)
        {
            isFloat = false;
            if (token.Length == 0) return token;
            var last = token[^1];
            switch (last)
            {
                case 'b' or 'B' or 's' or 'S' or 'l' or 'L':
                    return token[..^1];
                case 'f' or 'F' or 'd' or 'D':
                    isFloat = true;
                    return token[..^1];
                default:
                    isFloat = token.Contains('.') || token.Contains('e') || token.Contains('E');
                    return token;
            }
        }

        private string ParseQuoted()
        {
            SkipWs();
            var quote = _text[_pos];
            _pos++;
            var sb = new StringBuilder();
            while (_pos < _text.Length)
            {
                var c = _text[_pos++];
                if (c == quote) return sb.ToString();
                if (c == '\\' && _pos < _text.Length)
                {
                    var e = _text[_pos++];
                    switch (e)
                    {
                        case 'n': sb.Append('\n'); break;
                        case 'r': sb.Append('\r'); break;
                        case 't': sb.Append('\t'); break;
                        case 'b': sb.Append('\b'); break;
                        case 'f': sb.Append('\f'); break;
                        case 'u':
                            if (_pos + 4 <= _text.Length &&
                                int.TryParse(_text.AsSpan(_pos, 4), NumberStyles.HexNumber, CultureInfo.InvariantCulture, out var code))
                            {
                                sb.Append((char)code);
                                _pos += 4;
                            }
                            break;
                        default: sb.Append(e); break;
                    }
                    continue;
                }
                sb.Append(c);
            }
            throw new FormatException("Unterminated string");
        }

        private void Expect(char c)
        {
            SkipWs();
            if (_pos >= _text.Length || _text[_pos] != c)
                throw new FormatException($"Expected '{c}' at offset {_pos}");
            _pos++;
        }
    }
}
