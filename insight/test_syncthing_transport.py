"""Disposable HTTP fixtures; never reconfigure a running Syncthing instance."""
import json
import os
from pathlib import Path
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch
from urllib.parse import urlsplit
import xml.etree.ElementTree as ET

import syncthing_transport as transport


class TransportTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name)/'hermes'
        self.config = Path(self.tmp.name)/'config.xml'
        self.responses = {
            '/rest/system/status': {'myID':'LOCAL'},
            '/rest/system/connections': {'connections':{
                'REMOTE':{'connected':True,'paused':False,'type':'tcp-server','address':'SECRET_ADDRESS'},
                'OTHER':{'connected':False,'paused':True}}},
            '/rest/db/status': {'state':'idle','needBytes':0,'needTotalItems':0},
            '/rest/db/completion': {'completion':100,'needBytes':0,'needItems':0,'remoteState':'valid'},
        }
        self.paths = []
        self.failure = None
        owner = self
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, format, *args):
                pass
            def do_GET(self):
                owner.paths.append(self.path)
                assert self.headers.get('X-API-Key') == 'FIXTURE_SECRET'
                route = urlsplit(self.path).path
                if owner.failure and route == owner.failure[0]:
                    code = owner.failure[1]
                    if code == 'slow_headers':
                        try:
                            for byte in b'HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\n{}':
                                self.connection.sendall(bytes([byte]))
                                time.sleep(.2)
                        except OSError:
                            pass
                        return
                    if code == 'timeout':
                        time.sleep(2.3)
                        return
                    self.send_response(code)
                    self.send_header('Location', 'http://192.0.2.1/private')
                    self.send_header('Content-Length','0')
                    self.end_headers()
                    return
                body = json.dumps(owner.responses[route]).encode()
                self.send_response(200)
                self.send_header('Content-Length',str(len(body)))
                self.end_headers()
                try:
                    self.wfile.write(body)
                except (BrokenPipeError, ConnectionResetError):
                    pass
        self.server = ThreadingHTTPServer(('127.0.0.1',0),Handler)
        self.server.daemon_threads = True
        thread = threading.Thread(target=self.server.serve_forever,daemon=True)
        thread.start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.address = '127.0.0.1:'+str(self.server.server_port)
        self.write_config()
        env = patch.dict(os.environ, {'INSIGHT_SYNCTHING_CONFIG':str(self.config)})
        env.start()
        self.addCleanup(env.stop)

    def write_config(self, address=None, folder=None, paused='false'):
        root = ET.Element('configuration')
        gui = ET.SubElement(root,'gui',enabled='true',tls='false')
        ET.SubElement(gui,'address').text = address or self.address
        ET.SubElement(gui,'apikey').text = 'FIXTURE_SECRET'
        f = ET.SubElement(root,'folder',id='PRIVATE_FOLDER',path=str(folder or self.home/'omp-memory/sync'))
        ET.SubElement(f,'paused').text = paused
        ET.SubElement(f,'device',id='LOCAL')
        ET.SubElement(f,'device',id='REMOTE')
        ET.SubElement(root,'device',id='REMOTE',name='Windows SECRET_NAME')
        ET.SubElement(root,'device',id='OTHER',name='Secret company folder')
        self.config.write_bytes(ET.tostring(root))

    def observe(self):
        return transport.observe(self.home)

    def test_connected_projection_and_privacy(self):
        value = self.observe()
        self.assertEqual(value['status'],'connected')
        self.assertEqual(len(value['rows']),1)
        row = value['rows'][0]
        self.assertEqual(row['peer'],'Windows')
        self.assertEqual(row['completion_percent'],100)
        self.assertEqual(row['connection_type'],'direct')
        self.assertEqual(value['local']['pending_items'],0)
        self.assertEqual(set(value), {'available','status','availability','observed_at','rows','local','note'})
        self.assertEqual(set(row), {'peer','status','paused','connection_type','completion_percent','pending_bytes','pending_items','completion_state','remote_folder_state'})
        text = json.dumps(value)
        for private in ('FIXTURE_SECRET','SECRET_ADDRESS','SECRET_NAME','PRIVATE_FOLDER','REMOTE','OTHER',str(self.home)):
            self.assertNotIn(private,text)
        self.assertEqual(len(self.paths),4)
        self.assertIn('folder=PRIVATE_FOLDER&device=REMOTE',self.paths[-1])

    def test_disconnected_suppresses_historical_completion(self):
        self.responses['/rest/system/connections']['connections']['REMOTE']['connected'] = False
        value = self.observe()
        self.assertEqual(value['status'],'disconnected')
        self.assertIsNone(value['rows'][0]['completion_percent'])
        self.assertFalse(any('/rest/db/completion' in p for p in self.paths))

    def test_paused_device(self):
        self.responses['/rest/system/connections']['connections']['REMOTE']['paused'] = True
        self.assertEqual(self.observe()['status'],'disconnected')

    def test_missing_expected_peer(self):
        del self.responses['/rest/system/connections']['connections']['REMOTE']
        self.assertEqual(self.observe()['status'],'unknown')

    def test_invalid_connection_bool(self):
        self.responses['/rest/system/connections']['connections']['REMOTE']['connected'] = 1
        self.assertEqual(self.observe()['status'],'unknown')

    def test_remote_paused_and_not_sharing(self):
        for state in ('paused','notSharing','unknown'):
            self.responses['/rest/db/completion']['remoteState'] = state
            self.assertIsNone(self.observe()['rows'][0]['completion_percent'])

    def test_local_paused(self):
        self.write_config(paused='true')
        self.assertIsNone(self.observe()['rows'][0]['completion_percent'])

    def test_partial_and_invalid_numbers(self):
        self.responses['/rest/db/completion'].update(completion=True,needBytes=None,needItems=-1)
        value = self.observe()
        self.assertEqual(value['rows'][0]['completion_state'],'partial observation')
        self.assertIsNone(value['rows'][0]['completion_percent'])
        self.assertIsNone(value['rows'][0]['pending_bytes'])
        for bad in (True,False,None,-1,1.5,float('nan'),float('inf'),10**400):
            self.assertIsNone(transport.number(bad))

    def test_partial_auth_failure_preserves_connection_not_completion(self):
        self.failure = ('/rest/db/completion',401)
        row = self.observe()['rows'][0]
        self.assertEqual(row['status'],'connected')
        self.assertEqual(row['completion_state'],'unavailable')
        self.assertIsNone(row['completion_percent'])

    def test_unauthorized_and_redirect_fail_closed(self):
        for code in (401,403,302):
            self.paths.clear()
            self.failure = ('/rest/system/connections',code)
            value = self.observe()
            self.assertEqual(value['status'],'unknown')
            self.assertFalse(value['available'])
            self.assertEqual(len(self.paths),2)

    def test_timeout_is_bounded_and_sanitized(self):
        self.failure = ('/rest/system/connections','timeout')
        before = time.monotonic()
        value = self.observe()
        self.assertLess(time.monotonic()-before,3)
        self.assertEqual(value['status'],'unknown')
        self.assertNotIn('FIXTURE_SECRET',json.dumps(value))

    def test_slow_drip_headers_have_absolute_timeout(self):
        self.failure = ('/rest/system/connections','slow_headers')
        before = time.monotonic()
        self.assertEqual(self.observe()['status'],'unknown')
        self.assertLess(time.monotonic()-before,3)

    def test_byte_cap(self):
        self.responses['/rest/system/status']['private'] = 'X'*(transport.LIMIT+1)
        self.assertEqual(self.observe()['status'],'unknown')

    def test_loopback_validation(self):
        for address in ('192.0.2.1:8384','localhost:8384','0.0.0.0:8384','user:secret@127.0.0.1:8384','127.0.0.1:8384/path','127.0.0.1:8384?x=1','127.0.0.1:8384#fragment'):
            self.write_config(address=address)
            self.assertEqual(self.observe()['status'],'unknown')
        self.assertEqual(self.paths,[])
        self.assertEqual(transport.endpoint('[::1]:8384').hostname,'::1')

    def test_unconfigured_and_unrelated_folder(self):
        self.write_config(folder=Path(self.tmp.name)/'company')
        self.assertEqual(self.observe()['availability'],'exchange folder not configured')
        self.assertEqual(self.paths,[])
        with patch.object(transport,'config_path',return_value=None):
            self.assertEqual(self.observe()['availability'],'not configured')

    def test_malformed_config_and_entity_rejection(self):
        for raw in (b'<invalid', b'<!DOCTYPE configuration><configuration/>'):
            self.config.write_bytes(raw)
            self.assertEqual(self.observe()['status'],'unknown')
        self.assertEqual(self.paths,[])

    def test_relay_and_unknown_route(self):
        c = self.responses['/rest/system/connections']['connections']['REMOTE']
        c['type'] = 'relay-client'
        self.assertEqual(self.observe()['rows'][0]['connection_type'],'relay')
        c['type'] = 'private unexpected'
        self.assertEqual(self.observe()['rows'][0]['connection_type'],'unknown')

    def test_transport_survives_missing_sqlite_and_updates_all_consumers(self):
        import adapters
        value = adapters.operations(self.home, adapters.MemoryReader(), None)
        self.assertFalse(value['sync']['available'])
        self.assertEqual(value['sync']['transport'],'connected')
        self.assertEqual(value['sync']['peers']['transport'],'connected')
        self.assertEqual(value['sync']['transport_status']['status'],'connected')

    def test_partial_local_failure(self):
        self.failure = ('/rest/db/status',403)
        value = self.observe()
        self.assertEqual(value['status'],'connected')
        self.assertEqual(value['local']['state'],'unknown')
        self.assertIsNone(value['local']['pending_bytes'])


if __name__ == '__main__':
    unittest.main()
