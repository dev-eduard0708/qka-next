"""Windows service host for QKA Desktop Agent.

Prefers pywin32 when available. Falls back to a hidden console loop suitable for
nssm/sc.exe wrappers used by the silent installer.
"""

from __future__ import annotations

import logging
import sys
import threading

log = logging.getLogger("qka.agent.service")


def run_service() -> int:
    try:
        import win32event  # type: ignore
        import win32service  # type: ignore
        import win32serviceutil  # type: ignore
        import servicemanager  # type: ignore
    except ImportError:
        log.warning("pywin32 not available; running foreground loop (use nssm/sc for service)")
        from qka_desktop_agent.main import run_loop

        return run_loop()

    class QkaDesktopAgentService(win32serviceutil.ServiceFramework):
        _svc_name_ = "QkaDesktopAgent"
        _svc_display_name_ = "QKA Desktop Agent"
        _svc_description_ = "Indexes approved local employee folders read-only for QKA-Next."

        def __init__(self, args):
            win32serviceutil.ServiceFramework.__init__(self, args)
            self.stop_event = win32event.CreateEvent(None, 0, 0, None)
            self.stop_flag = [False]

        def SvcStop(self):
            self.ReportServiceStatus(win32service.SERVICE_STOP_PENDING)
            self.stop_flag[0] = True
            win32event.SetEvent(self.stop_event)

        def SvcDoRun(self):
            servicemanager.LogMsg(
                servicemanager.EVENTLOG_INFORMATION_TYPE,
                servicemanager.PYS_SERVICE_STARTED,
                (self._svc_name_, ""),
            )
            from qka_desktop_agent.main import run_loop

            thread = threading.Thread(target=run_loop, kwargs={"stop_flag": self.stop_flag}, daemon=True)
            thread.start()
            win32event.WaitForSingleObject(self.stop_event, win32event.INFINITE)
            thread.join(timeout=30)

    if len(sys.argv) == 1:
        servicemanager.Initialize()
        servicemanager.PrepareToHostSingle(QkaDesktopAgentService)
        servicemanager.StartServiceCtrlDispatcher()
        return 0
    win32serviceutil.HandleCommandLine(QkaDesktopAgentService)
    return 0
