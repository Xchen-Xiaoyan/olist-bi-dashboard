"""Download the public Olist e-commerce dataset (9 CSVs, ~125 MB) into data/raw/.

Source: Olist's own repository, https://github.com/olist/work-at-olist-data (CC BY-NC-SA 4.0).
"""
import urllib.request
from pathlib import Path

BASE = "https://raw.githubusercontent.com/olist/work-at-olist-data/master/datasets/"
FILES = ["olist_customers_dataset", "olist_geolocation_dataset", "olist_order_items_dataset",
         "olist_order_payments_dataset", "olist_order_reviews_dataset", "olist_orders_dataset",
         "olist_products_dataset", "olist_sellers_dataset", "product_category_name_translation"]
RAW = Path(__file__).resolve().parents[1] / "data" / "raw"


def main():
    RAW.mkdir(parents=True, exist_ok=True)
    for f in FILES:
        dst = RAW / f"{f}.csv"
        if dst.exists():
            continue
        urllib.request.urlretrieve(BASE + f + ".csv", dst)
        print(f"{dst.stat().st_size / 1e6:6.1f} MB  {dst.name}")


if __name__ == "__main__":
    main()
