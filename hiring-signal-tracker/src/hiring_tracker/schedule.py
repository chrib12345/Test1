"""Scheduling entrypoints (Section 10).

Poll cadence: daily by default (weekly is fine and cheaper). The scheduled
routine wires into the existing Composio scheduled-routine layer; an OS-scheduler
fallback command is provided.

Composio: register ``scheduled_poll`` as the routine callable. It reuses the same
Postgres (DATABASE_URL) and does a full poll, then normalize + metrics.

OS-scheduler fallback (cron, daily 06:00 UTC):
    0 6 * * *  cd /path/to/hiring-signal-tracker && \
        . .venv/bin/activate && hiring-tracker poll --then-metrics >> run.log 2>&1

Windows Task Scheduler (PowerShell):
    schtasks /Create /SC DAILY /ST 06:00 /TN HiringSignalPoll ^
        /TR "cmd /c cd C:\\path\\hiring-signal-tracker && .venv\\Scripts\\hiring-tracker poll --then-metrics"
"""

from __future__ import annotations

from .config import get_settings
from .logging_util import get_logger

log = get_logger("schedule")


def scheduled_poll(run_normalize: bool = True, run_metrics: bool = True) -> dict:
    """Entrypoint for the Composio scheduled routine (and the OS fallback).

    Returns a JSON-serializable result summary. Never raises for a single
    company's failure — those are captured in the run summary.
    """
    from .ingest import run_watchlist

    settings = get_settings()
    summary = run_watchlist(settings)
    result: dict = {"poll": summary.to_dict()}

    if run_normalize and settings.anthropic_api_key:
        try:
            from .normalize import run_normalization

            result["normalize"] = run_normalization(settings)
        except Exception as e:  # normalization is best-effort, must not fail the poll
            log.warning("normalization skipped: %s", e)
            result["normalize_error"] = str(e)
    elif run_normalize:
        log.info("normalization skipped: no ANTHROPIC_API_KEY")

    if run_metrics:
        from .metrics import run_metrics as _run_metrics

        result["metrics"] = _run_metrics()

    return result
