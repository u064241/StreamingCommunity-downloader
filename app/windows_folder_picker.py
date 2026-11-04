"""Native Windows Shell folder selection, called on a worker thread."""
import ctypes
import os
import threading
from ctypes import wintypes

_picker_lock = threading.Lock()


def available() -> bool:
    return os.name == "nt"


def choose_folder(initial: str = "") -> str | None:
    if not available():
        raise RuntimeError("La scelta cartella nativa è disponibile solo su Windows.")
    if not _picker_lock.acquire(blocking=False):
        raise RuntimeError("Una finestra di scelta cartella è già aperta.")
    try:
        return _show_dialog(initial)
    finally:
        _picker_lock.release()


def _show_dialog(initial: str) -> str | None:
    shell = ctypes.WinDLL("shell32", use_last_error=True)
    ole = ctypes.WinDLL("ole32", use_last_error=True)
    user = ctypes.WinDLL("user32", use_last_error=True)
    ole.CoInitializeEx.argtypes = [ctypes.c_void_p, wintypes.DWORD]
    ole.CoInitializeEx.restype = ctypes.c_long
    ole.CoUninitialize.argtypes = []
    ole.CoTaskMemFree.argtypes = [ctypes.c_void_p]
    result = ole.CoInitializeEx(None, 2)  # Apartment-threaded Shell dialog.
    if result < 0:
        raise RuntimeError("Impossibile inizializzare la finestra di Windows.")
    try:
        callback_type = ctypes.WINFUNCTYPE(ctypes.c_int, wintypes.HWND, wintypes.UINT,
                                         ctypes.c_ssize_t, ctypes.c_ssize_t)
        user.SendMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, ctypes.c_void_p]
        user.SendMessageW.restype = ctypes.c_ssize_t
        user.GetForegroundWindow.restype = wintypes.HWND
        initial_buffer = ctypes.create_unicode_buffer(initial)

        @callback_type
        def initialize(hwnd, message, lparam, data):
            if message == 1 and initial:  # BFFM_INITIALIZED / BFFM_SETSELECTIONW
                user.SendMessageW(hwnd, 0x467, 1, ctypes.cast(initial_buffer, ctypes.c_void_p))
            return 0

        class BrowseInfo(ctypes.Structure):
            _fields_ = [("hwndOwner", wintypes.HWND), ("pidlRoot", ctypes.c_void_p),
                        ("pszDisplayName", wintypes.LPWSTR), ("lpszTitle", wintypes.LPCWSTR),
                        ("ulFlags", wintypes.UINT), ("lpfn", callback_type),
                        ("lParam", ctypes.c_ssize_t), ("iImage", ctypes.c_int)]

        display = ctypes.create_unicode_buffer(32768)
        info = BrowseInfo(user.GetForegroundWindow(), None, ctypes.cast(display, wintypes.LPWSTR),
                          "Scegli la cartella dei download", 0x51, initialize, 0, 0)
        shell.SHBrowseForFolderW.argtypes = [ctypes.POINTER(BrowseInfo)]
        shell.SHBrowseForFolderW.restype = ctypes.c_void_p
        shell.SHGetPathFromIDListEx.argtypes = [ctypes.c_void_p, wintypes.LPWSTR, wintypes.DWORD, wintypes.DWORD]
        shell.SHGetPathFromIDListEx.restype = wintypes.BOOL
        selection = shell.SHBrowseForFolderW(ctypes.byref(info))
        if not selection:
            return None
        try:
            path = ctypes.create_unicode_buffer(32768)
            if not shell.SHGetPathFromIDListEx(selection, path, len(path), 0):
                raise RuntimeError("La selezione non è una cartella del filesystem.")
            return path.value
        finally:
            ole.CoTaskMemFree(selection)
    finally:
        ole.CoUninitialize()
