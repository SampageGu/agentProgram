import unittest

from tags import normalize_tags


class NormalizeTagsTests(unittest.TestCase):
    def test_trims_deduplicates_and_discards_empty_tags(self) -> None:
        self.assertEqual(
            normalize_tags([" Python ", "", "Agent", "python", "  "]),
            ["python", "agent"],
        )

    def test_preserves_first_seen_order(self) -> None:
        self.assertEqual(normalize_tags(["B", "a", "b"]), ["b", "a"])


if __name__ == "__main__":
    unittest.main()
