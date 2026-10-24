"""Manual owner-only native R2 credential smoke. Never collected by pytest."""
import secrets
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from agentmesh_log.storage.r2creds import WindowsR2Credentials


def main():
    if sys.platform!='win32': raise SystemExit('Unsupported OS')
    if input('Type smoke to create and remove two disposable R2 credential items: ')!='smoke': return 1
    backend=WindowsR2Credentials(); service='agentmesh.r2.smoke.'+secrets.token_hex(16)
    pair=(secrets.token_hex(16),secrets.token_hex(32))
    try:
        backend.store(service,*pair)
        if backend.load(service)!=pair: raise SystemExit('Roundtrip failed')
        print('R2 credential roundtrip passed')
    finally: backend.delete(service)
    return 0


if __name__=='__main__': raise SystemExit(main())
