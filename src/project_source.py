"""Minimal attributed projection of the declared source, preserving all rows."""

import argparse, csv, gzip, hashlib, io, json, platform, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FIELDS = [
    "datetime",
    "resolutioncode",
    "offshoreonshore",
    "region",
    "gridconnectiontype",
    "measured",
    "dayaheadforecast",
    "monitoredcapacity",
    "decrementalbidid",
]


def main():
    p = argparse.ArgumentParser()
    p.add_argument("private_dir", type=Path)
    a = p.parse_args()
    manifest = ROOT / "data/source_manifest.json"
    out = ROOT / "data/records.csv.gz"
    assert not manifest.exists() and not out.exists()
    requests = json.loads((a.private_dir / "requests.json").read_text(encoding="utf-8"))
    assert len(requests) == 26 and all(r["status"] == 200 for r in requests)
    for record in requests:
        assert (
            hashlib.sha256((a.private_dir / record["file"]).read_bytes()).hexdigest()
            == record["sha256"]
        )
    before = json.loads(
        (a.private_dir / "metadata-before.json").read_text(encoding="utf-8")
    )["metas"]["default"]
    after = json.loads(
        (a.private_dir / "metadata-after.json").read_text(encoding="utf-8")
    )["metas"]["default"]
    markers = ["data_processed", "metadata_processed", "records_count"]
    assert all(before[k] == after[k] for k in markers)
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(
        stream, fieldnames=["source_file", "source_row", *FIELDS], lineterminator="\n"
    )
    writer.writeheader()
    counts = {}
    for year in range(2018, 2024):
        for region in ["Flanders", "Wallonia"]:
            name = f"{year}-{region}.csv"
            with (a.private_dir / name).open(encoding="utf-8-sig", newline="") as f:
                reader = csv.DictReader(f, delimiter=";")
                assert reader.fieldnames == FIELDS
                count = 0
                for count, row in enumerate(reader, 1):
                    writer.writerow(dict(source_file=name, source_row=count, **row))
            counts[name] = count
    data = gzip.compress(stream.getvalue().encode(), mtime=0)
    out.write_bytes(data)
    value = dict(
        source="Elia Transmission Belgium SA,ods031",
        source_url="https://opendata.elia.be/explore/dataset/ods031/information/",
        license="CC BY 4.0,Elia Open Data License",
        license_url="https://opendata.elia.be/pages/licence/",
        changes="Minimal selected fields,source row binding;no omission or imputation;derived hourly/policy results separately labeled",
        endorsement=False,
        protocol_commit=requests[0]["protocol_commit"],
        files={"records.csv.gz": hashlib.sha256(data).hexdigest()},
        source_records=sum(counts.values()),
        selected_fields=FIELDS,
        rows_by_source=counts,
        requests=requests,
        metadata_markers={k: before[k] for k in markers},
        projection_runtime=dict(
            python=platform.python_version(),
            implementation=platform.python_implementation(),
        ),
        limits="Current source-labeled day-ahead estimates;not validated metering or original issuance-version receipts. Offshore discrepancy warning in official metadata;offshore excluded. Original responses and source pages private.",
    )
    manifest.write_bytes((json.dumps(value, indent=2) + "\n").encode())
    print(
        json.dumps(
            dict(
                status="PROJECTED_NOT_INDEPENDENTLY_VERIFIED",
                source_records=sum(counts.values()),
                compressed_bytes=len(data),
            )
        )
    )


if __name__ == "__main__":
    main()
