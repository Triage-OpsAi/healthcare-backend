import unittest
from datetime import datetime, timedelta, timezone

from app.services.ward_voice_service import (
    calculate_balance,
    calculate_iv_volume,
    normalize_confirmed_fluid_ml,
    shift_window,
)


class WardVoiceArithmeticTests(unittest.TestCase):
    def test_balance_is_intake_minus_output(self):
        self.assertEqual(calculate_balance(335, 260), 75)
        self.assertEqual(calculate_balance(100, 180), -80)

    def test_iv_volume_uses_rate_times_elapsed_hours(self):
        start = datetime(2026, 7, 29, 14, 0, tzinfo=timezone.utc)
        self.assertEqual(calculate_iv_volume(25, start, start + timedelta(hours=5)), 125)
        self.assertEqual(calculate_iv_volume(25, start, start + timedelta(minutes=30)), 12)

    def test_shift_window_is_twelve_hours(self):
        now = datetime(2026, 7, 29, 14, 30, tzinfo=timezone.utc)
        start, end = shift_window(now)
        self.assertEqual(start.hour, 8)
        self.assertEqual(end - start, timedelta(hours=12))

    def test_litres_are_normalized_to_ml_deterministically(self):
        self.assertEqual(
            normalize_confirmed_fluid_ml(None, "1 litre of water", None),
            1000,
        )
        self.assertEqual(
            normalize_confirmed_fluid_ml(0.5, None, "liter"),
            500,
        )

    def test_ml_values_are_not_changed(self):
        self.assertEqual(
            normalize_confirmed_fluid_ml(500, "output", "ml"),
            500,
        )


if __name__ == "__main__":
    unittest.main()
