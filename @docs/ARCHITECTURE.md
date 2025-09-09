# Architecture

This document outlines core services and the order flow.

## Components
```mermaid
graph LR
  subgraph Control Plane
    API[api (FastAPI)]<-->RISK[riskd]
    RISK<-->REDIS[(redis)]
    API<-->PG[(postgres)]
    LEDGER[explainability ledger]-->PG
  end

  subgraph Execution Plane
    SOR[SOR (smart order router)]<-->RISK
    SOR<-->REDIS
    SOR-- routes -->ADP[venue adapters]
    ADP-- REST/WebSocket -->VENUE[(Exchanges/Brokers)]
  end

  subgraph Research
    BT[backtester]-->RISK
    BT-->SOR
    BT<-->PG
  end

  API<-->SOR
  RISK-->LEDGER
```

## Order Flow (Data Path)
```mermaid
sequenceDiagram
  participant C as Client
  participant API as api
  participant R as riskd
  participant S as SOR
  participant A as Venue Adapter
  participant V as Venue
  participant L as Ledger

  C->>API: POST /orders (symbol, qty, side)
  API->>R: gate_trade(req)
  R-->>API: Allow | Pause | Halt (+reasons)
  API->>L: append(reasoning hash chain)
  alt Allowed
    API->>S: route(order, prefs)
    S->>A: place(order on venue X)
    A->>V: submit
    V-->>A: ack/fill updates
    A-->>S: status/fills
    S-->>API: final status
    API->>L: append(exec, latency, venue)
    API-->>C: 200 OK (order id, status)
  else Blocked
    API-->>C: 4xx (blocked; reasons)
  end
```

## Notes
- riskd enforces guardrails (DD, flash‑crash, kill‑switch, VaR step‑down) and compliance (whitelist/Shariah).
- explainability ledger is append‑only with hash chaining; persisted in Postgres and mirrored to logs.
- redis caches prices/state for risk and routing; postgres stores orders, trades, analytics.
- backtester reuses the same risk/SOR paths for reproducibility.

See also: @docs/RISK_GUARDRAILS.md, @docs/SHARIAH_FILTER.md, @docs/OPERATIONS.md.
