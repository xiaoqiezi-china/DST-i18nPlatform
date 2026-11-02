
# 本轮改动契约（v1.1）

## A. 异步扫描（新增接口）

POST /api/workspace/scan        body { path: string }
  200 -> { scanId, path, name, modsDir, kubejsDir, configDir, candidates: string[] }
  400 -> { error: string, valid: false, candidates: string[] }   // 目录不合法
  409 -> { error: "正在扫描中", scanId }                          // 已有扫描在跑

GET /api/workspace/scan/{scanId} -> ScanProgress
  {
    scanId, path, name,
    status: "running" | "done" | "cancelled" | "error",
    phase: "prepare" | "mods" | "kubejs" | "ftb" | "finish",
    phaseLabel: string,          // 中文，例如 "正在解析模组 (3/57)"
    current: number, total: number, percent: number,
    currentItem: string,         // 当前处理的文件名
    modsFound: number,
    elapsedMs: number,
    error: string | null,
    warnings: string[]
  }

POST /api/workspace/scan/{scanId}/cancel -> { ok: boolean }

GET /api/workspace -> 原有 WorkspaceInfo，额外增加：
  scanning: boolean, scanId: string | null

POST /api/workspace/open 仍然可用（同步扫描，兼容旧调用）。

## B. 设置（新增字段）

SettingsDto 增加：
  llmModel              默认 "deepseek-v4-pro"
  llmSystemPrompt       用户自定义提示词；空字符串表示使用默认
  llmSystemPromptDefault 只读，默认提示词全文
  suggestedModels       ["deepseek-v4-pro", "deepseek-flash"]

POST /api/settings/reset-prompt -> 把 llmSystemPrompt 置空并返回 SettingsDto

## C. 导出（覆盖包不再需要版本号）

POST /api/export/kubejs-overlay
  body { files?: [{ path, entries }], targetLanguage? }
  - files 省略或为空 -> 导出全部 kubejs 语言文件（用磁盘上已有 zh_cn 作为底稿）
  - 产物 zip 内路径：kubejs/<相对路径（语言名换成 zh_cn）>
  - 文件名：<整合包名>-kubejs-zh_cn.zip

POST /api/export/ftb-overlay
  body { files?: [{ path, entries }], targetLanguage? }
  - files 省略或为空 -> 导出全部 ftbquests lang 文件
  - 产物 zip 内路径：config/ftbquests/<相对路径>
  - 文件名：<整合包名>-ftbquests-zh_cn.zip

请求体不再包含 packName / packVersion；后端忽略这两个字段（兼容旧前端）。
