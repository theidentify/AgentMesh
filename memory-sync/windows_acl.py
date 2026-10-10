"""Fail-closed Windows private-storage ACL adapter (no localized command output).

Only newly created objects may be provisioned. Existing identities are verified,
not silently repaired. Native Windows execution requires an operator host gate.
Win32 security APIs are called in-process: a PowerShell child per check made every
private read/write cost seconds on Windows. Descriptors are copied out of OS memory
and decoded here, so the decoder is portable and testable without Windows.
"""
from __future__ import annotations
import functools
import os
from pathlib import Path
import re
import struct
from types import SimpleNamespace

FULL_CONTROL = 0x1f01ff
SYSTEM = 'S-1-5-18'


def validate(report, *, directory):
    """Accept only protected, explicit user/SYSTEM grants and current-user owner."""
    if type(report) is not dict or set(report) != {'sid', 'owner', 'control', 'directory', 'rules'}:
        raise ValueError('unverifiable Windows ACL')
    sid = report['sid']
    if type(sid) is not str or not re.fullmatch(r'S-1-5-21-(?:[0-9]+-){3}[0-9]+', sid):
        raise ValueError('unsupported Windows user SID')
    if report['owner'] != sid or type(report['directory']) is not bool or report['directory'] != directory:
        raise ValueError('Windows private owner/type mismatch')
    control = report['control']
    # DaclPresent and DaclProtected; reject null, inherited/unprotected DACLs.
    if type(control) is not int or control & 0x1004 != 0x1004:
        raise ValueError('Windows private ACL must be protected')
    if type(report['rules']) is not list or not report['rules']:
        raise ValueError('Windows private ACL has no explicit grants')
    user_full = False
    for ace in report['rules']:
        if type(ace) is not dict or set(ace) != {'sid', 'type', 'flags', 'mask'}:
            raise ValueError('unsupported Windows ACE')
        if ace['sid'] not in (sid, SYSTEM) or type(ace['type']) is not int or ace['type'] != 0:
            raise ValueError('unapproved Windows ACL principal or ACE type')
        # Only object/container inheritance, no inherited or inherit-only ACEs.
        if type(ace['flags']) is not int or ace['flags'] not in ((0, 3) if directory else (0,)):
            raise ValueError('unsupported or inherited Windows ACE')
        if type(ace['mask']) is not int or not 0 < ace['mask'] <= FULL_CONTROL or ace['mask'] & ~FULL_CONTROL:
            raise ValueError('unsupported Windows ACL rights')
        user_full |= ace['sid'] == sid and ace['mask'] == FULL_CONTROL
    if not user_full: raise ValueError('Windows private ACL requires user full control')
    return report


USER_SID = r'S-1-5-21-(?:[0-9]+-){3}[0-9]+'
# ACCESS_ALLOWED/DENIED/SYSTEM_AUDIT/ALARM: the non-callback, non-object CommonAce types.
COMMON_ACE_TYPES = (0, 1, 2, 3)
FILE_ATTRIBUTE_DIRECTORY = 0x10
FILE_ATTRIBUTE_REPARSE_POINT = 0x400
INVALID_FILE_ATTRIBUTES = 0xFFFFFFFF
OWNER_SECURITY_INFORMATION = 0x1
DACL_SECURITY_INFORMATION = 0x4
PROTECTED_DACL_SECURITY_INFORMATION = 0x80000000
SE_FILE_OBJECT = 1
TOKEN_QUERY = 0x8
TOKEN_USER = 1


def parse_sid(data):
    """Decode a binary SID prefix; returns (string, length)."""
    if len(data) < 8 or data[0] != 1 or data[1] > 15 or len(data) < 8 + 4 * data[1]:
        raise ValueError('malformed Windows SID')
    authority = int.from_bytes(data[2:8], 'big')
    parts = struct.unpack_from('<%dI' % data[1], data, 8)
    prefix = 'S-1-' + (str(authority) if authority < 1 << 32 else '0x%012X' % authority)
    return '-'.join((prefix, *map(str, parts))), 8 + 4 * data[1]


def parse_acl(data):
    """Decode a binary DACL into the report's rule projection; reject other ACE kinds."""
    if len(data) < 8: raise ValueError('malformed Windows ACL')
    revision, _, size, count, _ = struct.unpack_from('<BBHHH', data)
    if revision not in (2, 3, 4) or not 8 <= size <= len(data): raise ValueError('malformed Windows ACL')
    rules, offset = [], 8
    for _ in range(count):
        if offset + 4 > size: raise ValueError('malformed Windows ACE')
        kind, flags, length = struct.unpack_from('<BBH', data, offset)
        if length < 16 or offset + length > size: raise ValueError('malformed Windows ACE')
        # Reject callback/object/conditional and other unsupported ACEs.
        if kind not in COMMON_ACE_TYPES: raise ValueError('unsupported Windows ACE')
        sid, sid_length = parse_sid(data[offset + 8:offset + length])
        if 8 + sid_length > length: raise ValueError('malformed Windows ACE')
        # Signed like the .NET AccessMask projection the policy was written against.
        rules.append({'sid': sid, 'type': kind, 'flags': flags, 'mask': struct.unpack_from('<i', data, offset + 4)[0]})
        offset += length
    return rules


def provision_sddl(sid, directory):
    """Owner = user; protected DACL granting only user and SYSTEM full control."""
    if type(sid) is not str or not re.fullmatch(USER_SID, sid): raise ValueError('unsupported Windows user SID')
    inherit = 'OICI' if directory else ''
    return f'O:{sid}D:P(A;{inherit};FA;;;{sid})(A;{inherit};FA;;;SY)'


@functools.cache
def windows_api():
    import ctypes
    from ctypes import wintypes as w
    P, ref = ctypes.c_void_p, ctypes.POINTER
    advapi = ctypes.WinDLL('advapi32', use_last_error=True)
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    def bind(dll, name, restype, *argtypes):
        function = getattr(dll, name)
        function.restype, function.argtypes = restype, argtypes
        return function
    return SimpleNamespace(
        ctypes=ctypes, w=w,
        GetCurrentProcess=bind(kernel, 'GetCurrentProcess', w.HANDLE),
        CloseHandle=bind(kernel, 'CloseHandle', w.BOOL, w.HANDLE),
        LocalFree=bind(kernel, 'LocalFree', P, P),
        GetFileAttributesW=bind(kernel, 'GetFileAttributesW', w.DWORD, w.LPCWSTR),
        OpenProcessToken=bind(advapi, 'OpenProcessToken', w.BOOL, w.HANDLE, w.DWORD, ref(w.HANDLE)),
        GetTokenInformation=bind(advapi, 'GetTokenInformation', w.BOOL, w.HANDLE, ctypes.c_int, P, w.DWORD, ref(w.DWORD)),
        IsValidSid=bind(advapi, 'IsValidSid', w.BOOL, P),
        GetLengthSid=bind(advapi, 'GetLengthSid', w.DWORD, P),
        ConvertStringSecurityDescriptorToSecurityDescriptorW=bind(
            advapi, 'ConvertStringSecurityDescriptorToSecurityDescriptorW', w.BOOL, w.LPCWSTR, w.DWORD, ref(P), ref(w.ULONG)),
        GetSecurityDescriptorOwner=bind(advapi, 'GetSecurityDescriptorOwner', w.BOOL, P, ref(P), ref(w.BOOL)),
        GetSecurityDescriptorDacl=bind(advapi, 'GetSecurityDescriptorDacl', w.BOOL, P, ref(w.BOOL), ref(P), ref(w.BOOL)),
        GetSecurityDescriptorControl=bind(advapi, 'GetSecurityDescriptorControl', w.BOOL, P, ref(w.WORD), ref(w.DWORD)),
        SetNamedSecurityInfoW=bind(advapi, 'SetNamedSecurityInfoW', w.DWORD, w.LPWSTR, ctypes.c_int, w.DWORD, P, P, P, P),
        GetNamedSecurityInfoW=bind(advapi, 'GetNamedSecurityInfoW', w.DWORD, w.LPCWSTR, ctypes.c_int, w.DWORD,
                                   ref(P), ref(P), ref(P), ref(P), ref(P)))


def checked(api, ok):
    if not ok: raise api.ctypes.WinError(api.ctypes.get_last_error())


def sid_at(api, pointer):
    if not pointer or not api.IsValidSid(pointer): raise ValueError('invalid Windows SID')
    return parse_sid(api.ctypes.string_at(pointer, api.GetLengthSid(pointer)))[0]


def current_user_sid(api):
    ctypes, w = api.ctypes, api.w
    token = w.HANDLE()
    checked(api, api.OpenProcessToken(api.GetCurrentProcess(), TOKEN_QUERY, ctypes.byref(token)))
    try:
        size = w.DWORD()
        api.GetTokenInformation(token, TOKEN_USER, None, 0, ctypes.byref(size))  # size probe; fails by design
        if not size.value: raise ctypes.WinError(ctypes.get_last_error())
        buffer = ctypes.create_string_buffer(size.value)
        checked(api, api.GetTokenInformation(token, TOKEN_USER, buffer, size, ctypes.byref(size)))
        # TOKEN_USER begins with SID_AND_ATTRIBUTES.Sid.
        return sid_at(api, ctypes.c_void_p.from_buffer(buffer).value)
    finally:
        api.CloseHandle(token)


def provision_native(api, path, sid, directory):
    ctypes, w = api.ctypes, api.w
    descriptor = ctypes.c_void_p()
    checked(api, api.ConvertStringSecurityDescriptorToSecurityDescriptorW(
        provision_sddl(sid, directory), 1, ctypes.byref(descriptor), None))
    try:
        owner, dacl, present, defaulted = ctypes.c_void_p(), ctypes.c_void_p(), w.BOOL(), w.BOOL()
        checked(api, api.GetSecurityDescriptorOwner(descriptor, ctypes.byref(owner), ctypes.byref(defaulted)))
        checked(api, api.GetSecurityDescriptorDacl(descriptor, ctypes.byref(present), ctypes.byref(dacl), ctypes.byref(defaulted)))
        if not owner.value or not present.value or not dacl.value: raise ValueError('invalid private descriptor')
        error = api.SetNamedSecurityInfoW(path, SE_FILE_OBJECT, OWNER_SECURITY_INFORMATION | DACL_SECURITY_INFORMATION
                                          | PROTECTED_DACL_SECURITY_INFORMATION, owner, None, dacl, None)
        if error: raise ctypes.WinError(error)
    finally:
        api.LocalFree(descriptor)


def read_native(api, path):
    ctypes, w = api.ctypes, api.w
    owner, dacl, descriptor = ctypes.c_void_p(), ctypes.c_void_p(), ctypes.c_void_p()
    error = api.GetNamedSecurityInfoW(path, SE_FILE_OBJECT, OWNER_SECURITY_INFORMATION | DACL_SECURITY_INFORMATION,
                                      ctypes.byref(owner), None, ctypes.byref(dacl), None, ctypes.byref(descriptor))
    if error: raise ctypes.WinError(error)
    try:
        control, revision = w.WORD(), w.DWORD()
        checked(api, api.GetSecurityDescriptorControl(descriptor, ctypes.byref(control), ctypes.byref(revision)))
        # A NULL DACL yields no rules, which validate() rejects.
        rules = parse_acl(ctypes.string_at(dacl.value, ctypes.c_uint16.from_address(dacl.value + 2).value)) if dacl.value else []
        return sid_at(api, owner.value), control.value, rules
    finally:
        api.LocalFree(descriptor)


def native_report(path, provision):
    """Same projection the policy validates: user, owner, control, kind and DACL rules."""
    api = windows_api()
    attributes = api.GetFileAttributesW(path)
    if attributes == INVALID_FILE_ATTRIBUTES: raise api.ctypes.WinError(api.ctypes.get_last_error())
    if attributes & FILE_ATTRIBUTE_REPARSE_POINT: raise ValueError('reparse point')
    directory = bool(attributes & FILE_ATTRIBUTE_DIRECTORY)
    sid = current_user_sid(api)
    if provision: provision_native(api, path, sid, directory)
    owner, control, rules = read_native(api, path)
    return {'sid': sid, 'owner': owner, 'control': control, 'directory': directory, 'rules': rules}


def apply(path, *, provision=False):
    if os.name != 'nt': raise ValueError('native Windows ACL adapter requires Windows')
    path = Path(path)
    try:
        report = native_report(str(path), provision)
    except (OSError, ValueError) as exc:
        raise ValueError('Windows private ACL could not be verified') from exc
    return validate(report, directory=path.is_dir())
