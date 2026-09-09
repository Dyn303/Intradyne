# Point-in-time equity universe

Generated 2026-09-03 02:00 UTC by `scripts/equity_pit_universe.py`.

Amendment A3. Membership is point-in-time and includes names that have
since delisted: a security belongs to every snapshot it was listed in,
and its loss is taken at its last traded price. Building a universe from
today's listings instead selects securities *because they survived*.

## Coverage

| | count |
|---|---|
| symbols ever listed | 23,246 |
| listed today | 14,409 |
| since delisted | 8,837 |
| **mortality** | **38.0%** |

After the Stock / 6 exchanges filter:
**16,097 listings** enter the universe.

A backtest run over today's listings alone would omit the delisted
8,837 entirely -- 38.0% of everything that
ever traded, and the part that by construction did worst.

## A ticker is not an identity

619 symbols carry more than one listing, and two quite
different situations hide in that number:

- **305 sequential reuses.** The first delisted before the
  second floated *and* the issuer differs -- the ticker was reassigned to
  an unrelated company. `ACCL` was Accelrys until 2014 and is Acco Group
  now; `ADCT` was ADC Telecommunications and is ADC Therapeutics.
- **232 concurrent listings.** Both live at once: one
  issuer's shares and its senior notes, or a cross-listing. Not reuse,
  and counting it as such overstates the problem.

Names are normalised before comparison, because a re-registration is not
a reassignment -- "Absolute Software Corp" and "Absolute Software
Corporation" are one company, and a raw string compare calls them two.

A universe keyed by ticker splices reused symbols into a single series.
The unit here is a **listing** -- a `SYMBOL@ipoDate` interval -- so the
two are distinct rows and no price series is ever joined across them.

| symbol | was | until | became | from |
|---|---|---|---|---|
| `AAC` | Ares Acquisition Corporation - C | 2023-11-06 | Ares Acquisition Corp III - Clas | 2026-08-27 |
| `AAC-U` | Ares Acquisition Corporation - U | 2026-06-30 | Ares Acquisition Corp III - Unit | 2026-06-30 |
| `AACIU` | Armada Acquisition Corp I - Unit | 2024-08-15 | Armada Acquisition Corp III - Un | 2026-02-18 |
| `ACCL` | Accelrys Inc | 2014-05-06 | Acco Group Holdings Ltd | 2025-10-17 |
| `ADCT` | ADC TELECOMMUNICATIONS INC | 2010-12-20 | Adc Therapeutics SA | 2020-05-15 |
| `ADPT` | Adeptus Health Inc | 2017-04-20 | Adaptive Biotechnologies Corp | 2019-06-27 |
| `AERO` | Aero Grow International Inc | 2021-02-26 | Grupo Aeromexico S.A.B. De C.V. | 2025-11-06 |
| `AFGC` | Africa Growth Cp | 2019-08-29 | American Financial Group Inc | 2019-12-04 |
| `AGNCP` | American Capital Agency Corp | 2017-09-18 | AGNC Investment Corp | 2020-02-05 |
| `AIB` | Apollo Investment Corp Pfd. | 2026-03-20 | BlockchAIn Digital Infrastructur | 2026-03-20 |
| `AIQ` | ALLIANCE HEALTHCARE SERVICES INC | 2017-08-31 | Global X Artificial Intelligence | 2018-05-16 |
| `ALC` | Assisted Living Concepts LLC | 2013-10-17 | Alcon Inc | 2019-04-09 |
| ... | *293 more* | | | |

Concurrent listings mean a symbol can be doubly live. At most
**68** symbols on any one rebalance date (median 21,
on 44 of 44 dates) against a universe of
thousands. The later flotation wins, and the choice is counted here
rather than made silently.

## What survivorship would have cost

Universe size at each date, against the subset still listed today --
which is what a backtest built from a current ticker list would see.

| as of | point-in-time | still listed today | missing | bias |
|---|---|---|---|---|
| 2005-01-01 | 3,121 | 1,939 | 1,182 | **37.9%** |
| 2008-12-27 | 4,032 | 2,317 | 1,715 | **42.5%** |
| 2012-12-22 | 4,664 | 2,643 | 2,021 | **43.3%** |
| 2016-12-17 | 5,856 | 3,361 | 2,495 | **42.6%** |
| 2020-12-12 | 6,386 | 4,482 | 1,904 | **29.8%** |
| 2024-12-07 | 7,390 | 6,500 | 890 | **12.0%** |

The missing names are not a random sample. A security is absent
precisely because it stopped trading, so the omission runs one way and
a backtest over today's list is flattered by exactly the constituents
that did worst.

## Universe over time

| date | size | added | removed |
|---|---|---|---|
| 2005-01-01 | 3,121 | 0 | 0 |
| 2005-07-02 | 3,203 | 82 | 0 |
| 2005-12-31 | 3,327 | 125 | 1 |
| 2006-07-01 | 3,412 | 86 | 1 |
| 2006-12-30 | 3,636 | 224 | 0 |
| 2007-06-30 | 3,769 | 133 | 0 |
| 2007-12-29 | 3,907 | 142 | 4 |
| 2008-06-28 | 3,979 | 72 | 0 |
| 2008-12-27 | 4,032 | 56 | 3 |
| 2009-06-27 | 4,061 | 37 | 8 |
| 2009-12-26 | 4,105 | 75 | 31 |
| 2010-06-26 | 4,205 | 119 | 19 |
| 2010-12-25 | 4,306 | 123 | 22 |
| 2011-06-25 | 4,406 | 131 | 31 |
| 2011-12-24 | 4,443 | 83 | 46 |
| 2012-06-23 | 4,556 | 129 | 16 |
| 2012-12-22 | 4,664 | 139 | 31 |
| 2013-06-22 | 4,776 | 161 | 49 |
| 2013-12-21 | 4,878 | 186 | 84 |
| 2014-06-21 | 5,034 | 208 | 52 |
| 2014-12-20 | 5,415 | 464 | 83 |
| 2015-06-20 | 5,480 | 176 | 111 |
| 2015-12-19 | 5,544 | 227 | 163 |
| 2016-06-18 | 5,831 | 379 | 92 |
| 2016-12-17 | 5,856 | 222 | 197 |
| 2017-06-17 | 5,907 | 210 | 159 |
| 2017-12-16 | 5,864 | 328 | 371 |
| 2018-06-16 | 5,838 | 250 | 276 |
| 2018-12-15 | 5,826 | 280 | 292 |
| 2019-06-15 | 5,866 | 233 | 193 |
| 2019-12-14 | 5,942 | 300 | 224 |
| 2020-06-13 | 5,928 | 218 | 232 |
| 2020-12-12 | 6,386 | 669 | 211 |
| 2021-06-12 | 7,364 | 1,291 | 313 |
| 2021-12-11 | 7,848 | 943 | 459 |
| 2022-06-11 | 8,110 | 526 | 264 |
| 2022-12-10 | 7,878 | 195 | 427 |
| 2023-06-10 | 7,434 | 203 | 647 |
| 2023-12-09 | 7,313 | 240 | 361 |
| 2024-06-08 | 7,242 | 239 | 310 |
| 2024-12-07 | 7,390 | 326 | 178 |
| 2025-06-07 | 7,657 | 389 | 122 |
| 2025-12-06 | 8,090 | 564 | 131 |
| 2026-06-06 | 8,768 | 862 | 184 |

## What this does not do

**Liquidity is not judged here.** A3 also requires tradeability measured
at each rebalance date, which needs per-symbol volume history -- tens of
thousands of requests against a one-per-second quota. Membership is the
survivorship half, and it is the half that cannot be reconstructed after
the fact. Apply a liquidity floor downstream, on the reduced candidate
set, judged on data available at the rebalance date and never on today's
volume. Using current liquidity to decide what was tradeable in 2019
leaks the future exactly as using current listings does.

**No delisting reason is given.** A merger at a premium and a bankruptcy
both appear here as a `delistingDate`. They are not the same event for a
long-only strategy, and any result sensitive to that distinction needs a
corporate-actions source this does not have.

**Ratios are not screened.** Shariah permissibility is decided by
`scripts/screen_equities.py`, and that script does not decide either --
it produces a worksheet. See `docs/EQUITY_SCREENING.md`.
