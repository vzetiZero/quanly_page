import unittest

from views.widgets import chunked_batches


class TestConcurrencyBatching(unittest.TestCase):
    def test_batches_items_by_worker_count(self) -> None:
        items = list(range(10))
        self.assertEqual(chunked_batches(items, 3), [[0, 1, 2], [3, 4, 5], [6, 7, 8], [9]])


if __name__ == "__main__":
    unittest.main()
