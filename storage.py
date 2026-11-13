import hashlib
import json
import sqlite3

class Archive:
    def __init__(self, path):
        self.connection = sqlite3.connect(path, timeout=5)
        self.connection.execute('PRAGMA foreign_keys=ON')
        self.connection.executescript(
            'CREATE TABLE IF NOT EXISTS captures('
            'id INTEGER PRIMARY KEY, source TEXT NOT NULL, digest TEXT NOT NULL,'
            'raw BLOB NOT NULL, rows_json TEXT NOT NULL, UNIQUE(source,digest));'
            'CREATE TABLE IF NOT EXISTS indexes('
            'id INTEGER PRIMARY KEY, source TEXT NOT NULL,'
            'notebook_digest TEXT NOT NULL, notebook_name TEXT,'
            'events_digest TEXT, report_json TEXT NOT NULL,'
            'created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,'
            'UNIQUE(source,notebook_digest,events_digest));')

    def save(self, source, raw, rows):
        if not source.strip():
            raise ValueError('来源标识不能为空')
        digest = hashlib.sha256(raw).hexdigest()
        payload = json.dumps(rows, ensure_ascii=False, sort_keys=True)
        with self.connection:
            self.connection.execute(
                'INSERT OR IGNORE INTO captures(source,digest,raw,rows_json) VALUES(?,?,?,?)',
                (source, digest, raw, payload))
            record = self.connection.execute(
                'SELECT id FROM captures WHERE source=? AND digest=?', (source, digest)).fetchone()
        return record[0]

    def list(self):
        return [dict(id=row[0], source=row[1], digest=row[2], rows=json.loads(row[3]))
                for row in self.connection.execute(
                    'SELECT id,source,digest,rows_json FROM captures ORDER BY id')]

    def raw(self, capture_id):
        row = self.connection.execute('SELECT raw FROM captures WHERE id=?', (capture_id,)).fetchone()
        if row is None:
            raise KeyError(capture_id)
        return bytes(row[0])

    def save_index(self, source, report, notebook_raw, events_raw=None, notebook_name=None):
        """语义采集索引按来源 + 笔记本摘要 + 事件摘要去重，cell 序号不参与身份判断。"""
        if not source.strip():
            raise ValueError('来源标识不能为空')
        notebook_digest = report.get('notebook_digest') or \
            'sha256:' + hashlib.sha256(notebook_raw).hexdigest()
        events_digest = 'sha256:' + hashlib.sha256(events_raw).hexdigest() \
            if events_raw is not None else None
        payload = json.dumps(report, ensure_ascii=False, sort_keys=True)
        if events_digest is None:
            lookup = ('SELECT id FROM indexes WHERE source=? AND notebook_digest=? '
                      'AND events_digest IS NULL')
            parameters = (source, notebook_digest)
        else:
            lookup = ('SELECT id FROM indexes WHERE source=? AND notebook_digest=? '
                      'AND events_digest=?')
            parameters = (source, notebook_digest, events_digest)
        with self.connection:
            self.connection.execute(
                'INSERT OR IGNORE INTO indexes(source,notebook_digest,notebook_name,'
                'events_digest,report_json) VALUES(?,?,?,?,?)',
                (source, notebook_digest, notebook_name, events_digest, payload))
            record = self.connection.execute(lookup, parameters).fetchone()
        return record[0]

    def list_indexes(self):
        return [dict(id=row[0], source=row[1], notebook_digest=row[2],
                     notebook_name=row[3], events_digest=row[4],
                     report=json.loads(row[5]), created_at=row[6])
                for row in self.connection.execute(
                    'SELECT id,source,notebook_digest,notebook_name,events_digest,'
                    'report_json,created_at FROM indexes ORDER BY id')]

    def close(self):
        self.connection.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()
