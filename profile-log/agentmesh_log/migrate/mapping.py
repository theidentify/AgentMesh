"""Strict owner-selected project routing; no inferred security boundaries."""
from pathlib import Path
import unicodedata
from ..encoding import parse
from .source import rows


def target_valid(value):
    return type(value) is str and (value == 'skip' or
        (len(value.split('/')) >= 1 and all(p and p not in ('.','..') for p in value.split('/'))
         and not any(unicodedata.category(c) == 'Cc' or c == '\\' for c in value)))


class Mapping:
    def __init__(self, value):
        if type(value) is not dict or set(value) != {'format','global','null_project','projects','prefixes'}:
            raise ValueError('invalid mapping')
        if value['format'] != 'agentmesh-migration-mapping-v1': raise ValueError('invalid mapping')
        if type(value['projects']) is not dict or type(value['prefixes']) is not list: raise ValueError('invalid mapping')
        if any(type(k) is not str or not k for k in value['projects']): raise ValueError('invalid mapping')
        prefixes = set()
        targets = [value['global'],value['null_project'],*value['projects'].values()]
        for entry in value['prefixes']:
            if type(entry) is not dict or set(entry) != {'prefix','target'}: raise ValueError('invalid mapping')
            prefix = entry['prefix']
            if type(prefix) is not str or not prefix or prefix in prefixes: raise ValueError('invalid mapping')
            prefixes.add(prefix); targets.append(entry['target'])
        if not all(target_valid(t) for t in targets): raise ValueError('invalid mapping')
        self.value = value
        self.targets = set(targets) - {'skip'}

    @classmethod
    def load(cls, path):
        return cls(parse(Path(path).read_text(encoding='utf-8').encode('utf-8')))

    def target(self, row):
        if row['scope'] == 'global': return self.value['global']
        name = row.get('project')
        if name is None: return self.value['null_project']
        if name in self.value['projects']: return self.value['projects'][name]
        for entry in sorted(self.value['prefixes'], key=lambda e: -len(e['prefix'])):
            if name.startswith(entry['prefix']): return entry['target']
        raise ValueError('unmapped project')


def plan(database, mapping=None):
    groups = {}; unmapped = set()
    for row in rows(database):
        name = row.get('project')
        key = name if name is not None else ''
        group = groups.setdefault(key, dict(project=name,items=0,summaries=0))
        group['items' if row['table'] == 'memory_items' else 'summaries'] += 1
        if mapping is not None:
            try: mapping.target(row)
            except ValueError:
                if name is not None: unmapped.add(name)
        elif name is not None: unmapped.add(name)
    return dict(projects=[groups[k] for k in sorted(groups)], unmapped=sorted(unmapped))
