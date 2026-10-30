"""Reconstruct every source/calendar/hour with SQLite and Decimal, no analysis imports."""

import csv, gzip, json, sqlite3
from collections import Counter, defaultdict
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, localcontext
from fractions import Fraction
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def rows(name):
    with gzip.open(ROOT / name, "rt", encoding="utf-8", newline="") as f:
        yield from csv.DictReader(f)


def main():
    connection = sqlite3.connect(":memory:")
    connection.execute(
        "CREATE TABLE raw(idx INTEGER PRIMARY KEY,region TEXT,stamp INTEGER,reasons TEXT,measured TEXT,forecast TEXT,capacity TEXT)"
    )
    checks = 0
    expected_raw = []
    for index, r in enumerate(rows("data/records.csv.gz")):
        reasons = []
        stamp = None
        try:
            t = datetime.fromisoformat(r["datetime"].replace("Z", "+00:00"))
            assert t.tzinfo is not None
            t = t.astimezone(timezone.utc)
            stamp = int(t.timestamp())
        except (ValueError, TypeError, AssertionError):
            reasons.append("invalid_timestamp")
        if stamp is not None and (
            not 1514764800 <= stamp < 1704067200 or stamp % 900 or t.microsecond
        ):
            reasons.append("outside_or_unaligned_calendar")
        if (
            r["region"] not in ["Flanders", "Wallonia"]
            or r["offshoreonshore"] != "Onshore"
            or r["gridconnectiontype"] != "Dso"
        ):
            reasons.append("outside_source_categories")
        if r["resolutioncode"] != "PT15M":
            reasons.append("wrong_interval_length")
        for field in ["measured", "dayaheadforecast", "monitoredcapacity"]:
            try:
                value = Decimal(r[field])
                assert value.is_finite()
                if value < 0 or (field == "monitoredcapacity" and value == 0):
                    reasons.append(field + "_outside_sign_rule")
            except (InvalidOperation, AssertionError):
                reasons.append(field + "_unavailable_or_nonfinite")
        bound = (
            stamp
            if stamp is not None
            and 1514764800 <= stamp < 1704067200
            and stamp % 900 == 0
            and not t.microsecond
            else None
        )
        connection.execute(
            "INSERT INTO raw VALUES(?,?,?,?,?,?,?)",
            (
                index,
                r["region"],
                bound,
                ";".join(reasons),
                r["measured"],
                r["dayaheadforecast"],
                r["monitoredcapacity"],
            ),
        )
        expected_raw.append(
            (r["source_file"], r["source_row"], r["datetime"], r["region"], reasons)
        )
    connection.execute("CREATE INDEX keys ON raw(region,stamp)")
    counts = {
        (r, t): n
        for r, t, n in connection.execute(
            "SELECT region,stamp,count(*) FROM raw WHERE stamp IS NOT NULL GROUP BY region,stamp"
        )
    }
    for i, a in enumerate(rows("results/source_row_eligibility.csv.gz")):
        source_file, source_row, dt, region, reasons = expected_raw[i]
        db = connection.execute("SELECT stamp FROM raw WHERE idx=?", (i,)).fetchone()
        stamp = db[0]
        if stamp is not None and counts.get((region, stamp), 0) > 1:
            reasons = [*reasons, "duplicate_source_key"]
        assert (
            a["source_index"] == str(i)
            and a["source_file"] == source_file
            and a["source_row"] == source_row
            and a["datetime"] == dt
            and a["region"] == region
            and a["eligible"] == str(not reasons)
            and set(filter(None, a["reasons"].split(";"))) == set(reasons)
        ), ("Raw eligibility", i)
        checks += 7
    # Join the independent calendar to source multiplicities instead of trusting emitted eligibility.
    connection.execute("CREATE TABLE calendar(region TEXT,stamp INTEGER)")
    connection.executemany(
        "INSERT INTO calendar VALUES(?,?)",
        (
            (region, stamp)
            for region in ["Flanders", "Wallonia"]
            for stamp in range(1514764800, 1704067200, 900)
        ),
    )
    joins = connection.execute(
        "SELECT c.region,c.stamp,count(r.idx),group_concat(r.reasons),r.measured,r.forecast,r.capacity FROM calendar c LEFT JOIN raw r ON c.region=r.region AND c.stamp=r.stamp GROUP BY c.region,c.stamp ORDER BY c.region,c.stamp"
    )
    hourly = defaultdict(list)
    coverage = defaultdict(
        lambda: dict(
            planned_hours=0, eligible_hours=0, planned_slots=0, eligible_slots=0
        )
    )
    reason_counts = Counter()
    expected_slot_count = 0
    for actual, joined in zip(
        rows("results/quarter_hour_calendar.csv.gz"), joins, strict=True
    ):
        region, stamp, n, why, measured, forecast, capacity = joined
        reasons = (
            ["missing_source_key"]
            if n == 0
            else (
                ["duplicate_source_key"]
                if n > 1
                else list(filter(None, (why or "").split(";")))
            )
        )
        good = not reasons
        iso = datetime.fromtimestamp(stamp, timezone.utc).isoformat()
        assert actual == dict(
            datetime=iso,
            region=region,
            source_rows=str(n),
            eligible=str(good),
            reasons=";".join(reasons),
        ), ("Slot", iso, region)
        checks += 5
        expected_slot_count += 1
        reason_counts.update(reasons)
        hour = stamp // 3600 * 3600
        hourly[(region, hour)].append((good, reasons, measured, forecast, capacity))
    for actual, (region, stamp) in zip(
        rows("results/hourly_inputs.csv.gz"), sorted(hourly), strict=True
    ):
        slots = hourly[(region, stamp)]
        assert len(slots) == 4
        good = all(r[0] for r in slots)
        t = datetime.fromtimestamp(stamp, timezone.utc)
        year = t.year
        quarter = (t.month - 1) // 3 + 1
        key = f"{region}|{year}|Q{quarter}"
        c = coverage[key]
        c["planned_hours"] += 1
        c["eligible_hours"] += good
        c["planned_slots"] += 4
        c["eligible_slots"] += sum(r[0] for r in slots)
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
        expected = dict(
            datetime=t.isoformat(),
            region=region,
            year=str(year),
            quarter=str(quarter),
            split=role,
            eligible=str(good),
            reasons=";".join(sorted({x for r in slots for x in r[1]})),
        )
        with localcontext() as context:
            context.prec = 80
            for index, name in [
                (2, "measured_exact"),
                (3, "forecast_exact"),
                (4, "capacity_exact"),
            ]:
                value = (
                    Fraction(sum((Decimal(r[index]) for r in slots), Decimal(0)) / 4)
                    if good
                    else None
                )
                expected[name] = (
                    f"{value.numerator}/{value.denominator}" if good else ""
                )
        assert actual == expected, ("Hour", t, region)
        checks += len(expected)
    for c in coverage.values():
        c["pass_90_percent"] = 10 * c["eligible_hours"] >= 9 * c["planned_hours"]
    gate = json.loads((ROOT / "results/source_gate.json").read_text(encoding="utf-8"))
    assert gate["by_region_year_quarter"] == dict(coverage) and gate[
        "slot_reason_counts"
    ] == dict(reason_counts)
    assert (
        gate["planned_slots"] == expected_slot_count == 420672
        and gate["planned_hours"] == len(hourly) == 105168
    )
    assert gate["status"] == (
        "PASS"
        if all(c["pass_90_percent"] for c in coverage.values())
        else "FAIL_QUARTER_COVERAGE"
    )
    checks += 5
    result = dict(
        status="PASS",
        checks=checks,
        source_records=len(expected_raw),
        planned_slots=expected_slot_count,
        planned_hours=len(hourly),
        independence="Separate SQLite full-calendar joins and80digitDecimal integration;no analysis imports",
        limits="Source completeness and exact selected representations,not original issuance or physical meter validation",
    )
    (ROOT / "results/independent_inputs.json").write_bytes(
        (json.dumps(result, indent=2) + "\n").encode()
    )
    print(json.dumps(result))


if __name__ == "__main__":
    main()
