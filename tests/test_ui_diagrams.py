# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright 2026 Kumar Muthuraman
import asyncio
import json
import shutil
from urllib.parse import urlencode

import pytest

from teamrunner.build import load_team
from teamrunner.engine import Engine
from teamrunner.flowgraph import counted_graph, declared_graph
from teamrunner.new import EXAMPLES, copy_from
from teamrunner.run import Options, run_team
from teamrunner.ui import diagrams
from test_engine import FACT_CHECKED, Scripted
from test_flow import CHECKER
from test_validate import debate
from test_ui import running, get, post


def test_nested_counted_flow_matches_engine(tmp_path):
    folder = debate(tmp_path, **{'chair.md':FACT_CHECKED,'fact_checker.md':CHECKER})
    team,_ = load_team(folder)
    actor = Scripted({'fact_checker':[{'verdict':'fail'}]*12})
    path=[]
    asyncio.run(Engine(team,actor,lambda e:None,trace=path.append).run())
    graph = counted_graph(declared_graph(team),path)
    arcs = {(e['source'],e['target']) for e in graph['edges']}
    assert all(pair in arcs for pair in zip(path,path[1:]))
    assert sum(e['count'] for e in graph['edges']) == len(path)-1
    assert any(e['count'] > 1 for e in graph['edges'])
    assert any('limit 3' in e['labels'] for e in graph['edges'])


def history(tmp_path):
    team,_ = copy_from(EXAMPLES/'round_robin',tmp_path/'debate')
    a = run_team(team,Options(rehearse=True,quiet=True)).folder
    member = team/'03_judge.md'
    member.write_text(member.read_text()+'\nA new instruction.\n')
    b = run_team(team,Options(rehearse=True,quiet=True)).folder
    return team,a.relative_to(tmp_path).as_posix(),b.relative_to(tmp_path).as_posix()


def test_comparison_uses_recorded_source_and_paths(tmp_path):
    team,a,b = history(tmp_path)
    (team/'03_judge.md').write_text('current file must not be used')
    with running(tmp_path) as server:
        status,_,body = get(server,'/api/compare?'+urlencode({'left':a,'right':b}))
        assert status == 200
        data=json.loads(body)
        changed=[m for m in data['members'] if m['status']=='changed']
        assert len(changed)==1 and changed[0]['exact_source']
        assert '+A new instruction.' in changed[0]['diff']
        assert 'current file' not in body.decode()
        assert changed[0]['before']['version']['number']==1
        assert changed[0]['after']['version']['number']==2
        assert 'Recorded engine path' in data['left_path']['note']
        assert any(e['count'] for e in data['left_path']['graph']['edges'])
        assert get(server,'/api/compare?left=../outside&right='+b)[0]==403
        for asset in ['/vendor/mermaid.js','/diagrams.js']:
            assert get(server,asset,token=False)[0]==403
            assert get(server,asset)[0]==200


def test_legacy_and_damaged_graph_are_explicit():
    data={'events':[{'event':'run_started','flow_graph':{'nodes':[None],'edges':[]}},
                    {'event':'agent_message','from':'old member'},
                    {'event':'agent_message','from':'other member'},
                    {'event':'run_finished','flow_path':['bad']}]}
    result=diagrams.path_view(data)
    assert 'Observed completed turns only' in result['note']
    assert result['graph']['edges'][0]['count']==1
    assert 'old member' in result['mermaid']


def test_author_uses_same_mermaid(tmp_path):
    from teamrunner.explain import mermaid
    team,_=copy_from(EXAMPLES/'round_robin',tmp_path/'debate')
    loaded,_=load_team(team)
    with running(tmp_path) as server:
        status,_,body=get(server,'/api/diagram?path=debate/chair.md')
        assert status==200
        assert json.loads(body)['mermaid']==mermaid(loaded)


def test_saved_team_copy_preserves_history_and_restamps(tmp_path):
    team,a,b=history(tmp_path)
    saved=tmp_path/a/'resolved'
    shutil.copytree(team,saved,ignore=shutil.ignore_patterns('runs'))
    original=(saved/'team.yaml').read_bytes()
    with running(tmp_path) as server:
        status,data=post(server,'/api/restore-team',{'path':a,'destination':'saved_copy'})
        assert status==200 and data['path']=='saved_copy/team.yaml'
        copied=(tmp_path/data['path']).read_text()
        assert 'parent_id:' in copied
        assert (saved/'team.yaml').read_bytes()==original
        assert post(server,'/api/restore-team',{'path':a,'destination':'../escape'})[0]==403
        assert post(server,'/api/restore-team',{'path':a,'destination':'debate/nested'})[0]==400
        assert post(server,'/api/restore-team',{'path':b,'destination':'missing'})[0]==404
        (saved/'badlink').symlink_to(team,target_is_directory=True)
        assert post(server,'/api/restore-team',{'path':a,'destination':'links'})[0]==403


def test_stopped_path_has_no_invented_finish(tmp_path):
    from threading import Event
    from teamrunner.ui.runviews import detail
    team,_=copy_from(EXAMPLES/'round_robin',tmp_path/'debate')
    cancel=Event()
    result=run_team(team,Options(rehearse=True,quiet=True),cancel=cancel,on_turn=lambda turn:cancel.set())
    data=detail(tmp_path,result.folder.relative_to(tmp_path).as_posix())
    path=diagrams.path_view(data)
    assert result.status=='stopped'
    assert 'Recorded engine path' in path['note']
    assert not any(n['visits'] for n in path['graph']['nodes'] if n['kind']=='end')
    assert sum(e['count'] for e in path['graph']['edges'])==1


def test_untrusted_graph_labels_are_quoted_and_source_hash_checked(tmp_path):
    from teamrunner.flowgraph import mermaid_graph
    graph={'nodes':[{'id':'anything"\n%%{init:bad}%%','label':'"\n<script>alert(1)</script>','kind':'worker'}],'edges':[]}
    text=mermaid_graph(graph)
    assert '<script>' not in text and '%%{init:' not in text
    team,a,b=history(tmp_path)
    log=tmp_path/a/'events.jsonl'
    events=[json.loads(line) for line in log.read_text().splitlines()]
    next(e for e in events if e['event']=='run_started')['members'][0]['source_text']='tampered'
    log.write_text(''.join(json.dumps(e)+'\n' for e in events))
    compared=diagrams.compare(tmp_path,a,b)
    assert any(not m['exact_source'] for m in compared['members'])
