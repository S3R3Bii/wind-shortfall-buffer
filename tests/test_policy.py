"""Small invented policy cases, excluded from empirical outcomes."""

from fractions import Fraction as Q
from pathlib import Path
import sys, unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import model as m


class PolicyBoundaries(unittest.TestCase):
    def test_signed_errors_not_truncated_before_fit(self):
        self.assertEqual(m.fit([Q(-4), Q(-2), Q(1)])[1], Q(-5, 3))
        self.assertEqual(m.action(Q(-5, 3), Q(100), Q(1, 10)), 0)

    def test_exact_even_median(self):
        self.assertEqual(m.median([Q(-2), Q(1), Q(4), Q(9)]), Q(5, 2))

    def test_nearest_rank_endpoints(self):
        data = list(map(Q, [1, 2, 3, 4, 5]))
        self.assertEqual(m.quantile(data, Q(4, 5)), 4)
        self.assertEqual(m.quantile(data, Q(1)), 5)
        self.assertEqual(m.quantile(data, Q(1, 100)), 1)

    def test_quantile_invalid(self):
        for data, q in [([], Q(1, 2)), ([Q(1)], Q(0)), ([Q(1)], Q(2))]:
            with self.assertRaises(ValueError):
                m.quantile(data, q)

    def test_capacity_cap(self):
        self.assertEqual(m.action(Q(1, 2), Q(100), Q(1, 10)), 10)

    def test_scale_transfer_uses_same_normalized_policy(self):
        self.assertEqual(
            m.action(Q(1, 20), Q(200), Q(1, 10)),
            2 * m.action(Q(1, 20), Q(100), Q(1, 10)),
        )

    def test_invalid_physical_scale(self):
        for scale, cap in [(Q(0), Q(1, 10)), (Q(-1), Q(1, 10)), (Q(100), Q(-1))]:
            with self.assertRaises(ValueError):
                m.action(Q(1, 20), scale, cap)

    def test_positive_shortfall(self):
        self.assertEqual(m.components(Q(20), Q(5)), (Q(15), Q(0), True))

    def test_surplus_needs_no_upward_buffer(self):
        self.assertEqual(m.components(Q(-20), Q(5)), (Q(0), Q(5), False))

    def test_exact_coverage_boundary(self):
        self.assertEqual(m.components(Q(5), Q(5)), (Q(0), Q(0), False))

    def test_negative_reserve_rejected(self):
        with self.assertRaises(ValueError):
            m.components(Q(1), Q(-1))

    def test_no_negative_corrected_forecast(self):
        self.assertEqual(m.adjusted_forecast(Q(3), Q(1, 10), Q(100)), 0)

    def test_negative_bias_increases_forecast(self):
        self.assertEqual(m.adjusted_forecast(Q(3), Q(-1, 10), Q(100)), 13)

    def test_point_metric_units_and_bias(self):
        score = m.point_metrics([Q(-1), Q(1)], Q(100))
        self.assertEqual(score["mae_exact"], "1/1")
        self.assertEqual(score["mse_exact"], "1/1")
        self.assertEqual(score["bias_exact"], "0/1")
        self.assertEqual(score["normalized_mae"], "0.010000000000")

    def test_quantile_minimizes_declared_finite_loss(self):
        errors = list(map(Q, [-3, 0, 2, 5, 10]))
        r = m.quantile(errors, Q(4, 5))
        loss = lambda x: x + 5 * sum((max(e - x, Q(0)) for e in errors), Q(0)) / 5
        self.assertEqual(r, 5)
        self.assertEqual(loss(r), 10)
        self.assertLess(loss(r), loss(Q(0)))
        self.assertLess(loss(r), loss(Q(3)))
        self.assertEqual(loss(r), loss(Q(10)))

    def test_selection_tie_uses_index_not_list_order(self):
        chosen = m.choose(
            [
                dict(config_index=4, objective_exact="2/6"),
                dict(config_index=0, objective_exact="1/3"),
            ]
        )
        self.assertEqual(chosen["config_index"], 0)

    def test_selection_uses_exact_fraction(self):
        chosen = m.choose(
            [
                dict(
                    config_index=0,
                    objective_exact="1000000000000000001/1000000000000000000",
                ),
                dict(config_index=4, objective_exact="1/1"),
            ]
        )
        self.assertEqual(chosen["config_index"], 4)


if __name__ == "__main__":
    unittest.main()
