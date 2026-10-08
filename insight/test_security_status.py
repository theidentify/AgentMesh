"""Public security telemetry is not a management or trust API."""
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
import adapters


class SecurityStatusTests(unittest.TestCase):
    def test_legacy_ack_has_no_invented_signature_success_or_zero_failures(self):
        with tempfile.TemporaryDirectory() as folder:
            home = Path(folder); status = home / 'omp-memory/sync/status'; status.mkdir(parents=True)
            (status / 'mac.json').write_text(json.dumps({'format':'agentmesh-status-v1','node':'mac','updated_at':'2026-10-08T12:00:00Z','counts':{},'sync':{},'cycle':{}}))
            rows = adapters.peer_status(home)['rows']
            self.assertEqual(rows[0]['security']['policy'], 'legacy')
            self.assertIsNone(rows[0]['security']['verification']['failed_attempts'])
            self.assertIsNone(rows[0]['security']['verification']['last_success_at'])
            self.assertEqual(rows[1]['security']['policy'], 'unknown')

    def test_strict_status_allowlist_and_authenticated_probe_identifier(self):
        packet = '00000000-0000-4000-8000-000000000010'
        raw = {'format':'agentmesh-security-status-v1','policy':'required','pairing':'approved',
               'display_name':'Office Mac','wizard_step':'activation','next_action':'confirm_coordinated_legacy_boundary',
               'roundtrip':'verified','roundtrip_packet_uuid':packet,'roundtrip_verified_at':'2026-10-08T12:00:00Z',
               'verification':{'attempts':10,'failed_attempts':2,'last_success_at':'2026-10-08T11:59:00Z','last_failure_at':'2026-10-08T11:58:00Z','error':'PRIVATE'},
               'private_key':'PRIVATE','public_key':'PRIVATE','fingerprint':'PRIVATE','security_dir':'PRIVATE',
               'trust_store':{'secret':'PRIVATE'}, 'credentials':'PRIVATE'}
        result = adapters.security_projection(raw)
        self.assertEqual(result['policy'], 'required'); self.assertEqual(result['pairing'], 'approved')
        self.assertEqual(result['roundtrip'], 'reported verified')
        self.assertEqual(result['roundtrip_packet_uuid'], packet)
        self.assertEqual(result['verification']['failed_attempts'], 2)
        self.assertIn('unsigned status telemetry', result['source'])
        self.assertNotIn('PRIVATE', json.dumps(result))
        raw.pop('roundtrip_packet_uuid')
        self.assertEqual(adapters.security_projection(raw)['roundtrip'], 'unknown')

    def test_malformed_fields_remain_unknown_and_cannot_be_management_commands(self):
        raw = {'format':'agentmesh-security-status-v1','policy':['required'],'pairing':{'approve':True},
               'display_name':'<script>PRIVATE</script>','wizard_step':'/PRIVATE','next_action':'disable signatures',
               'roundtrip':'verified','roundtrip_packet_uuid':'../PRIVATE','roundtrip_verified_at':'PRIVATE',
               'verification':{'attempts':True,'failed_attempts':-1,'last_success_at':'PRIVATE','last_failure_at':'2026-10-08T12:00:00'}}
        result = adapters.security_projection(raw)
        self.assertEqual(result['policy'], 'unknown'); self.assertEqual(result['pairing'], 'unknown')
        self.assertEqual(result['roundtrip'], 'unknown'); self.assertIsNone(result['display_name'])
        self.assertIsNone(result['verification']['attempts']); self.assertIsNone(result['verification']['failed_attempts'])
        self.assertNotIn('PRIVATE', json.dumps(result))
        self.assertEqual(adapters.security_projection({'policy':'required'})['policy'], 'unknown')

    def test_local_database_configuration_and_actual_recorded_counters_are_separate(self):
        with tempfile.TemporaryDirectory() as folder:
            database = Path(folder) / 'fixture.db'
            with sqlite3.connect(database) as c:
                c.row_factory = sqlite3.Row
                initial = adapters.local_security(c)
                self.assertEqual(initial['policy'], 'legacy'); self.assertIsNone(initial['verification']['attempts'])
                c.execute('CREATE TABLE _sync_security(sender TEXT,group_id TEXT,node TEXT)')
                c.execute('CREATE TABLE _sync_verification(id INTEGER,attempts INTEGER,failed_attempts INTEGER,last_success_at TEXT,last_failure_at TEXT)')
                c.execute("INSERT INTO _sync_verification VALUES(1,3,1,'2026-10-08T12:00:00Z','2026-10-08T11:00:00Z')")
                strict = adapters.local_security(c)
                self.assertEqual(strict['policy'], 'required'); self.assertEqual(strict['verification']['attempts'], 3)
                self.assertEqual(strict['pairing'], 'unknown')
                self.assertIn('SQLite', strict['source'])


if __name__ == '__main__': unittest.main()
