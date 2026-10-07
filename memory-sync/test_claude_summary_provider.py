import json
import subprocess
import sys
from pathlib import Path

import pytest


def test_claude_adapter_uses_actual_model_and_validates_provenance(tmp_path):
    import claude_summary_provider as provider
    task = {'batch_id': 'batch', 'events': [{'id': 7}]}
    output = {'is_error': False, 'modelUsage': {'actual-model': {}},
              'structured_output': {'format': 'omp-sqlite-summary-response-v1',
                'batch_id': 'batch', 'provider': 'claimed', 'model': 'claimed',
                'items': [], 'summaries': []}}
    response = provider.decode_result(task, output)
    assert response['provider'] == 'anthropic/claude-code'
    assert response['model'] == 'actual-model'
    output['structured_output']['batch_id'] = 'wrong'
    with pytest.raises(ValueError):
        provider.decode_result(task, output)


def test_claude_adapter_schema_agrees_with_memory_store():
    import claude_summary_provider as provider
    from summarize_memory import KINDS, SCOPES
    schema = provider.schema({'batch_id': 'batch', 'events': [{'id': 7}]})
    item = schema['properties']['items']['items']['properties']
    assert set(item['kind']['enum']) == set(KINDS)
    assert set(item['scope']['enum']) == set(SCOPES)


def test_claude_adapter_command_is_noninteractive_and_has_no_agent_tools():
    import claude_summary_provider as provider
    task = {'batch_id': 'batch', 'events': [{'id': 7}]}
    args = provider.command(task, executable='claude', model='configured-model')
    assert args[args.index('--tools') + 1] == ''
    assert '--safe-mode' in args
    assert '--no-session-persistence' in args
    assert '--print' in args
    assert '--dangerously-skip-permissions' not in args
    schema = json.loads(args[args.index('--json-schema') + 1])
    assert schema['properties']['batch_id']['enum'] == ['batch']
    assert args[args.index('--model') + 1] == 'configured-model'
