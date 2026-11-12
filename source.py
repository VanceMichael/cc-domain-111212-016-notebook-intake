import json

def inspect_bytes(data):
    notebook = json.loads(data)
    if notebook.get('nbformat') != 4 or not isinstance(notebook.get('cells'), list):
        raise ValueError('只支持 nbformat 4 笔记本')
    result = []
    for index, cell in enumerate(notebook['cells']):
        source = cell.get('source', '')
        if isinstance(source, list):
            source = ''.join(source)
        result.append({'cell': index, 'id': cell.get('id'), 'kind': cell['cell_type'],
                       'source': source, 'output_count': len(cell.get('outputs', []))})
    return result
