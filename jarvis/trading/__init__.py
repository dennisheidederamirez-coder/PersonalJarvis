"""Demo trading core: research, signals, risk and simulated execution.

DEMO ONLY. This package holds no exchange client, no order API and no
credential path: every order is simulated against virtual capital
(``paper.py``), and a guard test keeps network clients out of the package.
Real-money trading is not a configuration away — it would need new code and
the owner's explicit approval.

Layers (each usable on its own, all deterministic — no model call):

- ``instruments`` — multi-asset instrument model; only crypto is demo-tradable
  for now, stocks/ETFs are analysis-only.
- ``data`` — OHLCV bars with provenance and a data-quality report.
- ``indicators`` — causal technical indicators (a value at bar ``i`` only
  uses bars ``<= i``).
- ``research`` — a sourced, time-stamped technical market snapshot.
- ``strategies`` — rule-based long/short strategies.
- ``risk`` — the independent risk manager (sizing, limits, kill switch,
  duplicate guard); nothing executes without its approval.
- ``paper`` — the simulated broker.
- ``engine`` — one bar loop used by backtests and the live demo alike.
- ``validation`` — walk-forward out-of-sample tests and the edge verdict:
  no statistically supported edge means no trade.
- ``metrics`` — hit rate, profit factor, drawdown, fees, Sharpe/Sortino.
- ``journal`` — every decision, rejection, fill and equity point.
"""
