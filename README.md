# notebook-intake

交互笔记本输出状态的采集所用的来源检查与原件留存工具。

## 当前能力

`source.py` 查看来源的现有结构，不把检查结果冒充完整业务采集。
`storage.py` 将原始字节、来源标识、摘要和检查结果一起保存在本地 SQLite，重复原件保留同一收件编号。
`sampledata.py` 构造可重复的本地数据；`transport.py` 提供独立的回环 HTTP 演练源与请求记录。
`intake.py` 按输出显示语义采集最终可见结果：静态 nbformat 输出先投影成规范 IOPub 事件，再与本地记录的 IOPub 事件用同一回放器处理；`demo.py` 用带断言的虚构夹具演示延迟清屏、跨 cell 显示更新与并行执行交织。
解释范围和来源变体见 `docs/source-contract.md`，显示语义规则见 `docs/intake-contract.md`，当前检查测试与将来新增业务能力的测试应分开保留（`tests/test_source.py` 与 `tests/test_intake.py`）。

## 显示语义采集

采集只读输入，不执行单元格，也不运行输出中的 HTML/JavaScript。报告按 cell id 与笔记本 `sha256` 摘要标识来源，同一笔记本重新提交不会仅凭 cell 序号认成原件：

* 事件按 `(session, parent)` 绑定执行输出区，同名连续 stream 片段拼接为同一块文本；
* `display_id` 作用域为 kernel session：`update_display_data` 只更新当前会话仍存活的显示，更新不是新输出；
* `clear_output(wait=true)` 挂起直到本执行的下一条输出才清屏，事件流结束仍挂起时报告明确的不完整状态；
* cell 重新执行产生新 parent 时旧输出区退役，之后的更新不能应用到旧输出；
* MIME bundle 保留全部候选、分类角色与选用理由，文字/表格/图片引用分开，图片以摘要文件落盘；
* 未知 widget MIME 标记 `dependency_missing` 并解释缺依赖，而不是空白成功；
* 事件文件截断时抢救完整事件并标记 `incomplete`，无法匹配的 parent/display_id 进入 `unmatched_events`。

## 环境

Python 3.11 或以上，仅标准库，无需凭据和外部数据。使用容器时可执行 `docker build -t notebook-intake .`，依赖及镜像须在正式运行前准备。
运行入口、测试与演示均不安装包，不访问公网。数据库使用临时路径，不向代码目录写入业务文件。

## 测试与构建

构建工具版本固定在 `requirements-build.txt`，在环境准备阶段执行 `python3 -m pip install -r requirements-build.txt`；容器构建已包含此步骤。应用运行仍仅使用标准库，测试及演示不安装依赖。

```sh
python3 -m unittest discover -s tests -v
python3 -m compileall -q app.py source.py sampledata.py storage.py transport.py intake.py demo.py
python3 -m build --wheel --no-isolation
```

## 快速演示

```sh
python3 app.py demo
python3 app.py intake-demo
```

`demo` 真实启动本地来源、领取原件、执行检查并保存临时数据库，展示检查结果与重复收件身份，退出后自动清理。
`intake-demo` 对虚构讲师笔记本执行显示语义采集，逐项断言延迟清屏、跨 cell 更新、并行交织、重跑隔离、widget 缺依赖与截断不完整，正常在数秒内完成，不以预录日志代替执行。

## 单独检查、收件与语义索引

```sh
python3 app.py fixtures /tmp/notebook-intake-fixtures
python3 app.py inspect /tmp/notebook-intake-fixtures/sample.ipynb
python3 app.py capture /tmp/notebook-intake-fixtures/sample.ipynb --database /tmp/notebook-intake.sqlite3 --source local-partner
python3 app.py list --database /tmp/notebook-intake.sqlite3
python3 app.py index /tmp/notebook-intake-fixtures/demo.ipynb \
  --events /tmp/notebook-intake-fixtures/demo-events.json \
  --blobs /tmp/notebook-intake-blobs \
  --database /tmp/notebook-intake.sqlite3 --source training-dept
```

`index` 不带 `--events` 时只回放静态 nbformat 输出；带 `--events` 时以事件记录决定最终屏幕内容，静态文档仅提供 cell 结构。`--blobs` 把图片候选按摘要落盘，`--preference` 可覆盖 MIME 阅读偏好，`--database/--source` 把报告按笔记本摘要（及事件摘要）去重写入 `indexes` 表。生成夹具不会覆盖已有文件。运行错误以 JSON 输出到标准错误并返回退出码 2；原始输入无效时不生成成功收件。内部原件与任何对外展示副本应分开管理，不写入真实个人数据。
