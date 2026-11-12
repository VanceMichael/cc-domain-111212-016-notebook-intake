import json

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
    return {'sample.ipynb':json.dumps(notebook).encode(),'events.json':json.dumps(events).encode(),
            'invalid.ipynb':b'{"nbformat":3,"cells":[]}'}
