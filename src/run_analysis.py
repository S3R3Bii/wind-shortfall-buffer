"""Fit on earlier Flanders labels; select on development before held evaluation."""

import argparse, csv, gzip, hashlib, io, json, platform, subprocess, time
from collections import defaultdict
from datetime import datetime, timezone
from fractions import Fraction as Q
from pathlib import Path
from model import (
    CONFIGS,
    CAPS,
    PENALTIES,
    exact,
    display,
    median,
    fit,
    action,
    components,
    adjusted_forecast,
    point_metrics,
    choose,
)

ROOT = Path(__file__).resolve().parents[1]


def write_json(path, value):
    path.write_bytes((json.dumps(value, indent=2) + "\n").encode())


def read(path):
    return json.loads((ROOT / path).read_text(encoding="utf-8"))


def read_rows():
    with gzip.open(
        ROOT / "results/hourly_inputs.csv.gz", "rt", encoding="utf-8", newline=""
    ) as stream:
        return list(csv.DictReader(stream))


def freeze_proof():
    path = ROOT / "results/frozen_dependencies.json"
    frozen = read("results/frozen_dependencies.json")
    commit = subprocess.check_output(
        ["git", "log", "-1", "--format=%H", "--", "results/frozen_dependencies.json"],
        cwd=ROOT,
        text=True,
    ).strip()
    assert commit
    assert subprocess.check_output(
        ["git", "show", commit + ":results/frozen_dependencies.json"], cwd=ROOT
    ) == path.read_bytes().replace(b"\r\n", b"\n")
    for name, digest in frozen["sha256"].items():
        assert (
            hashlib.sha256(
                (ROOT / name).read_bytes().replace(b"\r\n", b"\n")
                if Path(name).suffix != ".gz"
                else (ROOT / name).read_bytes()
            ).hexdigest()
            == digest
        ), ("Changed frozen file", name)
        assert (
            hashlib.sha256(
                subprocess.check_output(["git", "show", commit + ":" + name], cwd=ROOT)
            ).hexdigest()
            == digest
        ), ("Original freeze blob", name)
    return commit


def write_compressed(path, fields, iterator):
    # Explicit zero gzip timestamp makes replay bytes deterministic.
    with path.open("wb") as output:
        with gzip.GzipFile(
            filename="", mode="wb", fileobj=output, mtime=0
        ) as compressed:
            with io.TextIOWrapper(compressed, encoding="utf-8", newline="") as stream:
                writer = csv.DictWriter(stream, fieldnames=fields, lineterminator="\n")
                writer.writeheader()
                for row in iterator:
                    writer.writerow(row)


def groups(row, stage):
    keys = [("overall", "all"), ("quarter", row["year"] + "Q" + row["quarter"])]
    if stage == "held":
        keys.append(("year", row["year"]))
    return keys


def run(stage, out):
    start = time.perf_counter()
    assert (
        read("results/source_gate.json")["status"] == "PASS"
        and read("results/independent_inputs.json")["status"] == "PASS"
    )
    freeze = None
    if stage == "held":
        freeze = freeze_proof()
    rows = read_rows()
    eligible = [r for r in rows if r["eligible"] == "True"]
    if stage == "development":
        scales = {
            region: median(
                [
                    Q(r["capacity_exact"])
                    for r in eligible
                    if r["region"] == region and int(r["year"]) <= 2019
                ]
            )
            for region in ["Flanders", "Wallonia"]
        }
        train = [r for r in eligible if r["split"] == "train"]
        errors = [
            (Q(r["forecast_exact"]) - Q(r["measured_exact"])) / scales["Flanders"]
            for r in train
        ]
        statistics = fit(errors)
        fitted = dict(
            training_region="Flanders",
            training_years=[2018, 2019],
            training_n=len(train),
            scales={k: exact(v) for k, v in scales.items()},
            statistics={k: exact(v) for k, v in zip(CONFIGS, statistics)},
            source="Earlier eligible Flanders errors only;Wallonia earlier monitored-capacity covariates only,no Wallonia error fitting",
            training_key_sha256=hashlib.sha256(
                "\n".join(r["region"] + "|" + r["datetime"] for r in train).encode()
            ).hexdigest(),
        )
        write_json(out / "fitted.json", fitted)
    else:
        fitted = read("results/fitted.json")
        scales = {k: Q(v) for k, v in fitted["scales"].items()}
        statistics = [Q(fitted["statistics"][c]) for c in CONFIGS]
    cohort = (
        [r for r in eligible if r["split"] == "development"]
        if stage == "development"
        else [r for r in eligible if r["split"] in ["held_temporal", "held_geographic"]]
    )
    aggregates = defaultdict(
        lambda: dict(n=0, reserve=Q(0), shortfall=Q(0), excess=Q(0), events=0)
    )
    policy_count = 0

    def policy_rows():
        nonlocal policy_count
        for r in cohort:
            region = r["region"]
            scale = scales[region]
            error = Q(r["forecast_exact"]) - Q(r["measured_exact"])
            for index, config in enumerate(CONFIGS):
                for cap in CAPS:
                    reserve = action(statistics[index], scale, cap)
                    shortfall, excess, event = components(error, reserve)
                    for group, level in groups(r, stage):
                        a = aggregates[(region, config, index, cap, group, level)]
                        a["n"] += 1
                        a["reserve"] += reserve
                        a["shortfall"] += shortfall
                        a["excess"] += excess
                        a["events"] += event
                    policy_count += 1
                    yield dict(
                        datetime=r["datetime"],
                        region=region,
                        split=r["split"],
                        config=config,
                        config_index=index,
                        cap_ratio=exact(cap),
                        scale_exact=exact(scale),
                        forecast_exact=r["forecast_exact"],
                        measured_exact=r["measured_exact"],
                        reserve_exact=exact(reserve),
                        uncovered_exact=exact(shortfall),
                        excess_exact=exact(excess),
                        uncovered_event=int(event),
                    )

    fields = [
        "datetime",
        "region",
        "split",
        "config",
        "config_index",
        "cap_ratio",
        "scale_exact",
        "forecast_exact",
        "measured_exact",
        "reserve_exact",
        "uncovered_exact",
        "excess_exact",
        "uncovered_event",
    ]
    write_compressed(
        out / (stage + "_policy_predictions.csv.gz"), fields, policy_rows()
    )
    scores = []
    for (region, config, index, cap, group, level), a in sorted(aggregates.items()):
        n = a["n"]
        scale = scales[region]
        for penalty in PENALTIES:
            cost = (a["reserve"] + penalty * a["shortfall"]) / n
            normalized = cost / scale
            scores.append(
                dict(
                    region=region,
                    config=config,
                    config_index=index,
                    cap_ratio=exact(cap),
                    penalty=penalty,
                    group=group,
                    level=level,
                    n=n,
                    mean_reserve=display(a["reserve"] / n),
                    mean_uncovered=display(a["shortfall"] / n),
                    mean_excess=display(a["excess"] / n),
                    uncovered_events=a["events"],
                    coverage=display(Q(n - a["events"], n)),
                    mean_proxy_cost=display(cost),
                    normalized_cost=display(normalized),
                    reserve_sum_exact=exact(a["reserve"]),
                    uncovered_sum_exact=exact(a["shortfall"]),
                    excess_sum_exact=exact(a["excess"]),
                    mean_proxy_cost_exact=exact(cost),
                    normalized_cost_exact=exact(normalized),
                )
            )
    write_json(out / (stage + "_policy_metrics.json"), scores)
    if stage == "development":
        search = []
        for index, config in enumerate(CONFIGS):
            blocks = [
                r
                for r in scores
                if r["config"] == config
                and r["cap_ratio"] == "1/10"
                and r["penalty"] == 5
                and r["group"] == "quarter"
            ]
            assert len(blocks) == 4
            objective = sum((Q(r["normalized_cost_exact"]) for r in blocks), Q(0)) / 4
            search.append(
                dict(
                    config=config,
                    config_index=index,
                    objective_exact=exact(objective),
                    objective=display(objective),
                    blocks=[r["level"] for r in blocks],
                    status="PASS",
                )
            )
        selected = choose(search)
        write_json(out / "development_search.json", search)
        write_json(
            out / "selection.json",
            dict(
                config=selected["config"],
                config_index=selected["config_index"],
                objective_exact=selected["objective_exact"],
                metric="Equal-quarter normalized proxy cost,cap1/10,penalty5",
                tie_break="Lower fixed candidate index",
            ),
        )
    point_aggregates = defaultdict(list)
    point_count = 0

    def point_rows():
        nonlocal point_count
        for r in cohort:
            scale = scales[r["region"]]
            actual = Q(r["measured_exact"])
            forecast = Q(r["forecast_exact"])
            for config, bias in [
                ("source", Q(0)),
                ("mean_corrected", statistics[1]),
                ("median_corrected", statistics[2]),
            ]:
                estimate = adjusted_forecast(forecast, bias, scale)
                error = estimate - actual
                for group, level in groups(r, stage):
                    point_aggregates[(r["region"], config, group, level)].append(error)
                point_count += 1
                yield dict(
                    datetime=r["datetime"],
                    region=r["region"],
                    split=r["split"],
                    config=config,
                    scale_exact=exact(scale),
                    forecast_exact=exact(estimate),
                    measured_exact=exact(actual),
                    error_exact=exact(error),
                )

    write_compressed(
        out / (stage + "_point_predictions.csv.gz"),
        [
            "datetime",
            "region",
            "split",
            "config",
            "scale_exact",
            "forecast_exact",
            "measured_exact",
            "error_exact",
        ],
        point_rows(),
    )
    point_scores = [
        dict(
            region=region,
            config=config,
            group=group,
            level=level,
            **point_metrics(errors, scales[region])
        )
        for (region, config, group, level), errors in sorted(point_aggregates.items())
    ]
    write_json(out / (stage + "_point_metrics.json"), point_scores)
    path = out / (stage + "_execution.json")
    if not path.exists():
        write_json(
            path,
            dict(
                timestamp_utc=datetime.now(timezone.utc).isoformat(),
                elapsed_seconds=time.perf_counter() - start,
                python=platform.python_version(),
                freeze_commit=freeze,
                stage=stage,
                scope="Measured execution time,not review time,quota or billing",
            ),
        )
    selected = (
        read("results/selection.json")
        if stage == "held"
        else json.loads((out / "selection.json").read_text(encoding="utf-8"))
    )
    print(
        json.dumps(
            dict(
                status="COMPUTED_NOT_INDEPENDENTLY_VERIFIED",
                stage=stage,
                eligible_hours=len(cohort),
                policy_rows=policy_count,
                point_rows=point_count,
                policy_metric_rows=len(scores),
                point_metric_rows=len(point_scores),
                selection=selected,
                primary=[
                    r
                    for r in scores
                    if r["config"] == selected["config"]
                    and r["group"] == "overall"
                    and r["cap_ratio"] == "1/10"
                    and r["penalty"] == 5
                ],
            )
        )
    )


def main():
    p = argparse.ArgumentParser()
    p.add_argument("stage", choices=["development", "held"])
    p.add_argument("--output-dir", type=Path, default=ROOT / "results")
    a = p.parse_args()
    a.output_dir.mkdir(exist_ok=True)
    run(a.stage, a.output_dir)


if __name__ == "__main__":
    main()
