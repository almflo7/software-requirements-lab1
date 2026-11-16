"""美食广场按店铺展示，且新建空店可搜索。"""

import tempfile
import unittest
from pathlib import Path

from app.catalog import add_menu_item, add_store, search_stores
from app.cli import seed_demo


class StoreBrowsingTest(unittest.TestCase):
    def test_one_card_per_store_and_empty_store_visible(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "browse.sqlite3"
            seed_demo(path)
            add_menu_item(path, "merchant-demo", "store-demo", "小菜", "配菜", 800, 10)
            new_store_id = add_store(path, "merchant-demo", "新开的店", ("campus",), 300)

            cards = search_stores(path)
            self.assertEqual(len(cards), 2)
            self.assertEqual(len({card["store_id"] for card in cards}), 2)
            result = search_stores(path, "新开的店")
            self.assertEqual([card["store_id"] for card in result], [new_store_id])
            self.assertIsNone(result[0]["sample_item"])


if __name__ == "__main__":
    unittest.main()
