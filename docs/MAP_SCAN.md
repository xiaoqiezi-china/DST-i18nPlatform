# 地图文本解析引擎（mapscan）

用于扫描一个 Minecraft 世界存档 / 地图目录，列出所有可能需要汉化的文本。
引擎已接入主程序：`地图本地化` 页面可以选择目录、后台扫描、逐条编辑译文，
并把候选条目直接送进现有的 LLM / 机翻任务链。

## 快速使用

```powershell
python python\map_scan.py "D:\path\to\world" --json map-report.json
```

参数：

| 参数 | 说明 |
| --- | --- |
| `map` | 地图/存档目录，通常包含 `level.dat`、`dimensions/**/region/`（新版）或根目录 `region/`、`datapacks/` |
| `--json <file>` | 输出完整 JSON 报告 |
| `--source lang,function,sign` | 只看指定来源 |
| `--no-world` | 跳过区块、`level.dat`、`data/` 和 `players/` 下的 `.dat` / `.nbt` |
| `--no-packs` | 跳过 `datapacks/`、`resources.zip`、`resourcepacks/` |
| `--examples N` | 每种来源显示多少条示例 |

## 已覆盖的数据源

| 来源 | 位置 | 说明 |
| --- | --- | --- |
| `lang` | `datapacks/**/{assets,data}/<ns>/lang/<lang>.json`、`resources.zip`、`resourcepacks/` | 标准语言键值；优先 `en_us` 作为源语言，已有 `zh_cn` 时标记 `translated` |
| `function` | `data/<ns>/function(s)/**/*.mcfunction` | 每条命令交给 `CommandParser`，支持函数、宏行、`execute ... run` 递归；兼容 `pack_format_*/data/` 多版本叠加目录 |
| `sign` | 区块 `block_entities` / 结构 NBT | `front_text.messages`、`back_text.messages`、旧版 `Text1..Text4` |
| `command_block` | 区块 `block_entities`、结构 NBT | `Command` 字段，命令内部文本再交给命令解析器 |
| `entity` | 区块 `entities`、实体区域文件、玩家/存档 `.dat` | `CustomName` 等文本组件，JSON 或普通字符串 |
| `text_display` | 实体区域中的 `minecraft:text_display` | `text` 字段，支持 JSON 字符串、JSON 组件对象和组件数组 |
| `item` | 实体物品、箱子/物品展示框、结构 NBT、玩家 `.dat` | `display.Name`、`display.Lore`、`components.minecraft:custom_name` / `item_name` / `lore`；值可以是 JSON、SNBT `{text:...}` 或普通字符串 |
| `book` | 成书 / 书与笔 | 旧版 `pages`、`written_book_content` / `writable_book_content` 的 `title` / `pages[].raw` |
| `structure` | `data/**/*.nbt` | 结构里的告示牌、命令方块、实体、物品名 |
| `nbt` | `data/**/*.dat`、`players/**/*.dat` | 存档数据里的零散文本，按路径自动归类为 `item` / `entity` / `sign` 等 |
| `bossbar` | `level.dat` 的 `CustomBossEvents` | Boss 栏名称 |

## 命令解析器

`CommandParser` 与函数扫描器、命令方块扫描器解耦，输入一条命令字符串，输出文本候选。
当前重点处理：

- `tellraw`、`title`（title / subtitle / actionbar）
- `say`、`me`、`msg` / `tell` / `w`、`teammsg`、`kick` 的文本
- `summon`、`give`、`item`、`setblock`、`fill`、`data merge/modify` 里的 NBT
- `execute ... run <command>` 递归
- `bossbar add/set ... name`、`team add/modify ... displayName`、`scoreboard teams/objectives add/modify ...` 的显示名

NBT 文本提取规则在 `nbt_text.py`，命令解析器只负责命令语法；两边通过 `TextPart` 传递。

## 全量 JSON / SNBT 扫描

真实地图里的文本并不总是放在固定字段里。当前扫描器采用“结构兜底 + 全量疑似 JSON”两层策略：

1. **已知纯文本字段**：告示牌 `front_text.messages`、实体 `CustomName`、物品 `display.Name/Lore`、书页 `pages`、物品组件 `custom_name/item_name/lore` 等，普通字符串也直接收集。
2. **全量疑似 JSON**：任意 NBT 字符串只要以 `{`、`[` 或 `"` 开头，就尝试按 Minecraft JSON 文本组件解析；找到 `text`、`translate`、`fallback`、`extra`、`with` 键就收集。
3. **嵌套结构**：JSON 或 SNBT 里的任意嵌套对象/列表也会继续寻找文本组件，例如 `{"foo":{"text":"bar"}}`、`{components:{"minecraft:custom_name":{text:"..."}}}`。
4. **普通字符串兜底**：只对明确的显示字段（`CustomName`、`messages`、`pages[].raw`、`display.Name/Lore`、物品组件名等）收集普通字符串，避免把 `id`、`color`、`type` 之类的元数据当成可翻译文本。
5. **技术字段与图标过滤**：跳过文本组件里的 `nbt` / `storage` / `selector` / `score` / `font` / `click_event` 等数据路径和交互字段；过滤纯标点、控制字符、占位符（`%s`、`%2$s`、`$(icon)`）以及单字符字体图标。`translate` 键会跨出现位置去重。

这样可以覆盖地图作者常见的三种写法：

- 标准 JSON：`tellraw @a {"text":"你好"}`
- 命令内 SNBT：`{CustomName:'"名字"', components:{"minecraft:custom_name":{text:"物品名"}}}`
- 直接普通字符串：告示牌 `front_text.messages:["第一行"]`、物品组件 `custom_name:"物品名"`
- 文本展示实体：`minecraft:text_display` 的 `text` 字段是 JSON 字符串（也可能是组件对象/数组），按 `text_display` 单独归类

注意：命令块和函数仍然先由 `CommandParser` 识别命令语法，NBT 参数再交给同一套 NBT 文本扫描器。

## 输出结构

`map_scan.py --json` 的报告结构：

```json
{
  "root": "D:\\map",
  "stats": { "lang": 10, "function": 3, "sign": 2, "total": 15, "translated": 1, "untranslated": 14 },
  "languageFiles": [
    { "pack": "datapacks/demo", "path": "datapacks/demo::assets/demo/lang/en_us.json", "namespace": "demo", "language": "en_us", "entryCount": 2, "translatedCount": 1 }
  ],
  "packs": ["datapacks/demo", "datapacks/zipped.zip"],
  "warnings": [],
  "candidates": [
    {
      "id": "3f2a...",
      "text": "Ruby",
      "kind": "lang",
      "source": "lang",
      "file": "datapacks/demo::assets/demo/lang/en_us.json",
      "line": null,
      "nbtPath": "item.demo.ruby",
      "key": "item.demo.ruby",
      "namespace": "demo",
      "language": "en_us",
      "existing": null,
      "translated": false,
      "command": null,
      "owner": null,
      "raw": null
    }
  ]
}
```

`source` 用于界面分组，`kind` 区分 `literal` / `translate` / `lang` / `fallback`。
`nbtPath` 保留区块/结构内的具体位置，方便后续写回或定位。

## 主程序集成

### 网页工作区

侧边栏 `地图本地化` 提供完整的可视化流程：

- `选择地图目录` 直接打开 Windows 原生文件夹选择框；确认目录后立即后台扫描。
- 扫描完成后按 `路径 / 文件名 / 来源 / 键或 text / 原文 / 译文 / 状态` 展示每条候选。
- 路径只显示 20 个字符（超长时保留首尾），完整路径放在 `title` 悬浮提示；单击路径会把文件定位到资源管理器（压缩包内文件会先解析到临时目录）。
- 支持来源筛选、仅看未翻译、全文搜索、全选筛选结果、复制选中条目或全部译文。
- 译文可直接在表格里编辑；LLM / 机翻任务的结果先暂存，点 `应用结果` 后写入地图文档，可丢弃或还原。

### 地图翻译 API

地图翻译复用 `/api/translate`，通过 `kind="map"` 直接传条目，不经过 `documents.load`：

```json
{
  "kind": "map",
  "documentId": "map|0853c0bcf29f",
  "displayName": "Fetchr-5.3.3",
  "sourceLanguage": "en_us",
  "targetLanguage": "zh_cn",
  "entries": [{ "key": "候选 id", "value": "原文" }],
  "existing": { "候选 id": "已有译文" },
  "concurrency": 4,
  "batchSize": 24,
  "askUser": true
}
```

`concurrency` 控制同时在跑的批次数量（1–8），`translate.py` 使用
`ThreadPoolExecutor` 处理批次；每个批次仍独立处理占位符保护和 `__ask__`
术语询问，多个批次可同时产生待回答问题。

### 扫描 API

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| `POST` | `/api/map/scan` | body `{path, kind, includeWorld, includePacks}`，启动后台扫描；`kind` 可为 `map` 或 `resourcepack` |
| `GET` | `/api/map/scan/{id}` | 查询进度（阶段、百分比、文件数、候选数） |
| `GET` | `/api/map/scan/{id}/result` | 获取完整结果和统计 |
| `POST` | `/api/map/scan/{id}/cancel` | 取消扫描 |
| `POST` | `/api/map/reveal` | body `{scanId, file}`，解析压缩包内路径并在资源管理器中定位 |

扫描结果保存在内存中（最多保留最近 10 个任务），应用重启后需要重新扫描；
`lastMap` 会记在设置里，下一次打开系统文件夹选择框时从上次的地图目录开始。

### 材质包模式

“材质包汉化”与地图共用同一个表格、筛选和翻译任务链，区别只在扫描入口：

- 可以选材质包 `.zip`，也可以选已解压的材质包文件夹；
- 只读取 MC 标准资源包路径 `assets/<namespace>/lang/<locale>.json`；
- 每个命名空间优先用 `en_us` / `en_gb` 作为源语言，已有 `zh_cn` 键值会作为“已有译文”回填；
- 来源列显示为“材质包”，条目仍使用语言键，可直接复用 `/api/translate` 的高并发批次和术语询问；
- `pack_format_from_*` 多版本目录作为回退，根 `assets/` 始终优先。

### 原生目录与文件选择

`POST /api/workspace/pick` 接收 `{initial, description}`，由后端启动
Windows PowerShell 的 `System.Windows.Forms.FolderBrowserDialog`，返回用户选择的绝对路径。
取消时返回 `{path: null, cancelled: true}`；同一时间只允许打开一个选择框。

`POST /api/workspace/pick-file` 使用 `System.Windows.Forms.OpenFileDialog`，用于选择材质包 `.zip`；
同样返回绝对路径，并和目录选择共用同一个互斥锁。

前端所有“选择目录 / 选择地图目录 / 选择材质包”入口都走系统原生选择框，不再维护自己的逐级目录浏览器。

## 已知边界

- 暂不支持 LZ4 压缩区块、部分外部 `.mcc` 区块；遇到会写入 `warnings`。
- NBT 采用只读解析，暂不写回；后续做翻译写回时需要补 NBT 序列化。
- 命令解析器只提取文本，不执行命令语义；未知命令里的普通文本不会误收。
- 全量 JSON 扫描按 `text` / `translate` 等键判断，仍可能把某些非显示用途的 JSON 字段当成文本，后续可在界面里人工筛选。
- 文本组件按叶子节点输出（例如 `extra` 会拆成多条），后续可在翻译层合并为整句。
- 数据包语言文件按包内命名空间配对 `zh_cn`，跨多个资源包的同名键暂不合并。
- 仅支持 Java 版存档；基岩版 `level.dat` / `db` 结构不在范围内。

## 真实存档校准

已用一张 1.26.3 Fabric 测试存档跑通，观察到：

- 新版世界布局是 `dimensions/minecraft/overworld/{region,entities,data}`，不是根目录 `region/`；扫描器按 `.mca` 递归查找，已覆盖。
- `datapacks/` 为空时没有 `lang` / `function` 候选，属于正常。
- 告示牌 `front_text.messages` 是普通字符串数组，不是 JSON。
- 物品 `components.minecraft:custom_name` / `minecraft:lore` 既有普通字符串，也有 SNBT `{text:"..."}`。
- 成书和书与笔都存在，页数据在 `written_book_content` / `writable_book_content` 的 `pages[].raw`。
- 玩家 `players/data/*.dat` 里也有物品名称/描述，已纳入扫描。
- 命令方块里同时出现 `tellraw` JSON、`title` JSON、`summon` 的 SNBT 组件；统一由命令解析器和 NBT 扫描器处理。
- 当前这份存档的实体区没有 `minecraft:text_display`；解析器已支持它的 `text` 字段（JSON 字符串/组件对象/组件数组），放置文本展示实体后保存世界再扫描即可看到 `text_display` 来源。

## 国外地图校准：Fetchr

用 GitHub 开源地图 **Fetchr 5.3.3**（`NeunEinser/bingo`）做了真实地图验证：

- world 包：`https://github.com/NeunEinser/bingo/releases/download/5.3.3/Fetchr-5.3.3.zip`（约 1.05 MB）
- 布局：`level.dat`、空的根 `region/`、`datapacks/Fetchr.zip`、`resources.zip`
- 语言资源：195 个 `assets/**/lang/*.json`，其中 `assets/fetchr/lang/en_us.json` 是主要源语言
- 函数资源：`Fetchr.zip` 内 1031 个 `.mcfunction`，同时存在根 `data/` 和 `pack_format_from_*/data/` 多版本目录
- 结构资源：11 个 `data/*/structure/*.nbt`，包含告示牌、成书、物品展示框等

第一次只扫根 `data/` 时得到 646 条（function 仅 6 条），漏掉了多版本目录里的函数。修复后的最终扫描结果：

| 来源 | 数量 | 说明 |
| --- | ---: | --- |
| `lang` | 635 | 195 个语言文件；576 条已有 `zh_cn` 翻译，59 条待翻译 |
| `function` | 441 | 来自 96 个函数文件；389 条 `translate`、34 条 literal、18 条 fallback |
| `structure` | 475 | 来自 11 个结构文件；297 条 `translate`、178 条 literal；含 57 条告示牌、86 条成书文本 |
| 合计 | 1551 | `warnings` 为 0 |

这次验证推动了三处修正：

1. **多版本数据包路径归一化**：`pack_format_from_45_to_88.0/data/fetchr/function/...` 统一按 `data/` 逻辑路径扫描，避免漏掉版本叠加目录。
2. **SNBT 字符串继续解析**：`data modify ... set value '{ translate: "..." }'` 这类字符串不再作为整段原文输出，而是继续拆出 `translate` / `text` / `fallback`。
3. **技术字段与 UI 噪声过滤**：忽略 `nbt`、`storage`、`selector`、`score`、`font`、`click_event` 等字段，过滤 `%s`、`$(icon)`、控制字符、纯标点和单字符字体图标；同一 `translate` 键跨位置去重。

补充说明：

- Fetchr 的 `text_display` 只作为技术载具被 `summon`，没有实际显示文本；`text` 字段的 JSON 字符串、组件对象、组件数组三种形式已用直接 NBT 结构验证通过。
- Fetchr 的根 `region/` 为空，因此没有真实区块里的告示牌/命令方块/实体候选；这部分由之前的 Fabric 测试存档覆盖。
- 单字符过滤会牺牲极少数“单字母按钮”类文本，但能去掉大量自定义字体图标码；后续可在界面里提供“显示低置信度候选”开关。

后续拿到带数据包的地图时，优先校准：

1. 数据包实际目录/压缩包布局，以及 `assets` / `data` 下的 `lang` 路径；
2. 函数命令种类（是否大量 `data modify` / `item` / `execute`）；
3. 地图是否附带 `resources.zip` 或独立资源包；
4. 告示牌/书与笔/命名牌等是否需要按整句合并。

