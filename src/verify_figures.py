"""Check source values and actual SVG rectangles,including negative scenario changes."""

import hashlib, json, re, xml.etree.ElementTree as ET
from fractions import Fraction as Q
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main():
    read = lambda n: json.loads((ROOT / n).read_text())
    data = read("results/held_policy_metrics.json")
    scales = {k: Q(v) for k, v in read("results/fitted.json")["scales"].items()}
    expected = {}

    def get(region, c, cap="1/10", penalty=5):
        return next(
            r
            for r in data
            if r["region"] == region
            and r["config"] == c
            and r["cap_ratio"] == cap
            and r["penalty"] == penalty
            and r["group"] == "overall"
        )

    for j, region in enumerate(["Flanders", "Wallonia"]):
        for i, c in enumerate(["zero", "mean", "median", "q75", "q80", "q90"]):
            r = get(region, c)
            expected[f"cost-{j}-0-{i}"] = float(
                Q(r["reserve_sum_exact"]) / r["n"] / scales[region]
            )
            expected[f"cost-{j}-1-{i}"] = float(
                5 * Q(r["uncovered_sum_exact"]) / r["n"] / scales[region]
            )
            expected[f"coverage-{j}-{i}"] = 100 * float(r["coverage"])
        for k, penalty in enumerate([2, 5, 10]):
            for i, cap in enumerate(["1/20", "1/10", "1/5"]):
                expected[f"scenario-{k}-{j}-{i}"] = float(
                    Q(get(region, "zero", cap, penalty)["normalized_cost_exact"])
                    - Q(get(region, "q75", cap, penalty)["normalized_cost_exact"])
                )
    records = read("figures/artist_geometry.json")
    assert len(records) == len(expected) == 54
    checks = 0
    trees = {}
    for r in records:
        assert r["value"] == expected[r["gid"]]
        checks += 1
        name = r["figure"]
        if name not in trees:
            trees[name] = ET.parse(ROOT / "figures" / (name + ".svg"))
        group = trees[name].find('.//*[@id="' + r["gid"] + '"]')
        assert group is not None
        path = group.find("{http://www.w3.org/2000/svg}path")
        values = [
            float(v)
            for v in re.findall(
                r"[-+]?(?:\d*\.\d+|\d+)(?:[eE][-+]?\d+)?", path.attrib["d"]
            )
        ]
        assert len(values) >= 8
        for a, b in zip(values[:8], [v for point in r["points"] for v in point]):
            assert abs(a - b) < 1e-5
            checks += 1
    for name, digest in read("figures/manifest.json").items():
        assert (
            hashlib.sha256((ROOT / "figures" / name).read_bytes()).hexdigest() == digest
        )
        checks += 1
    result = dict(
        status="PASS",
        checks=checks,
        source_values=54,
        svg_coordinates=432,
        hashes=9,
        scope="Verified-result values,actual SVG bar coordinates and artifact hashes;scenario costs are not measured savings",
    )
    (ROOT / "results/figure_verification.json").write_bytes(
        (json.dumps(result, indent=2) + "\n").encode()
    )
    print(json.dumps(result))


if __name__ == "__main__":
    main()
