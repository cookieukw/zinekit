"""A permissive stand-in for PySide6, used only by tests/test_gui_smoke.py where
PySide6 is not installed.  It runs the editor's Python logic (signals, state,
threads) without drawing anything; it does not check Qt's API."""
__version__ = "0.0-fake"
FAKE = True
