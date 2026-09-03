# FDA Global Full-text Search

抓取 FDA OII Electronic Reading Room 中全部可下载记录的 PDF，提取正文
至 SQLite FTS5，并提供要求全部查询词命中的浏览器搜索界面。Record type 作为
可选过滤器，不限制全局索引范围。
每条结果保留 FDA 原始 PDF 下载链接。

## 安装

项目的直接依赖见 `requirements.txt`，包括 PDF 文本提取、页面渲染和本地
ONNX OCR。若系统没有 `python3-venv`，可安装到项目隔离目录：

```bash
python3 -m pip install --target .deps -r requirements.txt
```

后续命令在 Linux / WSL 中使用：

```bash
export PYTHONPATH=.deps:.
```

## 建立索引

先用少量记录验证：

```bash
python3 -m fda483.indexer --limit 10
```

抓取全部 FDA 公开记录：

```bash
python3 -m fda483.indexer --workers 2
```

持续滚动刷新（推荐每 12 小时一轮）：

```bash
python3 -m fda483.indexer --workers 2 --interval 43200
```

索引器使用数据库级单实例锁；同一数据库已有索引进程时，重复启动会明确失败。
`SIGINT` 或 `SIGTERM` 会停止提交新文档，等待正在处理的文档写入后退出。

索引器支持断点续建；已成功索引的文档会自动跳过。完整下载和文本提取需要
较长时间及足够磁盘空间。数据库
默认保存在 `data/fda483.sqlite3`，原始 PDF 不会持久保存。
FDA 元数据中少量记录的下载地址为 `/na`，索引器会明确计数并跳过这些
无法取得原文的条目。
索引版本会记录逐页 OCR 校验能力；提取规则升级后，旧版本记录会自动重新处理。
FDA 的服务端分页结果会发生漂移，因此发现阶段执行多轮稳定排序扫描，并按
FDA media ID 去重，直到不再发现新的 PDF 链接。

重新提取已有记录：

```bash
python3 -m fda483.indexer --refresh --workers 2
```

## 启动搜索

```bash
python3 -m fda483.server
```

打开 <http://127.0.0.1:8080>。可搜索 PDF 正文、企业名、FEI、州和机构类型，
并按 record type、地区或记录年份筛选。搜索不会把输入词自动扩展为近似拼写。
页面每 30 秒更新一次索引状态；有新文档写入时会提示刷新结果，不会在阅读期间
自动重排当前结果。

### 搜索语义

- 查询按单词分词并忽略大小写，最多使用前 8 个词。
- 每个词均采用原词前缀匹配，所有词之间使用 `AND`；多词不要求相邻。
- 不执行编辑距离、拼写纠正、同义词或近似词扩展。
- 结果采用 BM25 排序，FEI 和企业名权重最高，正文权重为基准值。

## 测试

```bash
python3 -m unittest
```

## 交付与部署状态

本仓库保留核心应用源码、本地运行方式和历史索引能力。面向团队的交付目标已经
切换为 BI EDP；EDP 部署脚手架和现役部署说明以内部 Bitbucket
[`GENFOX/genfox-fbi` 的 `dev` 分支](https://bitbucket.biscrum.com/projects/GENFOX/repos/genfox-fbi/browse?at=refs%2Fheads%2Fdev)
为准。

Bitbucket 仓库当前已包含应用源码以及 OpenDevStack Jenkins、OpenShift 和 Helm
脚手架，但 Python/OCR 镜像、SQLite 持久卷、Web Deployment 和定时增量索引
CronJob 仍待接入并验证。因此，代码已进入 EDP 仓库不代表应用已经部署或上线。

此前的腾讯云 Lighthouse 个人部署方案已经退役，不再是项目下一步。
本地历史索引已于 2026-08-28 完成，SQLite 数据保存在被 Git 忽略的 `data/`
目录中；索引数据库不会随源码推送到 GitHub 或 Bitbucket。

## 数据与准确性

- 元数据和 PDF 均来自 FDA 官方公开页面。
- 原生文本不足的页面会自动渲染并执行 ONNX OCR；OCR 后仍不足的文档标记为
  `ocr_required`，不会被误报为完整索引。
- FDA 法定遮盖内容无法恢复，搜索结果应以链接中的原始 PDF 为准。
