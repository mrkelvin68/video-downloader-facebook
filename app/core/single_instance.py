import ctypes
import os

_mutex_handle = None


def acquire_single_instance() -> bool:
    global _mutex_handle
    if os.name != "nt":
        return True

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateMutexW.argtypes = (
        ctypes.c_void_p,
        ctypes.c_bool,
        ctypes.c_wchar_p,
    )
    kernel32.CreateMutexW.restype = ctypes.c_void_p
    kernel32.CloseHandle.argtypes = (ctypes.c_void_p,)
    kernel32.CloseHandle.restype = ctypes.c_bool
    ctypes.set_last_error(0)
    handle = kernel32.CreateMutexW(
        None, True, "Local\\VideoDownloaderFacebook"
    )
    if not handle:
        raise ctypes.WinError(ctypes.get_last_error())
    if ctypes.get_last_error() == 183:
        kernel32.CloseHandle(handle)
        return False
    _mutex_handle = handle
    return True
