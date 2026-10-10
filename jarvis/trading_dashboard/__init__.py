"""Read model for the paper-trading dashboard.

Everything here READS an existing paper-trading journal (``jarvis.trading.
journal.SqliteJournal``) and turns it into the figures the dashboard shows.
Nothing writes, nothing talks to a network, nothing starts or stops a job:

- ``reader`` opens the journal through a read-only SQLite URI and returns one
  consistent snapshot per read (short transactions, a bounded busy wait, a
  cache keyed on the file's mtime);
- ``views`` builds the dashboard's sections from a snapshot, the
  pre-registered spec and the current time;
- ``reasons`` maps the journal's free-text reasons to stable codes the UI
  can translate.

The package lives outside ``jarvis.trading`` on purpose: the paper job pins
the source of ``jarvis.trading`` and ``jarvis.market_data`` by hash
(``paper_job.code_fingerprint``), and a dashboard must never change it.
"""

from jarvis.trading_dashboard.reader import (
    JournalReader,
    JournalSnapshot,
    ReadFailure,
    SourceState,
)

__all__ = ["JournalReader", "JournalSnapshot", "ReadFailure", "SourceState"]
