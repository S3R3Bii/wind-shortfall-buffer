"""Original buffer figures: proxy cost,observed coverage and scenario sensitivity."""

import hashlib, json
from fractions import Fraction as Q
from pathlib import Path
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

ROOT = Path(__file__).resolve().parents[1]
plt.rcParams.update(
    {
        "font.family": "DejaVu Sans",
        "font.size": 9,
        "svg.fonttype": "none",
        "svg.hashsalt": "wind-buffer26-v1",
        "axes.spines.top": False,
        "axes.spines.right": False,
    }
)


def main():
    data = json.loads((ROOT / "results/held_policy_metrics.json").read_text())
    scales = {
        k: Q(v)
        for k, v in json.loads((ROOT / "results/fitted.json").read_text())[
            "scales"
        ].items()
    }
    geometry = []
    hashes = {}
    configs = ["zero", "mean", "median", "q75", "q80", "q90"]
    labels = ["Zero", "Mean", "Median", "Q75*", "Q80", "Q90"]
    regions = ["Flanders", "Wallonia"]
    colors = ["#2c7287", "#d28a33"]

    def score(region, c, cap="1/10", penalty=5):
        return next(
            r
            for r in data
            if r["region"] == region
            and r["config"] == c
            and r["cap_ratio"] == cap
            and r["penalty"] == penalty
            and r["group"] == "overall"
        )

    def save(fig, name, artists):
        fig.text(
            0.5,
            0.12,
            "* Selected on development. S: fixed 2018–2019 monitored-capacity median.",
            ha="center",
            fontsize=7.5,
        )
        fig.canvas.draw()
        height = fig.get_figheight() * fig.dpi
        for gid, value, patch in artists:
            points = patch.get_transform().transform(patch.get_path().vertices[:4])
            geometry.append(
                dict(
                    figure=name,
                    gid=gid,
                    value=float(value),
                    points=[
                        [float(x * 72 / fig.dpi), float((height - y) * 72 / fig.dpi)]
                        for x, y in points
                    ],
                )
            )
        for ext in ["pdf", "svg", "png"]:
            path = ROOT / "figures" / (name + "." + ext)
            fig.savefig(
                path,
                dpi=160,
                metadata=(
                    {"Creator": "Wind shortfall buffer,Study26"}
                    if ext == "pdf"
                    else None
                ),
            )
            if ext == "svg":
                path.write_bytes(
                    (
                        "\n".join(
                            line.rstrip() for line in path.read_text().splitlines()
                        )
                        + "\n"
                    ).encode()
                )
            hashes[path.name] = hashlib.sha256(path.read_bytes()).hexdigest()
        plt.close(fig)

    fig, axes = plt.subplots(1, 2, figsize=(7.5, 3.8))
    fig.subplots_adjust(left=0.12, right=0.95, top=0.78, bottom=0.30, wspace=0.3)
    artists = []
    for j, region in enumerate(regions):
        ax = axes[j]
        bottom = [0] * 6
        for k, component in enumerate(["booked", "uncovered"]):
            values = [
                float(
                    Q(
                        score(region, c)["reserve_sum_exact"]
                        if k == 0
                        else score(region, c)["uncovered_sum_exact"]
                    )
                    * (1 if k == 0 else 5)
                    / score(region, c)["n"]
                    / scales[region]
                )
                for c in configs
            ]
            bars = ax.barh(
                range(6),
                values,
                left=bottom,
                height=0.65,
                color=colors[k],
                hatch="" if k == 0 else "//",
                label="Booked buffer / S" if k == 0 else "5 × uncovered / S",
            )
            for i, (v, p) in enumerate(zip(values, bars)):
                gid = f"cost-{j}-{k}-{i}"
                p.set_gid(gid)
                artists.append((gid, v, p))
            bottom = [a + b for a, b in zip(bottom, values)]
        ax.set_yticks(range(6), labels)
        ax.invert_yaxis()
        ax.set_xlim(0, 0.2)
        ax.set_xlabel("Normalized hypothetical proxy cost")
        ax.set_title(region + ": 26,260 held hours", loc="left", fontsize=10)
    fig.suptitle(
        "Pay for a buffer or retain uncovered wind shortfall",
        x=0.12,
        ha="left",
        fontsize=12,
    )
    fig.legend(
        *axes[0].get_legend_handles_labels(),
        ncol=2,
        frameon=False,
        fontsize=8,
        loc="lower center",
        bbox_to_anchor=(0.53, 0.01),
    )
    save(fig, "cost_components", artists)
    fig, ax = plt.subplots(figsize=(7.2, 3.6))
    fig.subplots_adjust(left=0.1, right=0.95, top=0.8, bottom=0.32)
    artists = []
    for j, region in enumerate(regions):
        values = [100 * float(score(region, c)["coverage"]) for c in configs]
        bars = ax.bar(
            [i + (j - 0.5) * 0.34 for i in range(6)],
            values,
            width=0.32,
            color=colors[j],
            hatch="" if j == 0 else "//",
            label=region,
        )
        for i, (v, p) in enumerate(zip(values, bars)):
            gid = f"coverage-{j}-{i}"
            p.set_gid(gid)
            artists.append((gid, v, p))
    ax.set_xticks(range(6), labels)
    ax.set_ylim(0, 100)
    ax.set_ylabel("Held hours with no uncovered shortfall (%)")
    ax.set_title(
        "Observed coverage differs across regions", loc="left", fontsize=12, pad=18
    )
    fig.legend(
        *ax.get_legend_handles_labels(),
        ncol=2,
        frameon=False,
        fontsize=8,
        loc="lower center",
        bbox_to_anchor=(0.5, 0.01),
    )
    save(fig, "observed_coverage", artists)
    fig, axes = plt.subplots(1, 3, figsize=(7.5, 3.7), sharey=True)
    fig.subplots_adjust(left=0.12, right=0.95, top=0.77, bottom=0.32, wspace=0.2)
    artists = []
    for k, penalty in enumerate([2, 5, 10]):
        ax = axes[k]
        for j, region in enumerate(regions):
            values = [
                float(
                    Q(score(region, "zero", cap, penalty)["normalized_cost_exact"])
                    - Q(score(region, "q75", cap, penalty)["normalized_cost_exact"])
                )
                for cap in ["1/20", "1/10", "1/5"]
            ]
            bars = ax.bar(
                [i + (j - 0.5) * 0.34 for i in range(3)],
                values,
                width=0.32,
                color=colors[j],
                hatch="" if j == 0 else "//",
                label=region,
            )
            for i, (v, p) in enumerate(zip(values, bars)):
                gid = f"scenario-{k}-{j}-{i}"
                p.set_gid(gid)
                artists.append((gid, v, p))
        ax.axhline(0, color="#444444", lw=0.7)
        ax.set_xticks(range(3), ["5%", "10%", "20%"])
        ax.set_title("Penalty ratio " + str(penalty), loc="left", fontsize=10)
        ax.set_xlabel("Hypothetical cap / S")
    axes[0].set_ylabel("Zero minus Q75 normalized proxy cost")
    fig.suptitle(
        "The assumed penalty changes the buffer tradeoff",
        x=0.12,
        ha="left",
        fontsize=12,
    )
    fig.legend(
        *axes[0].get_legend_handles_labels(),
        ncol=2,
        frameon=False,
        fontsize=8,
        loc="lower center",
        bbox_to_anchor=(0.5, 0.01),
    )
    save(fig, "scenario_sensitivity", artists)
    (ROOT / "figures/artist_geometry.json").write_bytes(
        (json.dumps(geometry, indent=2) + "\n").encode()
    )
    (ROOT / "figures/manifest.json").write_bytes(
        (json.dumps(hashes, indent=2, sort_keys=True) + "\n").encode()
    )
    print("FIGURES_CREATED", len(geometry), "SOURCE_VALUES")


if __name__ == "__main__":
    main()
