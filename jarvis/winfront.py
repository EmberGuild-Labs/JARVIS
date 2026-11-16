"""Window management: list, find, focus and arrange windows.

Same API on both platforms; the implementation lives in winfront_win.py (Win32)
and macfront.py (Quartz + Accessibility). A "window handle" is an int on both:
an HWND on Windows, a window-server window number on macOS.
"""
import sys

if sys.platform == "darwin":
    from .macfront import (_exe, _title, arrange, find_window, force_front,  # noqa: F401
                           front_after_launch, monitors, windows)
else:
    from .winfront_win import (_exe, _title, arrange, find_window, force_front,  # noqa: F401
                               front_after_launch, monitors, windows)
