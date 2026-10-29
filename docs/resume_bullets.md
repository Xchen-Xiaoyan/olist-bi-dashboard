# 简历条目

## 中文

**Olist 电商经营分析看板(Power BI)**|个人项目
技术栈:SQL(DuckDB)、Python、Power BI(DAX、Power Query)

- **数据清洗与建模**:基于 10 万笔订单、9 张业务表,统一买家去重、有效订单、评价去重等口径;构建订单 / 商品双粒度事实表与日期、买家、商品、卖家维度的星型模型,并以对账断言校验 GMV 一致性。
- **指标体系与看板**:设计 GMV、客单价、复购率、准时送达率、漏斗转化等 22 个 DAX 指标(含环比、同比时间智能),搭建经营总览、GMV 拆解、履约体验、用户留存 4 页交互看板。
- **业务洞察**:按"GMV = 买家数 × 人均订单数 × 客单价"拆解,发现 2018 年 GMV 同比 +140% 几乎全部来自拉新,复购率仅 3.0%;量化配送延迟对评分的影响(准时 4.29 分 vs 延迟 2.27 分),定位大促期间与东北部、北部的履约短板,提出复购激励与区域运力优化建议。

## English

**Olist E-commerce Performance Dashboard (Power BI)** | Personal project
SQL (DuckDB), Python, Power BI (DAX, Power Query)

- Cleaned and modeled 100k orders across 9 tables into a star schema (order- and item-level facts; date, buyer, product and seller dimensions). Standardized definitions for buyers, valid orders and reviews, and reconciled GMV across grains.
- Built a 22-measure DAX metric layer (GMV, AOV, repeat rate, on-time delivery, funnel conversion, MoM/YoY) and a 4-page interactive dashboard covering overview, growth decomposition, fulfillment and cohort retention.
- Decomposed GMV into buyers × orders per buyer × AOV and found that the +140% YoY growth in 2018 came almost entirely from new buyers, with only 3.0% repeat purchase. Quantified the review impact of late delivery (4.29 vs 2.27 stars), located fulfillment gaps during peak season and in the North/Northeast, and recommended repeat-purchase incentives and regional logistics fixes.

## 面试可以展开讲的点

1. **为什么用 `customer_unique_id`**:`customer_id` 每单一个,用它算复购率会是 0;这是数据口径陷阱。
2. **为什么同期群留存要预计算**:DAX 写动态同期群又慢又难维护,而留存矩阵不需要随筛选器变化;这是在灵活性和性能之间做取舍。
3. **怎么保证看板数字是对的**:SQL 分析与 Power BI 模型各算一遍,用 DAX 查询逐项对账,行数、GMV、准时率、漏斗全部一致。
4. **可视化的诚实性**:"人均订单数"这类波动很小的指标,纵轴从 0 开始,避免自动缩放把 1.00–1.04 的波动放大成剧烈起伏。
