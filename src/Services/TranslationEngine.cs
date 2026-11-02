using System.Collections.Concurrent;
using System.Text;
using System.Text.Json.Nodes;
using DstMcI18n.Core;

namespace DstMcI18n.Services;

/// <summary>
/// Runs batch translation for one document. LLM mode asks the model for strict JSON,
/// validates placeholders, and can pause to ask the user about domain terminology.
/// </summary>
public sealed class TranslationEngine
{
    private static readonly string Fence = new string((char)96, 3);

    private readonly LlmClient _llm;
    private readonly MachineTranslator _mt;
    private readonly ConcurrentDictionary<string, TranslationJob> _jobs = new(StringComparer.Ordinal);
    private readonly ConcurrentDictionary<string, CancellationTokenSource> _cancellations = new(StringComparer.Ordinal);
    private readonly ConcurrentDictionary<string, TaskCompletionSource<string>> _pendingAnswers = new(StringComparer.Ordinal);

    public TranslationEngine(LlmClient llm, MachineTranslator mt)
    {
        _llm = llm;
        _mt = mt;
    }

    public TranslationJob? Get(string id) => _jobs.TryGetValue(id, out var job) ? job : null;

    public void Cleanup(TimeSpan olderThan)
    {
        var cutoff = DateTimeOffset.UtcNow.Subtract(olderThan).ToUnixTimeMilliseconds();
        foreach (var kv in _jobs)
        {
            if (kv.Value.FinishedAt > 0 && kv.Value.FinishedAt < cutoff) _jobs.TryRemove(kv.Key, out _);
        }
    }

    public bool Cancel(string jobId)
    {
        if (_cancellations.TryGetValue(jobId, out var cts))
        {
            cts.Cancel();
            return true;
        }
        return false;
    }

    public bool Answer(string jobId, string questionId, string answer)
    {
        if (!_pendingAnswers.TryRemove(questionId, out var tcs)) return false;
        if (_jobs.TryGetValue(jobId, out var job))
        {
            lock (job)
            {
                var q = job.Questions.FirstOrDefault(x => x.Id == questionId);
                if (q is not null)
                {
                    q.Answer = answer;
                    q.Status = "answered";
                }
            }
        }
        tcs.TrySetResult(answer);
        return true;
    }

    public TranslationJob Start(TranslationRequest request, AppSettings settings)
    {
        var job = new TranslationJob
        {
            Id = Guid.NewGuid().ToString("N")[..12],
            DocumentId = request.DocumentId,
            DisplayName = request.DisplayName,
            Mode = request.Mode,
            Provider = request.Provider,
            StartedAt = DateTimeOffset.UtcNow.ToUnixTimeMilliseconds(),
            Status = "queued"
        };

        var existing = request.Existing ?? new Dictionary<string, string>();
        foreach (var entry in request.Entries)
        {
            var item = new TranslationItem { Key = entry.Key, Source = entry.Value, Status = "pending" };
            if (request.SkipExisting && !string.IsNullOrWhiteSpace(entry.Value) &&
                existing.TryGetValue(entry.Key, out var zh) && !string.IsNullOrWhiteSpace(zh))
            {
                item.Target = zh;
                item.Status = "skipped";
            }
            job.Items.Add(item);
        }

        job.Total = job.Items.Count;
        job.Skipped = job.Items.Count(i => i.Status == "skipped");
        job.Done = job.Skipped;
        _jobs[job.Id] = job;

        var cts = new CancellationTokenSource();
        _cancellations[job.Id] = cts;
        _ = Task.Run(() => RunAsync(job, request, settings, cts.Token));
        return job;
    }

    private async Task RunAsync(TranslationJob job, TranslationRequest request, AppSettings settings, CancellationToken ct)
    {
        try
        {
            job.Status = "running";
            var pending = job.Items.Where(i => i.Status == "pending").ToList();
            if (pending.Count == 0)
            {
                Finish(job, "done");
                return;
            }

            if (request.Mode == "mt")
                await RunMachineAsync(job, pending, request, settings, ct).ConfigureAwait(false);
            else
                await RunLlmAsync(job, pending, request, settings, ct).ConfigureAwait(false);

            Finish(job, ct.IsCancellationRequested ? "cancelled" : "done");
        }
        catch (OperationCanceledException)
        {
            Finish(job, "cancelled");
        }
        catch (Exception ex)
        {
            job.Error = ex.Message;
            Finish(job, "error");
        }
        finally
        {
            _cancellations.TryRemove(job.Id, out _);
            foreach (var item in job.Items.Where(i => i.Status == "running"))
                item.Status = "pending";
        }
    }

    private static void Finish(TranslationJob job, string status)
    {
        job.Status = status;
        job.FinishedAt = DateTimeOffset.UtcNow.ToUnixTimeMilliseconds();
        job.Done = job.Items.Count(i => i.Status is "done" or "skipped");
        job.Failed = job.Items.Count(i => i.Status == "error");
    }

    // ---------------- machine translation ----------------

    private async Task RunMachineAsync(
        TranslationJob job, List<TranslationItem> pending, TranslationRequest request,
        AppSettings settings, CancellationToken ct)
    {
        var provider = MachineTranslator.ResolveProvider(settings, request.Provider);
        job.Provider = provider;
        var chunkSize = Math.Clamp(request.BatchSize <= 0 ? 20 : request.BatchSize, 1, 50);

        foreach (var chunk in pending.Chunk(chunkSize).Select(c => c.ToList()))
        {
            ct.ThrowIfCancellationRequested();
            foreach (var item in chunk) item.Status = "running";

            var prepared = chunk.Select(item => Placeholders.Extract(item.Source)).ToList();
            try
            {
                var translated = await _mt.TranslateAsync(
                    settings, provider, prepared.Select(p => p.text).ToList(),
                    request.SourceLanguage, request.TargetLanguage, ct).ConfigureAwait(false);

                for (var i = 0; i < chunk.Count; i++)
                {
                    var item = chunk[i];
                    var raw = i < translated.Count ? translated[i] : "";
                    item.Target = Placeholders.Restore(raw, prepared[i].tokens);
                    item.Status = string.IsNullOrWhiteSpace(item.Target) ? "error" : "done";
                    if (item.Status == "error") item.Error = "机翻返回空结果";
                }
            }
            catch (Exception ex) when (ex is not OperationCanceledException)
            {
                foreach (var item in chunk)
                {
                    item.Status = "error";
                    item.Error = ex.Message;
                }
                job.Error ??= ex.Message;
                break;
            }

            job.Done = job.Items.Count(i => i.Status is "done" or "skipped");
            job.Failed = job.Items.Count(i => i.Status == "error");
        }
    }

    // ---------------- LLM translation ----------------

    private async Task RunLlmAsync(
        TranslationJob job, List<TranslationItem> pending, TranslationRequest request,
        AppSettings settings, CancellationToken ct)
    {
        var glossary = request.Glossary is null
            ? new Dictionary<string, string>()
            : new Dictionary<string, string>(request.Glossary);
        var batchSize = Math.Clamp(request.BatchSize <= 0 ? 20 : request.BatchSize, 4, 60);
        var context = request.Context ?? job.DisplayName;
        var askUser = request.AskUserEnabled && settings.AskUserEnabled;

        foreach (var chunk in pending.Chunk(batchSize).Select(c => c.ToList()))
        {
            ct.ThrowIfCancellationRequested();
            foreach (var item in chunk) item.Status = "running";

            var prepared = chunk.Select(item => (item, extracted: Placeholders.Extract(item.Source))).ToList();
            string reply;
            try
            {
                reply = await _llm.ChatAsync(
                    settings,
                    BuildSystemPrompt(request.SystemPrompt, request.TargetLanguage, glossary, askUser),
                    BuildUserPrompt(context, prepared.Select(p => (p.item.Key, p.extracted.text)).ToList()),
                    ct).ConfigureAwait(false);
            }
            catch (Exception ex) when (ex is not OperationCanceledException)
            {
                foreach (var item in chunk)
                {
                    item.Status = "error";
                    item.Error = ex.Message;
                }
                job.Error ??= ex.Message;
                break;
            }

            var (mapping, question) = ParseReply(reply, chunk);
            if (question is not null && askUser)
            {
                var asked = await AskUserAsync(job, question.Value, ct).ConfigureAwait(false);
                if (asked is not null)
                {
                    var term = question.Value.terms.FirstOrDefault();
                    if (!string.IsNullOrWhiteSpace(term)) glossary[term!] = asked;
                    try
                    {
                        reply = await _llm.ChatAsync(
                            settings,
                            BuildSystemPrompt(request.SystemPrompt, request.TargetLanguage, glossary, askUser),
                            BuildUserPrompt(context, prepared.Select(p => (p.item.Key, p.extracted.text)).ToList()),
                            ct).ConfigureAwait(false);
                        (mapping, question) = ParseReply(reply, chunk);
                    }
                    catch (Exception ex) when (ex is not OperationCanceledException)
                    {
                        foreach (var item in chunk)
                        {
                            item.Status = "error";
                            item.Error = ex.Message;
                        }
                        job.Error ??= ex.Message;
                        break;
                    }
                }
            }

            for (var i = 0; i < chunk.Count; i++)
            {
                var item = chunk[i];
                if (mapping.TryGetValue(i, out var translated) && !string.IsNullOrWhiteSpace(translated))
                {
                    item.Target = Placeholders.Restore(translated, prepared[i].extracted.tokens);
                    item.Status = "done";
                }
                else
                {
                    item.Status = "error";
                    item.Error = "模型未返回该条目的译文";
                }
            }

            job.Done = job.Items.Count(i => i.Status is "done" or "skipped");
            job.Failed = job.Items.Count(i => i.Status == "error");
        }
    }

    private async Task<string?> AskUserAsync(
        TranslationJob job, (string question, List<string> terms, List<string> options) question, CancellationToken ct)
    {
        var q = new TranslationQuestion
        {
            Id = Guid.NewGuid().ToString("N")[..10],
            Question = question.question,
            Terms = question.terms,
            Options = question.options,
            Status = "pending",
            AskedAt = DateTimeOffset.UtcNow.ToUnixTimeMilliseconds()
        };

        lock (job)
        {
            job.Questions.Add(q);
            job.Status = "waiting_user";
        }

        var tcs = new TaskCompletionSource<string>(TaskCreationOptions.RunContinuationsAsynchronously);
        _pendingAnswers[q.Id] = tcs;

        using var timeout = CancellationTokenSource.CreateLinkedTokenSource(ct);
        timeout.CancelAfter(TimeSpan.FromMinutes(5));
        try
        {
            using var registration = timeout.Token.Register(() => tcs.TrySetCanceled());
            var answer = await tcs.Task.ConfigureAwait(false);
            lock (job) job.Status = "running";
            return answer;
        }
        catch (OperationCanceledException)
        {
            lock (job)
            {
                q.Status = "expired";
                job.Status = "running";
            }
            _pendingAnswers.TryRemove(q.Id, out _);
            ct.ThrowIfCancellationRequested();
            return null;
        }
    }

    private static string BuildSystemPrompt(string? userPrompt, string targetLanguage, Dictionary<string, string> glossary, bool askUser)
        => PromptDefaults.Render(userPrompt, targetLanguage, glossary, askUser);

    private static string BuildUserPrompt(string context, List<(string key, string text)> items)
    {
        var payload = new JsonObject();
        foreach (var (key, text) in items) payload[key] = text;
        var sb = new StringBuilder();
        sb.AppendLine("Document: " + context);
        sb.AppendLine("Translate the values of this JSON object:");
        sb.AppendLine(payload.ToJsonString(JsonUtil.OutOptions));
        return sb.ToString();
    }

    private static (Dictionary<int, string> mapping, (string question, List<string> terms, List<string> options)? question)
        ParseReply(string reply, List<TranslationItem> chunk)
    {
        var mapping = new Dictionary<int, string>();
        (string, List<string>, List<string>)? question = null;
        var json = ExtractJsonObject(reply);
        if (json is null) return (mapping, question);

        JsonObject? obj;
        try
        {
            obj = JsonUtil.Parse(json) as JsonObject;
        }
        catch
        {
            return (mapping, question);
        }
        if (obj is null) return (mapping, question);

        if (obj["__ask__"] is JsonObject ask)
        {
            var q = ask["question"]?.GetValue<string>() ?? "请确认以下术语的译法";
            var terms = new List<string>();
            if (ask["terms"] is JsonArray ta)
                terms.AddRange(ta.Select(t => t?.GetValue<string>() ?? "").Where(s => s.Length > 0));
            var options = new List<string>();
            if (ask["options"] is JsonArray oa)
                options.AddRange(oa.Select(t => t?.GetValue<string>() ?? "").Where(s => s.Length > 0));
            question = (q, terms, options);
            return (mapping, question);
        }

        for (var i = 0; i < chunk.Count; i++)
        {
            var item = chunk[i];
            if (obj.TryGetPropertyValue(item.Key, out var node) && node is JsonValue value &&
                value.TryGetValue<string>(out var text))
            {
                mapping[i] = text;
                continue;
            }
            foreach (var kv in obj)
            {
                if (kv.Key.EndsWith(item.Key, StringComparison.Ordinal) && kv.Value is JsonValue v2 &&
                    v2.TryGetValue<string>(out var t2))
                {
                    mapping[i] = t2;
                    break;
                }
            }
        }
        return (mapping, question);
    }

    private static string? ExtractJsonObject(string reply)
    {
        if (string.IsNullOrWhiteSpace(reply)) return null;
        var text = reply.Trim();
        if (text.StartsWith(Fence, StringComparison.Ordinal))
        {
            var firstNewline = text.IndexOf('\n');
            if (firstNewline > 0) text = text[(firstNewline + 1)..];
            var lastFence = text.LastIndexOf(Fence, StringComparison.Ordinal);
            if (lastFence >= 0) text = text[..lastFence];
            text = text.Trim();
        }
        var start = text.IndexOf('{');
        var end = text.LastIndexOf('}');
        if (start < 0 || end <= start) return null;
        return text[start..(end + 1)];
    }
}