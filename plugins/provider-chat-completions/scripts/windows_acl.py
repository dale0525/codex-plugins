"""Small Windows ACL verifier for owner-only provider credential caches.

The verifier intentionally uses the native security descriptor APIs instead of
POSIX mode bits.  Windows ``st_mode`` values do not describe the NTFS DACL and
can therefore report a false permission failure for a cache written by Codex
Sync.
"""

from __future__ import annotations

import ctypes
from ctypes import wintypes
import os
from pathlib import Path


class WindowsAclError(Exception):
    """Raised when a Windows security descriptor cannot be proven private."""


SE_FILE_OBJECT = 1
OWNER_SECURITY_INFORMATION = 0x00000001
DACL_SECURITY_INFORMATION = 0x00000004
SE_DACL_PROTECTED = 0x1000
TOKEN_QUERY = 0x0008
TOKEN_USER_INFORMATION = 1
ACCESS_ALLOWED_ACE_TYPE = 0
INHERITED_ACE = 0x10
LOCAL_SYSTEM_SID = "S-1-5-18"


class _SidAndAttributes(ctypes.Structure):
    _fields_ = [("Sid", ctypes.c_void_p), ("Attributes", wintypes.DWORD)]


class _TokenUser(ctypes.Structure):
    _fields_ = [("User", _SidAndAttributes)]


class _AclSizeInformation(ctypes.Structure):
    _fields_ = [
        ("AceCount", wintypes.DWORD),
        ("AclBytesInUse", wintypes.DWORD),
        ("AclBytesFree", wintypes.DWORD),
    ]


class _AceHeader(ctypes.Structure):
    _pack_ = 1
    _fields_ = [
        ("AceType", ctypes.c_ubyte),
        ("AceFlags", ctypes.c_ubyte),
        ("AceSize", wintypes.WORD),
    ]


def _aligned_buffer(size: int):
    """Return an aligned, writable buffer for a variable-sized Win32 value."""
    word = ctypes.sizeof(ctypes.c_size_t)
    count = (size + word - 1) // word
    return (ctypes.c_size_t * count)()


def _raise() -> None:
    raise WindowsAclError()


def ensure_no_reparse_ancestors(path: Path) -> None:
    """Reject symlink/junction ancestors before reading a credential cache."""
    current = os.path.abspath(os.fspath(path))
    while True:
        try:
            metadata = os.lstat(current)
        except OSError as exc:
            raise WindowsAclError() from exc
        if getattr(metadata, "st_file_attributes", 0) & 0x0400:
            raise WindowsAclError()
        parent = os.path.dirname(current)
        if parent == current:
            return
        current = parent


def _current_user_sid(kernel32, advapi32):
    # Token APIs are exported by advapi32; process and handle helpers live in kernel32.
    kernel32.GetCurrentProcess.restype = wintypes.HANDLE
    advapi32.OpenProcessToken.argtypes = [
        wintypes.HANDLE,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.HANDLE),
    ]
    advapi32.OpenProcessToken.restype = wintypes.BOOL
    advapi32.GetTokenInformation.argtypes = [
        wintypes.HANDLE,
        wintypes.DWORD,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
    ]
    advapi32.GetTokenInformation.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL
    token = wintypes.HANDLE()
    if not advapi32.OpenProcessToken(
        kernel32.GetCurrentProcess(), TOKEN_QUERY, ctypes.byref(token)
    ):
        _raise()
    try:
        required = wintypes.DWORD()
        advapi32.GetTokenInformation(
            token,
            TOKEN_USER_INFORMATION,
            None,
            0,
            ctypes.byref(required),
        )
        if required.value <= 0:
            _raise()
        storage = _aligned_buffer(required.value)
        if not advapi32.GetTokenInformation(
            token,
            TOKEN_USER_INFORMATION,
            ctypes.byref(storage),
            required.value,
            ctypes.byref(required),
        ):
            _raise()
        token_user = ctypes.cast(ctypes.byref(storage), ctypes.POINTER(_TokenUser)).contents
        if not token_user.User.Sid:
            _raise()
        # Keep the backing storage alive while callers compare the SID.
        return token_user.User.Sid, storage
    finally:
        kernel32.CloseHandle(token)


def ensure_owner_only(path: Path) -> None:
    """Require a protected DACL containing only the current user (and no inheritance)."""
    path_value = os.fspath(path)
    ensure_no_reparse_ancestors(path_value)
    try:
        metadata = os.lstat(path_value)
    except OSError as exc:
        raise WindowsAclError() from exc
    file_attributes = getattr(metadata, "st_file_attributes", 0)
    if file_attributes & 0x0400:  # FILE_ATTRIBUTE_REPARSE_POINT
        raise WindowsAclError()

    try:
        advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    except (AttributeError, OSError, ValueError) as exc:
        raise WindowsAclError() from exc

    advapi32.GetNamedSecurityInfoW.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(ctypes.c_void_p),
    ]
    advapi32.GetNamedSecurityInfoW.restype = wintypes.DWORD
    advapi32.EqualSid.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    advapi32.EqualSid.restype = wintypes.BOOL
    advapi32.IsValidAcl.argtypes = [ctypes.c_void_p]
    advapi32.IsValidAcl.restype = wintypes.BOOL
    advapi32.IsValidSid.argtypes = [ctypes.c_void_p]
    advapi32.IsValidSid.restype = wintypes.BOOL
    advapi32.GetLengthSid.argtypes = [ctypes.c_void_p]
    advapi32.GetLengthSid.restype = wintypes.DWORD
    advapi32.GetSecurityDescriptorControl.argtypes = [
        ctypes.c_void_p,
        ctypes.POINTER(wintypes.WORD),
        ctypes.POINTER(wintypes.DWORD),
    ]
    advapi32.GetSecurityDescriptorControl.restype = wintypes.BOOL
    advapi32.GetSecurityDescriptorDacl.argtypes = [
        ctypes.c_void_p,
        ctypes.POINTER(wintypes.BOOL),
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(wintypes.BOOL),
    ]
    advapi32.GetSecurityDescriptorDacl.restype = wintypes.BOOL
    advapi32.GetAclInformation.argtypes = [
        ctypes.c_void_p,
        ctypes.c_void_p,
        wintypes.DWORD,
        wintypes.DWORD,
    ]
    advapi32.GetAclInformation.restype = wintypes.BOOL
    advapi32.GetAce.argtypes = [
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(ctypes.c_void_p),
    ]
    advapi32.GetAce.restype = wintypes.BOOL
    advapi32.ConvertSidToStringSidW.argtypes = [
        ctypes.c_void_p,
        ctypes.POINTER(ctypes.c_wchar_p),
    ]
    advapi32.ConvertSidToStringSidW.restype = wintypes.BOOL
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    kernel32.LocalFree.restype = ctypes.c_void_p

    descriptor = ctypes.c_void_p()
    owner = ctypes.c_void_p()
    dacl = ctypes.c_void_p()
    current_sid, _sid_storage = _current_user_sid(kernel32, advapi32)
    try:
        result = advapi32.GetNamedSecurityInfoW(
            path_value,
            SE_FILE_OBJECT,
            OWNER_SECURITY_INFORMATION | DACL_SECURITY_INFORMATION,
            ctypes.byref(owner),
            None,
            ctypes.byref(dacl),
            None,
            ctypes.byref(descriptor),
        )
        if result != 0 or not owner.value or not descriptor.value:
            _raise()
        if not advapi32.EqualSid(owner, current_sid):
            _raise()

        control = wintypes.WORD()
        revision = wintypes.DWORD()
        if not advapi32.GetSecurityDescriptorControl(
            descriptor, ctypes.byref(control), ctypes.byref(revision)
        ) or not (control.value & SE_DACL_PROTECTED):
            _raise()

        present = wintypes.BOOL()
        defaulted = wintypes.BOOL()
        if not advapi32.GetSecurityDescriptorDacl(
            descriptor,
            ctypes.byref(present),
            ctypes.byref(dacl),
            ctypes.byref(defaulted),
        ) or not present.value or not dacl.value:
            _raise()
        if not advapi32.IsValidAcl(dacl):
            _raise()

        acl_info = _AclSizeInformation()
        if not advapi32.GetAclInformation(
            dacl,
            ctypes.byref(acl_info),
            ctypes.sizeof(acl_info),
            2,  # AclSizeInformation
        ):
            _raise()
        # Codex Sync writes one explicit user ACE.  A LocalSystem ACE is also
        # trusted because Windows services may legitimately need to inspect the
        # cache; all other principals, including inherited/broad groups, fail.
        if acl_info.AceCount not in (1, 2):
            _raise()
        saw_user = False
        saw_system = False
        for index in range(acl_info.AceCount):
            ace = ctypes.c_void_p()
            if not advapi32.GetAce(dacl, index, ctypes.byref(ace)) or not ace.value:
                _raise()
            header = _AceHeader.from_address(ace.value)
            if header.AceType != ACCESS_ALLOWED_ACE_TYPE or header.AceFlags & INHERITED_ACE:
                _raise()
            dacl_start = dacl.value
            dacl_end = dacl_start + acl_info.AclBytesInUse
            if (
                header.AceSize < 16
                or ace.value < dacl_start + 8
                or ace.value + header.AceSize > dacl_end
            ):
                _raise()
            sid_address = ace.value + 8
            sid_count = ctypes.c_ubyte.from_address(sid_address + 1).value
            sid_length = 8 + sid_count * 4
            if sid_length > header.AceSize - 8:
                _raise()
            sid = ctypes.c_void_p(sid_address)
            if not advapi32.IsValidSid(sid) or advapi32.GetLengthSid(sid) != sid_length:
                _raise()
            if advapi32.EqualSid(sid, current_sid):
                if saw_user:
                    _raise()
                saw_user = True
                continue
            sid_text = ctypes.c_wchar_p()
            if not advapi32.ConvertSidToStringSidW(sid, ctypes.byref(sid_text)):
                _raise()
            try:
                is_system = sid_text.value == LOCAL_SYSTEM_SID
            finally:
                kernel32.LocalFree(ctypes.cast(sid_text, ctypes.c_void_p))
            if not is_system or saw_system:
                _raise()
            saw_system = True
        if not saw_user:
            _raise()
    except WindowsAclError:
        raise
    except (AttributeError, OSError, TypeError, ValueError) as exc:
        raise WindowsAclError() from exc
    finally:
        if descriptor.value:
            kernel32.LocalFree(descriptor)
