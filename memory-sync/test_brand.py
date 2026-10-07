import json
from pathlib import Path
import subprocess
import sys
import xml.etree.ElementTree as ET


def test_brand_is_ascii_and_help_keeps_machine_output_clean(tmp_path):
    import brand
    assert brand.banner().isascii()
    assert 'AgentMesh' in brand.banner()
    root = Path(__file__).parent
    result = subprocess.run([sys.executable, str(root / 'agentmesh.py'), '--help'], capture_output=True, text=True)
    assert result.returncode == 0
    assert brand.MARK in result.stdout
    import sqlite_memory
    db = tmp_path / 'local.db'
    sqlite_memory.init_database(db)
    result = subprocess.run([sys.executable, str(root / 'agentmesh.py'), '--database', str(db), 'recall', 'memory'], capture_output=True, text=True)
    assert result.returncode == 0
    assert json.loads(result.stdout)['query'] == 'memory'
    assert brand.MARK not in result.stdout


def test_brand_svg_assets_are_self_contained():
    root = Path(__file__).parent / 'assets'
    for name in ['agentmesh-icon.svg', 'agentmesh-logo.svg', 'agentmesh-icon-mono.svg']:
        path = root / name
        assert path.is_file()
        tree = ET.fromstring(path.read_text())
        assert tree.tag.endswith('svg')
        assert tree.get('viewBox')
        assert not any(e.tag.endswith('script') or e.tag.endswith('image') for e in tree.iter())
        assert 'https://' not in path.read_text()
