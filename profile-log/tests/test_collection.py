"""Keep test identifiers portable to Windows path limits."""
import ast
from pathlib import Path


def test_short_explicit_ids(request):
    missing = []
    for path in Path(__file__).parent.glob('test_*.py'):
        for node in ast.walk(ast.parse(path.read_text(encoding='utf-8'))):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                if node.func.attr == 'parametrize' and not any(k.arg == 'ids' for k in node.keywords):
                    missing.append(f'{path.name}:{node.lineno}')
    assert not missing, f'Parametrizations without explicit ids: {missing}'
    # Normalize both supported invocation directories to the longer repo-root form.
    lengths = [len('profile-log/tests/' + item.nodeid.split('tests/', 1)[-1])
               for item in request.session.items]
    assert max(lengths) <= 80, f'Longest collected node ID: {max(lengths)} characters'
