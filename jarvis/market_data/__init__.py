"""Read-only market data from free, keyless public exchange APIs.

Adapters for Bybit, OKX, Binance (REST API, not the restricted bulk
datasets) and Coinbase turn venue responses into ``jarvis.trading.data.BarSeries``
with provenance (``source`` names the venue, market kind and venue symbol)
plus funding and open-interest history where a venue offers it.

Rules:
- read-only and keyless; no account, no order endpoint, no credential;
- polite: requests stay far below each venue's published limit, retries are
  jittered and bounded, ``Retry-After`` is honoured, and the shared HTTP pool
  is reused (AP-33);
- the still-open bar is always dropped, so nothing that may still change
  reaches an analysis;
- spot and perpetual data, and different venues, are never merged into one
  series — they are compared (``crosscheck``) and one whole series is chosen;
- provider error bodies are never logged or stored (only status and code).

Nothing here starts on its own: a fetch happens only when a caller asks.
"""
