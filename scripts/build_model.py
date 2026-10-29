"""Clean the raw Olist tables and write a star schema for Power BI to data/model/*.csv.

Definitions (also in docs/metrics.md):
  window        2017-01-01 .. 2018-08-31 (complete months only)
  buyer         customer_unique_id (customer_id is per order)
  valid order   status not in (canceled, unavailable)
  GMV           item price + freight of valid orders
  on time       delivered on or before the estimated delivery date
"""
import sys
from pathlib import Path

import duckdb
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from categories import CATEGORIES, REGIONS  # noqa: E402

RAW, OUT = ROOT / "data" / "raw", ROOT / "data" / "model"
START, END = "2017-01-01", "2018-09-01"


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    c = duckdb.connect()
    for t in ["customers", "order_items", "order_payments", "order_reviews", "orders", "products", "sellers"]:
        c.execute(f"create view raw_{t} as select * from read_csv_auto('{(RAW / f'olist_{t}_dataset.csv').as_posix()}', header=true)")
    c.execute(f"create view raw_cat as select * from read_csv_auto('{(RAW / 'product_category_name_translation.csv').as_posix()}', header=true)")

    cats = pd.DataFrame([(k, v[0], v[1]) for k, v in CATEGORIES.items()], columns=["category_en", "category_cn", "category_group"])
    regions = pd.DataFrame(list(REGIONS.items()), columns=["state", "region"])
    c.register("cats", cats)
    c.register("regions", regions)

    # ---------- orders (grain: order) ----------
    c.execute(f"""
    create table orders as
    with rev as (   -- 547 orders have several reviews: keep the latest answer
        select order_id, review_score from (
            select *, row_number() over (partition by order_id order by review_answer_timestamp desc) rn
            from raw_order_reviews) where rn = 1),
    pay as (
        select order_id, arg_max(payment_type, payment_value) main_payment_type,
               max(payment_installments) installments, sum(payment_value) payment_value
        from raw_order_payments group by 1),
    items as (
        select order_id, count(*) n_items, sum(price) item_value, sum(freight_value) freight_value,
               count(distinct seller_id) n_sellers
        from raw_order_items group by 1)
    select o.order_id, cu.customer_unique_id, o.order_status,
           cast(o.order_purchase_timestamp as date) purchase_date,
           o.order_purchase_timestamp, o.order_approved_at, o.order_delivered_carrier_date,
           o.order_delivered_customer_date, cast(o.order_estimated_delivery_date as date) estimated_date,
           o.order_status not in ('canceled', 'unavailable') is_valid,
           o.order_status = 'delivered' and o.order_delivered_customer_date is not null is_delivered,
           coalesce(i.n_items, 0) n_items, coalesce(i.item_value, 0) item_value,
           coalesce(i.freight_value, 0) freight_value,
           coalesce(i.item_value, 0) + coalesce(i.freight_value, 0) order_value,
           i.n_sellers, p.main_payment_type, p.installments, p.payment_value, r.review_score
    from raw_orders o
    join raw_customers cu using (customer_id)
    left join items i using (order_id)
    left join pay p using (order_id)
    left join rev r using (order_id)
    where o.order_purchase_timestamp >= '{START}' and o.order_purchase_timestamp < '{END}'
    """)
    c.execute("""
    create table orders2 as
    select *,
        case when is_delivered then date_diff('day', order_purchase_timestamp, order_delivered_customer_date) end delivery_days,
        case when is_delivered then cast(order_delivered_customer_date as date) <= estimated_date end is_on_time,
        case when is_delivered then date_diff('day', estimated_date, cast(order_delivered_customer_date as date)) end days_vs_estimate,
        row_number() over (partition by customer_unique_id order by order_purchase_timestamp) buyer_order_seq
    from orders
    """)
    fact_orders = c.sql("""
        select order_id, customer_unique_id, order_status, purchase_date, is_valid, is_delivered, is_on_time,
               delivery_days, days_vs_estimate, n_items, n_sellers, item_value, freight_value, order_value,
               main_payment_type, installments, payment_value, review_score, buyer_order_seq,
               buyer_order_seq = 1 is_first_order,
               case when not is_delivered then '未送达' when is_on_time then '准时' else '延迟' end delivery_status,
               case when not is_delivered then '未送达' when days_vs_estimate <= 0 then '准时或提前'
                    when days_vs_estimate <= 3 then '延迟1-3天' when days_vs_estimate <= 7 then '延迟4-7天'
                    else '延迟7天以上' end lateness_bucket,
               case when not is_delivered then 5 when days_vs_estimate <= 0 then 1 when days_vs_estimate <= 3 then 2
                    when days_vs_estimate <= 7 then 3 else 4 end lateness_order,
               order_approved_at is not null is_approved,
               order_delivered_carrier_date is not null is_shipped
        from orders2 order by order_purchase_timestamp""").df()

    # ---------- order items (grain: item) ----------
    fact_items = c.sql(f"""
        select i.order_id, i.order_item_id, i.product_id, i.seller_id, o.purchase_date, o.is_valid,
               i.price, i.freight_value, i.price + i.freight_value item_gmv
        from raw_order_items i join orders o using (order_id)""").df()

    # ---------- dimensions ----------
    dim_product = c.sql("""
        select p.product_id, coalesce(p.product_category_name, 'sem_categoria') category_pt,
               coalesce(t.product_category_name_english, p.product_category_name, 'uncategorized') category_en,
               p.product_weight_g, p.product_photos_qty
        from raw_products p left join raw_cat t using (product_category_name)""").df()
    dim_product = dim_product.merge(cats, on="category_en", how="left")
    dim_product["category_cn"] = dim_product.category_cn.fillna("未分类")
    dim_product["category_group"] = dim_product.category_group.fillna("其他")

    dim_customer = c.sql("""
        select customer_unique_id, any_value(customer_state) state, any_value(customer_city) city
        from raw_customers group by 1""").df()
    first = fact_orders.groupby("customer_unique_id").purchase_date.min().rename("first_purchase_date")
    dim_customer = dim_customer.merge(first, on="customer_unique_id", how="inner").merge(regions, on="state", how="left")
    dim_customer["cohort_month"] = pd.to_datetime(dim_customer.first_purchase_date).dt.to_period("M").dt.to_timestamp()

    dim_seller = c.sql("select seller_id, seller_state state, seller_city city from raw_sellers").df().merge(
        regions, on="state", how="left")

    dates = pd.date_range(START, pd.Timestamp(END) - pd.Timedelta(days=1), freq="D")
    dim_date = pd.DataFrame({"date": dates})
    dim_date["year"] = dim_date.date.dt.year
    dim_date["month"] = dim_date.date.dt.month
    dim_date["year_month"] = dim_date.date.dt.strftime("%Y-%m")
    dim_date["month_start"] = dim_date.date.dt.to_period("M").dt.to_timestamp()
    dim_date["quarter"] = "Q" + dim_date.date.dt.quarter.astype(str)
    dim_date["weekday_no"] = dim_date.date.dt.day_of_week + 1
    dim_date["weekday"] = dim_date.weekday_no.map(dict(zip(range(1, 8), "周一 周二 周三 周四 周五 周六 周日".split())))

    # ---------- cohort retention (precomputed: DAX cohort matrices are slow and opaque) ----------
    fo = fact_orders[fact_orders.is_valid].merge(dim_customer[["customer_unique_id", "cohort_month"]], on="customer_unique_id")
    fo["order_month"] = pd.to_datetime(fo.purchase_date).dt.to_period("M").dt.to_timestamp()
    fo["months_since"] = ((fo.order_month.dt.year - fo.cohort_month.dt.year) * 12
                          + fo.order_month.dt.month - fo.cohort_month.dt.month)
    act = fo.groupby(["cohort_month", "months_since"]).customer_unique_id.nunique().rename("active_buyers").reset_index()
    size = act[act.months_since == 0].set_index("cohort_month").active_buyers.rename("cohort_size")
    cohort = act.merge(size, on="cohort_month")
    cohort["retention_rate"] = cohort.active_buyers / cohort.cohort_size
    # Month 0 is 100% by definition and would flatten a heatmap; the dashboard shows months 1-12.
    cohort = cohort[cohort.months_since.between(1, 12)].copy()
    cohort["cohort_label"] = cohort.cohort_month.dt.strftime("%Y-%m")

    tables = {"fact_orders": fact_orders, "fact_order_items": fact_items, "dim_product": dim_product,
              "dim_customer": dim_customer, "dim_seller": dim_seller, "dim_date": dim_date, "cohort_retention": cohort}
    for name, df in tables.items():
        df.to_csv(OUT / f"{name}.csv", index=False, encoding="utf-8")
        print(f"{name:18s} {len(df):>8,} rows  {df.shape[1]} cols")

    # ---------- sanity checks ----------
    v = fact_orders[fact_orders.is_valid]
    assert fact_orders.order_id.is_unique and dim_customer.customer_unique_id.is_unique
    assert abs(fact_items[fact_items.is_valid].item_gmv.sum() - v.order_value.sum()) < 1, "item GMV != order GMV"
    print(f"\nvalid orders {len(v):,} | GMV R$ {v.order_value.sum():,.0f} | buyers {v.customer_unique_id.nunique():,} | "
          f"uncategorized products {(dim_product.category_cn == '未分类').sum()}")


if __name__ == "__main__":
    main()
