"""Live: ask JARVIS (Claude Code backend) to schedule BeamNG, list, then cancel. Nothing launches."""
import sys, tempfile, time, logging
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
logging.basicConfig(level=logging.WARNING)
import jarvis.scheduler as sch
sch.SCHEDULE_PATH = Path(tempfile.mkdtemp()) / "schedule.json"
from jarvis.brain_code import CodeBrain
from jarvis.config import DEFAULTS
from jarvis.tools import ToolBox
class FakeHUD:
    state = "idle"
    def __getattr__(self, n): return lambda *a, **k: None
cfg = dict(DEFAULTS, code_dir=sys.argv[1])
tb = ToolBox(lambda q: False, FakeHUD(), print, cfg)
tb.scheduler = sch.Scheduler(lambda t: print("FIRED", t))
brain = CodeBrain(cfg, tb)
for q in ["Boot up BeamNG.drive at 17:00 today.",
          "Also remind me every weekday at 9am to check my email.",
          "What have I got scheduled?",
          "Cancel the BeamNG one."]:
    t = time.time(); print("\nYOU:", q)
    print("JARVIS:", brain.ask(q, on_status=lambda s: print("   [status]", s)), f"({time.time()-t:.1f}s)")
    print("   schedule now:", tb.scheduler.tasks and [(x['when'], x['action'], x['target'], x['repeat']) for x in tb.scheduler.tasks])
brain.reset()
