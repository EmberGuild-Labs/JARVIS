"""Scheduler logic (fake clock, temp file) + app lookup + a live 'schedule BeamNG' request."""
import datetime as dt
import logging
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
logging.basicConfig(level=logging.WARNING)

import jarvis.scheduler as sch
from jarvis.config import DEFAULTS
from jarvis.tools import ToolBox

real_path = sch.SCHEDULE_PATH
sch.SCHEDULE_PATH = Path(tempfile.mkdtemp()) / "schedule.json"
fired = []
s = sch.Scheduler(lambda t: fired.append(t["label"]))
now = dt.datetime.now()
in1 = (now + dt.timedelta(minutes=1)).replace(second=0, microsecond=0)
s.add(in1.isoformat(), "open_application", "BeamNG.drive", "none", "Launch BeamNG.drive")
s.add(in1.isoformat(), "remind", "stretch", "daily", "Stretch")
try:
    s.add((now - dt.timedelta(hours=1)).isoformat(), "remind", "x", "none", "past")
    print("FAIL: past one-off accepted")
except ValueError as e:
    print("past one-off rejected:", e)
s.tick(now)
assert not fired, "fired early"
s.tick(in1 + dt.timedelta(seconds=5))
print("fired:", fired)
assert fired == ["Launch BeamNG.drive", "Stretch"]
print("remaining:", s.listing())
assert len(s.tasks) == 1 and s.tasks[0]["when"].startswith((in1 + dt.timedelta(days=1)).date().isoformat())
# reload from disk survives restart
assert len(sch.Scheduler(lambda t: None).tasks) == 1
# missed by > grace: skipped, daily rolls forward
fired.clear(); s.tick(in1 + dt.timedelta(days=1, hours=2))
print("after long outage fired:", fired, "| next:", s.listing())
assert not fired
# weekdays roll skips weekend
fri = dt.datetime(2026, 11, 13, 9, 0)
print("weekday after Fri:", sch._next(fri, "weekdays").strftime("%A"))
print("cancel:", [t["label"] for t in s.cancel("stretch")], "| left:", s.listing())

# App lookup (does not launch)
cfg = dict(DEFAULTS)
tb = ToolBox(lambda q: False, None, print, cfg)
apps = tb._scan_apps()
hits = [k for k in apps if "beamng" in k]
print("BeamNG shortcuts found:", hits or "none")
print("scheduler logic: OK")
sch.SCHEDULE_PATH = real_path
