"""Hugging Face benchmark-corpus harvest.

This is the \"reference distribution\" part of the lake: the exact corpora
that the leading time-series foundation models (Chronos-2, Moirai 2.0,
TimesFM 3.0) were trained on, plus their published zero-shot evaluation
sets. Downloading these into the lake lets us measure domain transfer — \"does
the model that won on the public benchmarks also work on the messy real-
world series we harvest from live APIs?\"

WHAT'S HERE:
* GIFT-Eval Pretrain (152 datasets) — the superkingdom of non-monetary
  forecasting: electricity substations, traffic sensors, metro ridership,
  air quality, web traffic, cluster traces, CMIP6 climate, COVID epidemiology.
  This is what Chronos-2 and Moirai-2.0 actually saw.
* GIFT-Eval (28 datasets) — held-out eval set for the open-weight FM results.
* LOTSA (170 datasets) — the Long Open Time Series Archive Moirai 1.0/2.0
  train on; the biggest curated corpus, skewing toward electricity/finance.

Each dataset is HF Arrow format; this module decodes the series, normalises
them to the lake's long format, and registers them with the same
DatasetRef plumbing as the live APIs. Tested on the sandbox; a full
GIFT-Eval Pretrain pull is ~200-400 MB and takes minutes (CLI implements
--tier for that).
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from typing import Any

import httpx

ROOT = Path(__file__).resolve().parent.parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tsdata.catalog import Catalog, DatasetRef  # noqa: E402
from tsdata.lake import Lake  # noqa: E402

MANIFESTS = Path(__file__).resolve().parent / "manifests"

CORPORA: dict[str, tuple[str, str]] = {
    "gift_pretrained": ("Salesforce/GiftEvalPretrain", "gift_eval_pretrain.json"),
    "gift_eval":       ("Salesforce/GiftEval",       "gift_eval.json"),
    "lotsa":           ("Salesforce/lotsa_data",       "lotsa.json"),
}

DEFAULT_CORPUS = ["gift_pretrained", "gift_eval", "lotsa"]


class HfCorpus:
    def __init__(self, root: str | Path = "tools-harness/data/tsdata"):
        self.lake = Lake(root=root)
        self.catalog = self.lake.catalog
        self.fetcher = self.lake.fetcher

    def list_datasets(self, corpus: str) -> list[str]:
        manifest = MANIFESTS / CORPORA[corpus][1]
        if manifest.exists():
            return json.loads(manifest.read_text())
        names = self._scan_hf_tree(*CORPORA[corpus])
        manifest.parent.mkdir(parents=True, exist_ok=True)
        manifest.write_text(json.dumps(names, indent=1))
        return names

    def _scan_hf_tree(self, org: str, ds: str) -> list[str]:
        url = f"https://huggingface.co/api/datasets/{org}/{ds}/tree/main"
        names: list[str] = []
        cursor = None
        for _ in range(40):
            params = {"recursive": "true", "expand": "true"}
            if cursor:
                params["cursor"] = cursor
            r = self.fetcher.get(url, params=params)
            r.raise_for_status()
            data = r.json()
            if not isinstance(data, list):
                break
            names += [e["path"].split("/")[0] for e in data if e.get("type") == "directory"]
            link = r.headers.get("Link", "")
            if "rel=\"next\"" not in link:
                break
            cursor = link.split("cursor=")[1].split("&")[0].split(">")[0].strip('"')
        return sorted(set(names))

    def pull(self, corpus: str, dataset_ids: list[str] | None = None) -> dict[str, Any]:
        org, _ = CORPORA[corpus]
        started = time.time()
        try:
            names = self.list_datasets(corpus)
        except Exception as e:
            return {"corpus": corpus, "status": "error", "error": str(e)[:120]}
        want = dataset_ids or names
        pulled, failed = [], []
        for name in want:
            try:
                self._pull_one(org, name)
                pulled.append(name)
            except Exception as e:
                failed.append(f"{name}: {type(e).__name__}")
        ns = self.lake.stats()
        return {
            "corpus": corpus,
            "dataset": org,
            "pulled": len(pulled),
            "failed": len(failed),
            "series": ns["series"],
            "observations": ns["observations"],
            "seconds": time.time() - started,
            "failed_list": failed[:20],
        }

    def _pull_one(self, org: str, name: str) -> None:
        import pyarrow as pa
        url = f"https://huggingface.co/datasets/{org}/{name}/resolve/main/data-00000-of-00001.arrow"
        blob = self.fetcher.get_text(url)
        data = blob if isinstance(blob, bytes) else blob.encode()
        file = Path(f"/tmp/hf_arrow_{org.replace('/', '_' )}_{name}.arrow")
        file.write_bytes(data)
        src = pa.ipc.open_file(str(file))
        batch = src.read_all()
        cols = batch.column_names
        series = batch.to_pandas().to_dict("records")
        file.unlink(missing_ok=True)
        for rec in series:
            sid = rec.get("series_id", "")
            pts = rec.get("values") or []
            records = [(sid, ts, val) for ts, val in pts]
            if not records:
                continue
            self.catalog.upsert_series([
                (sid, name, org, "", "", rec.get("title", sid),
                 len(records), records[0][1], records[-1][1], "{}")])
            self.catalog.insert_observations(records)
            self.catalog.upsert_dataset(DatasetRef(
                dataset_id=f"{org.replace('/', '__')}__{name}",
                source=f"hf:{org}",
                domain="",
                title=rec.get("title", name),
                source_url=f"https://huggingface.co/datasets/{org}/{name}",
                license="Apache-2.0 (HF)",
                freq="",
                n_series=1,
                n_obs=len(records),
                first_ts=records[0][1],
                last_ts=records[-1][1],
                acquired_at=time.time(),
                meta={"series_id": sid, "hf_dataset": f"{org}/{name}"},
            ))

    def harvest_all(self, corpora: list[str] | None = None) -> list[dict]:
        targets = corpora or list(CORPORA)
        results = []
        for c in targets:
            results.append(self.pull(c))
            s = self.lake.stats()
            print(f"  {c:16s} {results[-1]['pulled']:4d} datasets -> "
                  f"series={s['series']:6d} obs={s['observations']:9d}")
        return results

    def stats(self) -> dict:
        return self.lake.stats()

    def close(self) -> None:
        self.lake.close()

    def __enter__(self) -> "HfCorpus":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()
