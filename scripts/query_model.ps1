# Run DAX queries against the model open in Power BI Desktop (its local Analysis Services instance),
# to check that tables loaded and measures return the numbers the SQL analysis found.
param([string]$OutFile = "$PSScriptRoot\..\docs\model_check.txt")

$bin = (Get-AppxPackage Microsoft.MicrosoftPowerBIDesktop).InstallLocation + "\bin"
Add-Type -Path "$bin\Microsoft.PowerBI.AdomdClient.dll"
$portFile = Get-ChildItem "$env:USERPROFILE\Microsoft\Power BI Desktop Store App\AnalysisServicesWorkspaces" -Recurse -Filter msmdsrv.port.txt |
    Sort-Object LastWriteTime -Descending | Select-Object -First 1
$port = (Get-Content $portFile.FullName -Raw -Encoding Unicode).Trim()
$conn = New-Object Microsoft.AnalysisServices.AdomdClient.AdomdConnection("Data Source=localhost:$port")
$conn.Open()
$db = $conn.GetSchemaDataSet("DBSCHEMA_CATALOGS", $null).Tables[0].Rows[0]["CATALOG_NAME"]
$conn.ChangeDatabase($db)

$queries = [ordered]@{
    "row counts" = 'EVALUATE { ("fact_orders", COUNTROWS(fact_orders)), ("fact_order_items", COUNTROWS(fact_order_items)), ("dim_customer", COUNTROWS(dim_customer)), ("dim_product", COUNTROWS(dim_product)), ("dim_date", COUNTROWS(dim_date)), ("cohort_retention", COUNTROWS(cohort_retention)) }'
    "headline KPIs" = 'EVALUATE ROW("GMV", [GMV], "orders", [订单数], "buyers", [买家数], "AOV", [客单价], "on_time", [准时送达率], "avg_review", [平均评分], "bad_review", [差评率], "repeat", [复购率], "freight_share", [运费占比], "new_buyer_gmv", [新买家GMV占比])'
    "monthly (last 4)" = 'EVALUATE TOPN(4, SUMMARIZECOLUMNS(dim_date[year_month], "GMV", [GMV], "MoM", [GMV环比], "YoY", [GMV同比], "buyers", [买家数]), dim_date[year_month], DESC)'
    "category top 3" = 'EVALUATE TOPN(3, SUMMARIZECOLUMNS(dim_product[category_cn], "GMV", [品类GMV], "share", [品类GMV占比]), [品类GMV], DESC)'
    "lateness vs review" = 'EVALUATE SUMMARIZECOLUMNS(fact_orders[lateness_bucket], "avg_review", [平均评分], "orders", COUNTROWS(fact_orders))'
    "funnel" = 'EVALUATE SUMMARIZECOLUMNS(funnel_stage[stage], "orders", [漏斗订单数], "conv", [漏斗转化率])'
    "region" = 'EVALUATE SUMMARIZECOLUMNS(dim_customer[region], "GMV", [GMV], "days", [平均配送天数], "on_time", [准时送达率])'
    "retention M1-M3" = 'EVALUATE SUMMARIZECOLUMNS(cohort_retention[months_since], FILTER(VALUES(cohort_retention[months_since]), cohort_retention[months_since] IN {1, 2, 3}), "retention", [留存率])'
    "sellers" = 'EVALUATE ROW("sellers", [卖家数], "top10_share", [头部10%卖家GMV占比], "gmv_per_seller", [人均卖家GMV], "late_orders", [延迟订单数])'
    "seller SP (bidirectional filter)" = 'EVALUATE CALCULATETABLE(ROW("late_orders", [延迟订单数], "on_time", [准时送达率]), dim_seller[state] = "SP")'
    "rfm" = 'EVALUATE SUMMARIZECOLUMNS(dim_customer[rfm_segment], "buyers", [买家数], "buyer_share", [买家占比], "gmv_share", [GMV占比], "per_buyer", [人均消费])'
    "metric switch" = 'EVALUATE SUMMARIZECOLUMNS(metric_switch[metric], "value", [所选指标], "title", [所选指标标题])'
    "drill-through label" = 'EVALUATE CALCULATETABLE(ROW("label", [当前品类], "gmv", [品类GMV], "on_time", [准时送达率]), dim_product[category_cn] = "健康美妆")'
}
$out = @("database: $db (port $port)")
foreach ($k in $queries.Keys) {
    $out += "`n== $k"
    try {
        $cmd = $conn.CreateCommand(); $cmd.CommandText = $queries[$k]
        $r = $cmd.ExecuteReader()
        $cols = 0..($r.FieldCount - 1) | ForEach-Object { $r.GetName($_) -replace '^\[|\]$', '' }
        $out += ($cols -join " | ")
        while ($r.Read()) { $out += ((0..($r.FieldCount - 1) | ForEach-Object { $v = $r.GetValue($_); if ($v -is [double]) { "{0:N4}" -f $v } else { "$v" } }) -join " | ") }
        $r.Close()
    } catch { $out += "ERROR: $($_.Exception.InnerException.Message)$($_.Exception.Message)" }
}
$conn.Close()
$out | Set-Content -Encoding utf8 $OutFile
"wrote $OutFile"
