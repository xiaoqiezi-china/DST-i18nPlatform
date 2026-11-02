using System.Net.Http.Headers;
using System.Text;
using System.Text.Json;
using System.Text.Json.Nodes;
using DstMcI18n.Core;

namespace DstMcI18n.Services;

public sealed class MtException : Exception
{
    public MtException(string message, Exception? inner = null) : base(message, inner) { }
}

/// <summary>Free/bring-your-own machine translation backends.</summary>
public sealed class MachineTranslator
{
    private readonly HttpClient _http;

    public MachineTranslator(HttpClient http)
    {
        _http = http;
    }

    public static string ToGoogleLang(string lang) => lang.ToLowerInvariant() switch
    {
        "zh_cn" or "zh-cn" or "zh" => "zh-CN",
        "zh_tw" or "zh-tw" => "zh-TW",
        "en_us" or "en" => "en",
        "ja_jp" or "ja" => "ja",
        "ko_kr" or "ko" => "ko",
        "ru_ru" or "ru" => "ru",
        "de_de" or "de" => "de",
        "fr_fr" or "fr" => "fr",
        "es_es" or "es" => "es",
        "pt_br" or "pt" => "pt",
        _ => lang.Replace('_', '-')
    };

    public async Task<List<string>> TranslateAsync(
        AppSettings settings,
        string provider,
        IReadOnlyList<string> texts,
        string sourceLanguage,
        string targetLanguage,
        CancellationToken ct)
    {
        if (texts.Count == 0) return new List<string>();
        provider = ResolveProvider(settings, provider);

        using var timeout = CancellationTokenSource.CreateLinkedTokenSource(ct);
        timeout.CancelAfter(TimeSpan.FromSeconds(Math.Clamp(settings.RequestTimeoutSeconds, 10, 600)));
        var token = timeout.Token;

        try
        {
            return provider switch
            {
                "google" => await GoogleAsync(texts, sourceLanguage, targetLanguage, token).ConfigureAwait(false),
                "bing" => await BingAsync(texts, sourceLanguage, targetLanguage, token).ConfigureAwait(false),
                "custom" => await CustomAsync(settings, texts, sourceLanguage, targetLanguage, token).ConfigureAwait(false),
                _ => throw new MtException("未知的机翻提供方: " + provider)
            };
        }
        catch (OperationCanceledException) when (!ct.IsCancellationRequested)
        {
            throw new MtException("机翻请求超时（" + settings.RequestTimeoutSeconds + " 秒），请检查网络或更换机翻提供方。");
        }
    }

    public static string ResolveProvider(AppSettings settings, string? requested)
    {
        var provider = string.IsNullOrWhiteSpace(requested) ? settings.MtProvider : requested!;
        provider = (provider ?? "auto").Trim().ToLowerInvariant();
        if (provider == "auto")
            provider = string.IsNullOrWhiteSpace(settings.MtEndpoint) ? "google" : "custom";
        return provider;
    }

    private async Task<List<string>> GoogleAsync(
        IReadOnlyList<string> texts, string source, string target, CancellationToken ct)
    {
        var results = new List<string>(texts.Count);
        var sl = ToGoogleLang(source);
        var tl = ToGoogleLang(target);

        foreach (var text in texts)
        {
            if (string.IsNullOrWhiteSpace(text)) { results.Add(text); continue; }
            var url = "https://translate.googleapis.com/translate_a/single?client=gtx&dt=t" +
                      "&sl=" + Uri.EscapeDataString(sl) +
                      "&tl=" + Uri.EscapeDataString(tl) +
                      "&q=" + Uri.EscapeDataString(text);
            try
            {
                var body = await GetStringAsync(url, ct).ConfigureAwait(false);
                var node = JsonUtil.Parse(body) as JsonArray;
                var sb = new StringBuilder();
                if (node is not null && node.Count > 0 && node[0] is JsonArray segments)
                {
                    foreach (var seg in segments)
                    {
                        if (seg is JsonArray pair && pair.Count > 0 && pair[0] is JsonValue v &&
                            v.TryGetValue<string>(out var piece))
                            sb.Append(piece);
                    }
                }
                results.Add(sb.Length > 0 ? sb.ToString() : text);
            }
            catch (Exception ex) when (ex is not OperationCanceledException)
            {
                throw new MtException("Google 翻译失败（可能是网络无法访问 translate.googleapis.com）: " + ex.Message, ex);
            }
        }
        return results;
    }

    private async Task<List<string>> BingAsync(
        IReadOnlyList<string> texts, string source, string target, CancellationToken ct)
    {
        var results = new List<string>(texts.Count);
        var sl = ToGoogleLang(source);
        var tl = ToGoogleLang(target);
        foreach (var text in texts)
        {
            if (string.IsNullOrWhiteSpace(text)) { results.Add(text); continue; }
            var url = "https://api-edge.cognitive.microsofttranslator.com/translate" +
                      "?api-version=3.0&from=" + Uri.EscapeDataString(sl) +
                      "&to=" + Uri.EscapeDataString(tl);
            try
            {
                using var request = new HttpRequestMessage(HttpMethod.Post, url);
                request.Headers.TryAddWithoutValidation("User-Agent",
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36");
                request.Content = new StringContent(
                    new JsonArray { new JsonObject { ["Text"] = text } }.ToJsonString(JsonUtil.CompactOptions),
                    Encoding.UTF8, "application/json");
                using var response = await _http.SendAsync(request, ct).ConfigureAwait(false);
                var body = await response.Content.ReadAsStringAsync(ct).ConfigureAwait(false);
                if (!response.IsSuccessStatusCode)
                    throw new MtException($"Bing 返回 HTTP {(int)response.StatusCode}");
                var node = JsonUtil.Parse(body) as JsonArray;
                var translated = node?[0]?["translations"]?[0]?["text"]?.GetValue<string>();
                results.Add(string.IsNullOrEmpty(translated) ? text : translated);
            }
            catch (Exception ex) when (ex is not OperationCanceledException and not MtException)
            {
                throw new MtException("Bing 翻译失败: " + ex.Message, ex);
            }
        }
        return results;
    }

    private async Task<List<string>> CustomAsync(
        AppSettings settings, IReadOnlyList<string> texts, string source, string target, CancellationToken ct)
    {
        if (string.IsNullOrWhiteSpace(settings.MtEndpoint))
            throw new MtException("自定义机翻需要填写接口地址（设置 -> 机翻 -> 自定义接口）。");

        var payload = new JsonObject
        {
            ["texts"] = new JsonArray(texts.Select(t => (JsonNode?)JsonValue.Create(t)).ToArray()),
            ["source"] = source,
            ["target"] = target
        };

        using var request = new HttpRequestMessage(HttpMethod.Post, settings.MtEndpoint.Trim());
        if (!string.IsNullOrWhiteSpace(settings.MtApiKey))
            request.Headers.Authorization = new AuthenticationHeaderValue("Bearer", settings.MtApiKey);
        request.Content = new StringContent(payload.ToJsonString(JsonUtil.CompactOptions), Encoding.UTF8, "application/json");

        using var response = await _http.SendAsync(request, ct).ConfigureAwait(false);
        var body = await response.Content.ReadAsStringAsync(ct).ConfigureAwait(false);
        if (!response.IsSuccessStatusCode)
            throw new MtException($"自定义机翻接口返回 HTTP {(int)response.StatusCode}: {body[..Math.Min(body.Length, 200)]}");

        var node = JsonUtil.Parse(body);
        var arr = node?["translations"] as JsonArray ?? node as JsonArray;
        if (arr is null) throw new MtException("自定义机翻接口响应缺少 translations 数组");
        return arr.Select(n => n?.GetValue<string>() ?? "").ToList();
    }

    private async Task<string> GetStringAsync(string url, CancellationToken ct)
    {
        using var request = new HttpRequestMessage(HttpMethod.Get, url);
        request.Headers.TryAddWithoutValidation("User-Agent",
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36");
        using var response = await _http.SendAsync(request, ct).ConfigureAwait(false);
        if (!response.IsSuccessStatusCode)
            throw new MtException($"HTTP {(int)response.StatusCode}");
        return await response.Content.ReadAsStringAsync(ct).ConfigureAwait(false);
    }

    public async Task<(bool ok, string provider, string message)> TestAsync(
        AppSettings settings, string? provider, CancellationToken ct)
    {
        var resolved = ResolveProvider(settings, provider);
        try
        {
            var result = await TranslateAsync(settings, resolved, new[] { "Hello world" }, "en_us", "zh_cn", ct)
                .ConfigureAwait(false);
            return (true, resolved, "连接成功: Hello world -> " + result[0]);
        }
        catch (Exception ex)
        {
            return (false, resolved, ex.Message);
        }
    }
}
