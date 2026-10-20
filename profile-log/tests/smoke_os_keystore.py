"""Manual owner-only native key-store smoke. Never collected by pytest."""
import sys
import tempfile
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from agentmesh_log.broker.keystore import BrokerHome,BrokerState
from agentmesh_log.oskeystore import OsKeyStore,current_backend


def main():
    if input('Type smoke to create and remove one disposable OS key-store item: ')!='smoke': return 1
    backend=current_backend()
    if backend is None: raise SystemExit('Unsupported OS')
    with tempfile.TemporaryDirectory(prefix='am-smoke-') as directory:
        home=BrokerHome(Path(directory).resolve()); state=BrokerState.create(['disposable'])
        store=OsKeyStore(home,backend)
        try:
            store.save(state)
            if store.load().record()!=state.record(): raise SystemExit('Roundtrip failed')
            print('OS key-store roundtrip passed')
        finally: backend.delete(state.device.device_id)
    return 0


if __name__=='__main__': raise SystemExit(main())
