from pathlib import Path

root = Path(__file__).resolve().parents[1] / "sandbox-repos" / "demo"
if root.exists():
    raise SystemExit("Demo directory exists; move it aside before creating a fresh fixture.")
root.mkdir(parents=True)
(root / "calculator.py").write_text("def add(a, b):\n    return a - b\n", encoding="utf-8")
(root / "test_calculator.py").write_text(
    """import unittest
from calculator import add


class CalculatorTests(unittest.TestCase):
    def test_positive(self):
        self.assertEqual(add(2, 3), 5)

    def test_negative(self):
        self.assertEqual(add(-2, -3), -5)

    def test_identity(self):
        self.assertEqual(add(7, 0), 7)
""",
    encoding="utf-8",
)
print(f"Disposable demo repository created: {root}")
