import base64
import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest

import app
from intake import collect, digest_bytes, parse_events
from sampledata import PNG_A, PNG_B, samples
from storage import Archive

WIDGET_MIME = 'application/vnd.jupyter.widget-view+json'


def make_notebook(cells):
    return json.dumps({'nbformat': 4, 'nbformat_minor': 5, 'metadata': {},
                       'cells': cells}).encode()


def cell_spec(cell_id, cell_type='code', outputs=None, **extra):
    cell = {'id': cell_id, 'cell_type': cell_type, 'metadata': {},
            'source': []}
    if cell_type == 'code':
        cell['execution_count'] = extra.pop('execution_count', 1)
        cell['outputs'] = outputs or []
    cell.update(extra)
    return cell


def ev(session, parent, cell, msg_type, content, output_index=None):
    event = {'session': session, 'parent': parent, 'cell': cell,
             'msg_type': msg_type, 'content': content, '_source': 'events'}
    if output_index is not None:
        event['_output_index'] = output_index
    return event


def stream(parent, cell, text, session='s', name='stdout'):
    return ev(session, parent, cell, 'stream', {'name': name, 'text': text})


def display(parent, cell, data, display_id=None, session='s', msg_type='display_data'):
    content = {'data': data, 'metadata': {}, 'transient': {}}
    if display_id:
        content['transient'] = {'display_id': display_id}
    return ev(session, parent, cell, msg_type, content)


def update(parent, cell, display_id, data, session='s'):
    return ev(session, parent, cell, 'update_display_data',
              {'data': data, 'metadata': {}, 'transient': {'display_id': display_id}})


def clear(parent, cell, wait=False, session='s'):
    return ev(session, parent, cell, 'clear_output', {'wait': wait})


def find_cell(report, cell_id):
    return next(cell for cell in report['cells'] if cell['cell_id'] == cell_id)


def text_of(report, cell_id):
    return ''.join(part['content'] for part in find_cell(report, cell_id)['reading']['text'])


class IntakeSemanticsTests(unittest.TestCase):
    def setUp(self):
        self.notebook = make_notebook([cell_spec('c1'), cell_spec('c2')])

    def test_delayed_clear_waits_for_next_output(self):
        events = [stream('p1', 'c1', 'old-1\n'), clear('p1', 'c1', wait=True),
                  stream('p1', 'c1', 'new-1\n')]
        report = collect(self.notebook, json.dumps(events).encode())
        self.assertEqual(text_of(report, 'c1'), 'new-1\n')
        removed = report['removed_outputs']
        self.assertEqual(len(removed), 1)
        self.assertIn('wait=true', removed[0]['reason'])
        self.assertEqual(removed[0]['event_seq'], 2)
        self.assertEqual(report['status'], 'complete')

    def test_pending_clear_at_end_is_incomplete(self):
        events = [stream('p1', 'c1', 'old\n'), clear('p1', 'c1', wait=True)]
        report = collect(self.notebook, json.dumps(events).encode())
        self.assertEqual(report['status'], 'incomplete')
        self.assertTrue(any('wait=true' in reason for reason in report['reasons']))
        # 清屏尚未生效，旧内容仍然是当前可见结果。
        self.assertEqual(text_of(report, 'c1'), 'old\n')
        self.assertEqual(find_cell(report, 'c1')['status'], 'incomplete')

    def test_immediate_clear_removes_outputs(self):
        events = [stream('p1', 'c1', 'gone\n'), clear('p1', 'c1', wait=False)]
        report = collect(self.notebook, json.dumps(events).encode())
        cell = find_cell(report, 'c1')
        self.assertEqual(cell['status'], 'cleared')
        self.assertEqual(cell['reading']['text'], [])
        self.assertIn('立即清屏', report['removed_outputs'][0]['reason'])

    def test_cross_cell_display_update_and_history(self):
        events = [display('p1', 'c1', {'image/png': PNG_A, 'text/plain': 'A'}, 'fig'),
                  update('p2', 'c2', 'fig', {'image/png': PNG_B, 'text/plain': 'B'})]
        report = collect(self.notebook, json.dumps(events).encode())
        figure_cell = find_cell(report, 'c1')
        images = figure_cell['reading']['images']
        self.assertEqual(len(images), 1)
        self.assertEqual(images[0]['sha256'],
                         digest_bytes(base64.b64decode(PNG_B)))
        item = figure_cell['reading']['items'][0]
        self.assertEqual(len(item['update_history']), 1)
        self.assertEqual(item['update_history'][0]['parent'], 'p2')
        # 原输出位置与更新位置都被保留。
        self.assertEqual([o['cell_id'] for o in item['provenance']], ['c1', 'c2'])
        # updater 自身不产生新输出。
        self.assertEqual(find_cell(report, 'c2')['status'], 'cleared')

    def test_update_missing_display_id_is_incomplete_not_resurrection(self):
        events = [update('p2', 'c2', 'ghost', {'text/plain': 'x'})]
        report = collect(self.notebook, json.dumps(events).encode())
        self.assertEqual(report['status'], 'incomplete')
        self.assertTrue(any('display_id' in row['reason']
                            for row in report['unmatched_events']))
        self.assertEqual(text_of(report, 'c2'), '')

    def test_rerun_retirement_blocks_later_update_to_old_output(self):
        events = [display('p1', 'c1', {'text/plain': 'v1'}, 'd1'),
                  stream('p2', 'c1', 'rerun\n'),
                  update('p3', 'c2', 'd1', {'text/plain': 'stale'})]
        report = collect(self.notebook, json.dumps(events).encode())
        self.assertEqual(text_of(report, 'c1'), 'rerun\n')
        self.assertTrue(any('替换' in row['reason']
                            for row in report['removed_outputs']))
        self.assertTrue(any('d1' in str(row) for row in report['unmatched_events']))
        self.assertNotIn('stale', text_of(report, 'c1'))

    def test_parallel_parents_keep_streams_separate(self):
        events = [stream('pa', 'c1', 'a1\n'), stream('pb', 'c2', 'b1\n'),
                  stream('pa', 'c1', 'a2\n'), stream('pb', 'c2', 'b2\n')]
        report = collect(self.notebook, json.dumps(events).encode())
        self.assertEqual(text_of(report, 'c1'), 'a1\na2\n')
        self.assertEqual(text_of(report, 'c2'), 'b1\nb2\n')

    def test_consecutive_same_name_stream_fragments_join(self):
        events = [stream('p1', 'c1', 'part1'), stream('p1', 'c1', 'part2')]
        report = collect(self.notebook, json.dumps(events).encode())
        items = find_cell(report, 'c1')['reading']['items']
        self.assertEqual(len(items), 1)
        self.assertEqual(text_of(report, 'c1'), 'part1part2')
        self.assertEqual(len(items[0]['provenance']), 2)

    def test_stream_names_do_not_merge(self):
        events = [ev('s', 'p1', 'c1', 'stream', {'name': 'stdout', 'text': 'out'}),
                  ev('s', 'p1', 'c1', 'stream', {'name': 'stderr', 'text': 'err'})]
        report = collect(self.notebook, json.dumps(events).encode())
        self.assertEqual(len(find_cell(report, 'c1')['reading']['items']), 2)

    def test_error_output_is_text_and_clears_pending_wait(self):
        events = [clear('p1', 'c1', wait=True),
                  ev('s', 'p1', 'c1', 'error',
                     {'ename': 'ValueError', 'evalue': 'bad',
                      'traceback': ['Traceback ...', 'ValueError: bad']})]
        report = collect(self.notebook, json.dumps(events).encode())
        self.assertEqual(report['status'], 'complete')
        self.assertIn('ValueError: bad', text_of(report, 'c1'))
        self.assertEqual(find_cell(report, 'c1')['reading']['items'][0]['kind'], 'error')

    def test_session_scoped_display_ids(self):
        events = [display('p1', 'c1', {'text/plain': 's1'}, 'd', session='s1'),
                  update('p2', 'c2', 'd', {'text/plain': 's2-rewrite'}, session='s2')]
        report = collect(self.notebook, json.dumps(events).encode())
        # s2 的更新找不到 s2 会话内的 display_id，不能影响 s1 的输出。
        self.assertEqual(text_of(report, 'c1'), 's1')
        self.assertTrue(any(row.get('display_id') == 'd'
                            for row in report['unmatched_events']))

    def test_mime_bundle_keeps_candidates_reasons_and_separates_kinds(self):
        data = {'text/plain': '  k\n0 v',
                'text/html': '<table><tr><td>v</td></tr></table>',
                'image/png': PNG_A,
                WIDGET_MIME: {'model_id': 'm'}}
        events = [display('p1', 'c1', data)]
        report = collect(self.notebook, json.dumps(events).encode())
        cell = find_cell(report, 'c1')
        item = cell['reading']['items'][0]
        self.assertEqual({c['mime'] for c in item['candidates']}, set(data))
        self.assertTrue(all(c['reason'] for c in item['candidates']))
        self.assertEqual(item['selected_mime'], 'text/plain')
        self.assertIn('阅读偏好', item['selected_reason'])
        self.assertEqual(len(cell['reading']['tables']), 1)
        self.assertEqual(len(cell['reading']['images']), 1)
        self.assertEqual(cell['reading']['widgets'][0]['status'],
                         'dependency_missing')
        self.assertEqual(item['status'], 'ok')

    def test_widget_only_bundle_is_dependency_missing_not_blank_success(self):
        events = [display('p1', 'c1', {WIDGET_MIME: {'model_id': 'm'}})]
        report = collect(self.notebook, json.dumps(events).encode())
        cell = find_cell(report, 'c1')
        self.assertEqual(cell['reading']['text'], [])
        self.assertEqual(cell['reading']['widgets'][0]['status'],
                         'dependency_missing')
        self.assertEqual(cell['reading']['items'][0]['status'],
                         'dependency_missing')

    def test_html_is_never_executed_and_kept_as_source(self):
        events = [display('p1', 'c1',
                          {'text/html': '<script>alert(1)</script><b>x</b>',
                           'text/plain': 'safe text'})]
        report = collect(self.notebook, json.dumps(events).encode())
        item = find_cell(report, 'c1')['reading']['items'][0]
        html_candidate = next(c for c in item['candidates'] if c['mime'] == 'text/html')
        self.assertIn('不执行', html_candidate['reason'])
        self.assertEqual(report['policy']['run_html_or_scripts'], False)
        self.assertEqual(text_of(report, 'c1'), 'safe text')

    def test_configurable_preference(self):
        events = [display('p1', 'c1', {'text/plain': 'p', 'text/markdown': 'md'})]
        report = collect(self.notebook, json.dumps(events).encode(),
                         preference=['text/plain', 'text/markdown'])
        self.assertEqual(find_cell(report, 'c1')['reading']['items'][0]
                         ['selected_mime'], 'text/plain')

    def test_image_blob_reference_uses_digest_and_dedupes(self):
        events = [display('p1', 'c1', {'image/png': PNG_A}),
                  display('p1', 'c2', {'image/png': PNG_A})]
        with tempfile.TemporaryDirectory() as directory:
            report = collect(self.notebook, json.dumps(events).encode(),
                             blobs_dir=directory)
            paths = [ref['blob_path']
                     for cell in report['cells'] for ref in cell['reading']['images']]
            self.assertEqual(len(paths), 2)
            self.assertEqual(len(set(paths)), 1)
            self.assertTrue(Path(paths[0]).exists())

    def test_provenance_points_back_to_static_output_position(self):
        notebook = make_notebook([
            cell_spec('c1', outputs=[
                {'output_type': 'stream', 'name': 'stdout', 'text': ['line\n']},
                {'output_type': 'display_data',
                 'data': {'text/plain': 'x'}, 'metadata': {},
                 'transient': {'display_id': 'd'}}])])
        report = collect(notebook)
        item = find_cell(report, 'c1')['reading']['items'][0]
        self.assertEqual(item['provenance'][0],
                         {'source': 'static', 'session': report['cells'][0]
                          ['areas'][0]['session'], 'parent': 'c1:exec1',
                          'cell_id': 'c1', 'cell_index': 0,
                          'event_seq': 0, 'output_index': 0})

    def test_static_mode_replays_saved_outputs_including_display_updates(self):
        notebook = make_notebook([
            cell_spec('c1', outputs=[
                {'output_type': 'display_data', 'data': {'text/plain': 'old'},
                 'metadata': {}, 'transient': {'display_id': 'd'}}]),
            cell_spec('c2', outputs=[
                {'output_type': 'update_display_data',
                 'data': {'text/plain': 'new'}, 'metadata': {},
                 'transient': {'display_id': 'd'}}])])
        report = collect(notebook)
        self.assertEqual(text_of(report, 'c1'), 'new')
        self.assertEqual(find_cell(report, 'c2')['status'], 'cleared')

    def test_truncated_events_are_salvaged_and_marked_incomplete(self):
        raw = json.dumps([stream('p1', 'c1', 'kept\n')]).encode()
        raw = raw[:-1] + b', '
        events, truncated = parse_events(raw)
        self.assertTrue(truncated)
        self.assertEqual(len(events), 1)
        report = collect(self.notebook, raw)
        self.assertEqual(report['status'], 'incomplete')
        self.assertTrue(any('截断' in reason for reason in report['reasons']))
        self.assertEqual(text_of(report, 'c1'), 'kept\n')

    def test_malformed_event_is_skipped_not_fatal(self):
        events = [stream('p1', 'c1', 'ok\n'), {'msg_type': 'stream'}]
        report = collect(self.notebook, json.dumps(events).encode())
        self.assertEqual(text_of(report, 'c1'), 'ok\n')
        self.assertEqual(report['status'], 'incomplete')
        self.assertTrue(any('结构不完整' in row['reason']
                            for row in report['unmatched_events']))

    def test_unknown_parent_cell_is_reported_not_attached(self):
        events = [stream('p1', 'ghost', 'x\n')]
        report = collect(self.notebook, json.dumps(events).encode())
        self.assertEqual(report['status'], 'incomplete')
        self.assertTrue(any('ghost' in row.get('cell_id', '')
                            for row in report['unmatched_events']))

    def test_identity_uses_digest_not_cell_sequence(self):
        changed = json.loads(self.notebook)
        changed['cells'][0]['source'] = ['# different']
        first = collect(self.notebook)
        second = collect(json.dumps(changed).encode())
        self.assertNotEqual(first['notebook_digest'], second['notebook_digest'])
        resubmitted = collect(self.notebook)
        self.assertEqual(resubmitted['notebook_digest'], first['notebook_digest'])

    def test_demo_fixtures_cover_required_scenarios(self):
        fixtures = samples()
        report = collect(fixtures['demo.ipynb'], fixtures['demo-events.json'])
        self.assertEqual(report['status'], 'complete')
        reasons = ' '.join(row['reason'] for row in report['removed_outputs'])
        self.assertIn('wait=true', reasons)
        figure = find_cell(report, 'figure')
        self.assertEqual(figure['reading']['images'][0]['sha256'],
                         digest_bytes(base64.b64decode(PNG_B)))
        edge = collect(fixtures['demo.ipynb'], fixtures['demo-events-edge.json'])
        self.assertEqual(edge['status'], 'incomplete')
        truncated = collect(fixtures['demo.ipynb'],
                            fixtures['demo-events-truncated.json'])
        self.assertEqual(truncated['status'], 'incomplete')

    def test_archive_index_dedupes_by_digest(self):
        with tempfile.TemporaryDirectory() as directory:
            db = Path(directory) / 'idx.sqlite3'
            events_raw = json.dumps([stream('p1', 'c1', 'x\n')]).encode()
            report = collect(self.notebook, events_raw)
            with Archive(db) as archive:
                first = archive.save_index('src', report, self.notebook, events_raw,
                                           notebook_name='nb.ipynb')
                again = archive.save_index('src', report, self.notebook, events_raw)
                self.assertEqual(first, again)
                listing = archive.list_indexes()
                self.assertEqual(len(listing), 1)
                self.assertEqual(listing[0]['notebook_digest'],
                                 report['notebook_digest'])
            changed = json.loads(self.notebook)
            changed['cells'][0]['source'] = ['changed']
            changed_raw = json.dumps(changed).encode()
            with Archive(db) as archive:
                other = archive.save_index(
                    'src', collect(changed_raw, events_raw), changed_raw, events_raw)
                self.assertNotEqual(first, other)
                self.assertEqual(len(archive.list_indexes()), 2)

    def test_cli_index_and_intake_demo(self):
        fixtures = samples()
        with tempfile.TemporaryDirectory() as directory:
            nb_path = Path(directory) / 'demo.ipynb'
            events_path = Path(directory) / 'demo-events.json'
            blobs = Path(directory) / 'blobs'
            db = str(Path(directory) / 'idx.sqlite3')
            nb_path.write_bytes(fixtures['demo.ipynb'])
            events_path.write_bytes(fixtures['demo-events.json'])
            with contextlib.redirect_stdout(io.StringIO()) as output:
                code = app.main(['index', str(nb_path), '--events', str(events_path),
                                 '--blobs', str(blobs), '--database', db,
                                 '--source', 'training-dept'])
            self.assertEqual(code, 0)
            payload = json.loads(output.getvalue())
            self.assertEqual(payload['status'], 'complete')
            self.assertTrue(any(blobs.iterdir()))
            with contextlib.redirect_stdout(io.StringIO()) as output:
                self.assertEqual(app.main(['intake-demo']), 0)
            self.assertGreater(len(json.loads(output.getvalue())['checks']), 20)


if __name__ == '__main__':
    unittest.main()
