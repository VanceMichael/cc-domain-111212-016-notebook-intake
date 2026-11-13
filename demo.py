"""输出显示语义采集的可重复演示。

演示全部基于 sampledata 的虚构夹具，在临时目录中生成图片引用，不执行
任何单元格或 HTML/脚本。每个场景附带必须成立的不变量断言。
"""

import base64
import json
import tempfile
from pathlib import Path

from intake import collect, digest_bytes
from sampledata import PNG_A, PNG_B, samples
from source import inspect_bytes


def _by_id(report, cell_id):
    return next(cell for cell in report['cells'] if cell['cell_id'] == cell_id)


def _items(cell):
    return cell['reading'].get('items', [])


def _cell_text(cell):
    return ''.join(part['content'] for part in cell['reading']['text'])


def _image_shas(cell):
    return sorted(ref['sha256'] for ref in cell['reading']['images'])


def run_demo():
    fixtures = samples()
    notebook_raw = fixtures['demo.ipynb']
    events_raw = fixtures['demo-events.json']
    checks = []

    def check(name, condition, detail=''):
        if not condition:
            raise AssertionError('演示不变量失败：{} {}'.format(name, detail))
        checks.append(name)

    with tempfile.TemporaryDirectory() as directory:
        blobs = Path(directory) / 'blobs'
        blobs.mkdir()
        static_report = collect(notebook_raw, blobs_dir=blobs, notebook_name='demo.ipynb')
        check('静态模式只产出静态会话', static_report['source_mode'] == 'static')
        # 对照：静态文档把被替换前的进度条和旧图也保留着。
        static_progress = _by_id(static_report, 'progress')
        check('静态模式保留已被清屏的旧进度条', 'Training 50%' in _cell_text(static_progress))

        report = collect(notebook_raw, events_raw, blobs_dir=blobs,
                         notebook_name='demo.ipynb', events_name='demo-events.json')
        check('完整事件流采集状态为 complete', report['status'] == 'complete', report['reasons'])
        check('采集策略声明不执行单元格与脚本',
              report['policy'] == {'execute_cells': False, 'run_html_or_scripts': False})

        # 场景一：clear_output(wait=True) 延迟清屏，旧进度条不再进入索引。
        progress = _by_id(report, 'progress')
        visible_text = _cell_text(progress)
        check('延迟清屏后只保留最终文本', visible_text == 'rerun line\n', visible_text)
        check('移除记录说明 wait 清屏与重跑替换',
              any('wait=true' in row['reason'] for row in report['removed_outputs']))
        check('旧屏幕内容（Training 50%）未混入索引', 'Training 50%' not in visible_text)

        # 场景二：跨 cell 的 display_id 更新，figure cell 显示的是 revision B。
        figure = _by_id(report, 'figure')
        png_b_sha = digest_bytes(base64.b64decode(PNG_B))
        png_a_sha = digest_bytes(base64.b64decode(PNG_A))
        check('figure 最终图片为更新后的 revision B',
              _image_shas(figure) == [png_b_sha], figure['reading']['images'])
        updater = _by_id(report, 'updater')
        check('更新不是新输出：updater 自身输出区为空', updater['status'] == 'cleared')
        check('figure 条目保留更新历史与原输出位置',
              _items(figure)[0]['update_history'] and
              _items(figure)[0]['provenance'][0]['cell_id'] == 'figure' and
              _items(figure)[0]['provenance'][-1]['cell_id'] == 'updater')
        check('所有 MIME 候选与选用理由均保留',
              all(item['candidates'] and item['selected_reason']
                  for item in _items(figure)))

        # 场景三：并行执行的事件交织，按 parent 隔离拼接。
        para = _by_id(report, 'para')
        parb = _by_id(report, 'parb')
        a_text = _cell_text(para)
        b_text = _cell_text(parb)
        check('并行 parent A 只含 A 片段', a_text == 'A-1\nA-2\n', a_text)
        check('并行 parent B 只含 B 片段', b_text == 'B-1\nB-2\n', b_text)
        check('每个并行 cell 只绑定一个最终 parent',
              len(para['areas']) == 1 and len(parb['areas']) == 1)

        # widget 缺依赖：显式说明而非空白成功。
        widget_cell = _by_id(report, 'widget-cell')
        check('未知 widget 状态显示缺依赖',
              any(w['status'] == 'dependency_missing'
                  for w in widget_cell['reading']['widgets']))
        check('widget 没有被当作成功文本', widget_cell['reading']['text'] == [])

        # 表格与文字分开，HTML 不执行；图片以引用留存到 blobs 目录。
        table_cell = _by_id(report, 'table-cell')
        check('表格引用单独分类', len(table_cell['reading']['tables']) == 1)
        check('文字与表格分开', len(table_cell['reading']['text']) == 1)
        check('图片以本地引用留存',
              all(ref['blob_path'] and Path(ref['blob_path']).exists()
                  for ref in report['cells'][2]['reading']['images']))

        # 重跑隔离：progress 重跑后，对旧 display_id 的更新不能应用到旧输出。
        edge_raw = fixtures['demo-events-edge.json']
        edge_report = collect(notebook_raw, edge_raw, blobs_dir=blobs,
                              notebook_name='demo.ipynb', events_name='demo-events-edge.json')
        check('针对已注销 display_id 的更新被拒绝',
              any('display_id' in row['reason'] for row in edge_report['unmatched_events']))
        check('存在不可应用更新时状态为 incomplete', edge_report['status'] == 'incomplete')
        edge_progress = _by_id(edge_report, 'progress')
        edge_text = _cell_text(edge_progress)
        check('旧输出未被另一次运行的更新改写', 'stale rewrite' not in edge_text)

        # 事件中途结束：截断文件给出明确不完整状态与原因。
        truncated_report = collect(notebook_raw, fixtures['demo-events-truncated.json'],
                                   blobs_dir=blobs, events_name='demo-events-truncated.json')
        check('截断事件流标记 incomplete', truncated_report['status'] == 'incomplete')
        check('截断原因明确', any('截断' in reason for reason in truncated_report['reasons']))

        # 重新提交身份不能仅凭 cell 序号：摘要不同即不同笔记本。
        tampered = json.loads(notebook_raw)
        tampered['cells'][2]['source'] = ['# changed content']
        changed_report = collect(json.dumps(tampered).encode(), events_raw,
                                 notebook_name='demo-copy.ipynb')
        check('内容变化产生不同笔记本摘要',
              changed_report['notebook_digest'] != report['notebook_digest'])
        check('来源位置使用 cell id 而非仅序号',
              all('cell_id' in cell for cell in report['cells']))

        # 旧能力（结构检查）仍可用于对照，但不冒充语义采集。
        structure_rows = inspect_bytes(notebook_raw)
        check('结构检查仍只报告 output_count',
              all(set(row) >= {'cell', 'id', 'kind', 'source', 'output_count'}
                  for row in structure_rows))

        return {
            'fixture': 'demo.ipynb / demo-events.json',
            'notebook_digest': report['notebook_digest'],
            'status': report['status'],
            'checks': checks,
            'scenarios': {
                'delayed_clear': {'removed_outputs': report['removed_outputs'],
                                  'final_progress_text': visible_text},
                'cross_cell_display_update': {
                    'cell': 'figure', 'image_sha256': png_b_sha,
                    'superseded_image_sha256': png_a_sha},
                'parallel_interleaving': {'para': a_text, 'parb': b_text}},
            'widgets': report['cells'][6]['reading']['widgets'],
            'tables': len(table_cell['reading']['tables']),
            'blob_dir': str(blobs),
            'edge_status': edge_report['status'],
            'truncated_status': truncated_report['status'],
        }


def main():
    print(json.dumps(run_demo(), ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == '__main__':
    main()
