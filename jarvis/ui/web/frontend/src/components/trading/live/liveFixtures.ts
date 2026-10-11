/**
 * LIVE history responses for the dashboard's tests, generated once from the
 * synthetic demo archive (`tests/fakes/fake_live_archive.py`) — no real account.
 */
import type {
  TradingLiveCostsView,
  TradingLiveOverview,
  TradingLivePositionsView,
  TradingLiveReconciliationView,
  TradingLiveSource,
  TradingLiveSourcesView,
  TradingLiveTradesView,
} from "@/types/trading";

export const liveOverview: TradingLiveOverview = {
  "source": {
    "state": "ok",
    "detail": "",
    "file_name": "live_history.sqlite",
    "read_at_ms": 1790000000000,
    "archive_mtime_ms": 1789996400000,
    "size_bytes": 307200,
    "read_ms": 4.2,
    "last_sync_ms": 1789827200000,
    "last_import_ms": 1789913780000,
    "warnings": []
  },
  "margin_coin": "USDT",
  "balance": {
    "ts_ms": 1789827200000,
    "wallet": 1009.7625,
    "available": 1009.7625,
    "unrealized": 0.0
  },
  "balance_series": [
    {
      "ts_ms": 1789481600000,
      "wallet": 979.7335,
      "available": 678.6962,
      "unrealized": 1.25
    },
    {
      "ts_ms": 1789654400000,
      "wallet": 984.7625,
      "available": 984.7625,
      "unrealized": 0.0
    },
    {
      "ts_ms": 1789827200000,
      "wallet": 1009.7625,
      "available": 1009.7625,
      "unrealized": 0.0
    }
  ],
  "totals": {
    "realized": 2.2958,
    "fees": 13.6672,
    "funding": -3.8661,
    "net": -15.2375,
    "closed": 37,
    "wins": 19,
    "losses": 18,
    "win_rate": 0.5135135135135135
  },
  "totals_30d": {
    "realized": -8.8565,
    "fees": 2.1405,
    "funding": -1.4529,
    "net": -12.4499,
    "closed": 6,
    "wins": 3,
    "losses": 3,
    "win_rate": 0.5
  },
  "pnl_series": [
    {
      "day": "2026-08-01",
      "net": -7.7015,
      "cumulative": -10.5795
    },
    {
      "day": "2026-08-02",
      "net": -1.4145,
      "cumulative": -11.994
    },
    {
      "day": "2026-08-08",
      "net": -0.9948,
      "cumulative": -12.9888
    },
    {
      "day": "2026-08-11",
      "net": 10.5045,
      "cumulative": -2.4843
    },
    {
      "day": "2026-08-15",
      "net": -1.1837,
      "cumulative": -3.668
    },
    {
      "day": "2026-08-21",
      "net": 0.8804,
      "cumulative": -2.7876
    },
    {
      "day": "2026-08-24",
      "net": -6.5553,
      "cumulative": -9.3429
    },
    {
      "day": "2026-08-27",
      "net": -4.6915,
      "cumulative": -14.0344
    },
    {
      "day": "2026-09-02",
      "net": 1.7735,
      "cumulative": -12.2609
    },
    {
      "day": "2026-09-03",
      "net": 1.5458,
      "cumulative": -10.7151
    },
    {
      "day": "2026-09-08",
      "net": -9.3408,
      "cumulative": -20.0559
    },
    {
      "day": "2026-09-16",
      "net": 4.8184,
      "cumulative": -15.2375
    }
  ],
  "open_positions": 0,
  "open_unrealized": 0.0,
  "fills": 110,
  "orders": 67,
  "findings": {
    "error": 4,
    "warning": 3
  },
  "last_run": {
    "id": 7,
    "kind": "import",
    "source": "csv:journal",
    "started_ms": 1789913780000,
    "finished_ms": 1789913780000,
    "status": "ok",
    "requests": 0,
    "inserted": 14,
    "updated": 0,
    "unchanged": 0,
    "rejected": 0,
    "error_code": null
  }
};

export const livePositions: TradingLivePositionsView = {
  "source": {
    "state": "ok",
    "detail": "",
    "file_name": "live_history.sqlite",
    "read_at_ms": 1790000000000,
    "archive_mtime_ms": 1789996400000,
    "size_bytes": 307200,
    "read_ms": 4.2,
    "last_sync_ms": 1789827200000,
    "last_import_ms": 1789913780000,
    "warnings": []
  },
  "open": [],
  "closed": [
    {
      "position_id": "P000253",
      "symbol": "BTCUSDT",
      "side": "long",
      "status": "closed",
      "qty": 0.005,
      "entry_price": 60207.45,
      "close_price": 61250.0,
      "fee": 0.3644,
      "funding": -0.03,
      "realized": 5.2128,
      "net": 4.8184,
      "unrealized": null,
      "leverage": 1,
      "opened_ms": 1789308800000,
      "closed_ms": 1789568000000,
      "origin": "api",
      "findings": []
    },
    {
      "position_id": "P000246",
      "symbol": "BTCUSDT",
      "side": "long",
      "status": "closed",
      "qty": 0.009,
      "entry_price": 54335.92,
      "close_price": 53399.54,
      "fee": 0.5818,
      "funding": -0.3316,
      "realized": -8.4274,
      "net": -9.3408,
      "unrealized": null,
      "leverage": 1,
      "opened_ms": 1788720289916,
      "closed_ms": 1788843939748,
      "origin": "api",
      "findings": []
    },
    {
      "position_id": "P000239",
      "symbol": "SOLUSDT",
      "side": "long",
      "status": "closed",
      "qty": 0.737,
      "entry_price": 135.64,
      "close_price": 138.08,
      "fee": 0.1211,
      "funding": -0.1314,
      "realized": 1.7983,
      "net": 1.5458,
      "unrealized": null,
      "leverage": 1,
      "opened_ms": 1788400630900,
      "closed_ms": 1788449683322,
      "origin": "api",
      "findings": []
    },
    {
      "position_id": "P000232",
      "symbol": "ETHUSDT",
      "side": "long",
      "status": "closed",
      "qty": 0.13,
      "entry_price": 3066.94,
      "close_price": 3084.58,
      "fee": 0.4798,
      "funding": -0.0399,
      "realized": 2.2932,
      "net": 1.7735,
      "unrealized": null,
      "leverage": 1,
      "opened_ms": 1788084897484,
      "closed_ms": 1788334991868,
      "origin": "api",
      "findings": []
    }
  ],
  "total_closed": 37,
  "offset": 0,
  "limit": 4,
  "symbols": [
    "BTCUSDT",
    "ETHUSDT",
    "SOLUSDT"
  ]
};

export const liveTrades: TradingLiveTradesView = {
  "source": {
    "state": "ok",
    "detail": "",
    "file_name": "live_history.sqlite",
    "read_at_ms": 1790000000000,
    "archive_mtime_ms": 1789996400000,
    "size_bytes": 307200,
    "read_ms": 4.2,
    "last_sync_ms": 1789827200000,
    "last_import_ms": 1789913780000,
    "warnings": []
  },
  "fills": [
    {
      "trade_id": "T000260",
      "order_id": "O000259",
      "position_id": "P000253",
      "symbol": "BTCUSDT",
      "side": "sell",
      "qty": 0.005,
      "price": 61250.0,
      "fee": 0.1838,
      "realized": 5.2128,
      "role": "taker",
      "ts_ms": 1789568000000,
      "origin": "api"
    },
    {
      "trade_id": "T000255",
      "order_id": "O000254",
      "position_id": "P000253",
      "symbol": "BTCUSDT",
      "side": "buy",
      "qty": 0.005,
      "price": 60207.45,
      "fee": 0.1806,
      "realized": 0.0,
      "role": "taker",
      "ts_ms": 1789308800000,
      "origin": "api"
    },
    {
      "trade_id": "T000252",
      "order_id": "O000251",
      "position_id": "P000246",
      "symbol": "BTCUSDT",
      "side": "sell",
      "qty": 0.004,
      "price": 53399.54,
      "fee": 0.1282,
      "realized": -3.7455,
      "role": "taker",
      "ts_ms": 1788843939748,
      "origin": "api"
    },
    {
      "trade_id": "T000250",
      "order_id": "O000249",
      "position_id": "P000246",
      "symbol": "BTCUSDT",
      "side": "sell",
      "qty": 0.005,
      "price": 53399.54,
      "fee": 0.1602,
      "realized": -4.6819,
      "role": "taker",
      "ts_ms": 1788782114832,
      "origin": "api"
    }
  ],
  "total": 110,
  "offset": 0,
  "limit": 4,
  "symbols": [
    "BTCUSDT",
    "ETHUSDT",
    "SOLUSDT"
  ],
  "fees": 14.0672
};

export const liveCosts: TradingLiveCostsView = {
  "source": {
    "state": "ok",
    "detail": "",
    "file_name": "live_history.sqlite",
    "read_at_ms": 1790000000000,
    "archive_mtime_ms": 1789996400000,
    "size_bytes": 307200,
    "read_ms": 4.2,
    "last_sync_ms": 1789827200000,
    "last_import_ms": 1789913780000,
    "warnings": []
  },
  "totals": {
    "realized": 2.2958,
    "fees": 13.6672,
    "funding": -3.8661,
    "net": -15.2375,
    "closed": 37,
    "wins": 19,
    "losses": 18,
    "win_rate": 0.5135135135135135
  },
  "by_month": [
    {
      "key": "2026-09",
      "positions": 4,
      "realized": 0.8769,
      "fees": 1.5471,
      "funding_paid": -0.5329,
      "funding_received": 0.0,
      "funding": -0.5329,
      "net": -1.2031,
      "fee_share": 0.08725051743487652
    },
    {
      "key": "2026-08",
      "positions": 8,
      "realized": -7.8544,
      "fees": 2.2953,
      "funding_paid": -1.414,
      "funding_received": 0.4073,
      "funding": -1.0067,
      "net": -11.1564,
      "fee_share": 0.07278625518474828
    },
    {
      "key": "2026-07",
      "positions": 7,
      "realized": 26.5911,
      "fees": 3.2335,
      "funding_paid": -1.2622,
      "funding_received": 0.4027,
      "funding": -0.8595,
      "net": 22.4981,
      "fee_share": 0.055495581463600493
    }
  ],
  "by_symbol": [
    {
      "key": "SOLUSDT",
      "positions": 12,
      "realized": -5.2511,
      "fees": 5.0435,
      "funding_paid": -2.0132,
      "funding_received": 0.6133,
      "funding": -1.3999,
      "net": -11.6945,
      "fee_share": 0.08161280561313572
    },
    {
      "key": "BTCUSDT",
      "positions": 15,
      "realized": -3.9871,
      "fees": 5.0412,
      "funding_paid": -1.7188,
      "funding_received": 0.856,
      "funding": -0.8628,
      "net": -9.8911,
      "fee_share": 0.07938960149292514
    },
    {
      "key": "ETHUSDT",
      "positions": 10,
      "realized": 11.534,
      "fees": 3.5825,
      "funding_paid": -1.6876,
      "funding_received": 0.0842,
      "funding": -1.6034,
      "net": 6.3481,
      "fee_share": 0.0759805896899695
    }
  ],
  "fee_share": 0.07925421983257523,
  "maker_fills": 0,
  "taker_fills": 110,
  "unknown_role_fills": 0
};

export const liveReconciliation: TradingLiveReconciliationView = {
  "source": {
    "state": "ok",
    "detail": "",
    "file_name": "live_history.sqlite",
    "read_at_ms": 1790000000000,
    "archive_mtime_ms": 1789996400000,
    "size_bytes": 307200,
    "read_ms": 4.2,
    "last_sync_ms": 1789827200000,
    "last_import_ms": 1789913780000,
    "warnings": []
  },
  "journals": [
    "control"
  ],
  "journal": "control",
  "journal_from_ms": 1784588144046,
  "journal_to_ms": 1789308800000,
  "pnl_basis": "net",
  "positions_compared": 14,
  "entries_compared": 14,
  "matched": 13,
  "balance_checks": 2,
  "counts": {
    "fill_fee_mismatch": 1,
    "fee_mismatch": 1,
    "funding_mismatch": 1,
    "pnl_mismatch": 1,
    "unexplained_balance_change": 1,
    "missing_at_venue": 1,
    "missing_in_journal": 1
  },
  "findings": [
    {
      "code": "fill_fee_mismatch",
      "severity": "error",
      "group": "internal",
      "position_id": "P000001",
      "entry_id": null,
      "symbol": "ETHUSDT",
      "venue_value": 0.3592,
      "other_value": 0.7592,
      "difference": -0.4,
      "ts_ms": null
    },
    {
      "code": "fee_mismatch",
      "severity": "error",
      "group": "journal",
      "position_id": "P000176",
      "entry_id": "J-0003",
      "symbol": "BTCUSDT",
      "venue_value": 0.3987,
      "other_value": 0.95,
      "difference": -0.5513,
      "ts_ms": null
    },
    {
      "code": "funding_mismatch",
      "severity": "error",
      "group": "journal",
      "position_id": "P000204",
      "entry_id": "J-0007",
      "symbol": "BTCUSDT",
      "venue_value": -0.3569,
      "other_value": 0.0,
      "difference": -0.3569,
      "ts_ms": null
    },
    {
      "code": "pnl_mismatch",
      "severity": "error",
      "group": "journal",
      "position_id": "P000190",
      "entry_id": "J-0005",
      "symbol": "BTCUSDT",
      "venue_value": -0.9948,
      "other_value": -3.3948,
      "difference": 2.4,
      "ts_ms": null
    },
    {
      "code": "unexplained_balance_change",
      "severity": "warning",
      "group": "balance",
      "position_id": null,
      "entry_id": null,
      "symbol": "USDT",
      "venue_value": 25.0,
      "other_value": 0.0,
      "difference": 25.0,
      "ts_ms": 1789827200000
    },
    {
      "code": "missing_at_venue",
      "severity": "warning",
      "group": "journal",
      "position_id": null,
      "entry_id": "J-9999",
      "symbol": "SOLUSDT",
      "venue_value": null,
      "other_value": null,
      "difference": null,
      "ts_ms": null
    },
    {
      "code": "missing_in_journal",
      "severity": "warning",
      "group": "journal",
      "position_id": "P000183",
      "entry_id": null,
      "symbol": "BTCUSDT",
      "venue_value": null,
      "other_value": null,
      "difference": null,
      "ts_ms": null
    }
  ]
};

export const liveSources: TradingLiveSourcesView = {
  "source": {
    "state": "ok",
    "detail": "",
    "file_name": "live_history.sqlite",
    "read_at_ms": 1790000000000,
    "archive_mtime_ms": 1789996400000,
    "size_bytes": 307200,
    "read_ms": 4.2,
    "last_sync_ms": 1789827200000,
    "last_import_ms": 1789913780000,
    "warnings": []
  },
  "counts": {
    "position": 37,
    "fill": 110,
    "order": 67,
    "balance": 3,
    "journal_entry": 14
  },
  "runs": [
    {
      "id": 7,
      "kind": "import",
      "source": "csv:journal",
      "started_ms": 1789913780000,
      "finished_ms": 1789913780000,
      "status": "ok",
      "requests": 0,
      "inserted": 14,
      "updated": 0,
      "unchanged": 0,
      "rejected": 0,
      "error_code": null
    },
    {
      "id": 6,
      "kind": "import",
      "source": "csv:positions",
      "started_ms": 1789913720000,
      "finished_ms": 1789913720000,
      "status": "ok",
      "requests": 0,
      "inserted": 15,
      "updated": 0,
      "unchanged": 22,
      "rejected": 0,
      "error_code": null
    },
    {
      "id": 5,
      "kind": "import",
      "source": "csv:fills",
      "started_ms": 1789913660000,
      "finished_ms": 1789913660000,
      "status": "ok",
      "requests": 0,
      "inserted": 2,
      "updated": 1,
      "unchanged": 107,
      "rejected": 0,
      "error_code": null
    },
    {
      "id": 4,
      "kind": "import",
      "source": "csv:fills",
      "started_ms": 1789913600000,
      "finished_ms": 1789913600000,
      "status": "partial",
      "requests": 0,
      "inserted": 43,
      "updated": 0,
      "unchanged": 65,
      "rejected": 2,
      "error_code": null
    },
    {
      "id": 3,
      "kind": "sync",
      "source": "simulated",
      "started_ms": 1789827200000,
      "finished_ms": 1789827200000,
      "status": "ok",
      "requests": 6,
      "inserted": 1,
      "updated": 0,
      "unchanged": 2,
      "rejected": 0,
      "error_code": null
    }
  ],
  "imports": [
    {
      "run": {
        "id": 7,
        "kind": "import",
        "source": "csv:journal",
        "started_ms": 1789913780000,
        "finished_ms": 1789913780000,
        "status": "ok",
        "requests": 0,
        "inserted": 14,
        "updated": 0,
        "unchanged": 0,
        "rejected": 0,
        "error_code": null
      },
      "kind": "journal",
      "file_digest": "653be35736f1",
      "rows": 15,
      "pnl_basis": "net",
      "pnl_basis_source": "inferred",
      "issue_counts": {
        "duplicate_in_file": 1,
        "pnl_basis_inferred": 1
      },
      "issues": [
        {
          "line": 10,
          "code": "duplicate_in_file",
          "severity": "info",
          "field": null
        },
        {
          "line": null,
          "code": "pnl_basis_inferred",
          "severity": "warning",
          "field": "pnl"
        }
      ]
    },
    {
      "run": {
        "id": 6,
        "kind": "import",
        "source": "csv:positions",
        "started_ms": 1789913720000,
        "finished_ms": 1789913720000,
        "status": "ok",
        "requests": 0,
        "inserted": 15,
        "updated": 0,
        "unchanged": 22,
        "rejected": 0,
        "error_code": null
      },
      "kind": "positions",
      "file_digest": "3894601bc69b",
      "rows": 37,
      "pnl_basis": "net",
      "pnl_basis_source": "inferred",
      "issue_counts": {
        "fee_sign_flipped": 1,
        "pnl_basis_inferred": 1
      },
      "issues": [
        {
          "line": null,
          "code": "fee_sign_flipped",
          "severity": "warning",
          "field": "fee"
        },
        {
          "line": null,
          "code": "pnl_basis_inferred",
          "severity": "warning",
          "field": "pnl"
        }
      ]
    },
    {
      "run": {
        "id": 5,
        "kind": "import",
        "source": "csv:fills",
        "started_ms": 1789913660000,
        "finished_ms": 1789913660000,
        "status": "ok",
        "requests": 0,
        "inserted": 2,
        "updated": 1,
        "unchanged": 107,
        "rejected": 0,
        "error_code": null
      },
      "kind": "fills",
      "file_digest": "16d3209b8380",
      "rows": 110,
      "pnl_basis": "gross",
      "pnl_basis_source": "declared",
      "issue_counts": {},
      "issues": []
    },
    {
      "run": {
        "id": 4,
        "kind": "import",
        "source": "csv:fills",
        "started_ms": 1789913600000,
        "finished_ms": 1789913600000,
        "status": "partial",
        "requests": 0,
        "inserted": 43,
        "updated": 0,
        "unchanged": 65,
        "rejected": 2,
        "error_code": null
      },
      "kind": "fills",
      "file_digest": "6424931eadc8",
      "rows": 110,
      "pnl_basis": "gross",
      "pnl_basis_source": "declared",
      "issue_counts": {
        "bad_timestamp": 1,
        "bad_number": 1
      },
      "issues": [
        {
          "line": 4,
          "code": "bad_timestamp",
          "severity": "error",
          "field": "time"
        },
        {
          "line": 6,
          "code": "bad_number",
          "severity": "error",
          "field": "price"
        }
      ]
    }
  ],
  "coverage": [
    {
      "stream": "positions",
      "intervals": [
        {
          "from_ms": 1776521600000,
          "to_ms": 1789827200000
        }
      ],
      "gaps": []
    },
    {
      "stream": "fills",
      "intervals": [
        {
          "from_ms": 1776521600000,
          "to_ms": 1789827200000
        }
      ],
      "gaps": []
    },
    {
      "stream": "orders",
      "intervals": [
        {
          "from_ms": 1781709200000,
          "to_ms": 1789827200000
        }
      ],
      "gaps": [
        {
          "stream": "orders",
          "from_ms": 1776521600000,
          "to_ms": 1781709200000,
          "route": "export",
          "note": "beyond_retention"
        }
      ]
    }
  ],
  "since_ms": 1776521600000
};

export const liveMissingSource: TradingLiveSource = {
  ...liveOverview.source,
  state: "missing",
  detail: "no live archive at this path",
  archive_mtime_ms: null,
  size_bytes: null,
  read_ms: null,
  last_sync_ms: null,
  last_import_ms: null,
  warnings: [],
};

export const LIVE_RESPONSES: Record<string, unknown> = {
  "live/overview": liveOverview,
  "live/positions": livePositions,
  "live/trades": liveTrades,
  "live/costs": liveCosts,
  "live/reconciliation": liveReconciliation,
  "live/sources": liveSources,
};
