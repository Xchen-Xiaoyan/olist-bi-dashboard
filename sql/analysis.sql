-- Business analysis queries over the star schema (data/model/*.csv), run by scripts/analyze.py.
-- Each query is preceded by a "-- name: <id>" line.

-- name: monthly
select d.month_start, count(*) orders, count(distinct o.customer_unique_id) buyers,
       sum(o.order_value) gmv, sum(o.order_value) / count(*) aov
from fact_orders o join dim_date d on o.purchase_date = d.date
where o.is_valid
group by 1 order by 1;

-- name: gmv_decomposition
-- GMV = buyers x orders per buyer x average order value; compare Jan-Aug 2018 with Jan-Aug 2017.
with p as (
    select case when year(purchase_date) = 2017 then '2017' else '2018' end period,
           count(distinct customer_unique_id) buyers, count(*) orders, sum(order_value) gmv
    from fact_orders where is_valid and month(purchase_date) <= 8 group by 1)
select period, buyers, orders * 1.0 / buyers orders_per_buyer, gmv / orders aov, gmv from p order by 1;

-- name: category_pareto
with c as (
    select p.category_cn, p.category_group, sum(i.item_gmv) gmv
    from fact_order_items i join dim_product p using (product_id)
    where i.is_valid group by 1, 2)
select category_cn, category_group, gmv, gmv / sum(gmv) over () share,
       sum(gmv) over (order by gmv desc) / sum(gmv) over () cum_share,
       row_number() over (order by gmv desc) rnk
from c order by gmv desc;

-- name: region
select c.region, count(*) orders, sum(o.order_value) gmv, avg(o.delivery_days) avg_delivery_days,
       avg(case when o.is_on_time then 1.0 else 0 end) filter (where o.is_delivered) on_time_rate,
       avg(o.review_score) avg_review, avg(o.freight_value / nullif(o.order_value, 0)) freight_share
from fact_orders o join dim_customer c using (customer_unique_id)
where o.is_valid group by 1 order by gmv desc;

-- name: delivery_vs_review
select case when not is_on_time then 'late' else 'on_time' end delivery, count(*) orders,
       avg(review_score) avg_review,
       avg(case when review_score <= 2 then 1.0 else 0 end) share_1_2_star
from fact_orders where is_delivered and review_score is not null group by 1;

-- name: lateness_buckets
select case when days_vs_estimate <= 0 then '0 准时或提前' when days_vs_estimate <= 3 then '1 延迟1-3天'
            when days_vs_estimate <= 7 then '2 延迟4-7天' else '3 延迟7天以上' end bucket,
       count(*) orders, avg(review_score) avg_review
from fact_orders where is_delivered and review_score is not null group by 1 order by 1;

-- name: funnel
select count(*) placed,
       sum(case when is_approved then 1 else 0 end) approved,
       sum(case when is_shipped then 1 else 0 end) shipped,
       sum(case when is_delivered then 1 else 0 end) delivered,
       sum(case when is_delivered and review_score >= 4 then 1 else 0 end) delivered_good_review
from fact_orders;

-- name: repeat
select count(*) buyers, avg(case when n > 1 then 1.0 else 0 end) repeat_rate, avg(n) orders_per_buyer
from (select customer_unique_id, count(*) n from fact_orders where is_valid group by 1);

-- name: retention_curve
select months_since, sum(active_buyers) * 1.0 / sum(cohort_size) retention
from cohort_retention where months_since between 1 and 6 group by 1 order by 1;

-- name: monthly_on_time
select d.month_start, avg(case when o.is_on_time then 1.0 else 0 end) on_time_rate,
       avg(o.review_score) avg_review, avg(o.delivery_days) avg_days
from fact_orders o join dim_date d on o.purchase_date = d.date
where o.is_delivered group by 1 order by 1;

-- name: seller_concentration
with s as (
    select seller_id, sum(item_gmv) gmv from fact_order_items where is_valid group by 1),
r as (select *, row_number() over (order by gmv desc) rk, count(*) over () n from s)
select max(n) sellers,
       sum(case when rk <= ceil(n * 0.1) then gmv end) / sum(gmv) top10pct_share,
       sum(case when rk <= ceil(n * 0.2) then gmv end) / sum(gmv) top20pct_share,
       median(gmv) median_seller_gmv
from r;

-- name: late_orders_by_seller_state
select s.state seller_state, count(distinct o.order_id) delivered_orders,
       count(distinct case when not o.is_on_time then o.order_id end) late_orders,
       count(distinct case when not o.is_on_time then o.order_id end) * 1.0 / count(distinct o.order_id) late_rate
from fact_order_items i join fact_orders o using (order_id) join dim_seller s using (seller_id)
where o.is_delivered group by 1 having count(distinct o.order_id) > 500 order by late_orders desc limit 8;

-- name: late_order_concentration
-- share of late orders caused by the 10% of sellers with the most late orders
with s as (
    select i.seller_id, count(distinct case when not o.is_on_time then o.order_id end) late
    from fact_order_items i join fact_orders o using (order_id) where o.is_delivered group by 1),
r as (select *, row_number() over (order by late desc) rk, count(*) over () n from s)
select sum(case when rk <= ceil(n * 0.1) then late end) * 1.0 / sum(late) top10pct_seller_late_share from r;

-- name: rfm
select c.rfm_order, c.rfm_segment, count(*) buyers, count(*) * 1.0 / sum(count(*)) over () buyer_share,
       sum(c.monetary) gmv, sum(c.monetary) / sum(sum(c.monetary)) over () gmv_share,
       avg(c.monetary) avg_monetary, avg(c.recency_days) avg_recency
from dim_customer c where c.rfm_segment is not null group by 1, 2 order by 1;
