import importlib.util
import unittest
from pathlib import Path

spec = importlib.util.spec_from_file_location("compare_usage", Path(__file__).parents[1] / "benchmarks" / "compare_usage.py")
usage = importlib.util.module_from_spec(spec)
spec.loader.exec_module(usage)


class UsageTest(unittest.TestCase):
    def test_setup_and_retries_are_counted_and_quality_failure_visible(self):
        result = usage.compare({"assisted_setup_model_tokens": 100, "tasks": [{"id": "task", "baseline": {"passed": True, "calls": [{"total_tokens": 1000}]}, "assisted": {"passed": False, "calls": [{"total_tokens": 50}, {"total_tokens": 50}]}}]})
        self.assertEqual(result["assisted_total_model_tokens_including_setup"], 200)
        self.assertEqual(result["aggregate_reduction_percent"], 80)
        self.assertEqual(result["quality_gate"], "FAILED")

    def test_missing_telemetry_is_rejected(self):
        with self.assertRaises(ValueError):
            usage.total({"passed": True, "calls": [{"estimated_tokens": 500}]})
        with self.assertRaises(ValueError):
            usage.compare({"tasks": []})
