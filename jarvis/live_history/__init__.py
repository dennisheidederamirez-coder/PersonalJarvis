"""A local, durable archive of REAL (live) exchange history — read-only towards the venue.

This package keeps what the primary exchange reports about the owner's real
account — closed and open positions, fills, orders, balance snapshots — in
its own SQLite file, so the history survives the venue's limited look-back
(orders: 90 days, cancelled orders: 3 days) and can be reconciled against an
external trading journal.

It never trades. The venue client knows a fixed allow-list of GET endpoints
and nothing else; there is no order, cancel, leverage, margin or transfer
path in this package, and a guard test keeps it that way.

LIVE and PAPER never mix:

- the archive is its own file, tagged ``mode = live`` at creation, and refuses
  to open a paper-trading journal (and the paper dashboard's reader rejects
  an archive as "not a paper-trading journal");
- every record type is LIVE-only; the paper job's journal and its frozen code
  (``jarvis.trading``, ``jarvis.market_data``) are not imported here.

Modules:

- ``models``     — the normalized records (``Decimal`` money, UTC ms times);
- ``symbols``    — one canonical symbol for ``BTCUSDT``, ``BTC/USDT``, …;
- ``archive``    — the SQLite store: stable keys, duplicate detection,
  revisions, sync runs and covered time windows;
- ``venue_rest`` — the signed, rate-limited, GET-only venue client;
- ``normalize``  — venue payloads → records;
- ``sync``       — one sync run (dry run or apply) and the backfill planner;
- ``reconcile``  — internal consistency checks and the journal comparison.
"""
