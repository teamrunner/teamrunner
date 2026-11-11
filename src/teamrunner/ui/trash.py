# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright 2026 Kumar Muthuraman
"""Recoverable deletion using the operating system's trash; never unlink as fallback."""

from datetime import datetime
import os
from pathlib import Path
import shutil
import sys
from urllib.parse import quote
from uuid import uuid4


def move_to_trash(path: Path):
    if sys.platform == "win32":
        import ctypes
        from ctypes import wintypes

        class Operation(ctypes.Structure):
            _fields_ = [("hwnd", wintypes.HWND), ("wFunc", wintypes.UINT),
                        ("pFrom", wintypes.LPCWSTR), ("pTo", wintypes.LPCWSTR),
                        ("fFlags", wintypes.WORD), ("fAnyOperationsAborted", wintypes.BOOL),
                        ("hNameMappings", ctypes.c_void_p), ("lpszProgressTitle", wintypes.LPCWSTR)]

        operation = Operation()
        operation.wFunc = 3  # FO_DELETE
        operation.pFrom = str(path) + "\0\0"
        operation.fFlags = 0x40 | 0x10 | 0x4 | 0x400  # ALLOWUNDO, NOCONFIRMATION, SILENT, NOERRORUI
        if ctypes.windll.shell32.SHFileOperationW(ctypes.byref(operation)) or operation.fAnyOperationsAborted:
            raise OSError("Could not move the file to the Recycle Bin.")
        return
    if sys.platform == "darwin":
        _mac_trash(path)
        return
    trash = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share")) / "Trash"
    files, info = trash / "files", trash / "info"
    files.mkdir(parents=True, mode=0o700, exist_ok=True)
    info.mkdir(parents=True, mode=0o700, exist_ok=True)
    name = f"{path.stem}-{uuid4().hex}{path.suffix}"
    metadata = info / (name + ".trashinfo")
    metadata.write_text(f"[Trash Info]\nPath={quote(str(path))}\n"
                        f"DeletionDate={datetime.now():%Y-%m-%dT%H:%M:%S}\n", encoding="utf-8")
    try:
        shutil.move(str(path), files / name)
    except OSError:
        metadata.unlink()
        raise


def _mac_trash(path: Path):
    """Use Finder's native trash service, including its Put Back metadata."""
    import ctypes

    ctypes.CDLL("/System/Library/Frameworks/Foundation.framework/Foundation")
    objc = ctypes.CDLL("/usr/lib/libobjc.A.dylib")
    pointer = ctypes.c_void_p
    objc.objc_getClass.argtypes = [ctypes.c_char_p]
    objc.objc_getClass.restype = pointer
    objc.sel_registerName.argtypes = [ctypes.c_char_p]
    objc.sel_registerName.restype = pointer
    objc.objc_autoreleasePoolPush.restype = pointer
    objc.objc_autoreleasePoolPop.argtypes = [pointer]
    pool = objc.objc_autoreleasePoolPush()
    try:
        address = ctypes.cast(objc.objc_msgSend, pointer).value
        send = ctypes.CFUNCTYPE(pointer, pointer, pointer)(address)
        send_string = ctypes.CFUNCTYPE(pointer, pointer, pointer, ctypes.c_char_p)(address)
        send_pointer = ctypes.CFUNCTYPE(pointer, pointer, pointer, pointer)(address)
        trash = ctypes.CFUNCTYPE(ctypes.c_bool, pointer, pointer, pointer,
                                ctypes.POINTER(pointer), ctypes.POINTER(pointer))(address)
        selector = objc.sel_registerName
        text = send_string(objc.objc_getClass(b"NSString"), selector(b"stringWithUTF8String:"),
                           os.fsencode(path))
        url = send_pointer(objc.objc_getClass(b"NSURL"), selector(b"fileURLWithPath:"), text)
        manager = send(objc.objc_getClass(b"NSFileManager"), selector(b"defaultManager"))
        error = pointer()
        if not trash(manager, selector(b"trashItemAtURL:resultingItemURL:error:"),
                     url, None, ctypes.byref(error)):
            raise OSError("Could not move the file to the Trash.")
    finally:
        objc.objc_autoreleasePoolPop(pool)
