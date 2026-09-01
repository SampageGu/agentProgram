import unittest

from calculator import safe_divide


class SafeDivideTests(unittest.TestCase):
    def test_regular_division(self) -> None:
        self.assertEqual(safe_divide(8, 2), 4)

    def test_zero_divisor_returns_none(self) -> None:
        self.assertIsNone(safe_divide(8, 0))


if __name__ == "__main__":
    unittest.main()
