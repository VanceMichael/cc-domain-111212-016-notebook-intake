# 输出显示语义采集契约

## 输入与安全边界

两类只读来源，采集过程均不执行单元格，也不运行输出中的 HTML/JavaScript：

1. 静态 nbformat 4 文档（`.ipynb`）：保存的 outputs 先投影成规范 IOPub 事件，再与事件来源走同一回放器，保证两条路径语义一致。
2. 本地记录的 IOPub 事件（`.json` 数组），每条投影为 `session`、`parent`、`cell`、`msg_type`、`content`，可带 `_source` 与 `_output_index` 记录原输出位置。

事件文件在结尾被截断时，抢救所有完整事件并在报告顶层给出 `status: incomplete` 与原因；结构不完整的事件跳过并进入 `unmatched_events`，不静默成功。

## 执行绑定与输出区

* 输出区键为 `(session, parent)`，parent 即 execute 请求消息；每条事件必须显式绑定 parent 与 cell。
* 同一 session 内同一 cell 出现新 parent 时，旧输出区整体退役并从 display_id 注册表注销，进入 `removed_outputs`。因此另一次运行的更新不可能应用到旧输出。
* 并行执行的多个 parent 事件可在流中交织；stream 拼接只在同一 parent、同名（stdout/stderr 不混）的相邻片段间发生。
* parent 绑定的 cell 不在笔记本中时，事件无法归属，记入 `unmatched_events` 并标记不完整。

## display_id 与更新

* display_id 作用域为 kernel session，不是单个 cell。注册表保存当前仍存活的显示条目集合。
* `display_data`/`execute_result` 携带 `transient.display_id` 时注册；`update_display_data` 整体替换该 id 在**当前会话**内所有存活条目的 MIME bundle，并追加更新历史（事件序号、parent、替换前摘要）。
* 更新不产生新输出：发起更新的 cell 输出区保持为空（`status: cleared`）。
* 目标 display_id 已被清屏、退役或属于其他 session 时，更新被拒绝并记录原因，绝不复活或新建输出。

## clear_output 语义

* `wait=false`（默认）：立即清空当前 parent 输出区并注销其中的 display_id。
* `wait=true`：不清屏，只挂起；当本执行的下一条输出（stream/display/execute_result/error）到达时才清空此前内容，再放入新输出。
* 事件流结束时仍有挂起的 wait 清屏：清屏未生效，旧内容仍为当前可见结果，但 cell 与报告整体标记 `incomplete` 并说明原因。

## MIME bundle 与阅读表示

* 全部候选 MIME 原样保留，每个候选记录角色（text/image/widget/html/script/unknown）、是否选中与理由。
* 选中顺序由可配置偏好决定（默认 `text/markdown` → `text/plain` → `text/latex` → 图片 → HTML/脚本 → JSON）。widget MIME 永不选中为阅读表示。
* 阅读表示按类别分开：`text`（含选中的文本内容与长文本截断标记）、`tables`（检测到表格标记的 HTML 源码摘要）、`images`（MIME、sha256、字节数，`--blobs` 时附本地引用路径）、`widgets`。
* 图片以引用而非内联数据进入索引；SVG 同样不渲染、不执行其中脚本。HTML/JavaScript 只留存源码并标注不执行。
* widget MIME（`application/vnd.jupyter.widget*`）标记 `dependency_missing`，说明缺少 Jupyter Widget 运行时/扩展，不呈现空白成功。
* 无任何可渲染 MIME 时标记 `no_readable_renderer`，候选仍保留。

## 来源身份与位置

* 笔记本身份为内容 `sha256` 摘要，cell 位置同时给出稳定 cell id 与序号；重新提交内容变化的笔记本得到不同摘要，不会仅凭 cell 序号认成同一份。
* 每个可见条目携带 `provenance`：来源（static/events）、session、parent、cell id、cell 序号、事件序号、静态输出序号；被更新过的条目保留创建与每次更新的全部位置。
* 索引库 `indexes` 表按 `(source, notebook_digest, events_digest)` 去重。

## 报告状态

`complete` 仅当没有截断、没有未匹配事件、没有挂起清屏；否则为 `incomplete`，`reasons` 给出人类可读原因。cell 级状态另有 `no_output`、`cleared`、`incomplete` 之分，资料员可同时看到最终阅读表示、原输出位置与所有被移除内容的去向。
