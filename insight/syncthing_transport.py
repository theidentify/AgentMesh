"""Content-free, read-only Syncthing observation for the exact exchange folder."""
from datetime import datetime, timezone
import http.client
import ipaddress
import json
import math
import os
from pathlib import Path
import re
import socket
import stat
import threading
import time
from urllib.parse import urlencode, urlsplit
import xml.etree.ElementTree as ET

LIMIT = 1024 * 1024
NOTE = ('Syncthing file transport only. Local needs describe this Mac; remote completion describes the paired exchange peer. '
        'Disconnected, paused or incomplete observations have no current completion. 100% file delivery is not database application or recall. '
        'Completed-cycle ACKs and database receipts are separate evidence.')


def number(value, percent=False):
    if type(value) not in (int, float) or not 0 <= value <= (100 if percent else 2**53-1):
        return None
    if not math.isfinite(value):
        return None
    return value if percent or value == int(value) else None


def boolean(value):
    return value if type(value) is bool else None


def endpoint(address, tls=False):
    if not isinstance(address, str) or any(c.isspace() or ord(c) < 32 for c in address):
        raise ValueError('Invalid endpoint')
    parsed = urlsplit(('https://' if tls else 'http://') + address)
    if (parsed.scheme not in ('http', 'https') or parsed.username is not None or parsed.password is not None or
            parsed.path or parsed.query or parsed.fragment or not parsed.port or parsed.hostname is None):
        raise ValueError('Invalid endpoint')
    # Numeric loopback only: no DNS resolution, proxies or alternate destinations.
    if not ipaddress.ip_address(parsed.hostname).is_loopback:
        raise ValueError('Loopback required')
    return parsed


def request(base, key, path, deadline):
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise TimeoutError()
    cls = http.client.HTTPSConnection if base.scheme == 'https' else http.client.HTTPConnection
    conn = cls(base.hostname, base.port, timeout=min(2, remaining))
    response = None
    expired = threading.Event()
    def abort():
        expired.set()
        # Absolute timeout also stops slow-drip HTTP headers and bodies.
        sock = conn.sock or getattr(getattr(getattr(response, 'fp', None), 'raw', None), '_sock', None)
        if sock:
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
    timer = threading.Timer(min(2, remaining), abort)
    timer.daemon = True
    timer.start()
    try:
        conn.request('GET', path, headers={'X-API-Key':key, 'Accept':'application/json'})
        response = conn.getresponse()
        # Never follow redirects; never include raw errors or responses in the projection.
        if response.status != 200:
            raise ValueError('Unavailable')
        length = response.getheader('Content-Length')
        if length and int(length) > LIMIT:
            raise ValueError('Oversized')
        raw = bytearray()
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError()
            sock = conn.sock or (getattr(getattr(response.fp, 'raw', None), '_sock', None))
            if sock:
                sock.settimeout(min(2, remaining))
            chunk = response.read1(min(65536, LIMIT + 1 - len(raw)))
            raw.extend(chunk)
            if len(raw) > LIMIT:
                raise ValueError('Oversized')
            if not chunk:
                break
        if expired.is_set() or time.monotonic() >= deadline:
            raise TimeoutError()
        value = json.loads(raw)
        if not isinstance(value, dict):
            raise ValueError('Invalid response')
        return value
    finally:
        timer.cancel()
        conn.close()


def config_path(home):
    override = os.environ.get('INSIGHT_SYNCTHING_CONFIG')
    if override:
        path = Path(override).expanduser()
        if not path.is_absolute() or path.resolve().is_relative_to(Path(__file__).resolve().parent.parent):
            raise ValueError('Private absolute configuration required')
        return path
    roots = [Path(home).expanduser().parent, Path.home()]
    for root in roots:
        for suffix in ('Library/Application Support/Syncthing/config.xml', '.local/state/syncthing/config.xml', '.config/syncthing/config.xml'):
            path = root/suffix
            if path.is_file():
                return path
    return None


def observe(home):
    result = {'available':False, 'status':'unknown', 'availability':'not configured',
              'observed_at':datetime.now(timezone.utc).isoformat(), 'rows':[],
              'local':{'state':'unknown', 'paused':None, 'pending_bytes':None, 'pending_items':None}, 'note':NOTE}
    try:
        path = config_path(home)
        if path is None:
            return result
        fd = os.open(path, os.O_RDONLY | getattr(os, 'O_NONBLOCK', 0))
        with os.fdopen(fd, 'rb') as source:
            if not stat.S_ISREG(os.fstat(source.fileno()).st_mode):
                raise ValueError('Regular configuration required')
            raw = source.read(LIMIT + 1)
        if len(raw) > LIMIT or b'<!DOCTYPE' in raw or b'<!ENTITY' in raw:
            raise ValueError('Invalid configuration')
        root = ET.fromstring(raw)
        gui = root.find('gui')
        if gui is None or gui.get('enabled') == 'false':
            raise ValueError('GUI unavailable')
        base = endpoint(gui.findtext('address', ''), gui.get('tls') == 'true')
        key = gui.findtext('apikey', '')
        if not key or len(key) > 1024 or any(ord(c) < 32 for c in key):
            raise ValueError('Key unavailable')
        exchange = (Path(home)/'omp-memory/sync').resolve()
        folders = [f for f in root.findall('folder') if f.get('path') and
                   Path(f.get('path', '')).expanduser().is_absolute() and Path(f.get('path', '')).expanduser().resolve() == exchange]
        if len(folders) != 1:
            result['availability'] = 'exchange folder not configured'
            return result
        folder = folders[0]
        folder_id = folder.get('id')
        if not folder_id:
            raise ValueError('Folder identity unavailable')
        deadline = time.monotonic() + 6
        my_id = request(base, key, '/rest/system/status', deadline).get('myID')
        if not isinstance(my_id, str) or not my_id:
            raise ValueError('Local identity unavailable')
        peers = list(dict.fromkeys(d.get('id') for d in folder.findall('device') if d.get('id') != my_id))
        if not peers or len(peers) > 8 or any(not p for p in peers):
            result['availability'] = 'paired exchange peer not configured'
            return result
        names = {d.get('id'):d.get('name', '') for d in root.findall('device')}
        connections = request(base, key, '/rest/system/connections', deadline).get('connections')
        if not isinstance(connections, dict):
            raise ValueError('Invalid connections')
        paused = folder.findtext('paused')
        result['local']['paused'] = paused == 'true' if paused in ('true', 'false') else None
        try:
            local = request(base, key, '/rest/db/status?' + urlencode({'folder':folder_id}), deadline)
            state = local.get('state')
            result['local'].update(state=state if state in ('idle', 'scanning', 'scan-waiting', 'sync-waiting', 'sync-preparing', 'syncing', 'cleaning', 'clean-waiting', 'error', 'unknown') else 'unknown',
                                   pending_bytes=number(local.get('needBytes')), pending_items=number(local.get('needTotalItems')))
        except (OSError, ValueError, http.client.HTTPException):
            pass
        for index, peer in enumerate(peers):
            c = connections.get(peer)
            c = c if isinstance(c, dict) else {}
            connected, paused = boolean(c.get('connected')), boolean(c.get('paused'))
            row = {'peer':'Windows' if re.search(r'windows', names.get(peer, ''), re.I) else 'Exchange peer ' + str(index+1),
                   'status':'connected' if connected is True and paused is False else 'disconnected' if connected is False or paused is True else 'unknown',
                   'paused':paused, 'connection_type':'unknown', 'completion_percent':None, 'pending_bytes':None, 'pending_items':None,
                   'completion_state':'unavailable', 'remote_folder_state':'unknown'}
            if row['status'] == 'connected':
                kind = c.get('type')
                row['connection_type'] = 'relay' if kind in ('relay-client', 'relay-server') else 'direct' if kind in ('tcp-client', 'tcp-server', 'quic-client', 'quic-server') else 'unknown'
                try:
                    completion = request(base, key, '/rest/db/completion?' + urlencode({'folder':folder_id, 'device':peer}), deadline)
                    remote = completion.get('remoteState')
                    row['remote_folder_state'] = remote if remote in ('valid', 'paused', 'notSharing', 'unknown') else 'unknown'
                    if remote == 'valid' and result['local']['paused'] is False:
                        row.update(completion_percent=number(completion.get('completion'), True),
                                   pending_bytes=number(completion.get('needBytes')), pending_items=number(completion.get('needItems')))
                        if all(row[k] is not None for k in ('completion_percent', 'pending_bytes', 'pending_items')):
                            row['completion_state'] = 'current file observation'
                        else:
                            row['completion_state'] = 'partial observation'
                except (OSError, ValueError, http.client.HTTPException):
                    pass
            result['rows'].append(row)
        statuses = [row['status'] for row in result['rows']]
        result.update(available=True, availability='observed', status='disconnected' if 'disconnected' in statuses else 'unknown' if 'unknown' in statuses else 'connected')
    except Exception:
        # Source failures must not disable independent database/ACK observations.
        result['availability'] = 'unavailable or invalid configuration'
    result['observed_at'] = datetime.now(timezone.utc).isoformat()
    return result
