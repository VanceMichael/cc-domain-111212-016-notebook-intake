import argparse
import base64
import json
from pathlib import Path
import sys
import tempfile

from intake import collect
from source import inspect_bytes
from sampledata import samples
from storage import Archive
from transport import ReplaySource, fetch

MAX_INPUT = 16*1024*1024

def read_input(path):
    with Path(path).open('rb') as handle:
        data = handle.read(MAX_INPUT+1)
    if len(data) > MAX_INPUT:
        raise ValueError('输入超过接收限制')
    return data

def demo():
    fixtures = samples()
    name = next(key for key in fixtures if key.startswith('sample.'))
    data = fixtures[name]
    routes = {'GET /sample':[{'base64':base64.b64encode(data).decode()}]}
    with tempfile.TemporaryDirectory() as directory, ReplaySource(routes) as server:
        status, headers, raw = fetch(server.url + '/sample')
        if status != 200:
            raise ValueError('来源请求失败')
        rows = inspect_bytes(raw)
        with Archive(Path(directory)/'archive.sqlite3') as archive:
            first = archive.save('local-demo', raw, rows)
            repeated = archive.save('local-demo', raw, rows)
            return dict(input=name, bytes=len(raw), captures=len(archive.list()),
                        same_capture=first==repeated, rows=rows, requests=len(server.requests))

def main(argv=None):
    parser = argparse.ArgumentParser(description='来源检查与原件留存')
    commands = parser.add_subparsers(dest='command', required=True)
    for name in ('inspect', 'capture'):
        command = commands.add_parser(name)
        command.add_argument('path')
        if name == 'capture':
            command.add_argument('--database', required=True)
            command.add_argument('--source', required=True)
    listing = commands.add_parser('list')
    listing.add_argument('--database', required=True)
    fixtures = commands.add_parser('fixtures')
    fixtures.add_argument('directory')
    index = commands.add_parser('index', description='按输出显示语义采集最终可见结果')
    index.add_argument('path')
    index.add_argument('--events', help='本地记录的 IOPub 事件 JSON；缺省只使用静态 nbformat 输出')
    index.add_argument('--database', help='可选：把采集报告按笔记本摘要写入索引库')
    index.add_argument('--source', help='写入索引库时的来源标识')
    index.add_argument('--blobs', help='可选目录：把图片候选以摘要文件名落盘为引用')
    index.add_argument('--preference', help='逗号分隔的 MIME 阅读偏好顺序覆盖')
    commands.add_parser('intake-demo')
    commands.add_parser('demo')
    args = parser.parse_args(argv)
    try:
        if args.command == 'demo':
            result = demo()
        elif args.command == 'intake-demo':
            from demo import run_demo
            result = run_demo()
        elif args.command == 'fixtures':
            destination = Path(args.directory)
            destination.mkdir(parents=True, exist_ok=True)
            for name, raw in samples().items():
                path = destination/name
                if path.exists():
                    raise ValueError('不覆盖已有夹具文件：'+name)
                path.write_bytes(raw)
            result = dict(files=sorted(samples()))
        elif args.command == 'list':
            with Archive(args.database) as archive:
                result = archive.list()
        elif args.command == 'index':
            raw = read_input(args.path)
            events_raw = Path(args.events).read_bytes() if args.events else None
            if len(events_raw or b'') > MAX_INPUT:
                raise ValueError('事件输入超过接收限制')
            preference = [item.strip() for item in args.preference.split(',')] \
                if args.preference else None
            if args.blobs:
                Path(args.blobs).mkdir(parents=True, exist_ok=True)
            result = collect(raw, events_raw, preference=preference,
                             blobs_dir=args.blobs, notebook_name=Path(args.path).name,
                             events_name=Path(args.events).name if args.events else None)
            if args.database:
                if not args.source:
                    raise ValueError('写入索引库需要 --source')
                with Archive(args.database) as archive:
                    result = dict(index_id=archive.save_index(
                        args.source, result, raw, events_raw,
                        notebook_name=Path(args.path).name),
                        status=result['status'], notebook_digest=result['notebook_digest'])
        else:
            raw = read_input(args.path)
            rows = inspect_bytes(raw)
            if args.command == 'inspect':
                result = rows
            else:
                with Archive(args.database) as archive:
                    result = dict(capture_id=archive.save(args.source,raw,rows), rows=len(rows))
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
        return 0
    except Exception as error:
        print(json.dumps(dict(error=type(error).__name__, reason=str(error)), ensure_ascii=False), file=sys.stderr)
        return 2

if __name__ == '__main__':
    raise SystemExit(main())
