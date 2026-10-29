# 简历条目

## 中文

**Olist 电商经营分析看板(Power BI)**|个人项目
技术栈:SQL(DuckDB)、Python、Power BI(DAX、Power Query)

- **数据清洗与建模**:基于 10 万笔订单、9 张业务表,统一买家去重、有效订单、评价去重等口径;构建订单 / 商品双粒度事实表与日期、买家、商品、卖家维度的星型模型,并以对账断言校验 GMV 一致性。
- **指标体系与交互看板**:设计 GMV、复购率、准时送达率、漏斗转化、头部卖家集中度等 33 个 DAX 指标(含时间智能与动态标题),搭建 6 页交互看板,支持品类钻取、悬停提示页、跨页同步筛选和指标切换。
- **业务洞察**:按"GMV = 买家数 × 人均订单数 × 客单价"拆解,发现 2018 年 GMV 同比 +140% 几乎全部来自拉新,复购率仅 3.0%;量化配送延迟对评分的影响(准时 4.29 分 vs 延迟 2.27 分),发现 10% 的卖家造成 74% 的延迟订单;通过 RFM 分层定位贡献 21.7% GMV 的"高价值待唤醒"客户,提出召回与卖家时效治理建议。

## English

**Olist E-commerce Performance Dashboard (Power BI)** | Personal project
SQL (DuckDB), Python, Power BI (DAX, Power Query)

- Cleaned and modeled 100k orders across 9 tables into a star schema (order- and item-level facts; date, buyer, product and seller dimensions). Standardized definitions for buyers, valid orders and reviews, and reconciled GMV across grains.
- Built a 33-measure DAX layer (GMV, repeat rate, on-time delivery, funnel conversion, top-seller concentration, time intelligence, dynamic titles) and a 6-page interactive dashboard with drill-through, report-page tooltips, synced slicers and a metric switcher.
- Decomposed GMV into buyers × orders per buyer × AOV and found that the +140% YoY growth came almost entirely from new buyers, with only 3.0% repeat purchase. Quantified the review cost of late delivery (4.29 vs 2.27 stars) and found that 10% of sellers caused 74% of late orders. Used RFM segmentation to identify lapsed high-value buyers (21.7% of GMV) as the reactivation target.

## 面试可以展开讲的点

1. **为什么用 `customer_unique_id`**:`customer_id` 每单一个,用它算复购率会是 0;这是数据口径陷阱。
2. **为什么把商品 ↔ 订单设为双向筛选**:否则按卖家或品类筛选时,准时率、评分这些订单级指标不会变化;双向筛选有歧义路径的风险,这里只有一条路径,所以是安全的。
3. **怎么保证看板数字是对的**:SQL 分析与 Power BI 模型各算一遍,用 DAX 查询逐项对账。这个过程抓到了两个 bug:`top` 是 DAX 保留字;`BLANK() = FALSE()` 为真,导致未送达订单被算成"延迟"(圣保罗州卖家的延迟订单数从 6,546 修正为 5,015)。
4. **可视化的诚实性**:"人均订单数"这类波动很小的指标,纵轴从 0 开始,避免自动缩放把 1.00–1.04 的波动放大成剧烈起伏;留存矩阵去掉"第 0 个月 = 100%"的列,让热力图颜色体现真实差异。
5. **RFM 为什么不按教科书三维打分**:97% 的买家只下过一单,F 维度几乎没有区分度,所以改用 R × M 加"是否复购"做规则分层。
