"""Regression checks for the first UI usability review."""

import json
import shutil
from urllib.parse import urlencode

import pytest

from teamrunner.new import EXAMPLES, copy_from
from teamrunner.ui import api, runviews
from test_ui import get, post, running, recorded_runs


@pytest.mark.parametrize("relative", ["Research/runs/study1", "runs"])
def test_package_beneath_unrelated_runs_folder_remains_editable(tmp_path, relative):
    root = tmp_path / relative
    copy_from(EXAMPLES / 'round_robin', root)
    with running(root) as server:
        status, _, body = get(server, '/api/file?path=01_for.md')
        file = json.loads(body)
        assert status == 200 and not file['readonly']
        assert post(server, '/api/save', {**file, 'text':file['text']+'\nAn edit.\n'})[0] == 200
        assert post(server, '/api/validate', {'path':''})[0] == 200
        assert post(server, '/api/member', {'path':'researcher.md'})[0] == 200
        assert post(server, '/api/new', {'path':'new_team'})[0] == 200
        (root/'runs'/'old').mkdir(parents=True)
        (root/'runs'/'old'/'output.md').write_text('a record')
        file = json.loads(get(server,'/api/file?path=runs/old/output.md')[2])
        assert file['readonly']
        assert post(server, '/api/save', {**file, 'text':'altered'})[0] == 403


def test_direct_snapshot_subtree_is_still_readonly(tmp_path):
    record = tmp_path/'runs'/'record'
    root = record/'resolved'
    root.mkdir(parents=True)
    (record/'run.json').write_text('{}')
    (root/'notes.md').write_text('original')
    with pytest.raises(api.OutsideRoot):
        api.editable(root, 'notes.md')


def test_non_text_error_and_invalid_member_do_not_write_files(tmp_path):
    root, _ = copy_from(EXAMPLES/'round_robin', tmp_path/'team')
    (root/'binary.md').write_bytes(b'\xff\x00')
    with running(root) as server:
        status, _, body = get(server, '/api/file?path=binary.md')
        assert status == 400 and 'plain text' in json.loads(body)['error']
        assert 'codec' not in body.decode()
        for name in ['my agent.md', '123.md', 'done.md', 'bad-name.md']:
            status, _ = post(server, '/api/member', {'path':name})
            assert status == 400 and not (root/name).exists()
        assert post(server, '/api/member', {'path':'good_agent2.md'})[0] == 200
        assert post(server, '/api/member', {'path':'02_agent.md'})[0] == 200


def test_case_only_rename_and_real_collision(tmp_path):
    root, _ = copy_from(EXAMPLES/'round_robin', tmp_path/'team')
    path = root/'researcher.md'
    path.write_text('original')
    file = api.read_file(root, path.name)
    result = api.rename_file(root, path.name, 'Researcher.md', file['revision'])
    assert result['path'] == 'Researcher.md'
    assert 'Researcher.md' in {p.name for p in root.iterdir()}
    assert 'researcher.md' not in {p.name for p in root.iterdir()}
    other = root/'analyst.md'
    other.write_text('other')
    with pytest.raises(api.Conflict):
        api.rename_file(root, result['path'], other.name, result['revision'])
    # Distinct case-sensitive hard links must not be mistaken for the same name.
    if not (root/'researcher.md').exists():
        (root/'researcher.md').hardlink_to(root/'Researcher.md')
        with pytest.raises(api.Conflict):
            api.rename_file(root, result['path'], 'researcher.md', result['revision'])


def test_member_version_cache_reuses_history_and_invalidates(recorded_runs, monkeypatch):
    root = recorded_runs
    cache = runviews.HistoryVersions()
    records, _ = runviews.records(root)
    selected = records[0].folder.relative_to(root).as_posix()
    calls = []
    original = runviews.member_versions
    def counted(history):
        calls.append(len(history))
        return original(history)
    monkeypatch.setattr(runviews, 'member_versions', counted)
    first = runviews.detail(root, selected, cache)
    assert runviews.detail(root, selected, cache)['members'] == first['members']
    assert len(calls) == 1
    # An edited event log invalidates the cache even without a new run directory.
    log = root/selected/'events.jsonl'
    log.write_text(log.read_text()+'\n')
    runviews.detail(root, selected, cache)
    assert len(calls) == 2
    clone = root/selected
    destination = clone.parent/'2026-11-08_120000_debate'
    shutil.copytree(clone, destination)
    runviews.detail(root, selected, cache)
    assert len(calls) == 3
    shutil.rmtree(destination)
    runviews.detail(root, selected, cache)
    assert len(calls) == 4


def test_version_cache_is_used_by_run_and_comparison_routes(recorded_runs, monkeypatch):
    calls = []
    original = runviews.member_versions
    def counted(history):
        calls.append(True)
        return original(history)
    monkeypatch.setattr(runviews, 'member_versions', counted)
    records, _ = runviews.records(recorded_runs)
    paths = [record.folder.relative_to(recorded_runs).as_posix() for record in records[:2]]
    with running(recorded_runs) as server:
        for path in paths:
            assert get(server, '/api/run?'+urlencode({'path':path}))[0] == 200
            assert get(server, '/api/run-path?'+urlencode({'path':path}))[0] == 200
        assert get(server, '/api/compare?'+urlencode(dict(zip(['left','right'],paths))))[0] == 200
    assert len(calls) == 1


def test_workspace_named_runs_recognizes_actual_record_children(tmp_path):
    root = tmp_path/'Research'/'runs'
    root.mkdir(parents=True)
    (root/'notes.txt').write_text('workspace notes')
    assert not api.read_file(root, 'notes.txt')['readonly']
    record = root/'old'
    record.mkdir()
    (record/'run.json').write_text('{}')
    (record/'output.md').write_text('record')
    assert api.read_file(root, 'old/output.md')['readonly']
    with pytest.raises(api.OutsideRoot):
        api.editable(root, 'old/output.md')


def test_incremental_transcript_preserves_cards_and_lazy_read_context():
    from pathlib import Path
    import subprocess
    import sys

    script = Path(__file__).with_name('ui_transcript_check.js')
    result = subprocess.run([sys.executable, '-m', 'deno', 'run', '--allow-read', str(script)],
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
