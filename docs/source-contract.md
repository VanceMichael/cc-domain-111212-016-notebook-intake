# 来源格式与业务语义

静态文档为 nbformat4，source/text 均可字符串或字符串数组。IOPub 夹具投影 session、parent、cell、msg_type、content；display_id 的作用域为 kernel session，不是单个 cell。clear_output(wait=true) 直到该执行产生下一条输出才清空；更新不是新输出。新执行 parent 需显式绑定 cell；重新运行的输出区由执行映射决定。MIME 阅读偏好可配置，不执行 HTML/JavaScript，widget 缺状态需解释。

本地演练夹具由 `sampledata.py` 生成，均为虚构业务数据。`sample` 是当前可查看输入，`invalid` 是边界失败输入，其余用于对照来源变体。生成不会下载资源。

本地 HTTP 演练器采用方法与原始路径作为路由键，值为按调用次数返回的响应数组，耗尽后维持最后一项。响应可定义 status、headers、text/base64、require_headers、chunk_bytes、disconnect_after。每次请求保留方法、路径、头与正文摘要表示，便于核对实际访问；演练器只监听回环地址。

原始输入上限 16 MiB 是现有查看入口的保护值，不代表未来持续采集需一次读完整来源。格式含义和截断错误不能被 HTTP 200 覆盖。
