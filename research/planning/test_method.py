"""规划方法的合成测试。不访问外场数据，也不连接飞行服务。"""
import math
import unittest

from method import (
    ANGLE_SIGMA,
    crlb,
    density_greedy,
    detection,
    enumerate_sites,
    eigenvalues,
    fuse,
    optimal_budgeted,
    plan_sites,
    resample,
    schedule,
    visible,
)


def device(**values):
    row = {"type": "EO", "x": 0.0, "y": 0.0, "z": 20.0, "azimuth": 0.0, "fov": 90.0, "pitch": 0.0, "vertical_fov": 80.0, "range": 200.0, "cost": 1.0, "pd": 0.8}
    row.update(values)
    return row


class PlanningMethodTest(unittest.TestCase):
    def test_eigenvalues_match_known_symmetric_matrix(self):
        values = eigenvalues([[2.0, 1.0, 0.0], [1.0, 2.0, 0.0], [0.0, 0.0, 3.0]])
        self.assertEqual([round(item, 6) for item in values], [1.0, 3.0, 3.0])

    def test_independent_fusion_and_occlusion(self):
        self.assertAlmostEqual(fuse([0.5, 0.5]), 0.75)
        radar = device(id="R", type="RADAR", azimuth=90.0, fov=40.0, range=200.0)
        point = (100.0, 0.0, 20.0)
        building = {"x": 40.0, "y": -10.0, "w": 20.0, "d": 20.0, "h": 40.0}
        self.assertTrue(visible(radar, point, []))
        self.assertFalse(visible(radar, point, [building]))
        self.assertEqual(detection(device(id="B", type="ADSB"), point, []), 0.0)

    def test_density_greedy_is_not_the_budgeted_optimum(self):
        cheap = {"id": "cheap", "cost": 2.0, "elements": [("a", 3.0)]}
        broad = {"id": "broad", "cost": 5.0, "elements": [("b", 2.0), ("c", 2.0), ("d", 2.0)]}
        greedy = density_greedy([cheap, broad], budget=5.0)
        optimal = optimal_budgeted([cheap, broad], budget=5.0)
        self.assertEqual([item["id"] for item in greedy["chosen"]], ["cheap"])
        self.assertEqual(greedy["weight"], 3.0)
        self.assertEqual(optimal["weight"], 6.0)
        self.assertLess(greedy["weight"], optimal["weight"])

    def test_site_greedy_keeps_installed_and_ignores_precision_only_gain(self):
        installed = [device(id="R-0", type="RADAR", x=0.0, y=0.0, azimuth=90.0, fov=50.0, range=150.0, cost=0.0)]
        route = [(60.0, 0.0, 30.0), (60.0, 40.0, 30.0)]
        precision_only = device(id="E-side", x=60.0, y=-80.0, azimuth=0.0, fov=50.0, range=120.0, cost=3.0)
        gap = device(id="E-gap", x=60.0, y=90.0, azimuth=180.0, fov=40.0, range=80.0, cost=4.0)
        plan = plan_sites(route, installed, [precision_only, gap], [], budget=10.0)
        self.assertEqual(plan["installed_ids"], ["R-0"])
        self.assertEqual([item["id"] for item in plan["selected"]], ["E-gap"])
        self.assertEqual(plan["added_cost"], 4.0)
        alone = crlb(installed, (60.0, 0.0, 30.0), [])
        both = crlb(installed + [precision_only], (60.0, 0.0, 30.0), [])
        self.assertTrue(math.isfinite(alone) and both < alone)

    def test_angle_only_needs_a_second_look(self):
        one = device(id="E-1", azimuth=90.0, fov=40.0)
        other = device(id="E-2", x=80.0, y=80.0, azimuth=180.0, fov=40.0)
        point = (80.0, 0.0, 20.0)
        self.assertTrue(math.isinf(crlb([one], point, [])))
        self.assertTrue(math.isfinite(crlb([one, other], point, [])))
        self.assertEqual(ANGLE_SIGMA["EO"], 0.008)

    def test_enumeration_picks_the_cheaper_feasible_subset(self):
        installed = []
        route = [(50.0, 0.0, 30.0), (50.0, 10.0, 30.0)]
        cheap = device(id="R-cheap", type="RADAR", azimuth=90.0, fov=40.0, range=120.0, cost=5.0, pd=0.9)
        costly = device(id="R-costly", type="RADAR", azimuth=90.0, fov=40.0, range=120.0, cost=9.0, pd=0.9)
        blind = device(id="E-blind", azimuth=270.0, fov=20.0, range=30.0, cost=1.0)
        chosen = enumerate_sites(route, installed, [costly, blind, cheap], [], budget=10.0, coverage_target=1.0, precision_target=1.0, precision_limit=30.0)
        self.assertEqual([item["id"] for item in chosen["selected"]], ["R-cheap"])
        self.assertEqual(chosen["added_cost"], 5.0)
        with self.assertRaises(ValueError):
            enumerate_sites(route, installed, [device(id=f"C{i}") for i in range(11)], [], budget=10.0)

    def test_schedule_points_at_the_segment_mean(self):
        radar = device(id="R", type="RADAR", azimuth=90.0, fov=30.0, range=80.0)
        optical = device(id="E", type="EO", x=0.0, y=120.0, azimuth=180.0, fov=80.0, range=200.0)
        route = [(40.0, 0.0, 30.0), (40.0, 80.0, 30.0)]
        rows = schedule(route, [radar, optical], [])
        self.assertEqual(rows[0]["watchers"], ["R", "E"])
        self.assertEqual(rows[0]["period_s"], 4.0)
        self.assertTrue(rows[0]["cooperate"])
        self.assertEqual(rows[1]["watchers"], ["E"])
        self.assertEqual(rows[1]["period_s"], 2.0)
        self.assertFalse(rows[1]["cooperate"])
        self.assertAlmostEqual(rows[1]["pointing"]["E"]["azimuth"], 153.435, places=2)
        self.assertEqual(rows[2]["watchers"], [])

    def test_resample_uses_the_planning_step(self):
        points = resample([(0.0, 0.0, 30.0), (100.0, 0.0, 30.0)])
        self.assertEqual(len(points), 3)
        self.assertEqual(points[1], (50.0, 0.0, 30.0))


if __name__ == "__main__":
    unittest.main()
