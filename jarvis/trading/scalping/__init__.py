"""Scalping research branch (demo only).

Separate from the swing families (trend, breakout, mean reversion) and from
the running paper test: own strategies, own execution simulator on
trade-level data, own controller limits, own validation and test budget.
See docs/trading-scalping.md and docs/trading-scalping-plan.md.

- ``tape`` — individual trades (with the aggressor side) and bars built
  from them;
- ``microsim`` — order execution against a trade tape: latency, spread,
  maker/taker fees, queue position, partial fills, participation caps,
  post-only and venue rejections — never an instant fill at a bar price;
- ``levels`` — daily open, previous day/week high/low (completed periods only);
- ``strategies`` — three candidates: momentum breakout, EMA50/EMA200 trend
  with volume, order-flow at reference levels (PVSRA + aggressor delta);
- ``controller`` — portfolio limits that can only refuse: trade frequency,
  loss streak cooldown, daily loss across accounts, concurrent exposure,
  correlated positions;
- ``profiles`` — named risk profiles; anything but the conservative default
  is locked until the owner approves it;
- ``splits`` — train / validation / untouched test with embargo gaps.
"""
