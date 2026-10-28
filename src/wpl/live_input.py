"""Opt-in Windows foreground-window input for live sessions."""
from __future__ import annotations

import ctypes
import os
from dataclasses import dataclass

from .engine.events import Event, apply_event
from .engine.schedule import validate_duration

VK_BACK = 0x08
VK_LEFT = 0x25
VK_RETURN = 0x0D
VK_RIGHT = 0x27
KEYEVENTF_EXTENDEDKEY = 0x0001
KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_UNICODE = 0x0004
INPUT_KEYBOARD = 1
MIN_LIVE_DURATION_MS = 30 * 60_000
HWND = ctypes.c_void_p
DWORD = ctypes.c_ulong


def _windows_user32():
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.EnumWindows.argtypes = [ctypes.c_void_p, ctypes.c_ssize_t]
    user32.EnumWindows.restype = ctypes.c_int
    user32.IsWindow.argtypes = [HWND]
    user32.IsWindow.restype = ctypes.c_int
    user32.IsWindowVisible.argtypes = [HWND]
    user32.IsWindowVisible.restype = ctypes.c_int
    user32.GetWindowThreadProcessId.argtypes = [HWND, ctypes.POINTER(DWORD)]
    user32.GetWindowThreadProcessId.restype = DWORD
    user32.GetWindowTextLengthW.argtypes = [HWND]
    user32.GetWindowTextLengthW.restype = ctypes.c_int
    user32.GetWindowTextW.argtypes = [HWND, ctypes.c_wchar_p, ctypes.c_int]
    user32.GetWindowTextW.restype = ctypes.c_int
    user32.GetForegroundWindow.argtypes = []
    user32.GetForegroundWindow.restype = HWND
    user32.ShowWindow.argtypes = [HWND, ctypes.c_int]
    user32.ShowWindow.restype = ctypes.c_int
    user32.SetForegroundWindow.argtypes = [HWND]
    user32.SetForegroundWindow.restype = ctypes.c_int
    user32.SendInput.argtypes = [ctypes.c_uint, ctypes.c_void_p, ctypes.c_int]
    user32.SendInput.restype = ctypes.c_uint
    return user32


def _input_type():
    class MOUSEINPUT(ctypes.Structure):
        _fields_ = [("dx", ctypes.c_int32), ("dy", ctypes.c_int32), ("mouseData", ctypes.c_uint32),
                    ("dwFlags", ctypes.c_uint32), ("time", ctypes.c_uint32), ("dwExtraInfo", ctypes.c_size_t)]

    class KEYBDINPUT(ctypes.Structure):
        _fields_ = [("wVk", ctypes.c_ushort), ("wScan", ctypes.c_ushort), ("dwFlags", ctypes.c_uint32),
                    ("time", ctypes.c_uint32), ("dwExtraInfo", ctypes.c_size_t)]

    class HARDWAREINPUT(ctypes.Structure):
        _fields_ = [("uMsg", ctypes.c_uint32), ("wParamL", ctypes.c_ushort), ("wParamH", ctypes.c_ushort)]

    class INPUTUNION(ctypes.Union):
        _fields_ = [("mi", MOUSEINPUT), ("ki", KEYBDINPUT), ("hi", HARDWAREINPUT)]

    class INPUT(ctypes.Structure):
        _fields_ = [("type", ctypes.c_uint32), ("union", INPUTUNION)]

    return INPUT


class LiveInputError(RuntimeError):
    pass


class FocusChangedError(LiveInputError):
    pass


@dataclass(frozen=True)
class WindowTarget:
    hwnd: int
    process_id: int
    title: str


@dataclass(frozen=True)
class InputAction:
    kind: str
    value: str | int


def validate_live_duration(duration_ms: int) -> int:
    validate_duration(duration_ms)
    if duration_ms < MIN_LIVE_DURATION_MS:
        raise LiveInputError("Focused-window sessions require at least 30 minutes.")
    return duration_ms


def validate_target_state(target: WindowTarget, valid: bool, foreground_hwnd: int) -> None:
    if not valid:
        raise LiveInputError("The selected target window is no longer available.")
    if foreground_hwnd != target.hwnd:
        raise FocusChangedError("The selected target lost focus; no further input was sent.")


def plan_event_input(current_text: str, event: Event) -> tuple[list[InputAction], str]:
    """Plan an event as append-relative keystrokes and return the resulting live text."""
    if not event.is_text_op:
        return [], current_text
    updated = apply_event(current_text, event)
    assert event.pos is not None
    suffix_length = len(current_text) - event.pos - len(event.removed)
    actions: list[InputAction] = []
    if suffix_length:
        actions.append(InputAction("left", suffix_length))
    if event.removed:
        actions.append(InputAction("backspace", len(event.removed)))
    if event.inserted:
        actions.append(InputAction("text", event.inserted))
    if suffix_length:
        actions.append(InputAction("right", suffix_length))
    return actions, updated


def enumerate_windows() -> list[WindowTarget]:
    """List visible, titled top-level windows other than this process."""
    if os.name != "nt":
        return []
    user32 = _windows_user32()
    current_pid = os.getpid()
    targets: list[WindowTarget] = []
    callback_type = ctypes.WINFUNCTYPE(ctypes.c_int, HWND, ctypes.c_ssize_t)

    def visit(hwnd, _lparam):
        if not user32.IsWindowVisible(hwnd):
            return True
        process_id = DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(process_id))
        if process_id.value == current_pid:
            return True
        length = user32.GetWindowTextLengthW(hwnd)
        if not length:
            return True
        title = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, title, len(title))
        if title.value:
            targets.append(WindowTarget(int(hwnd), process_id.value, title.value))
        return True

    callback = callback_type(visit)
    user32.EnumWindows(ctypes.cast(callback, ctypes.c_void_p), 0)
    return targets


class WindowsInputBackend:
    """Send Unicode keystrokes only while the selected window remains foreground."""

    def __init__(self, target: WindowTarget) -> None:
        if os.name != "nt":
            raise LiveInputError("Focused-window input is available only on Windows.")
        self.target = target
        self._text = ""
        self._user32 = _windows_user32()

    @property
    def text(self) -> str:
        return self._text

    def target_is_valid(self) -> bool:
        if not self._user32.IsWindow(self.target.hwnd) or not self._user32.IsWindowVisible(self.target.hwnd):
            return False
        process_id = DWORD()
        self._user32.GetWindowThreadProcessId(self.target.hwnd, ctypes.byref(process_id))
        return process_id.value == self.target.process_id

    def foreground_hwnd(self) -> int:
        return int(self._user32.GetForegroundWindow() or 0)

    def is_own_process_foreground(self) -> bool:
        hwnd = self.foreground_hwnd()
        if not hwnd:
            return False
        process_id = DWORD()
        self._user32.GetWindowThreadProcessId(hwnd, ctypes.byref(process_id))
        return process_id.value == os.getpid()

    def has_target_focus(self) -> bool:
        try:
            self._assert_foreground()
        except LiveInputError:
            return False
        return True

    def activate_target(self) -> None:
        if not self.target_is_valid():
            raise LiveInputError("The selected target window is no longer available.")
        self._user32.ShowWindow(self.target.hwnd, 9)
        self._user32.SetForegroundWindow(self.target.hwnd)
        self._assert_foreground()

    def apply_event(self, event: Event) -> None:
        actions, updated = plan_event_input(self._text, event)
        for action in actions:
            if action.kind in ("left", "right", "backspace"):
                key = {"left": VK_LEFT, "right": VK_RIGHT, "backspace": VK_BACK}[action.kind]
                flags = KEYEVENTF_EXTENDEDKEY if action.kind in ("left", "right") else 0
                for _ in range(int(action.value)):
                    self._send_key(key, flags)
            else:
                self._send_text(str(action.value))
        self._text = updated

    def _assert_foreground(self) -> None:
        validate_target_state(self.target, self.target_is_valid(), self.foreground_hwnd())

    def _send_key(self, key: int, flags: int = 0) -> None:
        self._assert_foreground()
        self._send_inputs([(key, 0, flags), (key, 0, flags | KEYEVENTF_KEYUP)])

    def _send_text(self, text: str) -> None:
        for index, char in enumerate(text):
            if char == "\r":
                if index + 1 < len(text) and text[index + 1] == "\n":
                    continue
                self._send_key(VK_RETURN)
                continue
            if char == "\n":
                self._send_key(VK_RETURN)
                continue
            self._assert_foreground()
            units = char.encode("utf-16-le")
            for offset in range(0, len(units), 2):
                scan = int.from_bytes(units[offset:offset + 2], "little")
                self._send_inputs([(0, scan, KEYEVENTF_UNICODE), (0, scan, KEYEVENTF_UNICODE | KEYEVENTF_KEYUP)])

    def _send_inputs(self, keys: list[tuple[int, int, int]]) -> None:
        INPUT = _input_type()
        records = (INPUT * len(keys))()
        for index, (key, scan, flags) in enumerate(keys):
            records[index].type = INPUT_KEYBOARD
            records[index].union.ki.wVk = key
            records[index].union.ki.wScan = scan
            records[index].union.ki.dwFlags = flags
        sent = self._user32.SendInput(len(records), ctypes.cast(records, ctypes.c_void_p), ctypes.sizeof(INPUT))
        if sent != len(records):
            raise LiveInputError("Windows did not accept the complete input event.")
