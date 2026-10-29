# Open the project in Power BI Desktop and run a full data refresh through its local Analysis Services
# instance (TMSL), so no clicks in the UI are needed.
param([int]$TimeoutSec = 240)

$pbip = (Resolve-Path "$PSScriptRoot\..\powerbi\OlistDashboard.pbip").Path
$wsRoot = "$env:USERPROFILE\Microsoft\Power BI Desktop Store App\AnalysisServicesWorkspaces"
$t0 = Get-Date
Start-Process "$env:LOCALAPPDATA\Microsoft\WindowsApps\PBIDesktopStore.exe" -ArgumentList "`"$pbip`""

# Wait for this session's model: a port file written after launch, and the database loaded.
$bin = (Get-AppxPackage Microsoft.MicrosoftPowerBIDesktop).InstallLocation + "\bin"
Add-Type -Path "$bin\Microsoft.PowerBI.AdomdClient.dll"
$conn = $null
do {
    Start-Sleep 5
    $pf = Get-ChildItem $wsRoot -Recurse -Filter msmdsrv.port.txt -ErrorAction SilentlyContinue |
        Where-Object { $_.LastWriteTime -gt $t0 } | Sort-Object LastWriteTime -Descending | Select-Object -First 1
    if ($pf) {
        try {
            $port = (Get-Content $pf.FullName -Raw -Encoding Unicode).Trim()
            $c = New-Object Microsoft.AnalysisServices.AdomdClient.AdomdConnection("Data Source=localhost:$port")
            $c.Open()
            $cat = $c.GetSchemaDataSet("DBSCHEMA_CATALOGS", $null).Tables[0].Rows
            if ($cat.Count -gt 0) { $conn = $c; $db = $cat[0]["CATALOG_NAME"] } else { $c.Close() }
        } catch { }
    }
} until ($conn -or ((Get-Date) - $t0).TotalSeconds -gt $TimeoutSec)
if (-not $conn) { "model not ready after $TimeoutSec s"; exit 1 }
"model ready on port $port after $([int]((Get-Date) - $t0).TotalSeconds) s"

$tmsl = @{ refresh = @{ type = "full"; objects = @(@{ database = "$db" }) } } | ConvertTo-Json -Depth 5 -Compress
$t1 = Get-Date
$cmd = $conn.CreateCommand(); $cmd.CommandText = $tmsl
try { $cmd.ExecuteNonQuery() | Out-Null; "refresh done in $([int]((Get-Date) - $t1).TotalSeconds) s" }
catch { "refresh failed: $($_.Exception.InnerException.Message)$($_.Exception.Message)"; exit 1 }
finally { $conn.Close() }
