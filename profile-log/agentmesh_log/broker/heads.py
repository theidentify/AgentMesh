"""Scope-local revision components retain every concurrent head."""


def heads(rows):
    by_id = {r['id']: r for r in rows}; parent = {i:i for i in by_id}; superseded = set()
    def root(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]; i = parent[i]
        return i
    for row in rows:
        previous = by_id.get(row.get('revises'))
        if previous and previous['scope'] == row['scope']:
            parent[root(row['id'])] = root(previous['id']); superseded.add(previous['id'])
    groups = {}
    for row in rows:
        if row['id'] not in superseded: groups.setdefault(root(row['id']), []).append(row)
    result = []
    for group in groups.values():
        group.sort(key=lambda r:(r['created_at'],r['id']), reverse=True)
        result.append({**group[0], 'conflict': len(group)>1, 'alternatives': [r['id'] for r in group[1:]]})
    return result
