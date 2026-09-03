# FDA OpenRecords Search

[English](README.md) | **简体中文**

**让散落在 PDF 和扫描件里的 FDA 公开记录真正可搜索。**

FDA OpenRecords Search 是一个面向 [FDA Office of Inspections and
Investigations (OII) Electronic
Reading Room](https://www.fda.gov/about-fda/office-inspections-and-investigations/oii-foia-electronic-reading-room)
公开记录的本地全文搜索工具。

项目从 FDA 官方页面发现可下载的 PDF，将元数据和正文写入 SQLite FTS5，
并提供轻量级浏览器界面。扫描件会在常规文本提取不足时自动使用本地 ONNX OCR；
搜索结果始终保留 FDA 官方原始文件链接。

> [!IMPORTANT]
> 本项目不是 FDA 官方产品，也不隶属于或代表 FDA。索引内容可能因网站更新、
> PDF 解析或 OCR 识别而不完整；需要作出判断时，请以结果中的 FDA 原始文件为准。

## 解决什么问题

FDA OII Electronic Reading Room 发布了检查、合规和信息公开相关记录，但这些
资料主要以独立 PDF 的形式存在。官方列表适合按已知元数据浏览，却很难回答这类
需要跨文档检索的问题：

- 哪些记录提到某个具体的生产、质量或无菌控制问题？
- 某个企业、FEI 或机构类型在不同年份出现过哪些记录？
- 同一个关键词在不同 record type、州或国家中分别出现在哪里？
- 一份扫描版 PDF 的正文是否包含目标术语？

仅依赖文件名和列表筛选无法搜索 PDF 内文；逐份打开文件成本很高；扫描件通常又
没有可直接检索的文本层。不同 record type 分散浏览，也让跨记录类型的调查和
资料核对变得困难。

FDA OpenRecords Search 将这些公开记录转化为一个可在本地运行、可重复构建的
全文索引：

1. 从 FDA 官方页面发现记录并保留官方来源链接。
2. 提取每份 PDF 的正文，对文本不足的页面自动执行 OCR。
3. 将元数据和正文统一写入 SQLite FTS5。
4. 提供跨 record type 的关键词搜索、筛选、上下文片段和同步状态。

它缩短的是“找到相关原始记录”的时间，而不是替代合规判断。项目不分析违规
性质、不推断被遮盖内容，也不把 OCR 结果包装成权威结论；用户可以从每条结果
直接回到 FDA 原始 PDF 复核。

## 功能

- 跨 record type 索引 FDA OII Electronic Reading Room 中可下载的公开 PDF
- 搜索 PDF 正文、企业名、FEI、州、国家和机构类型
- 按 record type、地区和记录年份筛选
- 使用 SQLite FTS5 执行快速的本地全文检索
- 对原生文本不足的页面执行本地 ONNX OCR
- 支持并发提取、断点续建、定时增量刷新和单实例锁
- 显示发现、提取和 OCR 状态，不让单个异常 PDF 中断整个索引任务
- 不持久保存下载的 PDF，仅保存搜索所需的元数据和提取文本

## 工作方式

```text
FDA Electronic Reading Room
          |
          v
  发现记录和官方 PDF 链接
          |
          v
   PDF 文本提取 -> 文本不足时逐页 OCR
          |
          v
      SQLite + FTS5
          |
          v
   本地 HTTP API + 浏览器界面
```

所有网络请求仅面向 `fda.gov`。默认数据库为
`data/fda_search.sqlite3`，该目录已被 Git 忽略。

## 环境要求

- Python 3.11 或更高版本
- Linux、WSL 或其他提供 `fcntl` 的类 Unix 环境
- SQLite 构建包含 FTS5（常见 Python 发行版默认包含）
- 足够的磁盘空间和网络时间；完整索引需要下载并处理全部公开 PDF

## 快速开始

### 1. 克隆仓库

```bash
git clone https://github.com/meetwk0916/FDA.git
cd FDA
```

### 2. 安装依赖

推荐使用虚拟环境：

```bash
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install -r requirements.txt
export PYTHONPATH=src
```

如果环境中无法创建虚拟环境，也可以把依赖安装到已忽略的项目目录：

```bash
python3 -m pip install --target .deps -r requirements.txt
export PYTHONPATH=.deps:src
```

后续命令均需要保留对应的虚拟环境或 `PYTHONPATH` 设置。

### 3. 建立一个小型测试索引

```bash
python3 -m fda_search.indexer --limit 10
```

### 4. 启动搜索服务

```bash
python3 -m fda_search.server
```

打开 <http://127.0.0.1:8080>。

## 建立和维护索引

抓取全部可下载记录：

```bash
python3 -m fda_search.indexer --workers 2
```

每 12 小时执行一次增量刷新：

```bash
python3 -m fda_search.indexer --workers 2 --interval 43200
```

强制重新提取已有记录：

```bash
python3 -m fda_search.indexer --refresh --workers 2
```

使用其他数据库文件：

```bash
python3 -m fda_search.indexer --database /path/to/index.sqlite3
python3 -m fda_search.server --database /path/to/index.sqlite3
```

索引器具有以下运行特性：

- 已成功处理且提取版本未变化的文档会自动跳过。
- 提取规则升级后，旧版本记录会自动重新处理。
- 同一数据库只允许一个索引进程，重复启动会明确报错。
- 收到 `SIGINT` 或 `SIGTERM` 后停止提交新文档，并等待正在处理的文档写入。
- FDA 元数据中没有有效下载地址的记录会被计数并跳过。
- FDA 服务端分页可能发生漂移，因此发现阶段会进行稳定排序、多轮扫描和
  media ID 去重。

### 索引器参数

| 参数 | 说明 |
| --- | --- |
| `--database PATH` | SQLite 数据库路径，默认 `data/fda_search.sqlite3` |
| `--limit N` | 仅处理最新的 N 条记录，适合快速验证 |
| `--workers N` | 并行 PDF 处理线程数，默认 `2` |
| `--refresh` | 忽略已有提取结果并重新处理 |
| `--interval SECONDS` | 按指定间隔持续执行增量索引 |

## 搜索语义

- 查询按 Unicode 单词分词并忽略大小写，最多使用前 8 个词。
- 每个词使用原词前缀匹配，所有词之间使用 `AND`。
- 多个查询词不要求在正文中相邻。
- 不执行编辑距离、拼写纠正、同义词或近似词扩展。
- 结果使用 BM25 排序，其中 FEI 和企业名的权重较高。

例如，`quality control` 只返回同时包含 `quality*` 和 `control*` 的记录。

## HTTP API

服务默认监听 `127.0.0.1:8080`。可通过命令行修改监听地址和端口：

```bash
python3 -m fda_search.server --host 0.0.0.0 --port 8080
```

### `GET /api/search`

| 参数 | 说明 |
| --- | --- |
| `q` | 全文查询 |
| `state` | 州或地区精确筛选 |
| `year` | 四位记录年份 |
| `record_type` | record type 精确筛选 |
| `limit` | 返回数量，范围 1-100，默认 20 |
| `offset` | 分页偏移量，默认 0 |

示例：

```bash
curl "http://127.0.0.1:8080/api/search?q=quality%20control&limit=10"
```

### `GET /api/status`

返回数据源发现数量、文档提取状态和当前同步进度：

```bash
curl http://127.0.0.1:8080/api/status
```

## 项目结构

```text
src/fda_search/
├── database.py       # SQLite 表结构和 FTS5 索引
├── indexer.py        # FDA 记录发现、PDF 提取、OCR 和增量同步
├── search.py         # 查询构造、筛选、排序和状态统计
├── server.py         # 标准库 HTTP 服务和 JSON API
└── static/           # 浏览器界面
tests/                # unittest 测试
```

项目刻意保持服务端轻量：爬取编排和 HTTP 服务使用 Python 标准库，SQLite
同时承担持久化和全文检索，不需要独立搜索服务器。

## 测试

```bash
python3 -m unittest
```

测试使用临时数据库和模拟网络/PDF 输入，不会执行完整 FDA 抓取。

## 数据、隐私与准确性

- 元数据和 PDF 来自 FDA 官方公开页面。
- 原始 PDF 仅在处理期间使用临时文件，不会持久保存。
- 本地数据库包含从公开文件提取的文本，不应提交到 Git。
- 原生文本不足的页面会渲染后执行 OCR。
- OCR 后仍无法获得足够文本的文档会标记为 `ocr_required`，而不是误报为
  完整索引。
- 单个格式异常、加密或无法解析的 PDF 会记录错误，不影响其他文档。
- FDA 文件中的法定遮盖内容无法恢复，本项目不会推断或补全被遮盖内容。
- 使用者应遵守 FDA 网站的适用条款，并控制抓取频率。

## 参与贡献

欢迎提交 Issue 和 Pull Request。提交改动前请：

1. 保持网络访问仅限 FDA 官方 URL，并保留结果中的官方来源链接。
2. 不要提交 PDF、SQLite 索引、OCR 模型缓存或其他生成文件。
3. 不要改变当前的大小写不敏感、词前缀、全词 `AND` 搜索语义，除非改动目标
   明确要求如此。
4. 修改提取完整性规则时同步递增 `EXTRACTION_VERSION`。
5. 运行 `python3 -m unittest` 并说明新增行为的验证方式。

修改文档时，请保持 [README.md](README.md) 与
[README.zh-CN.md](README.zh-CN.md) 内容同步。

## 许可证

本项目采用 [MIT License](LICENSE)。
