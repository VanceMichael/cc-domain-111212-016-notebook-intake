"""按输出显示语义采集笔记本最终可见结果。

数据来源有两类，均为只读：
* 静态 nbformat 4 文档，先把已保存输出投影成规范 IOPub 事件再统一回放；
* 本地记录的 IOPub 事件（session/parent/cell/msg_type/content 投影）。

采集过程不执行单元格，也不运行输出中的 HTML/JavaScript。所有可见条目同时
携带最终阅读表示与原始输出位置（cell id、输出序号、事件序号、parent 消息）。
"""

import hashlib
import json
from pathlib import Path

# 安全阅读偏好：文本在前，图片以引用留存，HTML/脚本排在最后且永不执行。
DEFAULT_PREFERENCE = [
    'text/markdown',
    'text/plain',
    'text/latex',
    'image/png',
    'image/jpeg',
    'image/gif',
    'image/svg+xml',
    'text/html',
    'application/javascript',
    'application/json',
]

WIDGET_PREFIX = 'application/vnd.jupyter.widget'
TEXT_CAP = 200_000
IMAGE_EXTENSIONS = {'image/png': '.png', 'image/jpeg': '.jpg', 'image/gif': '.gif',
                    'image/svg+xml': '.svg'}


def digest_bytes(data):
    return hashlib.sha256(data).hexdigest()


def as_text(value):
    """nbformat 的 source/text 允许字符串或字符串数组，统一成字符串。"""
    if isinstance(value, list):
        return ''.join(value)
    return value if isinstance(value, str) else ''


def classify_mime(mime):
    if mime.startswith('image/'):
        return 'image'
    if mime.startswith(WIDGET_PREFIX):
        return 'widget'
    if mime == 'text/html':
        return 'html'
    if mime == 'application/javascript':
        return 'script'
    if mime.startswith('text/') or mime == 'application/json':
        return 'text'
    return 'unknown'


def load_notebook(raw):
    notebook = json.loads(raw)
    if notebook.get('nbformat') != 4 or not isinstance(notebook.get('cells'), list):
        raise ValueError('只支持 nbformat 4 笔记本')
    return notebook, digest_bytes(raw)


def _salvage_events(raw):
    """事件文件在结尾被截断时，抢救出所有完整的事件对象。"""
    text = raw.decode('utf-8', 'replace').strip()
    if not text.startswith('['):
        raise ValueError('事件记录不是 JSON 数组')
    depth = 0
    in_string = False
    escaped = False
    last_complete = 0
    for index, char in enumerate(text):
        if in_string:
            if escaped:
                escaped = False
            elif char == '\\':
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == '{':
            depth += 1
        elif char == '}':
            depth -= 1
            if depth == 1:
                last_complete = index + 1
        elif char == '[':
            depth += 1
    if last_complete == 0:
        return []
    return json.loads(text[:last_complete] + ']')


def parse_events(raw):
    """返回 (事件列表, 是否截断)。截断或非法结构都给出明确状态而不是静默成功。"""
    if not raw or not raw.strip():
        raise ValueError('事件记录为空')
    try:
        events = json.loads(raw)
        truncated = False
    except json.JSONDecodeError:
        events = _salvage_events(raw)
        truncated = True
    if not isinstance(events, list):
        raise ValueError('事件记录必须是数组')
    return events, truncated


class Item:
    """输出区中的一个存活条目，对应屏幕上的一块可见内容。"""

    def __init__(self, kind, data, metadata, display_id, stream_name, origin):
        self.kind = kind                      # stream/display/result/error
        self.data = dict(data)               # MIME bundle，候选全部保留
        self.metadata = dict(metadata or {})
        self.display_id = display_id
        self.stream_name = stream_name
        self.origins = [origin]              # 创建及后续更新/拼接的全部位置
        self.update_history = []
        self.live = True

    def append_stream(self, text, origin):
        self.data['text/plain'] = self.data.get('text/plain', '') + text
        self.origins.append(origin)


class Area:
    """一次执行（parent 消息）在某个 cell 上的输出区。"""

    def __init__(self, session, parent, cell_id):
        self.session = session
        self.parent = parent
        self.cell_id = cell_id
        self.items = []
        self.pending_clear = False           # clear_output(wait=True) 挂起中
        self.retired = False
        self.execution_count = None


class Replayer:
    """按显示语义回放规范事件，维护输出区与会话级 display_id 注册表。"""

    def __init__(self, cell_index):
        self.cell_index = cell_index         # cell id -> 序号
        self.areas = {}                      # (session, parent) -> Area
        self.cell_parent = {}                # (session, cell id) -> parent
        self.displays = {}                   # session -> display_id -> set(Item)
        self.removed = []
        self.unmatched = []
        self.reasons = []
        self.sessions = []

    def _note(self, reason):
        if reason not in self.reasons:
            self.reasons.append(reason)

    def _area_for(self, event, seq):
        session = event['session']
        parent = event['parent']
        cell_id = event['cell']
        if session not in self.sessions:
            self.sessions.append(session)
        key = (session, parent)
        area = self.areas.get(key)
        if area is None:
            if cell_id not in self.cell_index:
                self.unmatched.append({
                    'event_seq': seq, 'session': session, 'parent': parent,
                    'cell_id': cell_id,
                    'reason': 'parent 绑定的 cell 不在笔记本中，事件无法归入输出区'})
                self._note('存在未匹配 cell 的执行事件，采集不完整')
                return None
            area = Area(session, parent, cell_id)
            self.areas[key] = area
            previous = self.cell_parent.get((session, cell_id))
            if previous is not None:
                # 同一 cell 重新执行：旧输出区退役，之后的更新不可能再触及旧输出。
                old = self.areas[(session, previous)]
                self._retire(old, seq)
            self.cell_parent[(session, cell_id)] = parent
        return area

    def _retire(self, area, seq):
        area.retired = True
        for item in area.items:
            self._unregister(item)
        item_count = len(area.items)
        area.items = []
        area.pending_clear = False
        self.removed.append({
            'cell_id': area.cell_id, 'session': area.session, 'parent': area.parent,
            'event_seq': seq, 'item_count': item_count,
            'reason': '同一 cell 出现新的 parent 执行，旧输出区被替换'})

    def _unregister(self, item):
        item.live = False
        if not item.display_id:
            return
        for registry in self.displays.values():
            holders = registry.get(item.display_id)
            if holders:
                holders.discard(item)
                if not holders:
                    del registry[item.display_id]

    def _clear(self, area, wait, seq):
        if wait:
            # wait=True：不清屏，挂起直到本执行的下一条输出到达。
            area.pending_clear = True
            return
        for item in area.items:
            self._unregister(item)
        self.removed.append({
            'cell_id': area.cell_id, 'session': area.session, 'parent': area.parent,
            'event_seq': seq, 'item_count': len(area.items),
            'reason': 'clear_output(wait=false) 立即清屏'})
        area.items = []

    def _flush_pending(self, area, seq):
        if not area.pending_clear:
            return
        for item in area.items:
            self._unregister(item)
        self.removed.append({
            'cell_id': area.cell_id, 'session': area.session, 'parent': area.parent,
            'event_seq': seq, 'item_count': len(area.items),
            'reason': 'clear_output(wait=true) 在本执行下一条输出到达时延迟清屏'})
        area.items = []
        area.pending_clear = False

    def _register(self, session, item):
        if not item.display_id:
            return
        self.displays.setdefault(session, {}).setdefault(item.display_id, set()).add(item)

    def handle(self, event, seq):
        origin = {
            'source': event['_source'], 'session': event['session'],
            'parent': event['parent'], 'cell_id': event['cell'],
            'cell_index': self.cell_index.get(event['cell']),
            'event_seq': seq, 'output_index': event.get('_output_index')}
        msg_type = event['msg_type']
        content = event['content']

        if msg_type == 'clear_output':
            area = self._area_for(event, seq)
            if area is not None:
                self._clear(area, bool(content.get('wait', False)), seq)
            return

        area = self._area_for(event, seq)
        if area is None:
            return

        if msg_type == 'stream':
            self._flush_pending(area, seq)
            name = content.get('name', 'stdout')
            text = as_text(content.get('text', ''))
            if area.items and area.items[-1].kind == 'stream' \
                    and area.items[-1].stream_name == name:
                # 同名连续 stream 片段属于同一块可见文本。
                area.items[-1].append_stream(text, origin)
            else:
                item = Item('stream', {'text/plain': text}, {}, None, name, origin)
                area.items.append(item)
            return

        # 以下输出类型到达前，挂起的 wait 清屏先生效。
        self._flush_pending(area, seq)

        if msg_type in ('display_data', 'execute_result'):
            bundle = content.get('data', {}) or {}
            data = {mime: as_text(payload) for mime, payload in bundle.items()}
            metadata = content.get('metadata', {}) or {}
            display_id = (content.get('transient') or {}).get('display_id')
            kind = 'result' if msg_type == 'execute_result' else 'display'
            item = Item(kind, data, metadata, display_id, None, origin)
            if msg_type == 'execute_result':
                area.execution_count = content.get('execution_count', area.execution_count)
            area.items.append(item)
            self._register(area.session, item)
            return

        if msg_type == 'update_display_data':
            display_id = (content.get('transient') or {}).get('display_id')
            targets = self.displays.get(area.session, {}).get(display_id)
            if not targets:
                # 目标已被清屏/替换或属于别的会话：绝不复活或改写旧输出。
                self.unmatched.append({
                    'event_seq': seq, 'session': area.session, 'parent': area.parent,
                    'cell_id': area.cell_id, 'display_id': display_id,
                    'reason': 'update_display_data 找不到当前会话仍存活的 display_id'})
                self._note('存在无法应用的 display_id 更新，采集不完整')
                return
            new_data = {mime: as_text(payload)
                        for mime, payload in (content.get('data') or {}).items()}
            new_metadata = content.get('metadata', {}) or {}
            for item in targets:
                item.update_history.append({
                    'event_seq': seq, 'parent': area.parent,
                    'previous_digest': digest_bytes(json.dumps(
                        item.data, ensure_ascii=False, sort_keys=True).encode())})
                item.data = dict(new_data)
                item.metadata = dict(new_metadata)
                item.origins.append(origin)
            return

        if msg_type == 'error':
            traceback = content.get('traceback') or []
            text = '\n'.join(traceback) if traceback else \
                '{}: {}'.format(content.get('ename', 'Error'), content.get('evalue', ''))
            item = Item('error', {'text/plain': text}, {}, None, None, origin)
            area.items.append(item)
            return

        self.unmatched.append({
            'event_seq': seq, 'session': area.session, 'parent': area.parent,
            'cell_id': area.cell_id, 'reason': '不支持的 msg_type: {}'.format(msg_type)})
        self._note('存在无法解释的事件类型，采集不完整')

    def finish(self):
        for area in self.areas.values():
            if area.pending_clear:
                self.reasons.append(
                    'cell {} 的 clear_output(wait=true) 等待下一条输出，事件流已结束，'
                    '清屏未生效'.format(area.cell_id))


def static_events(notebook, digest):
    """把静态笔记本的已保存输出投影成规范 IOPub 事件。"""
    session = 'static:' + digest[:12]
    events = []
    for index, cell in enumerate(notebook['cells']):
        if cell.get('cell_type') != 'code':
            continue
        cell_id = cell.get('id') or 'cell-{}'.format(index)
        parent = '{}:exec{}'.format(cell_id, cell.get('execution_count') or index)
        for output_index, output in enumerate(cell.get('outputs', []) or []):
            output_type = output.get('output_type')
            base = {'session': session, 'parent': parent, 'cell': cell_id,
                    '_source': 'static', '_output_index': output_index}
            if output_type == 'stream':
                event = dict(base, msg_type='stream',
                             content={'name': output.get('name', 'stdout'),
                                      'text': as_text(output.get('text', ''))})
            elif output_type in ('display_data', 'execute_result', 'update_display_data'):
                event = dict(base, msg_type=output_type, content={
                    'data': {mime: as_text(payload)
                             for mime, payload in (output.get('data') or {}).items()},
                    'metadata': output.get('metadata', {}) or {},
                    'transient': output.get('transient', {}) or {}})
            elif output_type == 'error':
                event = dict(base, msg_type='error', content={
                    'ename': output.get('ename', ''), 'evalue': output.get('evalue', ''),
                    'traceback': output.get('traceback', [])})
            else:
                continue
            events.append(event)
    return events


def _preference_rank(mime, preference):
    try:
        return preference.index(mime)
    except ValueError:
        return len(preference)


def _image_reference(mime, payload, blobs_dir):
    if mime == 'image/svg+xml':
        raw = payload.encode('utf-8')
    else:
        import base64
        raw = base64.b64decode(payload)
    reference = {'mime': mime, 'sha256': digest_bytes(raw), 'bytes': len(raw)}
    if blobs_dir is not None:
        path = Path(blobs_dir) / (reference['sha256'] + IMAGE_EXTENSIONS.get(mime, '.bin'))
        if not path.exists():
            path.write_bytes(raw)
        reference['blob_path'] = str(path)
    return reference


def _candidate_record(mime, selected):
    role = classify_mime(mime)
    if role == 'image':
        note = '二进制图片以摘要引用留存，不内联到索引'
        if mime == 'image/svg+xml':
            note += '；SVG 不渲染、不执行其中脚本'
    elif role == 'widget':
        note = '缺少 Jupyter Widget 运行时依赖，无法在索引中呈现'
    elif role == 'html':
        note = 'HTML 仅作为源码留存，不执行标记或脚本'
    elif role == 'script':
        note = '脚本不执行，仅留存源码'
    elif role == 'unknown':
        note = '无对应阅读渲染器，保留原始候选，不执行'
    else:
        note = '备选 MIME 候选'
    return {'mime': mime, 'role': role, 'selected': mime == selected, 'reason': note}


def render_item(item, preference, blobs_dir):
    """把一个存活条目转成分门别类的阅读表示，候选 MIME 与选用理由全部保留。"""
    mimes = sorted(item.data)
    readable = [mime for mime in mimes if classify_mime(mime) != 'widget']
    selected = None
    selected_reason = None
    if readable:
        selected = min(readable, key=lambda mime: (_preference_rank(mime, preference), mime))
        if item.kind == 'stream':
            selected_reason = '同名连续 stream 片段拼接后的文本（{}）'.format(item.stream_name)
        elif item.kind == 'error':
            selected_reason = '错误回溯文本（ename/evalue/traceback）'
        else:
            selected_reason = '按配置阅读偏好第 {} 位选中：{}'.format(
                _preference_rank(selected, preference) + 1, selected)
    candidates = [_candidate_record(mime, selected) for mime in mimes]

    entry = {
        'kind': item.kind,
        'display_id': item.display_id,
        'selected_mime': selected,
        'selected_reason': selected_reason,
        'candidates': candidates,
        'provenance': item.origins,
        'update_history': item.update_history,
        'text': [],
        'tables': [],
        'images': [],
        'widgets': [],
    }

    for mime in mimes:
        role = classify_mime(mime)
        payload = item.data[mime]
        if role == 'image':
            entry['images'].append(_image_reference(mime, payload, blobs_dir))
        elif role == 'widget':
            entry['widgets'].append({
                'mime': mime,
                'status': 'dependency_missing',
                'reason': '缺少 Jupyter Widget 运行时/扩展（{}），无法呈现 widget 状态，'
                          '需在带 ipywidgets 的环境中查看原笔记本'.format(mime)})
        elif role == 'html' and '<table' in payload.lower():
            entry['tables'].append({
                'mime': mime, 'source_sha256': digest_bytes(payload.encode()),
                'bytes': len(payload.encode()),
                'reason': '检测到表格标记，HTML 表格源码单独留存，不执行其中脚本'})
        elif role in ('text', 'html', 'script', 'unknown') and mime == selected:
            content = payload
            clipped = len(content) > TEXT_CAP
            if clipped:
                content = content[:TEXT_CAP]
            entry['text'].append({
                'mime': mime,
                'content': content,
                'clipped': clipped,
                'reason': selected_reason})

    if not selected and any(classify_mime(mime) == 'widget' for mime in mimes):
        entry['status'] = 'dependency_missing'
    elif not selected:
        entry['status'] = 'no_readable_renderer'
    else:
        entry['status'] = 'ok'
    return entry


def collect(notebook_raw, events_raw=None, *, preference=None, blobs_dir=None,
            notebook_name=None, events_name=None):
    """采集最终可见结果。events_raw 为 None 时只使用静态 nbformat 输出。"""
    notebook, nb_digest = load_notebook(notebook_raw)
    preference = list(preference or DEFAULT_PREFERENCE)
    cell_index = {cell.get('id') or 'cell-{}'.format(i): i
                  for i, cell in enumerate(notebook['cells'])}

    replayer = Replayer(cell_index)
    truncated = False
    if events_raw is None:
        canonical = static_events(notebook, nb_digest)
        source_kind = 'static'
    else:
        canonical, truncated = parse_events(events_raw)
        source_kind = 'events'
        if truncated:
            replayer._note('事件记录在结尾处被截断，仅抢救出完整事件，采集不完整')

    for seq, event in enumerate(canonical):
        if not isinstance(event, dict) or not all(
                isinstance(event.get(field), str)
                for field in ('session', 'parent', 'cell', 'msg_type')) \
                or not isinstance(event.get('content'), dict):
            replayer.unmatched.append({'event_seq': seq, 'reason': '事件结构不完整，已跳过'})
            replayer._note('存在结构不完整的事件，采集不完整')
            continue
        event = dict(event)
        event.setdefault('_source', source_kind)
        replayer.handle(event, seq)
    replayer.finish()

    cells = []
    for index, cell in enumerate(notebook['cells']):
        cell_id = cell.get('id') or 'cell-{}'.format(index)
        cell_record = {
            'cell_id': cell_id, 'cell_index': index, 'kind': cell.get('cell_type'),
            'execution_count': cell.get('execution_count'), 'status': 'complete',
            'reading': {'text': [], 'tables': [], 'images': [], 'widgets': []},
            'areas': []}
        if cell.get('cell_type') == 'markdown':
            source = as_text(cell.get('source', ''))
            cell_record['reading']['text'].append({
                'mime': 'text/markdown', 'content': source, 'clipped': False,
                'reason': 'Markdown 单元源码即最终阅读文本'})
            cell_record['provenance'] = {'source': 'notebook-source', 'cell_index': index}
            cells.append(cell_record)
            continue

        # 取最后一个绑定该 cell 的会话输出区（并行/重跑时其余区域已退役或让位）。
        bound = None
        for session in replayer.sessions:
            parent = replayer.cell_parent.get((session, cell_id))
            if parent is not None:
                bound = replayer.areas[(session, parent)]
        if bound is None:
            cell_record['status'] = 'no_output'
            cell_record['note'] = '事件记录中无该 cell 的执行输出' if events_raw is not None \
                else '静态笔记本中该 cell 无输出'
        else:
            cell_record['areas'].append({
                'session': bound.session, 'parent': bound.parent,
                'retired': bound.retired, 'pending_clear': bound.pending_clear})
            if not bound.items:
                cell_record['status'] = 'cleared'
                cell_record['note'] = '输出区最终为空（内容已清屏或被替换）'
            for item in bound.items:
                rendered = render_item(item, preference, blobs_dir)
                for category in ('text', 'tables', 'images', 'widgets'):
                    cell_record['reading'][category].extend(rendered.pop(category))
                cell_record['reading'].setdefault('items', []).append(rendered)
            if bound.pending_clear:
                cell_record['status'] = 'incomplete'
        cells.append(cell_record)

    complete = not replayer.reasons and not truncated
    return {
        'notebook_digest': 'sha256:' + nb_digest,
        'notebook_name': notebook_name,
        'events_name': events_name,
        'source_mode': source_kind,
        'status': 'complete' if complete else 'incomplete',
        'complete': complete,
        'reasons': replayer.reasons,
        'policy': {'execute_cells': False, 'run_html_or_scripts': False},
        'preference': preference,
        'cells': cells,
        'unmatched_events': replayer.unmatched,
        'removed_outputs': replayer.removed,
    }
