import json

# 两个内容不同的 1x1 PNG，用于演示 display_id 更新前后图片引用确实发生替换。
PNG_A = ('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==')
PNG_B = ('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==')
WIDGET_MIME = 'application/vnd.jupyter.widget-view+json'


def demo_notebook():
    """含进度条、图片、并行输出、widget 与表格的虚构讲师笔记本。

    静态 outputs 保留“保存时”的旧屏幕内容；events.json 才反映讲师屏幕上
    最终可见的结果，两者对照可验证索引不再收录已消失内容。
    """
    return {'nbformat': 4, 'nbformat_minor': 5, 'metadata': {}, 'cells': [
        {'id': 'lesson', 'cell_type': 'markdown', 'metadata': {},
         'source': ['# 培训演示：输出显示语义采集']},
        {'id': 'progress', 'cell_type': 'code', 'metadata': {}, 'execution_count': 1,
         'source': ['for step in range(3):\n', '    print(step)\n',
                    'clear_output(wait=True)\n', "print('Training 100%')"],
         'outputs': [
             {'output_type': 'stream', 'name': 'stdout', 'text': ['Training 0%\n']},
             {'output_type': 'stream', 'name': 'stdout', 'text': ['Training 50%\n']},
             {'output_type': 'stream', 'name': 'stdout', 'text': ['Training 100%\n']},
             {'output_type': 'display_data',
              'data': {'image/png': PNG_A, 'text/plain': ['<progress figure>']},
              'metadata': {}, 'transient': {'display_id': 'progress-fig'}}]},
        {'id': 'figure', 'cell_type': 'code', 'metadata': {}, 'execution_count': 2,
         'source': ['display(fig, display_id="fig-1")'],
         'outputs': [{'output_type': 'display_data',
                      'data': {'image/png': PNG_A, 'text/plain': ['fig revision A']},
                      'metadata': {}, 'transient': {'display_id': 'fig-1'}}]},
        {'id': 'updater', 'cell_type': 'code', 'metadata': {}, 'execution_count': 3,
         'source': ['update_display_data(..., display_id="fig-1")'], 'outputs': []},
        {'id': 'para', 'cell_type': 'code', 'metadata': {}, 'execution_count': 4,
         'source': ["print('A-1'); ..."], 'outputs': []},
        {'id': 'parb', 'cell_type': 'code', 'metadata': {}, 'execution_count': 5,
         'source': ["print('B-1'); ..."], 'outputs': []},
        {'id': 'widget-cell', 'cell_type': 'code', 'metadata': {}, 'execution_count': 6,
         'source': ['IntSlider()'],
         'outputs': [{'output_type': 'display_data',
                      'data': {WIDGET_MIME: {'model_id': 'demo-model', 'version_major': 2,
                                             'version_minor': 1}},
                      'metadata': {}}]},
        {'id': 'table-cell', 'cell_type': 'code', 'metadata': {}, 'execution_count': 7,
         'source': ['display(df)'],
         'outputs': [{'output_type': 'display_data',
                      'data': {'text/html': ['<table><tr><th>k</th></tr><tr><td>v</td></tr></table>'],
                               'text/plain': ['  k\n0 v']},
                      'metadata': {}}]}]}


def demo_events():
    """本地记录的 IOPub 事件：演示延迟清屏、跨 cell 更新与并行交织。"""
    session = 'lesson-session'

    def ev(parent, cell, msg_type, content, source='events', output_index=None):
        event = {'session': session, 'parent': parent, 'cell': cell,
                 'msg_type': msg_type, 'content': content, '_source': source}
        if output_index is not None:
            event['_output_index'] = output_index
        return event

    return [
        ev('p-prog1', 'progress', 'stream', {'name': 'stdout', 'text': 'Training 0%\n'}),
        ev('p-prog1', 'progress', 'stream', {'name': 'stdout', 'text': 'Training 50%\n'}),
        ev('p-prog1', 'progress', 'clear_output', {'wait': True}),
        # wait=true：直到本执行下一条输出才真正清屏，两条进度条在此刻被移除。
        ev('p-prog1', 'progress', 'stream', {'name': 'stdout', 'text': 'Training 100%\n'}),
        ev('p-prog1', 'progress', 'display_data',
           {'data': {'image/png': PNG_A, 'text/plain': '<progress figure>'},
            'metadata': {}, 'transient': {'display_id': 'progress-fig'}}),
        ev('p-fig', 'figure', 'display_data',
           {'data': {'image/png': PNG_A, 'text/plain': 'fig revision A'},
            'metadata': {}, 'transient': {'display_id': 'fig-1'}}),
        # 跨 cell 更新：updater 的 parent 改写 figure cell 中注册的 display_id。
        ev('p-upd', 'updater', 'update_display_data',
           {'data': {'image/png': PNG_B, 'text/plain': 'fig revision B'},
            'metadata': {}, 'transient': {'display_id': 'fig-1'}}),
        # 两个 parent 并行执行，事件在流中交织；拼接只发生在同一 parent 内部。
        ev('p-a', 'para', 'stream', {'name': 'stdout', 'text': 'A-1\n'}),
        ev('p-b', 'parb', 'stream', {'name': 'stdout', 'text': 'B-1\n'}),
        ev('p-a', 'para', 'stream', {'name': 'stdout', 'text': 'A-2\n'}),
        ev('p-b', 'parb', 'stream', {'name': 'stdout', 'text': 'B-2\n'}),
        ev('p-wid', 'widget-cell', 'display_data',
           {'data': {WIDGET_MIME: {'model_id': 'demo-model', 'version_major': 2,
                                   'version_minor': 1}}, 'metadata': {}}),
        ev('p-tab', 'table-cell', 'display_data',
           {'data': {'text/html': '<table><tr><th>k</th></tr><tr><td>v</td></tr></table>',
                     'text/plain': '  k\n0 v'}, 'metadata': {}}),
        # progress cell 重新执行：新 parent 替换旧输出区，progress-fig 随之注销。
        ev('p-prog2', 'progress', 'stream', {'name': 'stdout', 'text': 'rerun line\n'})]


def demo_edge_events():
    """在主事件流之后追加一条针对旧 display_id 的更新：目标已不存活，不能复活。"""
    events = demo_events()
    events.append({'session': 'lesson-session', 'parent': 'p-stale', 'cell': 'updater',
                   'msg_type': 'update_display_data',
                   'content': {'data': {'text/plain': 'stale rewrite'},
                               'metadata': {}, 'transient': {'display_id': 'progress-fig'}}})
    return events


def samples():
    notebook = {'nbformat':4,'nbformat_minor':5,'metadata':{},'cells':[
        {'id':'intro','cell_type':'markdown','metadata':{},'source':['# Lesson']},
        {'id':'calc','cell_type':'code','metadata':{},'execution_count':1,'source':['print(1)'],
         'outputs':[{'output_type':'stream','name':'stdout','text':['1','\\n']}]}]}
    events = [
        {'session':'s1','parent':'p1','cell':'calc','msg_type':'display_data','content':{'data':{'text/plain':'old'},'transient':{'display_id':'d1'}}},
        {'session':'s1','parent':'p1','cell':'calc','msg_type':'clear_output','content':{'wait':True}},
        {'session':'s1','parent':'p2','cell':'another','msg_type':'update_display_data','content':{'data':{'text/plain':'new'},'transient':{'display_id':'d1'}}},
        {'session':'s1','parent':'p1','cell':'calc','msg_type':'stream','content':{'name':'stdout','text':'done'}}]
    demo_book = demo_notebook()
    edge_events = demo_edge_events()
    truncated = json.dumps(edge_events, ensure_ascii=False)
    truncated = truncated[:truncated.rindex('"msg_type"') - 4].rstrip().rstrip(',')
    return {'sample.ipynb':json.dumps(notebook).encode(),'events.json':json.dumps(events).encode(),
            'invalid.ipynb':b'{"nbformat":3,"cells":[]}',
            'demo.ipynb': json.dumps(demo_book, ensure_ascii=False).encode(),
            'demo-events.json': json.dumps(demo_events(), ensure_ascii=False).encode(),
            'demo-events-edge.json': json.dumps(edge_events, ensure_ascii=False).encode(),
            'demo-events-truncated.json': truncated.encode()}
