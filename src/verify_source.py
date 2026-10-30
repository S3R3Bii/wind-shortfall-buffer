"""Independent Pandas/JSON reconciliation; public mode checks projection only."""

import argparse, csv, gzip, hashlib, json
from collections import Counter, defaultdict
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--private-dir", type=Path)
    a = p.parse_args()
    m = json.loads((ROOT / "data/source_manifest.json").read_text(encoding="utf-8"))
    checks = 0
    for name, digest in m["files"].items():
        assert hashlib.sha256((ROOT / "data" / name).read_bytes()).hexdigest() == digest
        checks += 1
    with gzip.open(
        ROOT / "data/records.csv.gz", "rt", encoding="utf-8", newline=""
    ) as f:
        rows = list(csv.DictReader(f))
    assert len(rows) == m["source_records"]
    checks += 1
    grouped = defaultdict(list)
    for r in rows:
        grouped[r["source_file"]].append(r)
    assert {k: len(v) for k, v in grouped.items()} == m["rows_by_source"]
    checks += 1
    if a.private_dir:
        import pandas as pd

        for receipt in m["requests"]:
            assert (
                hashlib.sha256(
                    (a.private_dir / receipt["file"]).read_bytes()
                ).hexdigest()
                == receipt["sha256"]
            )
            checks += 1
        fields = m["selected_fields"]

        def canonical(row):
            out = []
            for field in fields:
                value = row[field]
                if value is None or value == "":
                    out.append(("missing", ""))
                    continue
                if field in ["measured", "dayaheadforecast", "monitoredcapacity"]:
                    try:
                        out.append(("numeric", Decimal(str(value))))
                    except InvalidOperation:
                        out.append(("literal", str(value)))
                elif field == "datetime":
                    out.append(
                        (
                            "UTC",
                            datetime.fromisoformat(str(value).replace("Z", "+00:00"))
                            .astimezone(timezone.utc)
                            .isoformat(),
                        )
                    )
                else:
                    out.append(("text", str(value)))
            return tuple(out)

        for name, items in grouped.items():
            csvrows = pd.read_csv(
                a.private_dir / name, sep=";", dtype=str, keep_default_na=False
            ).to_dict("records")
            jsonrows = json.loads(
                (a.private_dir / name.replace(".csv", ".json")).read_text(
                    encoding="utf-8"
                ),
                parse_float=Decimal,
                parse_int=Decimal,
            )
            assert isinstance(jsonrows, list) and len(csvrows) == len(jsonrows) == len(
                items
            )
            checks += 1
            assert Counter(map(canonical, csvrows)) == Counter(
                map(canonical, jsonrows)
            ), ("Two-format source mismatch", name)
            checks += len(csvrows) * len(fields)
            for expected, (i, r) in zip(csvrows, enumerate(items, 1)):
                assert r["source_row"] == str(i) and all(
                    r[k] == expected[k] for k in fields
                ), ("Projection binding", name, i)
                checks += len(fields) + 1
    result = dict(
        status="PASS",
        mode=(
            "PRIVATE_ORIGINALS_PANDAS_JSON_PASS"
            if a.private_dir
            else "PUBLIC_HASH_SCHEMA_ONLY"
        ),
        checks=checks,
        source_records=len(rows),
        scope="Complete selected CSV/JSON multisets and public row bindings when private originals supplied;not historical issuance or meter validation",
    )
    if a.private_dir:
        (ROOT / "data/source_verification.json").write_bytes(
            (json.dumps(result, indent=2) + "\n").encode()
        )
    print(json.dumps(result))


if __name__ == "__main__":
    main()
