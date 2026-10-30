"""Retain the full UTC calendar and integrate only four complete quarter-hours."""

import argparse, csv, gzip, io, json
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from fractions import Fraction as Q
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
START = datetime(2018, 1, 1, tzinfo=timezone.utc)
END = datetime(2024, 1, 1, tzinfo=timezone.utc)


def exact(x):
    return f"{x.numerator}/{x.denominator}"


def parse(value):
    try:
        return Q(value) if value != "" else None
    except (ValueError, ZeroDivisionError):
        return None


def interval(row):
    reasons = []
    try:
        t = datetime.fromisoformat(row["datetime"].replace("Z", "+00:00"))
        if t.tzinfo is None:
            raise ValueError("Missing timezone")
        t = t.astimezone(timezone.utc)
    except (ValueError, TypeError):
        t = None
        reasons.append("invalid_timestamp")
    if t is not None and (
        not START <= t < END or t.minute % 15 or t.second or t.microsecond
    ):
        reasons.append("outside_or_unaligned_calendar")
    if (
        row["region"] not in ["Flanders", "Wallonia"]
        or row["offshoreonshore"] != "Onshore"
        or row["gridconnectiontype"] != "Dso"
    ):
        reasons.append("outside_source_categories")
    if row["resolutioncode"] != "PT15M":
        reasons.append("wrong_interval_length")
    values = {
        field: parse(row[field])
        for field in ["measured", "dayaheadforecast", "monitoredcapacity"]
    }
    for field, x in values.items():
        if x is None:
            reasons.append(field + "_unavailable_or_nonfinite")
        elif x < 0 or (field == "monitoredcapacity" and x == 0):
            reasons.append(field + "_outside_sign_rule")
    return t, values, reasons


def hourly_mean(quarter_hours):
    if len(quarter_hours) != 4 or len({t for t, _ in quarter_hours}) != 4:
        raise ValueError("Four distinct quarter-hours required")
    ordered = sorted(quarter_hours)
    base = ordered[0][0]
    if base.minute != 0 or any(
        t != base + timedelta(minutes=15 * i) for i, (t, _) in enumerate(ordered)
    ):
        raise ValueError("Complete aligned UTC hour required")
    return {
        field: sum((v[field] for _, v in ordered), Q(0)) / 4
        for field in ["measured", "dayaheadforecast", "monitoredcapacity"]
    }


def write_csv(path, rows):
    stream = io.StringIO(newline="")
    w = csv.DictWriter(stream, fieldnames=list(rows[0]), lineterminator="\n")
    w.writeheader()
    w.writerows(rows)
    path.write_bytes(gzip.compress(stream.getvalue().encode(), mtime=0))


def build(rows):
    grouped = defaultdict(list)
    raw_audit = []
    for i, r in enumerate(rows):
        t, values, reasons = interval(r)
        audit = dict(
            source_index=i,
            source_file=r["source_file"],
            source_row=r["source_row"],
            datetime=r["datetime"],
            region=r["region"],
            eligible=False,
            reasons="",
        )
        raw_audit.append(audit)
        if (
            t is not None
            and START <= t < END
            and t.minute % 15 == 0
            and not t.second
            and not t.microsecond
            and r["region"] in ["Flanders", "Wallonia"]
        ):
            grouped[(r["region"], t)].append((i, values, reasons))
        audit["reasons"] = ";".join(reasons)
    slots = []
    hours = []
    coverage = defaultdict(
        lambda: dict(
            planned_hours=0, eligible_hours=0, planned_slots=0, eligible_slots=0
        )
    )
    slot_reasons = Counter()
    for region in ["Flanders", "Wallonia"]:
        current = START
        while current < END:
            valid = []
            hour_reasons = []
            year = current.year
            quarter = (current.month - 1) // 3 + 1
            key = f"{region}|{year}|Q{quarter}"
            for offset in range(4):
                t = current + timedelta(minutes=15 * offset)
                items = grouped.get((region, t), [])
                reasons = []
                if not items:
                    reasons = ["missing_source_key"]
                elif len(items) > 1:
                    reasons = ["duplicate_source_key"]
                else:
                    reasons = list(items[0][2])
                for index, _, base in items:
                    raw_audit[index]["eligible"] = not reasons
                    raw_audit[index]["reasons"] = ";".join(sorted(set(base + reasons)))
                good = not reasons
                if good:
                    valid.append((t, items[0][1]))
                slots.append(
                    dict(
                        datetime=t.isoformat(),
                        region=region,
                        source_rows=len(items),
                        eligible=good,
                        reasons=";".join(reasons),
                    )
                )
                coverage[key]["planned_slots"] += 1
                coverage[key]["eligible_slots"] += good
                slot_reasons.update(reasons)
                hour_reasons.extend(reasons)
            good = len(valid) == 4
            coverage[key]["planned_hours"] += 1
            coverage[key]["eligible_hours"] += good
            role = (
                (
                    "train"
                    if year <= 2019
                    else "development" if year == 2020 else "held_temporal"
                )
                if region == "Flanders"
                else (
                    "capacity_reference_only"
                    if year <= 2019
                    else "unscored" if year == 2020 else "held_geographic"
                )
            )
            values = hourly_mean(valid) if good else None
            hours.append(
                dict(
                    datetime=current.isoformat(),
                    region=region,
                    year=year,
                    quarter=quarter,
                    split=role,
                    eligible=good,
                    reasons=";".join(sorted(set(hour_reasons))),
                    measured_exact=exact(values["measured"]) if good else "",
                    forecast_exact=exact(values["dayaheadforecast"]) if good else "",
                    capacity_exact=exact(values["monitoredcapacity"]) if good else "",
                )
            )
            current += timedelta(hours=1)
    for x in coverage.values():
        x["pass_90_percent"] = 10 * x["eligible_hours"] >= 9 * x["planned_hours"]
    gate = dict(
        status=(
            "PASS"
            if all(x["pass_90_percent"] for x in coverage.values())
            else "FAIL_QUARTER_COVERAGE"
        ),
        source_rows=len(rows),
        planned_slots=len(slots),
        eligible_slots=sum(r["eligible"] for r in slots),
        planned_hours=len(hours),
        eligible_hours=sum(r["eligible"] for r in hours),
        slot_reason_counts=dict(slot_reasons),
        by_region_year_quarter=dict(coverage),
        scope="Source completeness/sign/alignment checks;not exact issuance access,upscaled-meter validation or reserve reliability",
    )
    return raw_audit, slots, hours, gate


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--output-dir", type=Path, default=ROOT / "results")
    a = p.parse_args()
    a.output_dir.mkdir(exist_ok=True)
    with gzip.open(
        ROOT / "data/records.csv.gz", "rt", encoding="utf-8", newline=""
    ) as f:
        rows = list(csv.DictReader(f))
    raw, slots, hours, gate = build(rows)
    for name, records in [
        ("source_row_eligibility", raw),
        ("quarter_hour_calendar", slots),
        ("hourly_inputs", hours),
    ]:
        write_csv(a.output_dir / (name + ".csv.gz"), records)
    (a.output_dir / "source_gate.json").write_bytes(
        (json.dumps(gate, indent=2) + "\n").encode()
    )
    print(
        json.dumps(
            {
                k: gate[k]
                for k in [
                    "status",
                    "source_rows",
                    "planned_slots",
                    "eligible_slots",
                    "planned_hours",
                    "eligible_hours",
                    "slot_reason_counts",
                ]
            }
        )
    )
    assert gate["status"] == "PASS", "Fixed source gate failed;preserve before models"


if __name__ == "__main__":
    main()
