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
HIDDEN = {"fact_orders": {"lateness_order", "buyer_order_seq"}, "dim_product": {"category_pt"},
          "dim_customer": {"rfm_order"}}
SORT_BY = {("fact_orders", "lateness_bucket"): "lateness_order", ("funnel_stage", "stage"): "stage_no",
           ("dim_date", "weekday"): "weekday_no", ("dim_customer", "rfm_segment"): "rfm_order",
           ("metric_switch", "metric"): "metric_no"}
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
    "品类GMV": ("CALCULATE(SUM(fact_order_items[item_gmv]), fact_order_items[is_valid] = TRUE())", "#,0", "4 品类与卖家"),
    "品类GMV占比": ("DIVIDE([品类GMV], CALCULATE([品类GMV], ALL(dim_product)))", "0.0%", "4 品类与卖家"),
    "卖家数": ("CALCULATE(DISTINCTCOUNT(fact_order_items[seller_id]), fact_order_items[is_valid] = TRUE())", "#,0", "4 品类与卖家"),
    "卖家GMV": ("[品类GMV]", "#,0", "4 品类与卖家"),
    "人均卖家GMV": ("DIVIDE([品类GMV], [卖家数])", "#,0", "4 品类与卖家"),
    # ("top" is a reserved word in DAX, so the variable is top_sellers)
    "头部10%卖家GMV占比": ("VAR n_sellers = [卖家数]\n"
                     "VAR top_sellers = TOPN(ROUNDUP(n_sellers * 0.1, 0), VALUES(fact_order_items[seller_id]), [品类GMV], DESC)\n"
                     "RETURN DIVIDE(CALCULATE([品类GMV], top_sellers), [品类GMV])", "0.0%", "4 品类与卖家"),
    # is_delivered is required: in DAX BLANK() = FALSE() is true, so undelivered orders would count as late.
    "延迟订单数": ("CALCULATE(COUNTROWS(fact_orders), fact_orders[is_on_time] = FALSE(), fact_orders[is_delivered] = TRUE())",
               "#,0", "3 履约体验"),
    "留存率": ("DIVIDE(SUM(cohort_retention[active_buyers]), SUM(cohort_retention[cohort_size]))", "0.0%", "5 留存与分层"),
    "买家占比": ("DIVIDE([买家数], CALCULATE([买家数], ALL(dim_customer)))", "0.0%", "5 留存与分层"),
    "GMV占比": ("DIVIDE([GMV], CALCULATE([GMV], ALL(dim_customer)))", "0.0%", "5 留存与分层"),
    "人均消费": ("DIVIDE([GMV], [买家数])", "#,0.0", "5 留存与分层"),
    "所选指标": ("SWITCH(SELECTEDVALUE(metric_switch[metric_no], 1), 1, [GMV], 2, [订单数], 3, [买家数], 4, [客单价])",
             "#,0", "6 交互"),
    "所选指标标题": ('"月度 " & SELECTEDVALUE(metric_switch[metric], "GMV") & " 趋势(可切换指标)"', "", "6 交互"),
    "当前品类": ('"品类详情:" & SELECTEDVALUE(dim_product[category_cn], "全部品类")', "", "6 交互"),
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
    fmt_line = f"\t\tformatString: {fmt}\n" if fmt else ""  # text measures have no format string
    return head + fmt_line + f"\t\tdisplayFolder: {folder}\n\t\tlineageTag: {tag('measure', name)}\n\n"


RELATIONSHIPS = [
    ("fact_orders", "purchase_date", "dim_date", "date"),
    ("fact_orders", "customer_unique_id", "dim_customer", "customer_unique_id"),
    # Both directions, so product/seller filters reach order-level measures (on-time rate, reviews).
    ("fact_order_items", "order_id", "fact_orders", "order_id", "bothDirections"),
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
    refs = "".join(f"ref table {q(t)}\n" for t in TABLES + ["funnel_stage", "metric_switch", MEASURE_TABLE])
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

    switch_m = ("let\n    Source = #table(type table [metric_no = Int64.Type, metric = text], "
                '{{1, "GMV"}, {2, "订单数"}, {3, "买家数"}, {4, "客单价"}})\nin\n    Source')
    switch = (f"table metric_switch\n\tlineageTag: {tag('table', 'metric_switch')}\n\n"
              + column_block("metric_switch", "metric_no", "int64") + column_block("metric_switch", "metric", "string")
              + "\tpartition metric_switch = m\n\t\tmode: import\n\t\tsource =\n"
              + "\n".join("\t\t\t\t" + line for line in switch_m.split("\n")) + "\n")
    (d / "tables" / "metric_switch.tmdl").write_text(switch, encoding="utf-8")

    mt = (f"table {q(MEASURE_TABLE)}\n\tlineageTag: {tag('table', MEASURE_TABLE)}\n\n"
          + "".join(measure_block(n, *v) for n, v in MEASURES.items())
          + f"\tcolumn 占位\n\t\tdataType: string\n\t\tisHidden\n\t\tsummarizeBy: none\n\t\tlineageTag: {tag('mt', 'col')}\n\t\tsourceColumn: 占位\n\n"
          + "\tpartition 指标 = m\n\t\tmode: import\n\t\tsource =\n"
          + "\t\t\t\tlet\n\t\t\t\t    Source = #table(type table [占位 = text], {})\n\t\t\t\tin\n\t\t\t\t    Source\n")
    (d / "tables" / f"{MEASURE_TABLE}.tmdl").write_text(mt, encoding="utf-8")

    rel = ""
    for ft, fc, tt, tc, *opt in RELATIONSHIPS:
        rel += f"relationship {tag('rel', ft, fc)}\n"
        if opt:
            rel += f"\tcrossFilteringBehavior: {opt[0]}\n"
        rel += f"\tfromColumn: {q(ft)}.{q(fc)}\n\ttoColumn: {q(tt)}.{q(tc)}\n\n"
    (d / "relationships.tmdl").write_text(rel, encoding="utf-8")


# ======================================================================================
# Report (PBIR)
# ======================================================================================
VC = f"{SCHEMA}/item/report/definition/visualContainer/2.4.0/schema.json"
PAGE_W, PAGE_H = 1280, 760
TOP = 92  # content starts below the header row (title, page navigator, date slicer)
THEME_FILE = "OlistTheme.json"
THEME = {
    "name": "Olist",
    "dataColors": ["#1A56DB", "#F59E0B", "#16A34A", "#DC2626", "#7C3AED", "#0891B2", "#DB2777", "#65A30D"],
    "foreground": "#1F2937", "background": "#FFFFFF", "tableAccent": "#1A56DB",
    "good": "#16A34A", "bad": "#DC2626", "neutral": "#F59E0B",
    "textClasses": {"title": {"fontFace": "Segoe UI Semibold", "fontSize": 12, "color": "#111827"},
                    "label": {"fontFace": "Segoe UI", "fontSize": 10, "color": "#374151"}},
    "visualStyles": {
        "*": {"*": {"background": [{"show": True, "color": {"solid": {"color": "#FFFFFF"}}, "transparency": 0}],
                    "border": [{"show": True, "color": {"solid": {"color": "#E5E7EB"}}, "radius": 8}],
                    "dropShadow": [{"show": False}]}},
        "page": {"*": {"background": [{"color": {"solid": {"color": "#F3F4F6"}}, "transparency": 0}]}},
    },
}


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


def title_props(text=None, measure_name=None):
    text_expr = ({"expr": {"Measure": {"Expression": {"SourceRef": {"Entity": MEASURE_TABLE}}, "Property": measure_name}}}
                 if measure_name else lit(f"'{text}'"))
    return [{"properties": {"show": lit("true"), "text": text_expr, "fontSize": lit("12D"), "bold": lit("true"),
                            "fontColor": {"solid": {"color": lit("'#111827'")}}}}]


AXIS_FROM_ZERO = {"valueAxis": [{"properties": {"start": lit("0D")}}]}
NO_UNITS = {"labels": [{"properties": {"labelDisplayUnits": lit("1D")}}]}
MONTH_TOOLTIP = {"visualTooltip": [{"properties": {"show": lit("true"), "type": lit("'ReportPage'"),
                                                   "section": lit("'tooltip_month'")}}]}
DATE_SYNC = {"groupName": "date_range", "fieldChanges": True, "filterChanges": True}

COHORT_MATRIX = {
    "subTotals": [{"properties": {"rowSubtotals": lit("false"), "columnSubtotals": lit("false")}}],
    "columnHeaders": [{"properties": {"fontSize": lit("11D")}}],
    "rowHeaders": [{"properties": {"fontSize": lit("11D")}}],
    "values": [{"properties": {"fontSize": lit("11D")}}, {
        "properties": {"backColor": {"solid": {"color": {"expr": {"FillRule": {
            "Input": measure("留存率")["field"],
            "FillRule": {"linearGradient2": {
                "min": {"color": {"Literal": {"Value": "'#FFFFFF'"}}},
                "max": {"color": {"Literal": {"Value": "'#1A56DB'"}}},
                "nullColoringStrategy": {"strategy": {"Literal": {"Value": "'asZero'"}}}}}}}}}}},
        "selector": {"data": [{"dataViewWildcard": {"matchingOption": 1}}], "metadata": f"{MEASURE_TABLE}.留存率"}}],
}


class Page:
    def __init__(self, name, display, width=PAGE_W, height=PAGE_H, page_type=None, binding=None, filters=None):
        self.name, self.display, self.visuals = name, display, []
        self.width, self.height, self.type, self.binding, self.filters = width, height, page_type, binding, filters

    def add(self, vid, vtype, x, y, w, h, roles=None, ttl=None, sort=None, objects=None, container=None,
            sync=None, ttl_measure=None):
        v = {"visualType": vtype, "drillFilterOtherVisuals": True}
        if roles:
            v["query"] = {"queryState": {r: {"projections": p} for r, p in roles.items()}}
            if sort:
                field, direction = sort
                v["query"]["sortDefinition"] = {"sort": [{"field": field["field"], "direction": direction}],
                                                "isDefaultSort": True}
        vco = {}
        if ttl or ttl_measure:
            vco["title"] = title_props(ttl, ttl_measure)
        if container:
            vco.update(container)
        if vco:
            v["visualContainerObjects"] = vco
        if objects:
            v["objects"] = objects
        if sync:
            v["syncGroup"] = sync
        self.visuals.append({"$schema": VC, "name": vid,
                             "position": {"x": x, "y": y, "z": len(self.visuals), "width": w, "height": h},
                             "visual": v})

    def card(self, vid, m, x, y, w=200, h=88, font="22D"):
        # Display units "None" so cards show 15,688,397; a fixed font size keeps long numbers from truncating.
        self.add(vid, "card", x, y, w, h, {"Values": [measure(m)]},
                 objects={"labels": [{"properties": {"labelDisplayUnits": lit("1D"), "fontSize": lit(font)}}]})

    def kpis(self, names, y=TOP):
        w = (PAGE_W - 40 - 12 * (len(names) - 1)) // len(names)
        for i, m in enumerate(names):
            self.card(f"kpi_{i}", m, 20 + i * (w + 12), y, w=w)

    def text(self, vid, x, y, w, h, lines, size="10pt", bold_first=False):
        paras = []
        for i, line in enumerate(lines):
            style = {"fontSize": size}
            if bold_first and i == 0:
                style["fontWeight"] = "bold"
            paras.append({"textRuns": [{"value": line, "textStyle": style}]})
        self.add(vid, "textbox", x, y, w, h, objects={"general": [{"properties": {"paragraphs": paras}}]})

    def header(self, title):
        """Title, page navigator and a date-range slicer synced across all report pages."""
        self.text("header", 20, 6, 470, 54, [title], "14pt", True)
        self.add("nav", "pageNavigator", 500, 10, 560, 34,
                 objects={"pages": [{"properties": {"showHiddenPages": lit("false"), "showTooltipPages": lit("false")}}]})
        self.add("date_slicer", "slicer", 1070, 4, 190, 82, {"Values": [column("dim_date", "date", "下单日期")]},
                 objects={"data": [{"properties": {"mode": lit("'Between'")}}]}, sync=DATE_SYNC)

    def page_json(self):
        p = {"$schema": f"{SCHEMA}/item/report/definition/page/2.0.0/schema.json",
             "name": self.name, "displayName": self.display, "displayOption": "FitToPage",
             "height": self.height, "width": self.width}
        if self.type:
            p["type"] = self.type
            p["visibility"] = "HiddenInViewMode"
        if self.binding:
            p["pageBinding"] = self.binding
        if self.filters:
            p["filterConfig"] = {"filters": self.filters}
        return p


def build_pages():
    pages = []
    month = column("dim_date", "month_start", "月份")

    # ---------------- 1 经营总览 ----------------
    p = Page("overview", "经营总览")
    p.header("Olist 电商经营看板 | 金额单位 R$")
    p.kpis(["GMV", "订单数", "买家数", "客单价", "准时送达率", "平均评分"])
    y = TOP + 100
    # Horizontal single-select tiles; with nothing selected the measure defaults to GMV.
    p.add("metric_switch", "slicer", 20, y, 460, 60, {"Values": [column("metric_switch", "metric", "切换指标")]},
          objects={"data": [{"properties": {"mode": lit("'Basic'")}}],
                   "general": [{"properties": {"orientation": lit("1D")}}],
                   "selection": [{"properties": {"singleSelect": lit("true"), "selectAllCheckboxEnabled": lit("false")}}]})
    p.add("trend", "lineChart", 20, y + 66, 820, 250, {"Category": [month], "Y": [measure("所选指标")]},
          ttl_measure="所选指标标题", container=MONTH_TOOLTIP)
    p.add("cat_bar", "clusteredBarChart", 855, y, 405, 476,
          {"Category": [column("dim_product", "category_cn", "品类")], "Y": [measure("品类GMV")]},
          "品类 GMV 排名(右键品类可钻取详情)", sort=(measure("品类GMV"), "Descending"))
    y2 = y + 330
    p.add("region_col", "clusteredColumnChart", 20, y2, 400, 230,
          {"Category": [column("dim_customer", "region", "区域")], "Y": [measure("GMV")]}, "区域 GMV",
          sort=(measure("GMV"), "Descending"))
    p.add("slicer_group", "slicer", 435, y2, 405, 62, {"Values": [column("dim_product", "category_group", "品类大类")]},
          objects={"data": [{"properties": {"mode": lit("'Dropdown'")}}]})
    p.text("insight_overview", 435, y2 + 70, 405, 160, [
        "关键发现",
        "• 2018 年 1–8 月 GMV 同比 +140%,增长几乎全部来自买家数(+139%)。",
        "• 2017 年 11 月黑五 GMV 环比 +53%,为全期峰值。",
        "• 前 7 个品类贡献约 50% 的 GMV;东南部贡献约 65% 的 GMV。"], bold_first=True)
    pages.append(p)

    # ---------------- 2 GMV 拆解 ----------------
    p = Page("growth", "GMV 拆解")
    p.header("GMV = 买家数 × 人均订单数 × 客单价")
    p.kpis(["GMV", "买家数", "人均订单数", "客单价", "新买家GMV占比"])
    y = TOP + 100
    p.add("buyers_trend", "lineChart", 20, y, 405, 240, {"Category": [month], "Y": [measure("买家数")]},
          "买家数(增长主因)", container=MONTH_TOOLTIP)
    p.add("opb_trend", "lineChart", 437, y, 405, 240, {"Category": [month], "Y": [measure("人均订单数")]},
          "人均订单数(基本持平)", objects=AXIS_FROM_ZERO)
    p.add("aov_trend", "lineChart", 854, y, 406, 240, {"Category": [month], "Y": [measure("客单价")]},
          "客单价(基本持平)", objects=AXIS_FROM_ZERO)
    y2 = y + 255
    p.add("month_table", "tableEx", 20, y2, 822, 310,
          {"Values": [column("dim_date", "year_month", "月份"), measure("GMV"), measure("GMV环比"), measure("GMV同比"),
                      measure("买家数"), measure("新买家数"), measure("人均订单数"), measure("客单价")]}, "月度指标明细")
    p.text("insight_growth", 854, y2, 406, 310, [
        "关键发现",
        "• 2018.01–08 对比 2017.01–08:GMV +140%,买家数 +139%,人均订单数 1.029 → 1.023,客单价 R$158 → R$161。",
        "• 新买家贡献约 97% 的 GMV,增长几乎完全依赖新客获取。",
        "• 建议:增长策略应从'拉新'转向'复购',见'用户分层'与'用户留存'页。"], bold_first=True)
    pages.append(p)

    # ---------------- 3 履约与体验 ----------------
    p = Page("fulfillment", "履约与体验")
    p.header("配送时效决定用户评分")
    p.kpis(["准时送达率", "平均配送天数", "平均评分", "差评率", "运费占比"])
    y = TOP + 100
    p.add("late_review", "clusteredColumnChart", 20, y, 405, 270,
          {"Category": [column("fact_orders", "lateness_bucket", "送达时效")], "Y": [measure("平均评分")]},
          "送达时效 vs 平均评分")
    p.add("ontime_combo", "lineClusteredColumnComboChart", 437, y, 823, 270,
          {"Category": [month], "Y": [measure("订单数")], "Y2": [measure("准时送达率")]},
          "订单量与准时送达率(大促期间履约承压)", container=MONTH_TOOLTIP)
    y2 = y + 285
    p.add("funnel", "funnel", 20, y2, 405, 280,
          {"Category": [column("funnel_stage", "stage", "环节")], "Y": [measure("漏斗订单数")]}, "订单履约漏斗",
          objects=NO_UNITS)
    p.add("region_days", "clusteredBarChart", 437, y2, 405, 280,
          {"Category": [column("dim_customer", "region", "区域")], "Y": [measure("平均配送天数")]},
          "各区域平均配送天数", sort=(measure("平均配送天数"), "Descending"))
    p.text("insight_fulfillment", 854, y2, 406, 280, [
        "关键发现",
        "• 准时订单平均 4.29 分,延迟订单 2.27 分;延迟超 7 天 1.70 分,未送达 1.75 分。",
        "• 2017.11 黑五与 2018.02–03 准时率跌至 81%–88%,评分同步走低。",
        "• 北部、东北部平均配送 20–23 天,运费占比 26%–28%,评分最低。",
        "• 漏斗最大流失在'送达 → 好评'(约 22%)。"], bold_first=True)
    pages.append(p)

    # ---------------- 4 卖家分析 ----------------
    p = Page("sellers", "卖家分析")
    p.header("卖家集中度与履约质量")
    p.kpis(["卖家数", "头部10%卖家GMV占比", "人均卖家GMV", "延迟订单数", "准时送达率"])
    y = TOP + 100
    p.add("seller_scatter", "scatterChart", 20, y, 620, 300,
          {"Category": [column("dim_seller", "seller_id", "卖家")], "X": [measure("卖家GMV")],
           "Y": [measure("准时送达率")], "Size": [measure("订单数")]},
          "卖家规模 vs 准时送达率(气泡 = 订单数)")
    p.add("late_by_state", "clusteredBarChart", 652, y, 608, 300,
          {"Category": [column("dim_seller", "state", "卖家所在州")], "Y": [measure("延迟订单数")]},
          "延迟订单数(按卖家所在州)", sort=(measure("延迟订单数"), "Descending"))
    y2 = y + 315
    p.add("seller_table", "tableEx", 20, y2, 822, 250,
          {"Values": [column("dim_seller", "seller_id", "卖家"), column("dim_seller", "state", "州"),
                      measure("卖家GMV"), measure("订单数"), measure("准时送达率"), measure("延迟订单数"),
                      measure("平均评分")]},
          "卖家明细(按 GMV 排序)", sort=(measure("卖家GMV"), "Descending"))
    p.text("insight_sellers", 854, y2, 406, 250, [
        "关键发现",
        "• 3,029 个卖家中,头部 10% 贡献 66.5% 的 GMV,头部 20% 贡献 81.9%。",
        "• 延迟订单同样集中:延迟最多的 10% 卖家造成 73.8% 的延迟订单。",
        "• 圣保罗州(SP)卖家延迟订单最多(5,015 单,延迟率 7.3%)。",
        "建议:对延迟率高的头部卖家做时效考核与物流协同。"], bold_first=True)
    pages.append(p)

    # ---------------- 5 用户分层 (RFM) ----------------
    p = Page("rfm", "用户分层")
    p.header("RFM 用户分层:最近购买 × 消费金额 × 频次")
    p.kpis(["买家数", "复购率", "人均消费", "GMV"])
    y = TOP + 100
    seg = column("dim_customer", "rfm_segment", "用户分层")
    p.add("rfm_buyers", "clusteredBarChart", 20, y, 405, 280, {"Category": [seg], "Y": [measure("买家占比")]},
          "各分层买家占比")
    p.add("rfm_gmv", "clusteredBarChart", 437, y, 405, 280, {"Category": [seg], "Y": [measure("GMV占比")]},
          "各分层 GMV 占比")
    p.text("rfm_rules", 854, y, 406, 280, [
        "分层规则(截至 2018-09-01)",
        "• 复购客户:有效订单 ≥ 2 笔",
        "• 近期高价值 / 近期一般:最近 90 天内下单,消费额 ≥ / < 75 分位",
        "• 高价值待唤醒 / 一般待唤醒:91–270 天前下单,消费额 ≥ / < 75 分位",
        "• 已流失:最近一次下单在 270 天以前"], bold_first=True)
    y2 = y + 295
    p.add("rfm_table", "tableEx", 20, y2, 822, 270,
          {"Values": [seg, measure("买家数"), measure("买家占比"), measure("GMV"), measure("GMV占比"),
                      measure("人均消费"), measure("平均评分")]}, "分层明细")
    p.text("insight_rfm", 854, y2, 406, 270, [
        "关键发现与建议",
        "• '高价值待唤醒'仅占 9.4% 的买家,却贡献 21.7% 的 GMV,是召回营销的首要目标。",
        "• 复购客户只占 3.0%,人均消费 R$309,约为整体的 2 倍。",
        "• 39% 的买家已流失;对近期一般客户做首单后 30 天复购激励。"], bold_first=True)
    pages.append(p)

    # ---------------- 6 用户留存 ----------------
    p = Page("retention", "用户留存")
    p.header("按首购月份的同期群留存")
    p.kpis(["复购率", "人均订单数", "新买家数"])
    y = TOP + 100
    p.add("cohort", "pivotTable", 20, y, 840, 560,
          {"Rows": [column("cohort_retention", "cohort_label", "首购月份")],
           "Columns": [column("cohort_retention", "months_since", "首购后第 N 个月")],
           "Values": [measure("留存率")]}, "同期群留存矩阵(首购后第 N 个月仍下单的买家占比)", objects=COHORT_MATRIX)
    p.text("insight_retention", 875, y, 385, 560, [
        "关键发现",
        "• 复购率仅 3.0%,人均订单 1.03 单。",
        "• 首购后第 1 个月留存约 0.5%,之后长期低于 0.3%。",
        "• 平台以单次购买为主,增长对拉新高度依赖。",
        "建议",
        "• 首单后 30 天内的复购激励(优惠券、关联推荐)。",
        "• 优先修复延迟配送:差评用户几乎不会复购。",
        "• 对高复购品类(如家居、美妆)做会员或订阅试点。"], bold_first=True)
    pages.append(p)

    # ---------------- hidden: 品类详情 (drill-through) ----------------
    cat = column("dim_product", "category_cn", "品类")
    p = Page("category_detail", "品类详情", page_type="Drillthrough",
             filters=[{"name": "dt_category", "field": cat["field"], "type": "Categorical", "howCreated": "Drillthrough"}],
             binding={"name": "pb_category", "type": "Drillthrough",
                      "parameters": [{"name": "param_category", "boundFilter": "dt_category", "fieldExpr": cat["field"]}]})
    # Label underneath, transparent borderless Back button on top (higher z) that receives the click.
    p.text("back_label", 20, 12, 90, 40, ["← 返回"], "12pt", True)
    p.add("back", "actionButton", 20, 12, 90, 40,
          objects={"outline": [{"properties": {"show": lit("false")}}]},
          container={"visualLink": [{"properties": {"show": lit("true"), "type": lit("'Back'")}}],
                     "background": [{"properties": {"show": lit("false")}}],
                     "border": [{"properties": {"show": lit("false")}}]})
    p.add("cat_name", "card", 120, 6, 600, 50, {"Values": [measure("当前品类")]},
          objects={"labels": [{"properties": {"fontSize": lit("16D")}}],
                   "categoryLabels": [{"properties": {"show": lit("false")}}]})
    p.kpis(["品类GMV", "订单数", "准时送达率", "平均评分", "差评率"], y=66)
    p.add("cat_trend", "lineChart", 20, 166, 620, 280, {"Category": [month], "Y": [measure("品类GMV")]},
          "月度品类 GMV")
    p.add("cat_late", "clusteredColumnChart", 652, 166, 608, 280,
          {"Category": [column("fact_orders", "lateness_bucket", "送达时效")], "Y": [measure("平均评分")]},
          "该品类:送达时效 vs 平均评分")
    p.add("cat_sellers", "tableEx", 20, 460, 1240, 280,
          {"Values": [column("dim_seller", "seller_id", "卖家"), column("dim_seller", "state", "州"),
                      measure("卖家GMV"), measure("订单数"), measure("准时送达率"), measure("平均评分")]},
          "该品类卖家(按 GMV 排序)", sort=(measure("卖家GMV"), "Descending"))
    pages.append(p)

    # ---------------- hidden: 月度提示 (tooltip page) ----------------
    p = Page("tooltip_month", "月度提示", width=320, height=240, page_type="Tooltip",
             binding={"name": "pb_tooltip_month", "type": "Tooltip"})
    for i, m in enumerate(["GMV", "买家数", "客单价", "准时送达率"]):
        p.card(f"tt_{i}", m, 10 + (i % 2) * 155, 10 + (i // 2) * 112, w=145, h=102)
    pages.append(p)
    return pages


def write_report(pages):
    d = RP / "definition"
    (d / "pages").mkdir(parents=True)
    res = RP / "StaticResources" / "RegisteredResources"
    res.mkdir(parents=True)
    (res / THEME_FILE).write_text(json.dumps(THEME, ensure_ascii=False, indent=2), encoding="utf-8")
    (RP / "definition.pbir").write_text(json.dumps({
        "$schema": f"{SCHEMA}/item/report/definitionProperties/2.0.0/schema.json",
        "version": "4.0", "datasetReference": {"byPath": {"path": f"../{NAME}.SemanticModel"}}}, indent=2), encoding="utf-8")
    (d / "version.json").write_text(json.dumps({
        "$schema": f"{SCHEMA}/item/report/definition/versionMetadata/1.0.0/schema.json", "version": "2.0.0"}, indent=2),
        encoding="utf-8")
    (d / "report.json").write_text(json.dumps({
        "$schema": f"{SCHEMA}/item/report/definition/report/3.0.0/schema.json",
        "themeCollection": {"customTheme": {"name": THEME_FILE, "type": "RegisteredResources",
                                            "reportVersionAtImport": {"visual": "2.4.0", "page": "2.0.0",
                                                                      "report": "3.0.0"}}},
        "resourcePackages": [{"name": "RegisteredResources", "type": "RegisteredResources",
                              "items": [{"name": THEME_FILE, "path": THEME_FILE, "type": "CustomTheme"}]}]},
        indent=2), encoding="utf-8")
    (d / "pages" / "pages.json").write_text(json.dumps({
        "$schema": f"{SCHEMA}/item/report/definition/pagesMetadata/1.0.0/schema.json",
        "pageOrder": [p.name for p in pages], "activePageName": pages[0].name}, indent=2), encoding="utf-8")
    for p in pages:
        pd_ = d / "pages" / p.name
        (pd_ / "visuals").mkdir(parents=True)
        (pd_ / "page.json").write_text(json.dumps(p.page_json(), ensure_ascii=False, indent=2), encoding="utf-8")
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
    print(f"wrote {OUT / (NAME + '.pbip')}: {len(TABLES) + 3} tables, {len(MEASURES)} measures, "
          f"{len(RELATIONSHIPS)} relationships, {len(pages)} pages, {n_vis} visuals")


if __name__ == "__main__":
    main()
