"""Bounded canonical JSON frames shared by streams and byte-only pipes."""
import struct
from ..encoding import wire, parse, MAX_BYTES


def encode(obj):
    body = wire(obj)
    return struct.pack('!I', len(body)) + body


def length(header):
    if len(header) != 4: raise ValueError('invalid frame')
    size = struct.unpack('!I', header)[0]
    if not 0 < size <= MAX_BYTES: raise ValueError('invalid frame')
    return size


def decode(data):
    size = length(data[:4])
    if len(data) != size + 4: raise ValueError('invalid frame')
    return parse(data[4:])


def receive(read):
    def exact(n):
        chunks = bytearray()
        while len(chunks) < n:
            data = read(n-len(chunks))
            if not data: raise EOFError('connection closed')
            chunks.extend(data)
        return bytes(chunks)
    size = length(exact(4))
    return parse(exact(size))
