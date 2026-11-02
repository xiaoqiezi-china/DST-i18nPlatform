using System.Net.Http.Headers;
using System.Text;
using System.Text.Json;
using System.Text.Json.Nodes;
using DstMcI18n.Core;

namespace DstMcI18n.Services;

public sealed class LlmException : Exception
{
    public LlmException(string message, Exception? inner = null) : base(message, inner) { }
}

/// <summary>Minimal OpenAI-compatible chat-completions client (works with DeepSeek out of the box).</summary>
public sealed class LlmClient
{
    private readonly HttpClient _http;

    public LlmClient(HttpClient http)
    {
        _http = http;
    }

    public static string NormalizeBaseUrl(string baseUrl)
    {
        var url = string.IsNullOrWhiteSpace(baseUrl) ? "https://api.deepseek.com/v1" : baseUrl.Trim();
        url = url.TrimEnd('/');
        if (!url.EndsWith("/chat/completions", StringComparison.OrdinalIgnoreCase))
            url += "/chat/completions";
        return url;
    }

    public async Task<string> ChatAsync(AppSettings settings, string systemPrompt, string userPrompt, CancellationToken ct)
    {
        if (string.IsNullOrWhiteSpace(settings.LlmApiKey))
            throw new LlmException("尚未配置 LLM API Key，请在“设置”中填写 DeepSeek API Key。");

        var endpoint = NormalizeBaseUrl(settings.LlmBaseUrl);
        var payload = new JsonObject
        {
            ["model"] = settings.LlmModel,
            ["temperature"] = settings.LlmTemperature,
            ["max_tokens"] = settings.LlmMaxTokens,
            ["stream"] = false,
            ["messages"] = new JsonArray
            {
                new JsonObject { ["role"] = "system", ["content"] = systemPrompt },
                new JsonObject { ["role"] = "user", ["content"] = userPrompt }
            }
        };

        using var request = new HttpRequestMessage(HttpMethod.Post, endpoint);
        request.Headers.Authorization = new AuthenticationHeaderValue("Bearer", settings.LlmApiKey);
        request.Content = new StringContent(payload.ToJsonString(JsonUtil.CompactOptions), Encoding.UTF8, "application/json");

        using var timeoutCts = CancellationTokenSource.CreateLinkedTokenSource(ct);
        timeoutCts.CancelAfter(TimeSpan.FromSeconds(Math.Clamp(settings.RequestTimeoutSeconds, 10, 600)));

        HttpResponseMessage response;
        try
        {
            response = await _http.SendAsync(request, timeoutCts.Token).ConfigureAwait(false);
        }
        catch (OperationCanceledException) when (!ct.IsCancellationRequested)
        {
            throw new LlmException("LLM 请求超时（" + settings.RequestTimeoutSeconds + " 秒）。");
        }
        catch (HttpRequestException ex)
        {
            throw new LlmException("无法连接 LLM 服务: " + ex.Message, ex);
        }

        var body = await response.Content.ReadAsStringAsync(timeoutCts.Token).ConfigureAwait(false);
        if (!response.IsSuccessStatusCode)
            throw new LlmException($"LLM 返回 HTTP {(int)response.StatusCode}: {Truncate(body, 500)}");

        try
        {
            var node = JsonUtil.Parse(body) as JsonObject;
            var content = node?["choices"]?[0]?["message"]?["content"]?.GetValue<string>();
            if (content is null)
                throw new LlmException("LLM 响应缺少 choices[0].message.content");
            return content;
        }
        catch (JsonException ex)
        {
            throw new LlmException("无法解析 LLM 响应: " + Truncate(body, 300), ex);
        }
    }

    public async Task<(bool ok, string message)> TestAsync(AppSettings settings, CancellationToken ct)
    {
        try
        {
            var reply = await ChatAsync(settings, "You are a connectivity probe.", "Reply with the single word: ok", ct)
                .ConfigureAwait(false);
            return (true, "连接成功，模型 " + settings.LlmModel + " 回复: " + Truncate(reply.Trim(), 80));
        }
        catch (Exception ex)
        {
            return (false, ex.Message);
        }
    }

    private static string Truncate(string value, int max)
        => value.Length <= max ? value : value[..max] + "...";
}
