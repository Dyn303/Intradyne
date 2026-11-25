# Phase 4 release decision

**Reviewed on 2026-11-25.** Software candidate `32f443255745adfdef0fb14441115616ae0b73bc` on `codex/phase-3-paper-ops`. The dry-run environment uses one API worker, Caddy, PostgreSQL 16, production API authentication and an inactive strategy loop. It uses disposable local credentials, storage and alert routing.

| Gate | Decision | Evidence and owner |
| --- | --- | --- |
| Equity research | **Hold** | The independent cloud scorer at `e7c00bc4ea2c9e38fc5b559d0510c6120c53d2f1` reproduced 80/106 departed identities (75.5%), with 24/24 filing windows passing. Five qualifying histories and authenticated Alpha Vantage access remain outstanding. The research owner must verify the data and remaining preregistration gates before spending the slot. [Phase 2 qualification](RESEARCH_DATA_QUALIFICATION.md) |
| Crypto research | **Closed** | The preregistered holdout passed zero of four configurations. The research owner must apply the existing reopening and native-data rules. [Crypto holdout verdict](CRYPTO_REOPENING_PREREGISTRATION.md#outcome--hold-out-and-the-verdict) |
| Paper operability | **Pass in the rehearsal** | Candidate `32f4432` passed hosted CI and the authenticated Compose, Caddy, and PostgreSQL drill below. The deployment operator owns target-environment acceptance, including public DNS and certificate issuance and an external alert acknowledgement. [Operations runbook](../RUNBOOK.md) |
| Live trading | **Hold** | The hard live gate and the strategy-edge gate remain closed in source. The release operator must obtain an after-cost edge, testnet soak, verified external paging, configured caps, a timed halt drill, and explicit sign-off before any separate rollout decision. [Live readiness runbook](../RUNBOOK.md#8-going-live--not-yet-enabled) |

No operator acceptance of a deployment or a live rollout is recorded here. Equity execution would require a separate broker integration and compliance design. The ~1% daily target remains unverified.

## Independent verification

- The clean candidate passed 1,046 local tests with PostgreSQL 16 enabled. Ruff, mypy, the research hash chain, and exact regeneration of the published research log passed. Starlette emitted one AnyIO deprecation warning.
- [Hosted CI run 45631041557](https://github.com/Dyn303/Intradyne/actions/runs/45631041557) passed on [draft PR #88](https://github.com/Dyn303/Intradyne/pull/88). It passed PostgreSQL-enabled tests, research log freshness, Docker image build and smoke, lint, type checking, and Gitleaks. GitHub annotated a Node.js 20 deprecation for some workflow actions.
- The fresh build produced image manifest `sha256:a11a7b81b1ac585bd25f95c3c95a3e94dbc28707ab4c851313d3590c8077e78b`, 174,296,603 bytes. The authenticated Compose smoke verified paper mode, dashboard, research record, REST/OpenAPI, configured CORS, diagnostics, private metrics, a paper fill, and refusal of unreviewed parameter activation. An authenticated halt blocked the next paper order. The loopback Caddy certificate and hostname verified against its disposable CA.
- Internal Prometheus reported its API target up. Grafana health and the Prometheus and Alertmanager config checks passed. The local receiver logged `ack Trading_Halted firing` and returned HTTP 200 at 22:13 MYT. Public ACME and external paging were outside the rehearsal.
- PostgreSQL outage returned 503 on authenticated readiness through Caddy. Readiness recovered after restart. The custom-format dump and matching ledger were restored into a fresh database and ledger path. The restored service retained the halt and byte-for-byte ledger until an authorized paper release.

| Recovery evidence | SHA-256 |
| --- | --- |
| PostgreSQL dump | `9d3b1f42a149e2dc09114327762a351d6268d0266d93720aaf4b11ac4608d317` |
| Audit ledger | `dc7ecb55d5901825fe72acf4255855ef99d19c8001ba94cb705ff5fc0b9799dd` |
