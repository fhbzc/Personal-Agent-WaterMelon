"""ONE PICKER FOR FILES AND FOLDERS (the user, 2026-11-09: "为什么不能做一个按钮，根据我选的位置系统
自动判断是文件夹还是文件").

On a Mac the system's own panel takes either (NSOpenPanel, files and directories both). Windows'
picker is for files or for folders, never both: so its file picker, with one button more of its
own (IFileDialogCustomize) - "Open" takes the files chosen, as ever; the added button takes what is
selected, files or folders, or the folder it is in when nothing is. Whichever each is, is told
from the disk afterwards. Elsewhere, or where this fails, None: the caller asks which.
"""

from __future__ import annotations

import logging
import sys
from typing import Any, List, Optional

log = logging.getLogger(__name__)


def pick(window: Any, words: Any = None) -> Optional[List[str]]:
    """Paths chosen - files and folders alike - [] if none; None where it cannot be done here."""
    said = words or (lambda text: text)
    try:
        if sys.platform.startswith("win"):
            return _windows(window, said)
        if sys.platform == "darwin":
            return _mac()
    except Exception:
        log.warning("the one picker failed; asked which instead", exc_info=True)
    return None


# -- a Mac -----------------------------------------------------------------------------------
def _mac() -> List[str]:
    import AppKit  # type: ignore
    panel = AppKit.NSOpenPanel.openPanel()
    panel.setCanChooseFiles_(True)
    panel.setCanChooseDirectories_(True)
    panel.setAllowsMultipleSelection_(True)
    if panel.runModal() != AppKit.NSModalResponseOK:
        return []
    return [str(url.path()) for url in panel.URLs()]


# -- Windows ---------------------------------------------------------------------------------
_COM: dict = {}
SIGDN_FILESYSPATH = 0x80058000
FOS_FORCEFILESYSTEM, FOS_ALLOWMULTISELECT, FOS_PATHMUSTEXIST = 0x40, 0x200, 0x800
FOS_FILEMUSTEXIST = 0x1000
ADD_BUTTON = 601


def _interfaces() -> dict:
    """The shell's picker, as comtypes knows it - defined once."""
    if _COM:
        return _COM
    import ctypes
    from ctypes import HRESULT, POINTER, c_uint, c_ulong, c_void_p, c_wchar_p
    from ctypes.wintypes import DWORD, HWND
    from comtypes import COMMETHOD, GUID, IUnknown

    class IShellItem(IUnknown):
        _iid_ = GUID("{43826D1E-E718-42EE-BC55-A1E261C37BFE}")

    IShellItem._methods_ = [
        COMMETHOD([], HRESULT, "BindToHandler", (["in"], c_void_p), (["in"], POINTER(GUID)),
                  (["in"], POINTER(GUID)), (["out"], POINTER(c_void_p))),
        COMMETHOD([], HRESULT, "GetParent", (["out"], POINTER(POINTER(IShellItem)))),
        COMMETHOD([], HRESULT, "GetDisplayName", (["in"], c_ulong), (["out"], POINTER(c_void_p))),
        COMMETHOD([], HRESULT, "GetAttributes", (["in"], c_ulong), (["out"], POINTER(c_ulong))),
        COMMETHOD([], HRESULT, "Compare", (["in"], POINTER(IShellItem)), (["in"], c_ulong),
                  (["out"], POINTER(ctypes.c_int))),
    ]

    class IShellItemArray(IUnknown):
        _iid_ = GUID("{B63EA76D-1F85-456F-A19C-48159EFA858B}")
        _methods_ = [
            COMMETHOD([], HRESULT, "BindToHandler", (["in"], c_void_p), (["in"], POINTER(GUID)),
                      (["in"], POINTER(GUID)), (["out"], POINTER(c_void_p))),
            COMMETHOD([], HRESULT, "GetPropertyStore", (["in"], c_ulong), (["in"], POINTER(GUID)),
                      (["out"], POINTER(c_void_p))),
            COMMETHOD([], HRESULT, "GetPropertyDescriptionList", (["in"], c_void_p),
                      (["in"], POINTER(GUID)), (["out"], POINTER(c_void_p))),
            COMMETHOD([], HRESULT, "GetAttributes", (["in"], c_uint), (["in"], c_ulong),
                      (["out"], POINTER(c_ulong))),
            COMMETHOD([], HRESULT, "GetCount", (["out"], POINTER(DWORD))),
            COMMETHOD([], HRESULT, "GetItemAt", (["in"], DWORD), (["out"], POINTER(POINTER(IShellItem)))),
        ]

    class IFileDialogEvents(IUnknown):
        _iid_ = GUID("{973510DB-7D7F-452B-8975-74A85828D354}")
        _methods_ = [
            COMMETHOD([], HRESULT, "OnFileOk", (["in"], c_void_p)),
            COMMETHOD([], HRESULT, "OnFolderChanging", (["in"], c_void_p), (["in"], c_void_p)),
            COMMETHOD([], HRESULT, "OnFolderChange", (["in"], c_void_p)),
            COMMETHOD([], HRESULT, "OnSelectionChange", (["in"], c_void_p)),
            COMMETHOD([], HRESULT, "OnShareViolation", (["in"], c_void_p), (["in"], c_void_p),
                      (["in"], POINTER(c_uint))),
            COMMETHOD([], HRESULT, "OnTypeChange", (["in"], c_void_p)),
            COMMETHOD([], HRESULT, "OnOverwrite", (["in"], c_void_p), (["in"], c_void_p),
                      (["in"], POINTER(c_uint))),
        ]

    class IFileDialogControlEvents(IUnknown):
        _iid_ = GUID("{36116642-D713-4B97-9B83-7484A9D00433}")
        _methods_ = [
            COMMETHOD([], HRESULT, "OnItemSelected", (["in"], c_void_p), (["in"], DWORD), (["in"], DWORD)),
            COMMETHOD([], HRESULT, "OnButtonClicked", (["in"], c_void_p), (["in"], DWORD)),
            COMMETHOD([], HRESULT, "OnCheckButtonToggled", (["in"], c_void_p), (["in"], DWORD),
                      (["in"], ctypes.c_int)),
            COMMETHOD([], HRESULT, "OnControlActivating", (["in"], c_void_p), (["in"], DWORD)),
        ]

    class IFileDialog(IUnknown):
        _iid_ = GUID("{42F85136-DB7E-439C-85F1-E4075D135FC8}")
        _methods_ = [
            COMMETHOD([], HRESULT, "Show", (["in"], HWND)),          # IModalWindow
            COMMETHOD([], HRESULT, "SetFileTypes", (["in"], c_uint), (["in"], c_void_p)),
            COMMETHOD([], HRESULT, "SetFileTypeIndex", (["in"], c_uint)),
            COMMETHOD([], HRESULT, "GetFileTypeIndex", (["out"], POINTER(c_uint))),
            COMMETHOD([], HRESULT, "Advise", (["in"], POINTER(IFileDialogEvents)),
                      (["out"], POINTER(DWORD))),
            COMMETHOD([], HRESULT, "Unadvise", (["in"], DWORD)),
            COMMETHOD([], HRESULT, "SetOptions", (["in"], DWORD)),
            COMMETHOD([], HRESULT, "GetOptions", (["out"], POINTER(DWORD))),
            COMMETHOD([], HRESULT, "SetDefaultFolder", (["in"], POINTER(IShellItem))),
            COMMETHOD([], HRESULT, "SetFolder", (["in"], POINTER(IShellItem))),
            COMMETHOD([], HRESULT, "GetFolder", (["out"], POINTER(POINTER(IShellItem)))),
            COMMETHOD([], HRESULT, "GetCurrentSelection", (["out"], POINTER(POINTER(IShellItem)))),
            COMMETHOD([], HRESULT, "SetFileName", (["in"], c_wchar_p)),
            COMMETHOD([], HRESULT, "GetFileName", (["out"], POINTER(c_void_p))),
            COMMETHOD([], HRESULT, "SetTitle", (["in"], c_wchar_p)),
            COMMETHOD([], HRESULT, "SetOkButtonLabel", (["in"], c_wchar_p)),
            COMMETHOD([], HRESULT, "SetFileNameLabel", (["in"], c_wchar_p)),
            COMMETHOD([], HRESULT, "GetResult", (["out"], POINTER(POINTER(IShellItem)))),
            COMMETHOD([], HRESULT, "AddPlace", (["in"], POINTER(IShellItem)), (["in"], c_uint)),
            COMMETHOD([], HRESULT, "SetDefaultExtension", (["in"], c_wchar_p)),
            COMMETHOD([], HRESULT, "Close", (["in"], HRESULT)),
        ]

    class IFileOpenDialog(IFileDialog):
        _iid_ = GUID("{D57C7288-D4AD-4768-BE02-9D969532D960}")
        _methods_ = [
            COMMETHOD([], HRESULT, "SetClientGuid", (["in"], POINTER(GUID))),
            COMMETHOD([], HRESULT, "ClearClientData"),
            COMMETHOD([], HRESULT, "SetFilter", (["in"], c_void_p)),
            COMMETHOD([], HRESULT, "GetResults", (["out"], POINTER(POINTER(IShellItemArray)))),
            COMMETHOD([], HRESULT, "GetSelectedItems", (["out"], POINTER(POINTER(IShellItemArray)))),
        ]

    class IFileDialogCustomize(IUnknown):
        _iid_ = GUID("{E6FDD21A-163F-4975-9C8C-A69F1BA37034}")
        _methods_ = [
            COMMETHOD([], HRESULT, "EnableOpenDropDown", (["in"], DWORD)),
            COMMETHOD([], HRESULT, "AddMenu", (["in"], DWORD), (["in"], c_wchar_p)),
            COMMETHOD([], HRESULT, "AddPushButton", (["in"], DWORD), (["in"], c_wchar_p)),
        ]

    _COM.update(IShellItem=IShellItem, IShellItemArray=IShellItemArray, IFileDialog=IFileDialog,
                IFileOpenDialog=IFileOpenDialog, IFileDialogCustomize=IFileDialogCustomize,
                IFileDialogEvents=IFileDialogEvents, IFileDialogControlEvents=IFileDialogControlEvents,
                CLSID=GUID("{DC1C5A9C-E88A-4DDE-A5A1-60F82A20AEF7}"))
    return _COM


def _path(item: Any) -> Optional[str]:
    import ctypes
    raw = item.GetDisplayName(SIGDN_FILESYSPATH)
    if not raw:
        return None
    try:
        return ctypes.wstring_at(raw)
    finally:
        ctypes.windll.ole32.CoTaskMemFree(ctypes.c_void_p(raw))


def _paths(items: Any) -> List[str]:
    out = []
    for at in range(items.GetCount()):
        one = _path(items.GetItemAt(at))
        if one:
            out.append(one)
    return out


def _windows(window: Any, said: Any) -> List[str]:
    import comtypes
    from comtypes import COMObject
    from comtypes.client import CreateObject
    com = _interfaces()
    try:
        comtypes.CoInitialize()
    except OSError:
        pass                                             # already, on this thread
    dialog = CreateObject(com["CLSID"], interface=com["IFileOpenDialog"])
    dialog.SetOptions(dialog.GetOptions() | FOS_ALLOWMULTISELECT | FOS_FORCEFILESYSTEM
                      | FOS_PATHMUSTEXIST | FOS_FILEMUSTEXIST)
    dialog.SetTitle(said("Add files or folders"))
    custom = dialog.QueryInterface(com["IFileDialogCustomize"])
    custom.AddPushButton(ADD_BUTTON, said("Add the selected, or this folder"))
    taken: List[str] = []

    class Heard(COMObject):
        _com_interfaces_ = [com["IFileDialogEvents"], com["IFileDialogControlEvents"]]

        # Every one answered S_OK: an answer of "not done" from some stops the picker moving.
        def OnFileOk(self, *_a): return 0
        def OnFolderChanging(self, *_a): return 0
        def OnFolderChange(self, *_a): return 0
        def OnSelectionChange(self, *_a): return 0
        def OnShareViolation(self, *_a): return 0
        def OnTypeChange(self, *_a): return 0
        def OnOverwrite(self, *_a): return 0
        def OnItemSelected(self, *_a): return 0
        def OnCheckButtonToggled(self, *_a): return 0
        def OnControlActivating(self, *_a): return 0

        def OnButtonClicked(self, _custom: Any, which: int) -> int:
            log.debug("the picker's button %s", which)
            if which != ADD_BUTTON:
                return 0
            try:
                chosen = []
                try:
                    chosen = _paths(dialog.GetSelectedItems())
                except Exception:
                    pass                                 # nothing selected
                if not chosen:
                    here = _path(dialog.GetFolder())
                    chosen = [here] if here else []
                taken.extend(chosen)
                if chosen:
                    dialog.Close(1)                      # S_FALSE: Show returns, its results unread
            except Exception:
                log.warning("the picker's own button failed", exc_info=True)
            return 0

    heard = Heard()
    cookie = dialog.Advise(heard.QueryInterface(com["IFileDialogEvents"]))
    try:
        owner = int(window.wm_frame(), 16) if window is not None else 0
    except Exception:
        owner = 0
    try:
        try:
            dialog.Show(owner)
        except comtypes.COMError:
            return list(taken)                           # cancelled
        if taken:
            return list(taken)
        return _paths(dialog.GetResults())
    finally:
        try:
            dialog.Unadvise(cookie)
        except Exception:
            pass
