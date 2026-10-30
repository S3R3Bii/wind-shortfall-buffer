"""Fixed signed-error statistics and explicit hypothetical shortfall decisions."""

from fractions import Fraction as Q
from decimal import Decimal, localcontext

CONFIGS = ["zero", "mean", "median", "q75", "q80", "q90"]
CAPS = [Q(1, 20), Q(1, 10), Q(1, 5)]
PENALTIES = [2, 5, 10]


def choose(search):
    if not search:
        raise ValueError("Candidate scores required")
    return min(search, key=lambda r: (Q(r["objective_exact"]), r["config_index"]))


def exact(x):
    return f"{x.numerator}/{x.denominator}"


def display(x):
    with localcontext() as c:
        c.prec = 60
        return format(Decimal(x.numerator) / Decimal(x.denominator), ".12f")


def median(values):
    values = sorted(values)
    n = len(values)
    if not n:
        raise ValueError("Nonempty values required")
    return values[n // 2] if n % 2 else (values[n // 2 - 1] + values[n // 2]) / 2


def quantile(values, q):
    if not values or not 0 < q <= 1:
        raise ValueError("Nonempty values and quantile in(0,1] required")
    n = len(values)
    rank = (q.numerator * n + q.denominator - 1) // q.denominator
    return sorted(values)[rank - 1]


def fit(errors):
    if not errors:
        raise ValueError("Nonempty signed training residuals required")
    return [
        Q(0),
        sum(errors, Q(0)) / len(errors),
        median(errors),
        quantile(errors, Q(3, 4)),
        quantile(errors, Q(4, 5)),
        quantile(errors, Q(9, 10)),
    ]


def action(statistic, scale, cap):
    if scale <= 0 or cap < 0:
        raise ValueError("Positive scale and nonnegative cap required")
    return scale * min(cap, max(Q(0), statistic))


def components(error, reserve):
    if reserve < 0:
        raise ValueError("Reserve cannot be negative")
    shortfall = max(error - reserve, Q(0))
    excess = max(reserve - max(error, Q(0)), Q(0))
    return shortfall, excess, error > reserve


def adjusted_forecast(forecast, bias, scale):
    return max(Q(0), forecast - bias * scale)


def point_metrics(errors, scale):
    if not errors or scale <= 0:
        raise ValueError("Matched cases and positive scale required")
    n = len(errors)
    mae = sum(map(abs, errors), Q(0)) / n
    mse = sum((e * e for e in errors), Q(0)) / n
    bias = sum(errors, Q(0)) / n
    with localcontext() as c:
        c.prec = 60
        rmse = format(
            (Decimal(mse.numerator) / Decimal(mse.denominator)).sqrt(), ".12f"
        )
    return dict(
        n=n,
        mae=display(mae),
        mse=display(mse),
        rmse=rmse,
        bias=display(bias),
        normalized_mae=display(mae / scale),
        mae_exact=exact(mae),
        mse_exact=exact(mse),
        bias_exact=exact(bias),
    )
