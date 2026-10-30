"""Independent SQL cohort selection and exact policy/forecast reconstruction."""

import argparse, csv, gzip, hashlib, json, sqlite3, subprocess
from collections import defaultdict
from datetime import datetime
from decimal import Decimal, localcontext
from fractions import Fraction as F
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read(n):
    return json.loads((ROOT / n).read_text(encoding="utf-8"))


def rows(n):
    with gzip.open(ROOT / n, "rt", encoding="utf-8", newline="") as f:
        yield from csv.DictReader(f)


def ratio(x):
    return f"{x.numerator}/{x.denominator}"


def formatted(x):
    with localcontext() as c:
        c.prec = 80
        return format(Decimal(x.numerator) / Decimal(x.denominator), ".12f")


def decimal(x):
    x = F(x)
    with localcontext() as c:
        c.prec = 80
        d = Decimal(x.numerator) / Decimal(x.denominator)
    assert (
        F(d) == x
    ), "Source hourly value must have exact finite decimal representation"
    return d


def med(a):
    a = sorted(a)
    n = len(a)
    assert n
    return (a[(n - 1) // 2] + a[n // 2]) / 2


def main(stage):
    db = sqlite3.connect(":memory:")
    db.execute(
        "CREATE TABLE hours(region TEXT,stamp TEXT,yr INTEGER,role TEXT,actual TEXT,forecast TEXT,capacity TEXT)"
    )
    db.executemany(
        "INSERT INTO hours VALUES(?,?,?,?,?,?,?)",
        (
            (
                r["region"],
                r["datetime"],
                int(r["year"]),
                r["split"],
                r["measured_exact"],
                r["forecast_exact"],
                r["capacity_exact"],
            )
            for r in rows("results/hourly_inputs.csv.gz")
            if r["eligible"] == "True"
        ),
    )
    configs = ["zero", "mean", "median", "q75", "q80", "q90"]
    scales = {}
    for region in ["Flanders", "Wallonia"]:
        scales[region] = F(
            med(
                [
                    decimal(x[0])
                    for x in db.execute(
                        "SELECT capacity FROM hours WHERE region=? AND yr<2020",
                        (region,),
                    )
                ]
            )
        )
    training = list(
        db.execute(
            "SELECT stamp,actual,forecast FROM hours WHERE role='train' ORDER BY stamp"
        )
    )
    errors = sorted(
        [decimal(forecast) - decimal(actual) for _, actual, forecast in training]
    )
    n = len(errors)
    b = [
        F(0),
        sum(map(F, errors), F(0)) / n,
        F(med(errors)),
        F(errors[(3 * n + 3) // 4 - 1]),
        F(errors[(4 * n + 4) // 5 - 1]),
        F(errors[(9 * n + 9) // 10 - 1]),
    ]
    b = [v / scales["Flanders"] for v in b]
    expected_fit = dict(
        training_region="Flanders",
        training_years=[2018, 2019],
        training_n=n,
        scales={k: ratio(v) for k, v in scales.items()},
        statistics={k: ratio(v) for k, v in zip(configs, b)},
        source="Earlier eligible Flanders errors only;Wallonia earlier monitored-capacity covariates only,no Wallonia error fitting",
        training_key_sha256=hashlib.sha256(
            "\n".join("Flanders|" + r[0] for r in training).encode()
        ).hexdigest(),
    )
    assert read("results/fitted.json") == expected_fit
    where = (
        "role='development'"
        if stage == "development"
        else "role IN ('held_temporal','held_geographic')"
    )
    source = {
        (region, t): (F(actual), F(forecast), role)
        for region, t, actual, forecast, role in db.execute(
            "SELECT region,stamp,actual,forecast,role FROM hours WHERE " + where
        )
    }
    checks = 0
    seen = set()
    aggregates = defaultdict(lambda: [0, F(0), F(0), F(0), 0])
    for r in rows("results/" + stage + "_policy_predictions.csv.gz"):
        region, t, c, cap = r["region"], r["datetime"], r["config"], F(r["cap_ratio"])
        key = (region, t, c, cap)
        assert key not in seen
        seen.add(key)
        actual, forecast, role = source[(region, t)]
        index = configs.index(c)
        scale = scales[region]
        assert cap in [F(1, 20), F(1, 10), F(1, 5)]
        reserve = max(F(0), min(b[index], cap)) * scale
        uncovered = max(F(0), forecast - actual - reserve)
        excess = max(F(0), reserve - max(F(0), forecast - actual))
        event = forecast - actual > reserve
        expected = dict(
            datetime=t,
            region=region,
            split=role,
            config=c,
            config_index=str(index),
            cap_ratio=ratio(cap),
            scale_exact=ratio(scale),
            forecast_exact=ratio(forecast),
            measured_exact=ratio(actual),
            reserve_exact=ratio(reserve),
            uncovered_exact=ratio(uncovered),
            excess_exact=ratio(excess),
            uncovered_event=str(int(event)),
        )
        assert r == expected, key
        checks += 13
        groups = [
            ("overall", "all"),
            ("quarter", t[:4] + "Q" + str((int(t[5:7]) - 1) // 3 + 1)),
        ]
        if stage == "held":
            groups.append(("year", t[:4]))
        for group, level in groups:
            a = aggregates[(region, c, index, cap, group, level)]
            a[0] += 1
            a[1] += reserve
            a[2] += uncovered
            a[3] += excess
            a[4] += event
    assert len(seen) == len(source) * 18
    expected_scores = []
    for (region, c, index, cap, group, level), (
        n,
        reserve,
        uncovered,
        excess,
        events,
    ) in sorted(aggregates.items()):
        for penalty in [2, 5, 10]:
            cost = (reserve + penalty * uncovered) / n
            normalized = cost / scales[region]
            expected_scores.append(
                dict(
                    region=region,
                    config=c,
                    config_index=index,
                    cap_ratio=ratio(cap),
                    penalty=penalty,
                    group=group,
                    level=level,
                    n=n,
                    mean_reserve=formatted(reserve / n),
                    mean_uncovered=formatted(uncovered / n),
                    mean_excess=formatted(excess / n),
                    uncovered_events=events,
                    coverage=formatted(F(n - events, n)),
                    mean_proxy_cost=formatted(cost),
                    normalized_cost=formatted(normalized),
                    reserve_sum_exact=ratio(reserve),
                    uncovered_sum_exact=ratio(uncovered),
                    excess_sum_exact=ratio(excess),
                    mean_proxy_cost_exact=ratio(cost),
                    normalized_cost_exact=ratio(normalized),
                )
            )
    assert read("results/" + stage + "_policy_metrics.json") == expected_scores
    checks += len(expected_scores) * 20
    search = []
    if stage == "development":
        for index, c in enumerate(configs):
            blocks = [
                r
                for r in expected_scores
                if r["config"] == c
                and r["cap_ratio"] == "1/10"
                and r["penalty"] == 5
                and r["group"] == "quarter"
            ]
            assert len(blocks) == 4
            objective = sum((F(r["normalized_cost_exact"]) for r in blocks), F(0)) / 4
            search.append(
                dict(
                    config=c,
                    config_index=index,
                    objective_exact=ratio(objective),
                    objective=formatted(objective),
                    blocks=[r["level"] for r in blocks],
                    status="PASS",
                )
            )
        assert read("results/development_search.json") == search
        chosen = min(search, key=lambda r: (F(r["objective_exact"]), r["config_index"]))
        assert read("results/selection.json") == dict(
            config=chosen["config"],
            config_index=chosen["config_index"],
            objective_exact=chosen["objective_exact"],
            metric="Equal-quarter normalized proxy cost,cap1/10,penalty5",
            tie_break="Lower fixed candidate index",
        )
        checks += len(search) + 1
    point_seen = set()
    points = defaultdict(list)
    for r in rows("results/" + stage + "_point_predictions.csv.gz"):
        region, t, c = r["region"], r["datetime"], r["config"]
        key = (region, t, c)
        assert key not in point_seen
        point_seen.add(key)
        actual, forecast, role = source[(region, t)]
        scale = scales[region]
        bias = {"source": F(0), "mean_corrected": b[1], "median_corrected": b[2]}[c]
        estimate = max(F(0), forecast - bias * scale)
        error = estimate - actual
        assert r == dict(
            datetime=t,
            region=region,
            split=role,
            config=c,
            scale_exact=ratio(scale),
            forecast_exact=ratio(estimate),
            measured_exact=ratio(actual),
            error_exact=ratio(error),
        )
        checks += 8
        keys = [
            ("overall", "all"),
            ("quarter", t[:4] + "Q" + str((int(t[5:7]) - 1) // 3 + 1)),
        ]
        if stage == "held":
            keys.append(("year", t[:4]))
        for group, level in keys:
            points[(region, c, group, level)].append(error)
    assert len(point_seen) == len(source) * 3
    expected_points = []
    for (region, c, group, level), error in sorted(points.items()):
        n = len(error)
        mae = sum(map(abs, error), F(0)) / n
        mse = sum((e * e for e in error), F(0)) / n
        bias = sum(error, F(0)) / n
        with localcontext() as context:
            context.prec = 80
            rmse = format(
                (Decimal(mse.numerator) / Decimal(mse.denominator)).sqrt(), ".12f"
            )
        expected_points.append(
            dict(
                region=region,
                config=c,
                group=group,
                level=level,
                n=n,
                mae=formatted(mae),
                mse=formatted(mse),
                rmse=rmse,
                bias=formatted(bias),
                normalized_mae=formatted(mae / scales[region]),
                mae_exact=ratio(mae),
                mse_exact=ratio(mse),
                bias_exact=ratio(bias),
            )
        )
    assert read("results/" + stage + "_point_metrics.json") == expected_points
    checks += len(expected_points) * 14
    if stage == "held":
        commit = read("results/held_execution.json")["freeze_commit"]
        frozen = read("results/frozen_dependencies.json")
        assert subprocess.check_output(
            ["git", "show", commit + ":results/frozen_dependencies.json"], cwd=ROOT
        ) == (ROOT / "results/frozen_dependencies.json").read_bytes().replace(
            b"\r\n", b"\n"
        )
        for name, digest in frozen["sha256"].items():
            blob = (ROOT / name).read_bytes()
            blob = blob if name.endswith(".gz") else blob.replace(b"\r\n", b"\n")
            assert (
                hashlib.sha256(blob).hexdigest() == digest
                and hashlib.sha256(
                    subprocess.check_output(
                        ["git", "show", commit + ":" + name], cwd=ROOT
                    )
                ).hexdigest()
                == digest
            )
            checks += 2
    result = dict(
        status="PASS",
        stage=stage,
        checks=checks,
        training_labels=len(training),
        eligible_hours=len(source),
        policy_prediction_rows=len(seen),
        point_prediction_rows=len(point_seen),
        policy_metric_rows=len(expected_scores),
        point_metric_rows=len(expected_points),
        independence="Separate SQL role selection,Decimal signed training inputs,exact-rational actions/losses and all metrics;no analysis imports",
        limits="Finite historical-source and hypothetical-policy checks;not exact issuance,meter truth or reserve reliability",
    )
    (ROOT / ("results/independent_" + stage + ".json")).write_bytes(
        (json.dumps(result, indent=2) + "\n").encode()
    )
    print(json.dumps(result))


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("stage", choices=["development", "held"])
    main(p.parse_args().stage)
