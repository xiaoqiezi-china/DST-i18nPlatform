# DST MC I18n Tool — Web API 契约

所有接口返回 JSON（下载类除外）。错误统一返回 HTTP 4xx/5xx 且 body 为 { "error": "message" }。
服务只监听 127.0.0.1。

## 1. 基础

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | /api/health | { ok, name, version, pid } |
| GET | /api/settings | SettingsDto（apiKey 不回传明文，返回 llmApiKeySet:boolean） |
| POST | /api/settings | body = SettingsDto，保存并返回保存后的对象 |
| POST | /api/settings/test-llm | { ok, model, message } |
| POST | /api/settings/test-mt | { ok, provider, message } |

~~~ts
interface SettingsDto {
  llmBaseUrl: string;      // 默认 https://api.deepseek.com/v1
  llmApiKey: string;       // 提交时可填明文；返回时永远为空字符串
  llmApiKeySet: boolean;   // 只读
  llmModel: string;        // 默认 deepseek-chat
  llmTemperature: number;  // 0~2, 默认 0.3
  llmMaxTokens: number;    // 默认 4096
  mtProvider: string;      // auto | google | bing | custom
  mtEndpoint: string;
  mtApiKey: string;
  targetLanguage: string;  // 默认 zh_cn
  askUserEnabled: boolean; // 默认 true
  requestTimeoutSeconds: number;
  batchSize: number;       // 默认 24
  lastWorkspace: string;
}
~~~

## 2. 工作目录

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | /api/workspace?path= | 扫描并返回 WorkspaceInfo。path 省略时用当前已打开目录 |
| POST | /api/workspace/open | { path } 打开并扫描，返回 WorkspaceInfo |
| GET | /api/workspace/browse?path= | 网页内目录浏览器 |
| POST | /api/workspace/pick | 打开系统原生文件夹选择框，返回 { path: string|null } |
| POST | /api/workspace/refresh | 重新扫描当前目录 |

browse 返回：{ path, parent, isRoot, dirs:[{name,path,hasMods,hasKubejs,hasConfig,hasMinecraft}], drives:[{name,path}], suggestions:[{label,path,reason}] }

~~~ts
interface WorkspaceInfo {
  path: string; exists: boolean; name: string;
  modsDir: string|null; kubejsDir: string|null; configDir: string|null;
  gameVersion: string|null; loader: string|null;
  warnings: string[]; candidates: string[];
  mods: { found: boolean; root: string|null; warnings: string[]; mods: ModInfo[] };
  kubejs: { found: boolean; root: string|null; namespaces: string[]; warnings: string[]; files: KubejsLangFile[] };
  ftb: { found: boolean; root: string|null; languages: string[]; warnings: string[]; files: FtbLangFile[] };
  scannedAt: number;
}
interface ModInfo {
  id: string; fileName: string; name: string; modId: string; version: string;
  loader: string; path: string; isFolder: boolean; enabled: boolean;
  entryCount: number; translatedCount: number; error: string|null;
  langFiles: ModLangFile[];
}
interface ModLangFile { namespace: string; lang: string; archivePath: string; entryCount: number; hasZhCn: boolean; }
interface KubejsLangFile { namespace: string; lang: string; relativePath: string; entryCount: number; hasZhCn: boolean; }
interface FtbLangFile { lang: string; relativePath: string; entryCount: number; hasZhCn: boolean; }
~~~

## 3. 文档（键值表）

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | /api/document?kind=mod&file=<mod 文件名>&namespace=&lang= | mod 的 lang 文件 |
| GET | /api/document?kind=kubejs&path=<relativePath> | kubejs lang 文件 |
| GET | /api/document?kind=ftb&path=<relativePath> | ftbquests snbt 文件 |
| POST | /api/document | { kind, file?, namespace?, lang?, path? } 同上 |

~~~ts
interface LangDocument {
  id: string;            // 稳定 id，后续导出/翻译都用它
  kind: "mod"|"kubejs"|"ftb";
  ownerId: string;       // modId / namespace / "ftbquests"
  displayName: string;   // 例如 "Create 1.20.1"
  relativePath: string;
  format: "json"|"snbt";
  sourceLanguage: string;   // 默认 en_us
  targetLanguage: string;   // 默认 zh_cn
  entries: { key: string; value: string }[];
  existing: Record<string,string>;   // 已存在的中文翻译 key -> value（可能为空对象）
  hasExisting: boolean;
}
~~~

## 4. 翻译

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| POST | /api/translate | 启动任务，返回 { jobId } |
| GET | /api/translate/{jobId} | TranslationJob |
| POST | /api/translate/{jobId}/cancel | 取消 |
| POST | /api/translate/{jobId}/answer | { questionId, answer } |

请求体：

~~~ts
interface TranslateRequest {
  kind: "mod"|"kubejs"|"ftb";
  file?: string; namespace?: string; lang?: string; path?: string;
  mode: "llm"|"mt";
  provider?: string;
  entries?: { key: string; value: string }[];  // 省略则用文档全部条目
  glossary?: Record<string,string>;
  askUser?: boolean;
  skipExisting?: boolean;                      // 默认 true
}
~~~

~~~ts
interface TranslationJob {
  id: string; documentId: string; displayName: string;
  mode: string; provider: string|null;
  status: "queued"|"running"|"waiting_user"|"done"|"cancelled"|"error";
  total: number; done: number; failed: number; skipped: number;
  error: string|null;
  items: { key: string; source: string; target: string; status: "pending"|"running"|"done"|"error"|"skipped"; error: string|null }[];
  questions: { id: string; question: string; terms: string[]; options: string[]; status: "pending"|"answered"|"expired"; answer: string|null; askedAt: number }[];
  startedAt: number; finishedAt: number;
}
~~~

## 5. 导出

导出接口用 POST，返回文件下载（Content-Disposition: attachment）。

| 方法 | 路径 | body | 产物 |
| --- | --- | --- | --- |
| POST | /api/export/lang | ExportLangRequest | 单个 zh_cn.json |
| POST | /api/export/resourcepack | ExportPackRequest | 汉化资源包 zip |
| POST | /api/export/kubejs-overlay | ExportKubejsRequest | kubejs 覆盖包 zip |
| POST | /api/export/ftb-overlay | ExportFtbRequest | ftbquests 覆盖包 zip |
| POST | /api/export/write | 同上 + { overwrite:boolean } | 直接写回工作目录，返回 { written: string[] } |

~~~ts
interface ExportLangRequest {
  kind: "mod"|"kubejs"|"ftb";
  file?: string; namespace?: string; lang?: string; path?: string;
  entries: { key: string; value: string }[];
  targetLanguage?: string;
  fileName?: string;
}
interface ExportPackRequest {
  kind: "mod";
  file: string; namespace: string; lang?: string;
  entries: { key: string; value: string }[];
  targetLanguage?: string;
  packName?: string;          // 默认 汉化资源包
  packVersion?: string;       // 用户填写，例如 1.0.0
  mcVersion?: string;         // 1.16.5 | 1.17 | 1.18 | 1.19 | 1.20 | 1.21 ...
  packFormat?: number;        // 可选，直接指定 pack_format
  description?: string;
}
interface ExportKubejsRequest {
  files: { path: string; entries: { key: string; value: string }[] }[];
  targetLanguage?: string;
  packName?: string;
  packVersion?: string;
}
interface ExportFtbRequest {
  files: { path: string; entries: { key: string; value: string }[] }[];
  targetLanguage?: string;
  packName?: string;
  packVersion?: string;
}
~~~

## 6. 静态资源

GET / 返回内置的 index.html；/app.js、/style.css 等静态资源由同一端口提供。
前端全部打包进可执行文件（EmbeddedResource），因此发布产物只有 exe。
