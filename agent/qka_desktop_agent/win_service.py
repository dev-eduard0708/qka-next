"""Windows service host for QKA Desktop Agent.

Requires a real SCM start (msiexec/sc start). PyInstaller --onedir is required:
--onefile bootloaders do not satisfy StartServiceCtrlDispatcher under SCM and
surface Error 1920 (and local probes show pywintypes 1063).
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
        import pywintypes  # type: ignore
    except ImportError:
        log.error("pywin32 not available; cannot host Windows service")
        return 3

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

    # WiX ServiceInstall Arguments="service" → ImagePath ends with `…exe service`.
    # SCM start must host the dispatcher for that argv; HandleCommandLine is only for
    # manual `install|start|stop|debug`.
    argv1 = (sys.argv[1].strip().lower() if len(sys.argv) > 1 else "")
    if len(sys.argv) == 1 or argv1 == "service":
        try:
            servicemanager.Initialize()
            servicemanager.PrepareToHostSingle(QkaDesktopAgentService)
            servicemanager.StartServiceCtrlDispatcher()
        except pywintypes.error as exc:
            # 1063 = not started by SCM (local probe / accidental console launch).
            if int(getattr(exc, "winerror", 0) or 0) == 1063:
                log.error(
                    "StartServiceCtrlDispatcher failed (1063): process was not started by SCM. "
                    "Use: sc start QkaDesktopAgent / msiexec service start — not bare 'exe service'."
                )
                return 1063
            raise
        return 0
    win32serviceutil.HandleCommandLine(QkaDesktopAgentService)
    return 0
