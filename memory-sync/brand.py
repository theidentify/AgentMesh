"""Shared ASCII brand: presentation only; never decorate machine-readable JSON."""
MARK = '[o-A-o]'
TAGLINE = 'Local memory. Connected agents.'


def banner():
    return '\n'.join(['    o', '   / \\', '  o---o   AgentMesh', '          ' + TAGLINE])


def description(text):
    return MARK + ' AgentMesh\n' + TAGLINE + '\n\n' + (text or '')


def label(text):
    return MARK + ' AgentMesh: ' + text
