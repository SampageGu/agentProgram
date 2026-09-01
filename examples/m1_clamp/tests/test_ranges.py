import unittest

from ranges import clamp


class ClampTests(unittest.TestCase):
    def test_values_below_and_above_bounds(self) -> None:
        self.assertEqual(clamp(-2, 0, 10), 0)
        self.assertEqual(clamp(20, 0, 10), 10)

    def test_value_inside_range_is_unchanged(self) -> None:
        self.assertEqual(clamp(4, 0, 10), 4)


if __name__ == "__main__":
    unittest.main()
