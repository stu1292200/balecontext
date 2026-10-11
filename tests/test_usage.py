import importlib.util
import unittest
from pathlib import Path

spec = importlib.util.spec_from_file_location("compare_usage", Path(__file__).parents[1] / "benchmarks" / "compare_usage.py")
usage = importlib.util.module_from_spec(spec)
spec.loader.exec_module(usage)


class UsageTest(unittest.TestCase):
    def run_record(self, passed=True, tokens=1000, **extra):
        return {"passed": passed, "calls": [{"total_tokens": tokens}], **extra}

    def test_setup_and_retries_are_counted_and_quality_failure_visible(self):
        result = usage.compare({"assisted_setup_model_tokens": 100, "tasks": [{"id": "task", "baseline": {"passed": True, "calls": [{"total_tokens": 1000}]}, "assisted": {"passed": False, "calls": [{"total_tokens": 50}, {"total_tokens": 50}]}}]})
        self.assertEqual(result["assisted_total_model_tokens_including_setup"], 200)
        self.assertEqual(result["aggregate_reduction_percent"], 80)
        self.assertEqual(result["quality_gate"], "FAILED")
        self.assertEqual(result["regressed_task_ids"], ["task"])

    def test_improvement_cannot_hide_a_regression_in_another_task(self):
        result = usage.compare({"assisted_setup_model_tokens": 0, "tasks": [
            {"id": "previously-working", "baseline": self.run_record(), "assisted": self.run_record(False, 100)},
            {"id": "previously-failing", "baseline": self.run_record(False), "assisted": self.run_record(True, 100)},
        ]})
        self.assertEqual(result["baseline_passed"], result["assisted_passed"])
        self.assertEqual(result["quality_gate"], "FAILED")
        self.assertEqual(result["regressed_task_ids"], ["previously-working"])
        self.assertEqual(result["improved_task_ids"], ["previously-failing"])

    def test_no_regression_and_missing_timing_are_reported_explicitly(self):
        result = usage.compare({"assisted_setup_model_tokens": 0, "tasks": [
            {"id": "task", "baseline": self.run_record(), "assisted": self.run_record(tokens=1500)},
        ]})
        self.assertEqual(result["quality_gate"], "no-task-regressions")
        self.assertEqual(result["aggregate_reduction_percent"], -50)
        self.assertEqual(result["timing"], {"status": "not-recorded"})

    def test_time_comparison_includes_local_setup(self):
        result = usage.compare({"assisted_setup_model_tokens": 0, "assisted_setup_elapsed_seconds": 20, "tasks": [
            {"id": "task", "baseline": self.run_record(elapsed_seconds=100), "assisted": self.run_record(elapsed_seconds=40)},
        ]})
        self.assertEqual(result["timing"]["assisted_elapsed_seconds_including_setup"], 60)
        self.assertEqual(result["timing"]["aggregate_reduction_percent"], 40)
        self.assertEqual(result["timing"]["median_task_reduction_percent_excluding_setup"], 60)

    def test_partial_invalid_or_nonfinite_time_is_rejected(self):
        for invalid in (None, True, -1, "10", float("nan"), float("inf")):
            with self.subTest(value=invalid), self.assertRaises(ValueError):
                usage.compare({"assisted_setup_model_tokens": 0, "assisted_setup_elapsed_seconds": 0, "tasks": [
                    {"id": "task", "baseline": self.run_record(elapsed_seconds=10), "assisted": self.run_record(elapsed_seconds=invalid)},
                ]})
        with self.assertRaises(ValueError):
            usage.compare({"assisted_setup_model_tokens": 0, "tasks": [
                {"id": "task", "baseline": self.run_record(elapsed_seconds=10), "assisted": self.run_record()},
            ]})

    def test_malformed_call_is_rejected(self):
        for call in (None, [], "1000", {"total_tokens": True}):
            with self.subTest(call=call), self.assertRaises(ValueError):
                usage.total({"passed": True, "calls": [call]})

    def test_missing_telemetry_is_rejected(self):
        with self.assertRaises(ValueError):
            usage.total({"passed": True, "calls": [{"estimated_tokens": 500}]})
        with self.assertRaises(ValueError):
            usage.compare({"tasks": []})
