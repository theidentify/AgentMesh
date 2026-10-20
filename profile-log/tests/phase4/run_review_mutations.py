"""Offline reviewer mutation checks; source files are never modified.

Run with the prepared interpreter from either repository root or profile-log.
Each isolated child replaces one module's source in memory before pytest imports
its dedicated test. An expected assertion failure kills the mutant.
"""
import os
from pathlib import Path
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[2]
TEST='tests/phase4/test_mutations.py::'
MUTANTS={
 'K2':('keyring',"        if not bootstrap and not self.authorized(issuer,record['profile'],record['scope'],record['issued_at']):\n            raise KeyAccessError('unauthorized key issuer')\n",'', 'test_k2_no_scope'),
 'K6':('keyring',"        if record['recipient_device']=='recovery' and record['scope']!=record['profile']:\n            raise KeyAccessError('recovery requires profile root')\n",'', 'test_k6_recovery_root'),
 'K7':('keyring',"        if not self.authorized(record['issuer_device'],record['profile'],record['scope'],record['created_at']):\n            raise KeyAccessError('unauthorized epoch issuer')\n",'', 'test_k7_epoch_authority'),
 'K8':('keyring'," or record['scope'] not in ancestors",'', 'test_k8_sibling_scope'),
 'R3':('identity',"        verify_signature(record,public,domain('device-revocation'))\n",'', 'test_r3_forged_revocation'),
 'R4':('identity',"        if cert['issuer'] not in ('account','recovery'):\n            self.device_public(cert['issuer'],at=cert['issued_at'],seen=seen)\n",'', 'test_r4_issuer_chain'),
 'P2':('pairing',"data=dict(commit_hash=digest(commit),approver_hash=digest(approval),Nj=reveal['Nj'],Na=approval['Na'])","data=dict(commit_hash=digest(commit),Nj=reveal['Nj'])", 'test_p2_nonce_sas'),
 'P5':('pairing',"        if request_id in self.used or list(self.storage.list(prefix+'approver.json')) or list(self.storage.list(prefix+'reveal.json')):\n            raise ValueError('pairing_reused')\n",'', 'test_p5_reused_begin'),
 'P6':('pairing'," or approval['commit_hash']!=digest(commit)",'', 'test_p6_commit_replay'),
 # Supporting check for the review's backdating boundary.
 'parent-time':('keyring',"parent in valid and valid[parent]['created_at']<=r['created_at']","parent in valid", 'test_backdated_epoch'),
}
CHILD='''
import importlib
from pathlib import Path
import sys
module=importlib.import_module('agentmesh_log.'+sys.argv[1])
source=Path(module.__file__).read_text(encoding='utf-8')
old,new=sys.argv[2:4]
assert source.count(old)==1, 'mutation anchor must be unique'
exec(compile(source.replace(old,new),module.__file__,'exec'),module.__dict__)
import pytest
raise SystemExit(pytest.main(['-q','--tb=short','-p','no:cacheprovider',sys.argv[4]]))
'''


def main():
    failed=[]
    for name,(module,old,new,test) in MUTANTS.items():
        result=subprocess.run([sys.executable,'-c',CHILD,module,old,new,TEST+test],cwd=ROOT,
            env={**os.environ,'PYTHONDONTWRITEBYTECODE':'1','AGENTMESH_R2_LIVE':''},
            capture_output=True,text=True,encoding='utf-8')
        killed=result.returncode==1 and 'FAILED '+TEST+test in result.stdout and 'ERROR ' not in result.stdout and ' passed' not in result.stdout
        print(name+(': KILLED' if killed else ': NOT VERIFIED'))
        print(result.stdout,end='')
        if not killed:
            failed.append(name); print(result.stderr,end='')
    return bool(failed)


if __name__=='__main__': raise SystemExit(main())
