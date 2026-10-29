"""Generate the Power BI project (PBIP: TMDL semantic model + PBIR report) from data/model/*.csv.

Open powerbi/OlistDashboard.pbip in Power BI Desktop, then Refresh. Re-run this script after changing
measures or layout; it rewrites the generated folders.
"""
import json
import shutil
import uuid
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
MODEL_DIR = ROOT / "data" / "model"
OUT = ROOT / "powerbi"
NAME = "OlistDashboard"
SM, RP = OUT / f"{NAME}.SemanticModel", OUT / f"{NAME}.Report"
SCHEMA = "https://developer.microsoft.com/json-schemas/fabric"


def tag(*parts) -> str:
    """Deterministic lineage tags so regenerated files diff cleanly."""
    return str(uuid.uuid5(uuid.NAMESPACE_URL, "olist/" + "/".join(parts)))


def q(name: str) -> str:
    """Quote a TMDL object name when it is not a plain identifier."""
    return name if name.isascii() and name.replace("_", "").isalnum() else "'" + name.replace("'", "''") + "'"


# ======================================================================================
# Semantic model
# ======================================================================================
DATE_COLS = {"purchase_date", "date", "month_start", "first_purchase_date", "cohort_month"}
HIDDEN = {"fact_orders": {"lateness_order", "buyer_order_seq"}, "dim_product": {"category_pt"}}
SORT_BY = {("fact_orders", "lateness_bucket"): "lateness_order", ("funnel_stage", "stage"): "stage_no",
           ("dim_date", "weekday"): "weekday_no"}
TABLES = ["fact_orders", "fact_order_items", "dim_date", "dim_customer", "dim_product", "dim_seller", "cohort_retention"]


def column_types(table: str) -> dict[str, tuple[str, str]]:
    """column -> (TMDL dataType, Power Query type)"""
    df = pd.read_csv(MODEL_DIR / f"{table}.csv", nrows=20000)
    out = {}
    for col, dt in df.dtypes.items():
        if col in DATE_COLS:
            out[col] = ("dateTime", "type date")
        elif col.startswith("is_"):
            out[col] = ("boolean", "type logical")
        elif pd.api.types.is_integer_dtype(dt):
            out[col] = ("int64", "Int64.Type")
        elif pd.api.types.is_float_dtype(dt):
            out[col] = ("double", "type number")
        else:
            out[col] = ("string", "type text")
    return out


def csv_partition(table: str, types: dict) -> str:
    typed = ", ".join(f'{{"{c}", {t[1]}}}' for c, t in types.items())
    m = [
        "let",
        f'    Source = Csv.Document(File.Contents(DataFolder & "{table}.csv"), [Delimiter = ",", Encoding = 65001, QuoteStyle = QuoteStyle.Csv]),',
        "    Promoted = Table.PromoteHeaders(Source, [PromoteAllScalars = true]),",
        '    Blanks = Table.TransformColumns(Promoted, {}, each if _ = "" then null else _),',
        f'    Typed = Table.TransformColumnTypes(Blanks, {{{typed}}}, "en-US")',
        "in",
        "    Typed",
    ]
    body = "\n".join("\t\t\t\t" + line for line in m)
    return f"\tpartition {q(table)} = m\n\t\tmode: import\n\t\tsource =\n{body}\n"


def column_block(table: str, col: str, dtype: str) -> str:
    lines = [f"\tcolumn {q(col)}", f"\t\tdataType: {dtype}"]
    if dtype == "dateTime":
        lines.append("\t\tformatString: yyyy-mm-dd")
    if dtype in ("int64", "double") and not col.endswith(("value", "gmv", "price")):
        lines.append("\t\tsummarizeBy: none")
    elif dtype in ("int64", "double"):
        lines.append("\t\tsummarizeBy: sum")
    else:
        lines.append("\t\tsummarizeBy: none")
    if (table, col) in SORT_BY:
        lines.append(f"\t\tsortByColumn: {q(SORT_BY[(table, col)])}")
    if table == "dim_date" and col == "date":
        lines.append("\t\tisKey")
    if col in HIDDEN.get(table, set()):
        lines.append("\t\tisHidden")
    lines += [f"\t\tlineageTag: {tag(table, col)}", f"\t\tsourceColumn: {col}", ""]
    return "\n".join(lines) + "\n"


# name -> (DAX, format string, folder)
MEASURES = {
    "GMV": ("CALCULATE(SUM(fact_orders[order_value]), fact_orders[is_valid] = TRUE())", "#,0", "1 规模"),
    "订单数": ("CALCULATE(COUNTROWS(fact_orders), fact_orders[is_valid] = TRUE())", "#,0", "1 规模"),
    "买家数": ("CALCULATE(DISTINCTCOUNT(fact_orders[customer_unique_id]), fact_orders[is_valid] = TRUE())", "#,0", "1 规模"),
    "客单价": ("DIVIDE([GMV], [订单数])", "#,0.0", "1 规模"),
    "人均订单数": ("DIVIDE([订单数], [买家数])", "0.000", "1 规模"),
    "上月GMV": ("CALCULATE([GMV], DATEADD(dim_date[date], -1, MONTH))", "#,0", "2 增长"),
    "GMV环比": ("DIVIDE([GMV] - [上月GMV], [上月GMV])", "0.0%", "2 增长"),
    "去年同期GMV": ("CALCULATE([GMV], SAMEPERIODLASTYEAR(dim_date[date]))", "#,0", "2 增长"),
    "GMV同比": ("DIVIDE([GMV] - [去年同期GMV], [去年同期GMV])", "0.0%", "2 增长"),
    "新买家数": ("CALCULATE([买家数], fact_orders[is_first_order] = TRUE())", "#,0", "2 增长"),
    "新买家GMV占比": ("DIVIDE(CALCULATE([GMV], fact_orders[is_first_order] = TRUE()), [GMV])", "0.0%", "2 增长"),
    "复购率": ("DIVIDE(COUNTROWS(FILTER(VALUES(fact_orders[customer_unique_id]), "
               "CALCULATE(COUNTROWS(fact_orders), fact_orders[is_valid] = TRUE()) > 1)), [买家数])", "0.0%", "2 增长"),
    "准时送达率": ("DIVIDE(CALCULATE(COUNTROWS(fact_orders), fact_orders[is_on_time] = TRUE()), "
                "CALCULATE(COUNTROWS(fact_orders), fact_orders[is_delivered] = TRUE()))", "0.0%", "3 履约体验"),
    "平均配送天数": ("CALCULATE(AVERAGE(fact_orders[delivery_days]), fact_orders[is_delivered] = TRUE())", "0.0", "3 履约体验"),
    "平均评分": ("AVERAGE(fact_orders[review_score])", "0.00", "3 履约体验"),
    "差评率": ("DIVIDE(CALCULATE(COUNTROWS(fact_orders), fact_orders[review_score] <= 2, NOT ISBLANK(fact_orders[review_score])), "
              "CALCULATE(COUNTROWS(fact_orders), NOT ISBLANK(fact_orders[review_score])))", "0.0%", "3 履约体验"),
    "运费占比": ("DIVIDE(CALCULATE(SUM(fact_orders[freight_value]), fact_orders[is_valid] = TRUE()), [GMV])", "0.0%", "3 履约体验"),
    "品类GMV": ("CALCULATE(SUM(fact_order_items[item_gmv]), fact_order_items[is_valid] = TRUE())", "#,0", "4 品类"),
    "品类GMV占比": ("DIVIDE([品类GMV], CALCULATE([品类GMV], ALL(dim_product)))", "0.0%", "4 品类"),
    "留存率": ("DIVIDE(SUM(cohort_retention[active_buyers]), SUM(cohort_retention[cohort_size]))", "0.0%", "5 留存"),
    "漏斗订单数": ("SWITCH(SELECTEDVALUE(funnel_stage[stage_no]),\n"
                "    1, COUNTROWS(fact_orders),\n"
                "    2, CALCULATE(COUNTROWS(fact_orders), fact_orders[is_approved] = TRUE()),\n"
                "    3, CALCULATE(COUNTROWS(fact_orders), fact_orders[is_shipped] = TRUE()),\n"
                "    4, CALCULATE(COUNTROWS(fact_orders), fact_orders[is_delivered] = TRUE()),\n"
                "    5, CALCULATE(COUNTROWS(fact_orders), fact_orders[is_delivered] = TRUE(), fact_orders[review_score] >= 4))",
                "#,0", "3 履约体验"),
    "漏斗转化率": ("DIVIDE([漏斗订单数], CALCULATE([漏斗订单数], REMOVEFILTERS(funnel_stage), funnel_stage[stage_no] = 1))", "0.0%", "3 履约体验"),
}
MEASURE_TABLE = "指标"


def measure_block(name, dax, fmt, folder) -> str:
    if "\n" in dax:
        body = "\n".join("\t\t\t" + line for line in dax.split("\n"))
        head = f"\tmeasure {q(name)} =\n{body}\n"
    else:
        head = f"\tmeasure {q(name)} = {dax}\n"
    return head + f"\t\tformatString: {fmt}\n\t\tdisplayFolder: {folder}\n\t\tlineageTag: {tag('measure', name)}\n\n"


RELATIONSHIPS = [
    ("fact_orders", "purchase_date", "dim_date", "date"),
    ("fact_orders", "customer_unique_id", "dim_customer", "customer_unique_id"),
    ("fact_order_items", "order_id", "fact_orders", "order_id"),
    ("fact_order_items", "product_id", "dim_product", "product_id"),
    ("fact_order_items", "seller_id", "dim_seller", "seller_id"),
]


def write_semantic_model():
    d = SM / "definition"
    (d / "tables").mkdir(parents=True)
    (SM / "definition.pbism").write_text(json.dumps({
        "$schema": f"{SCHEMA}/item/semanticModel/definitionProperties/1.0.0/schema.json",
        "version": "4.2", "settings": {}}, indent=2), encoding="utf-8")
    (d / "database.tmdl").write_text("database\n\tcompatibilityLevel: 1600\n", encoding="utf-8")
    refs = "".join(f"ref table {q(t)}\n" for t in TABLES + ["funnel_stage", MEASURE_TABLE])
    (d / "model.tmdl").write_text(
        "model Model\n\tculture: zh-CN\n\tdefaultPowerBIDataSourceVersion: powerBI_V3\n"
        "\tdiscourageImplicitMeasures\n\tsourceQueryCulture: en-US\n\n" + refs, encoding="utf-8")
    folder = str(MODEL_DIR.resolve()) + "\\"
    (d / "expressions.tmdl").write_text(
        f'expression DataFolder = "{folder}" meta [IsParameterQuery = true, Type = "Text", IsParameterQueryRequired = true]\n'
        f"\tlineageTag: {tag('param', 'DataFolder')}\n", encoding="utf-8")

    for t in TABLES:
        types = column_types(t)
        txt = f"table {q(t)}\n\tlineageTag: {tag('table', t)}\n"
        if t == "dim_date":
            txt += "\tdataCategory: Time\n"
        txt += "\n" + "".join(column_block(t, c, dt[0]) for c, dt in types.items()) + csv_partition(t, types)
        (d / "tables" / f"{t}.tmdl").write_text(txt, encoding="utf-8")

    funnel_m = ("let\n    Source = #table(type table [stage_no = Int64.Type, stage = text], "
                '{{1, "1 下单"}, {2, "2 支付审核"}, {3, "3 发货"}, {4, "4 送达"}, {5, "5 好评(≥4星)"}})\nin\n    Source')
    funnel = (f"table funnel_stage\n\tlineageTag: {tag('table', 'funnel_stage')}\n\n"
              + column_block("funnel_stage", "stage_no", "int64") + column_block("funnel_stage", "stage", "string")
              + "\tpartition funnel_stage = m\n\t\tmode: import\n\t\tsource =\n"
              + "\n".join("\t\t\t\t" + line for line in funnel_m.split("\n")) + "\n")
    (d / "tables" / "funnel_stage.tmdl").write_text(funnel, encoding="utf-8")

    mt = (f"table {q(MEASURE_TABLE)}\n\tlineageTag: {tag('table', MEASURE_TABLE)}\n\n"
          + "".join(measure_block(n, *v) for n, v in MEASURES.items())
          + f"\tcolumn 占位\n\t\tdataType: string\n\t\tisHidden\n\t\tsummarizeBy: none\n\t\tlineageTag: {tag('mt', 'col')}\n\t\tsourceColumn: 占位\n\n"
          + "\tpartition 指标 = m\n\t\tmode: import\n\t\tsource =\n"
          + "\t\t\t\tlet\n\t\t\t\t    Source = #table(type table [占位 = text], {})\n\t\t\t\tin\n\t\t\t\t    Source\n")
    (d / "tables" / f"{MEASURE_TABLE}.tmdl").write_text(mt, encoding="utf-8")

    rel = "".join(f"relationship {tag('rel', ft, fc)}\n\tfromColumn: {q(ft)}.{q(fc)}\n\ttoColumn: {q(tt)}.{q(tc)}\n\n"
                  for ft, fc, tt, tc in RELATIONSHIPS)
    (d / "relationships.tmdl").write_text(rel, encoding="utf-8")


# ======================================================================================
# Report (PBIR)
# ======================================================================================
VC = f"{SCHEMA}/item/report/definition/visualContainer/2.4.0/schema.json"


def measure(name):
    return {"field": {"Measure": {"Expression": {"SourceRef": {"Entity": MEASURE_TABLE}}, "Property": name}},
            "queryRef": f"{MEASURE_TABLE}.{name}", "nativeQueryRef": name}


def column(table, col, display=None):
    p = {"field": {"Column": {"Expression": {"SourceRef": {"Entity": table}}, "Property": col}},
         "queryRef": f"{table}.{col}", "nativeQueryRef": col}
    if display:
        p["displayName"] = display
    return p


def lit(v):
    return {"expr": {"Literal": {"Value": v}}}


def title(text):
    return {"title": [{"properties": {"show": lit("true"), "text": lit(f"'{text}'"), "fontSize": lit("12D"),
                                      "bold": lit("true"), "fontColor": {"solid": {"color": lit("'#252423'")}}}}]}


AXIS_FROM_ZERO = {"valueAxis": [{"properties": {"start": lit("0D")}}]}

COHORT_MATRIX = {
    "subTotals": [{"properties": {"rowSubtotals": lit("false"), "columnSubtotals": lit("false")}}],
    "columnHeaders": [{"properties": {"fontSize": lit("11D")}}],
    "rowHeaders": [{"properties": {"fontSize": lit("11D")}}],
    "values": [{"properties": {"fontSize": lit("11D")}}, {
        "properties": {"backColor": {"solid": {"color": {"expr": {"FillRule": {
            "Input": measure("留存率")["field"],
            "FillRule": {"linearGradient2": {
                "min": {"color": {"Literal": {"Value": "'#FFFFFF'"}}},
                "max": {"color": {"Literal": {"Value": "'#118DFF'"}}},
                "nullColoringStrategy": {"strategy": {"Literal": {"Value": "'asZero'"}}}}}}}}}}},
        "selector": {"data": [{"dataViewWildcard": {"matchingOption": 1}}], "metadata": f"{MEASURE_TABLE}.留存率"}}],
}


class Page:
    def __init__(self, name, display):
        self.name, self.display, self.visuals = name, display, []

    def add(self, vid, vtype, x, y, w, h, roles=None, ttl=None, sort=None, objects=None):
        v = {"visualType": vtype, "drillFilterOtherVisuals": True}
        if roles:
            v["query"] = {"queryState": {r: {"projections": p} for r, p in roles.items()}}
            if sort:
                field, direction = sort
                v["query"]["sortDefinition"] = {"sort": [{"field": field["field"], "direction": direction}],
                                                "isDefaultSort": True}
        if ttl:
            v["visualContainerObjects"] = title(ttl)
        if objects:
            v["objects"] = objects
        self.visuals.append({"$schema": VC, "name": vid,
                             "position": {"x": x, "y": y, "z": len(self.visuals), "width": w, "height": h},
                             "visual": v})

    def card(self, vid, m, x, y, w=200, h=95):
        # Display units "None" (1D) so cards show 15,688,397 instead of "16 百万".
        self.add(vid, "card", x, y, w, h, {"Values": [measure(m)]},
                 objects={"labels": [{"properties": {"labelDisplayUnits": lit("1D")}}]})

    def text(self, vid, x, y, w, h, lines, size="10pt", bold_first=False):
        paras = []
        for i, line in enumerate(lines):
            style = {"fontSize": size}
            if bold_first and i == 0:
                style["fontWeight"] = "bold"
            paras.append({"textRuns": [{"value": line, "textStyle": style}]})
        self.add(vid, "textbox", x, y, w, h, objects={"general": [{"properties": {"paragraphs": paras}}]})


def build_pages():
    pages = []

    p = Page("overview", "经营总览")
    p.text("header", 20, 8, 1240, 50, ["Olist 巴西电商经营看板 | 2017.01 – 2018.08 | 金额单位:雷亚尔 (R$)"], "16pt", True)
    for i, m in enumerate(["GMV", "订单数", "买家数", "客单价", "准时送达率", "平均评分"]):
        p.card(f"kpi_{i}", m, 20 + i * 207, 62)
    p.add("gmv_trend", "lineChart", 20, 170, 820, 270,
          {"Category": [column("dim_date", "month_start", "月份")], "Y": [measure("GMV")]}, "月度 GMV 趋势")
    p.add("cat_bar", "clusteredBarChart", 860, 170, 400, 540,
          {"Category": [column("dim_product", "category_cn", "品类")], "Y": [measure("品类GMV")]},
          "品类 GMV 排名", sort=(measure("品类GMV"), "Descending"))
    p.add("region_col", "clusteredColumnChart", 20, 455, 410, 255,
          {"Category": [column("dim_customer", "region", "区域")], "Y": [measure("GMV")]}, "区域 GMV",
          sort=(measure("GMV"), "Descending"))
    p.add("slicer_group", "slicer", 445, 455, 395, 75, {"Values": [column("dim_product", "category_group", "品类大类")]},
          objects={"data": [{"properties": {"mode": lit("'Dropdown'")}}]})
    p.text("insight_overview", 445, 540, 395, 170, [
        "关键发现",
        "• 2018 年 1–8 月 GMV 同比 +140%,增长几乎全部来自买家数(+139%)。",
        "• 2017 年 11 月黑五 GMV 环比 +53%,为全期峰值。",
        "• 前 7 个品类贡献约 50% 的 GMV,健康美妆排名第一。",
        "• 东南部贡献约 65% 的 GMV。"], bold_first=True)
    pages.append(p)

    p = Page("growth", "GMV 拆解")
    p.text("header", 20, 8, 1240, 50, ["GMV 拆解:GMV = 买家数 × 人均订单数 × 客单价"], "16pt", True)
    for i, m in enumerate(["GMV", "买家数", "人均订单数", "客单价", "新买家GMV占比"]):
        p.card(f"kpi_{i}", m, 20 + i * 250, 62, w=235)
    p.add("buyers_trend", "lineChart", 20, 170, 410, 250,
          {"Category": [column("dim_date", "month_start", "月份")], "Y": [measure("买家数")]}, "买家数(增长主因)")
    p.add("opb_trend", "lineChart", 445, 170, 410, 250,
          {"Category": [column("dim_date", "month_start", "月份")], "Y": [measure("人均订单数")]}, "人均订单数(基本持平)",
          objects=AXIS_FROM_ZERO)
    p.add("aov_trend", "lineChart", 870, 170, 390, 250,
          {"Category": [column("dim_date", "month_start", "月份")], "Y": [measure("客单价")]}, "客单价(基本持平)",
          objects=AXIS_FROM_ZERO)
    p.add("month_table", "tableEx", 20, 435, 840, 275,
          {"Values": [column("dim_date", "year_month", "月份"), measure("GMV"), measure("GMV环比"), measure("GMV同比"),
                      measure("买家数"), measure("新买家数"), measure("人均订单数"), measure("客单价")]},
          "月度指标明细")
    p.text("insight_growth", 875, 435, 385, 275, [
        "关键发现",
        "• 2018.01–08 对比 2017.01–08:GMV +140%,买家数 +139%,人均订单数 1.029 → 1.023,客单价 R$158 → R$161。",
        "• 新买家贡献约 97% 的 GMV,增长几乎完全依赖新客获取。",
        "• 建议:增长策略应从'拉新'转向'复购',见'用户留存'页。"], bold_first=True)
    pages.append(p)

    p = Page("fulfillment", "履约与体验")
    p.text("header", 20, 8, 1240, 50, ["履约与体验:配送时效决定用户评分"], "16pt", True)
    for i, m in enumerate(["准时送达率", "平均配送天数", "平均评分", "差评率", "运费占比"]):
        p.card(f"kpi_{i}", m, 20 + i * 250, 62, w=235)
    p.add("late_review", "clusteredColumnChart", 20, 170, 410, 270,
          {"Category": [column("fact_orders", "lateness_bucket", "送达时效")], "Y": [measure("平均评分")]},
          "送达时效 vs 平均评分")
    p.add("ontime_combo", "lineClusteredColumnComboChart", 445, 170, 815, 270,
          {"Category": [column("dim_date", "month_start", "月份")], "Y": [measure("订单数")], "Y2": [measure("准时送达率")]},
          "订单量与准时送达率(大促期间履约承压)")
    p.add("funnel", "funnel", 20, 455, 410, 255,
          {"Category": [column("funnel_stage", "stage", "环节")], "Y": [measure("漏斗订单数")]}, "订单履约漏斗",
          objects={"labels": [{"properties": {"labelDisplayUnits": lit("1D")}}]})
    p.add("region_days", "clusteredBarChart", 445, 455, 410, 255,
          {"Category": [column("dim_customer", "region", "区域")], "Y": [measure("平均配送天数")]},
          "各区域平均配送天数", sort=(measure("平均配送天数"), "Descending"))
    p.text("insight_fulfillment", 870, 455, 390, 255, [
        "关键发现",
        "• 准时订单平均 4.29 分,延迟订单仅 2.27 分;延迟超 7 天降至 1.70 分,未送达订单 1.75 分。",
        "• 2017.11 黑五与 2018.02–03 准时率跌至 81%–88%,评分同步走低。",
        "• 北部、东北部平均配送 20–23 天,运费占比 26%–28%,评分最低。",
        "• 漏斗最大流失在'送达 → 好评'(约 22%)。"], bold_first=True)
    pages.append(p)

    p = Page("retention", "用户留存")
    p.text("header", 20, 8, 1240, 50, ["用户留存:按首购月份的同期群分析"], "16pt", True)
    for i, m in enumerate(["复购率", "人均订单数", "新买家数"]):
        p.card(f"kpi_{i}", m, 20 + i * 250, 62, w=235)
    p.add("cohort", "pivotTable", 20, 170, 840, 540,
          {"Rows": [column("cohort_retention", "cohort_label", "首购月份")],
           "Columns": [column("cohort_retention", "months_since", "首购后第 N 个月")],
           "Values": [measure("留存率")]}, "同期群留存矩阵(首购后第 N 个月仍下单的买家占比)",
          objects=COHORT_MATRIX)
    p.text("insight_retention", 875, 170, 385, 540, [
        "关键发现",
        "• 复购率仅 3.0%,人均订单 1.03 单。",
        "• 首购后第 1 个月留存约 0.5%,之后长期低于 0.3%。",
        "• 平台以单次购买为主,增长对拉新高度依赖。",
        "建议",
        "• 首单后 30 天内的复购激励(优惠券、关联推荐)。",
        "• 优先修复延迟配送:差评用户几乎不会复购。",
        "• 对高复购品类(如家居、美妆)做会员或订阅试点。"], bold_first=True)
    pages.append(p)
    return pages


def write_report(pages):
    d = RP / "definition"
    (d / "pages").mkdir(parents=True)
    (RP / "definition.pbir").write_text(json.dumps({
        "$schema": f"{SCHEMA}/item/report/definitionProperties/2.0.0/schema.json",
        "version": "4.0", "datasetReference": {"byPath": {"path": f"../{NAME}.SemanticModel"}}}, indent=2), encoding="utf-8")
    (d / "version.json").write_text(json.dumps({
        "$schema": f"{SCHEMA}/item/report/definition/versionMetadata/1.0.0/schema.json", "version": "2.0.0"}, indent=2),
        encoding="utf-8")
    (d / "report.json").write_text(json.dumps({
        "$schema": f"{SCHEMA}/item/report/definition/report/3.0.0/schema.json", "themeCollection": {}}, indent=2),
        encoding="utf-8")
    (d / "pages" / "pages.json").write_text(json.dumps({
        "$schema": f"{SCHEMA}/item/report/definition/pagesMetadata/1.0.0/schema.json",
        "pageOrder": [p.name for p in pages], "activePageName": pages[0].name}, indent=2), encoding="utf-8")
    for p in pages:
        pd_ = d / "pages" / p.name
        (pd_ / "visuals").mkdir(parents=True)
        (pd_ / "page.json").write_text(json.dumps({
            "$schema": f"{SCHEMA}/item/report/definition/page/2.0.0/schema.json",
            "name": p.name, "displayName": p.display, "displayOption": "FitToPage", "height": 720, "width": 1280},
            ensure_ascii=False, indent=2), encoding="utf-8")
        for v in p.visuals:
            vd = pd_ / "visuals" / v["name"]
            vd.mkdir()
            (vd / "visual.json").write_text(json.dumps(v, ensure_ascii=False, indent=2), encoding="utf-8")


def main():
    for p in (SM, RP):
        if p.exists():
            shutil.rmtree(p)
    write_semantic_model()
    pages = build_pages()
    write_report(pages)
    (OUT / f"{NAME}.pbip").write_text(json.dumps({
        "$schema": f"{SCHEMA}/pbip/pbipProperties/1.0.0/schema.json", "version": "1.0",
        "artifacts": [{"report": {"path": f"{NAME}.Report"}}], "settings": {"enableAutoRecovery": True}}, indent=2),
        encoding="utf-8")
    n_vis = sum(len(p.visuals) for p in pages)
    print(f"wrote {OUT / (NAME + '.pbip')}: {len(TABLES) + 2} tables, {len(MEASURES)} measures, "
          f"{len(RELATIONSHIPS)} relationships, {len(pages)} pages, {n_vis} visuals")


if __name__ == "__main__":
    main()
