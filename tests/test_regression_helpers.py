import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import real_llm_regression


class RegressionHelperTests(unittest.TestCase):
    def test_bubble_reflection_detector_accepts_two_surfaces_with_intervening_words(self):
        self.assertTrue(real_llm_regression.contains_bubble_reflection_mechanism("光在泡泡皮的两面都反射。"))

    def test_bubble_reflection_detector_still_requires_reflection(self):
        self.assertFalse(real_llm_regression.contains_bubble_reflection_mechanism("泡泡皮有两面。"))


if __name__ == "__main__":
    unittest.main()
