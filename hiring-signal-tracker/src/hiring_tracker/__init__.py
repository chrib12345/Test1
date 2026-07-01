"""Hiring-Signal Tracker.

A headless alternative-data service that snapshots the open requisitions of a
watchlist of public companies, diffs consecutive snapshots into a hiring
lifecycle, and exposes momentum/composition signals for event-driven research.

The unit of raw data is a *job posting*, never a hire. Everything downstream is
a momentum-and-mix signal to corroborate against filings.
"""

__version__ = "0.1.0"
