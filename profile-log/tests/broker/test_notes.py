from .test_tasks import shared, workspace_of


def note(agent, task, text, **kw):
    return agent.sign(agent.request(dict(op='note', task=task, scope='work/a', text=text, to=kw.get('to'))))


def test_notes_on_a_task(tmp_path):
    b, alice, bob, outsider = shared(tmp_path)
    assert alice.request(dict(op='note', task='AM-1', scope='work/a', text='hi', to=None)) == {'error': 'denied'}  # no task
    alice.task('create', 'AM-1', title='Index migration', claim=True)
    first = note(alice, 'AM-1', 'Should we rebuild snapshots first?', to=bob.identity.agent_id)
    second = note(bob, 'AM-1', 'Yes, rebuild first.')
    assert 'id' in first and 'id' in second
    seen = bob.request(dict(op='notes', task='am-1'))['notes']
    assert [n['text'] for n in seen] == ['Should we rebuild snapshots first?', 'Yes, rebuild first.']
    assert seen[0]['for_me'] and not seen[1]['for_me'] and seen[0]['author']['agent'] == alice.identity.agent_id
    assert outsider.request(dict(op='notes', task='AM-1', scope='work/a')) == {'error': 'denied'}
    assert outsider.request(dict(op='notes', task='AM-1'))['notes'] == []
    # notes are written only through the note op, and they are exempt from staged review
    forged = dict(kind='task_note', content='x', metadata=dict(task_note=dict(task='AM-1', to=None)))
    assert alice.request(dict(op='remember', body=forged, scope='work/a', tags=[], task=None, label=None)) == {'error': 'denied'}
    workspace_of(b, **{'review.staged': 1})
    note(alice, 'AM-1', 'Snapshots rebuilt.')
    assert bob.request(dict(op='notes', task='AM-1'))['notes'][-1]['text'] == 'Snapshots rebuilt.'
    for bad in (dict(text=''), dict(text='x' * 4001), dict(text='ok', to='nothex')):
        assert alice.request(dict(op='note', task='AM-1', scope='work/a', **{'to': None, **bad})) == {'error': 'invalid_request'}
    # recall still finds notes as ordinary searchable memory
    assert any(r['kind'] == 'task_note' for r in bob.request(dict(op='recall', query='rebuild snapshots'))['results'])
