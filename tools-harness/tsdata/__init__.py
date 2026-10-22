"""tsdata — a local lake of time-series data harvested from public web APIs.

The point of this package is to answer a question that has to be answered
*before* picking a forecasting model: what time-series data actually exists in
the world, at what frequency, how long, and how multivariate?

Two tiers:

1. **Curated benchmark corpora** (`manifests/`) — the *exact* pretraining sets
   used by the leading time-series foundation models. GIFT-Eval Pretrain (152
   datasets) is the corpus TimesFM 3.0 and Moirai 2.0 train on; the GIFT-Eval
   split (28 datasets) is their zero-shot eval set. These give us the
   "reference distribution" of the field.

2. **Live web APIs** (`sources.py`) — fresh, arbitrary series from the actual
   world: weather, climate, energy, finance, macro, health, transport, web
   traffic, geopolitics. This is the part that lets us forecast something the
   public benchmarks never saw.

Storage is a wide Parquet lake plus a SQLite catalog. Parquet because it is
columnar and reads back into pandas/arrow without a server; SQLite for the
catalog because the repo already standardises on `store.dbclose.connect`
(WAL + busy_timeout) for multi-process access.

Everything here is read-only with respect to the rest of the repo.
"""

from .catalog import Catalog, DatasetRef
from .lake import Lake

__all__ = ["Catalog", "DatasetRef", "Lake"]
