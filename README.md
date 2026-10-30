# Budget a buffer for day-ahead wind shortfalls

A wind forecast can look accurate on average and still leave large shortfalls when generation falls below the forecast. This study will compare six simple buffer policies under explicit capacity and shortfall-cost assumptions, including a zero-buffer baseline and a geographic outcome holdout.

**Current state: fixed protocol before collection.** No winning policy, reliability improvement or financial saving has been measured. The [protocol](PROTOCOL.md) defines the historical cohort, source gates, fitting and validation, reserve proxy and independent checks. These are simulated buffer decisions on reported wind data, not a grid dispatch or a validated reserve requirement. Formal-publication projects, including Azure optimization, remain private.
