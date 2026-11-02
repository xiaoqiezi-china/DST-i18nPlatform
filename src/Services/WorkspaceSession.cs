using System.Collections.Concurrent;
using DstMcI18n.Core;

namespace DstMcI18n.Services;

/// <summary>Holds the currently opened Minecraft instance, its last scan and any running scan.</summary>
public sealed class WorkspaceSession
{
    private readonly object _gate = new();
    private readonly WorkspaceScanner _scanner = new();
    private WorkspaceInfo? _info;

    private readonly ConcurrentDictionary<string, ScanState> _scans = new(StringComparer.Ordinal);
    private string? _activeScanId;

    public string? CurrentPath { get; private set; }

    public WorkspaceInfo? Current
    {
        get { lock (_gate) return _info; }
    }

    /// <summary>The scan currently running, if any.</summary>
    public string? ActiveScanId
    {
        get { lock (_gate) return _activeScanId; }
    }

    public bool IsScanning => ActiveScanId is not null;

    public WorkspaceInfo Open(string path)
    {
        path = Normalize(path);
        var info = _scanner.Scan(path);
        lock (_gate)
        {
            CurrentPath = info.Exists ? path : CurrentPath;
            _info = info;
            return info;
        }
    }

    public WorkspaceInfo Refresh()
    {
        var path = CurrentPath ?? throw new InvalidOperationException("尚未打开任何工作目录");
        return Open(path);
    }

    public WorkspaceInfo Require()
    {
        lock (_gate)
        {
            if (_info is null) throw new InvalidOperationException("尚未打开任何工作目录");
            return _info;
        }
    }

    // ---------------- asynchronous scan ----------------

    public sealed class ScanState
    {
        public ScanProgress Progress { get; } = new();
        public CancellationTokenSource Cancellation { get; } = new();
        public Task? Worker { get; set; }
    }

    public ScanState StartScan(string rawPath)
    {
        var path = Normalize(rawPath);
        if (!Directory.Exists(path))
            throw new DirectoryNotFoundException("目录不存在: " + path);

        var (valid, candidates) = WorkspaceScanner.Validate(path);
        if (!valid)
        {
            var hint = candidates.Count > 0
                ? "该目录下没有 mods/kubejs/config，可能选到了启动器根目录。请选择具体版本隔离目录。"
                : "该目录不是有效的 Minecraft / 整合包目录（未找到 mods、kubejs 或 config）。";
            var ex = new InvalidOperationException(hint);
            ex.Data["candidates"] = candidates;
            throw ex;
        }

        ScanState state;
        lock (_gate)
        {
            if (_activeScanId is not null && _scans.TryGetValue(_activeScanId, out var running) &&
                running.Progress.Status == "running")
                throw new InvalidOperationException("已有扫描正在进行，请先取消后再重新选择目录。");

            var id = Guid.NewGuid().ToString("N")[..12];
            state = new ScanState();
            state.Progress.ScanId = id;
            state.Progress.Path = path;
            state.Progress.Name = new DirectoryInfo(path.TrimEnd('\\', '/')).Name;
            state.Progress.Status = "running";
            state.Progress.Phase = "prepare";
            state.Progress.PhaseLabel = "正在准备…";
            _scans[id] = state;
            _activeScanId = id;
        }

        var startedAt = DateTimeOffset.UtcNow;
        state.Worker = Task.Run(() =>
        {
            var progress = state.Progress;
            try
            {
                var info = _scanner.Scan(path, new ScanReporter(p =>
                {
                    progress.Phase = p.Phase;
                    progress.PhaseLabel = p.PhaseLabel;
                    progress.Current = p.Current;
                    progress.Total = p.Total;
                    progress.Percent = p.Percent;
                    progress.CurrentItem = p.CurrentItem;
                    progress.ModsFound = p.ModsFound;
                    progress.ElapsedMs = (long)(DateTimeOffset.UtcNow - startedAt).TotalMilliseconds;
                }, state.Cancellation.Token));

                lock (_gate)
                {
                    CurrentPath = path;
                    _info = info;
                }
                progress.Warnings.Clear();
                progress.Warnings.AddRange(info.Warnings);
                progress.Phase = "finish";
                progress.PhaseLabel = "扫描完成";
                progress.Percent = 100;
                progress.Current = progress.Total = Math.Max(progress.Total, 1);
                progress.Status = "done";
            }
            catch (OperationCanceledException)
            {
                progress.Status = "cancelled";
                progress.PhaseLabel = "已取消";
            }
            catch (Exception ex)
            {
                progress.Status = "error";
                progress.Error = ex.Message;
                progress.PhaseLabel = "扫描失败";
            }
            finally
            {
                progress.ElapsedMs = (long)(DateTimeOffset.UtcNow - startedAt).TotalMilliseconds;
                lock (_gate)
                {
                    if (_activeScanId == progress.ScanId) _activeScanId = null;
                }
                // keep the finished state around briefly for the UI to read
                _ = Task.Delay(TimeSpan.FromMinutes(10)).ContinueWith(_ =>
                {
                    _scans.TryRemove(progress.ScanId, out var _);
                });
            }
        });

        return state;
    }

    public ScanProgress? GetScan(string scanId)
        => _scans.TryGetValue(scanId, out var state) ? state.Progress : null;

    public bool CancelScan(string scanId)
    {
        if (!_scans.TryGetValue(scanId, out var state)) return false;
        if (state.Progress.Status != "running") return false;
        state.Cancellation.Cancel();
        return true;
    }

    public static string Normalize(string path)
    {
        if (string.IsNullOrWhiteSpace(path)) throw new ArgumentException("路径不能为空");
        var p = path.Trim().Trim('"');
        if (p.Length == 0) throw new ArgumentException("路径不能为空");
        p = Path.GetFullPath(p);
        return p.TrimEnd(Path.DirectorySeparatorChar, Path.AltDirectorySeparatorChar);
    }
}
