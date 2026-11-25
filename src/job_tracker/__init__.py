"""job_tracker: LinkedIn job URLs -> structured jobs -> Google Sheet tracker.

Everything here is plain Python with no CLI or web code, so the same functions can
be called from the command line today and from a FastAPI backend / agents later.
"""
from .models import Job, RunResult
from .pipeline import run

__all__ = ["Job", "RunResult", "run"]
