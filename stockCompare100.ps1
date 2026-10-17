
[CmdletBinding()]
param(
    [ValidateRange(1, 100)]
    [int]$StartAt = 1,
    [ValidateRange(1, 100)]
    [int]$Count = 100,
    [ValidateSet("daily", "weekly")]
    [string]$Frequency = "daily",
    [ValidateNotNullOrEmpty()]
    [string]$OutputRoot = "C:\user\tyler\downloads\compare100stocks_audio_black"
)

$ErrorActionPreference = "Continue"

# ============================================================
# Configuration
# ============================================================

$Python = ".\.venv\Scripts\python.exe"
$LogFile = Join-Path $OutputRoot "generation.log"
$RenderWorkers = 2
$AudioDirectory = "C:\Users\tyler\Downloads\AiYoutube\audio"
$ChartContentScale = "0.90"
$ChartBackground = "black"
$Frequency = $Frequency.ToLowerInvariant()

# Verify that the script is running from the repository root.
if (-not (Test-Path $Python)) {
    Write-Error "Python executable not found at $Python. Run this script from your repository root."
    exit 1
}

# Create the root directory.
New-Item -ItemType Directory -Path $OutputRoot -Force | Out-Null

# Initialize logging.
"Stock comparison generation started: $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')" |
    Add-Content $LogFile
Add-Content $LogFile "Sampling frequency: $Frequency"

# ============================================================
# Comparison definitions
#
# Format:
# TickerA, LabelA, TickerB, LabelB, StartDate, VideoName
#
# Start dates are selected to approximate the beginning of
# usable trading history for ticker A. The actual first common
# trading date will depend on both stocks' available data.
# ============================================================

$Comparisons = @(

    # --------------------------------------------------------
    # PART 1: S&P 500 vs. 40 popular stocks
    # --------------------------------------------------------

    @("SPY", "S&P 500", "AAPL", "Apple", "1993-01-29", "sp500_vs_apple"),
    @("SPY", "S&P 500", "MSFT", "Microsoft", "1993-01-29", "sp500_vs_microsoft"),
    @("SPY", "S&P 500", "NVDA", "NVIDIA", "1999-01-22", "sp500_vs_nvidia"),
    @("SPY", "S&P 500", "AMZN", "Amazon", "1997-05-15", "sp500_vs_amazon"),
    @("SPY", "S&P 500", "GOOGL", "Alphabet", "2004-08-19", "sp500_vs_alphabet"),
    @("SPY", "S&P 500", "META", "Meta", "2012-05-18", "sp500_vs_meta"),
    @("SPY", "S&P 500", "TSLA", "Tesla", "2010-06-29", "sp500_vs_tesla"),
    @("SPY", "S&P 500", "NFLX", "Netflix", "2002-05-23", "sp500_vs_netflix"),
    @("SPY", "S&P 500", "AMD", "AMD", "1980-10-15", "sp500_vs_amd"),
    @("SPY", "S&P 500", "INTC", "Intel", "1993-01-29", "sp500_vs_intel"),

    @("SPY", "S&P 500", "AVGO", "Broadcom", "2009-08-06", "sp500_vs_broadcom"),
    @("SPY", "S&P 500", "ORCL", "Oracle", "1986-03-12", "sp500_vs_oracle"),
    @("SPY", "S&P 500", "CRM", "Salesforce", "2004-06-23", "sp500_vs_salesforce"),
    @("SPY", "S&P 500", "ADBE", "Adobe", "1986-03-12", "sp500_vs_adobe"),
    @("SPY", "S&P 500", "QCOM", "Qualcomm", "1991-12-16", "sp500_vs_qualcomm"),
    @("SPY", "S&P 500", "CSCO", "Cisco", "1990-02-16", "sp500_vs_cisco"),
    @("SPY", "S&P 500", "IBM", "IBM", "1993-01-29", "sp500_vs_ibm"),
    @("SPY", "S&P 500", "NOW", "ServiceNow", "2012-06-29", "sp500_vs_servicenow"),
    @("SPY", "S&P 500", "UBER", "Uber", "2019-05-10", "sp500_vs_uber"),
    @("SPY", "S&P 500", "SHOP", "Shopify", "2015-05-21", "sp500_vs_shopify"),

    @("SPY", "S&P 500", "PLTR", "Palantir", "2020-09-30", "sp500_vs_palantir"),
    @("SPY", "S&P 500", "COIN", "Coinbase", "2021-04-14", "sp500_vs_coinbase"),
    @("SPY", "S&P 500", "ARM", "Arm", "2023-09-14", "sp500_vs_arm"),
    @("SPY", "S&P 500", "SMCI", "Super Micro Computer", "2020-01-02", "sp500_vs_supermicro"),
    @("SPY", "S&P 500", "COST", "Costco", "1993-01-29", "sp500_vs_costco"),
    @("SPY", "S&P 500", "WMT", "Walmart", "1993-01-29", "sp500_vs_walmart"),
    @("SPY", "S&P 500", "HD", "Home Depot", "1993-01-29", "sp500_vs_homedepot"),
    @("SPY", "S&P 500", "MCD", "McDonald's", "1993-01-29", "sp500_vs_mcdonalds"),
    @("SPY", "S&P 500", "DIS", "Disney", "1993-01-29", "sp500_vs_disney"),
    @("SPY", "S&P 500", "KO", "Coca-Cola", "1993-01-29", "sp500_vs_cocacola"),

    @("SPY", "S&P 500", "PEP", "PepsiCo", "1993-01-29", "sp500_vs_pepsico"),
    @("SPY", "S&P 500", "NKE", "Nike", "1993-01-29", "sp500_vs_nike"),
    @("SPY", "S&P 500", "JPM", "JPMorgan Chase", "1993-01-29", "sp500_vs_jpmorgan"),
    @("SPY", "S&P 500", "GS", "Goldman Sachs", "1999-05-04", "sp500_vs_goldmansachs"),
    @("SPY", "S&P 500", "V", "Visa", "2008-03-19", "sp500_vs_visa"),
    @("SPY", "S&P 500", "MA", "Mastercard", "2006-05-25", "sp500_vs_mastercard"),
    @("SPY", "S&P 500", "XOM", "Exxon Mobil", "1993-01-29", "sp500_vs_exxon"),
    @("SPY", "S&P 500", "LLY", "Eli Lilly", "1993-01-29", "sp500_vs_lilly"),
    @("SPY", "S&P 500", "UNH", "UnitedHealth", "1993-01-29", "sp500_vs_unitedhealth"),
    @("SPY", "S&P 500", "BRK-B", "Berkshire Hathaway", "1996-05-09", "sp500_vs_berkshire"),

    # --------------------------------------------------------
    # PART 2: 60 comparisons between two popular stocks
    # --------------------------------------------------------

    @("AAPL", "Apple", "MSFT", "Microsoft", "1986-03-13", "apple_vs_microsoft"),
    @("AAPL", "Apple", "NVDA", "NVIDIA", "1999-01-22", "apple_vs_nvidia"),
    @("AAPL", "Apple", "AMZN", "Amazon", "1997-05-15", "apple_vs_amazon"),
    @("AAPL", "Apple", "GOOGL", "Alphabet", "2004-08-19", "apple_vs_alphabet"),
    @("AAPL", "Apple", "META", "Meta", "2012-05-18", "apple_vs_meta"),
    @("AAPL", "Apple", "TSLA", "Tesla", "2010-06-29", "apple_vs_tesla"),
    @("AAPL", "Apple", "NFLX", "Netflix", "2002-05-23", "apple_vs_netflix"),
    @("AAPL", "Apple", "AMD", "AMD", "1980-10-15", "apple_vs_amd"),
    @("AAPL", "Apple", "AVGO", "Broadcom", "2009-08-06", "apple_vs_broadcom"),
    @("AAPL", "Apple", "ORCL", "Oracle", "1986-03-12", "apple_vs_oracle"),

    @("MSFT", "Microsoft", "NVDA", "NVIDIA", "1999-01-22", "microsoft_vs_nvidia"),
    @("MSFT", "Microsoft", "AMZN", "Amazon", "1997-05-15", "microsoft_vs_amazon"),
    @("MSFT", "Microsoft", "GOOGL", "Alphabet", "2004-08-19", "microsoft_vs_alphabet"),
    @("MSFT", "Microsoft", "META", "Meta", "2012-05-18", "microsoft_vs_meta"),
    @("MSFT", "Microsoft", "TSLA", "Tesla", "2010-06-29", "microsoft_vs_tesla"),
    @("MSFT", "Microsoft", "NFLX", "Netflix", "2002-05-23", "microsoft_vs_netflix"),
    @("MSFT", "Microsoft", "AMD", "AMD", "1980-10-15", "microsoft_vs_amd"),
    @("MSFT", "Microsoft", "AVGO", "Broadcom", "2009-08-06", "microsoft_vs_broadcom"),
    @("MSFT", "Microsoft", "CRM", "Salesforce", "2004-06-23", "microsoft_vs_salesforce"),
    @("MSFT", "Microsoft", "ADBE", "Adobe", "1986-03-12", "microsoft_vs_adobe"),

    @("NVDA", "NVIDIA", "AMD", "AMD", "1999-01-22", "nvidia_vs_amd"),
    @("NVDA", "NVIDIA", "AVGO", "Broadcom", "2009-08-06", "nvidia_vs_broadcom"),
    @("NVDA", "NVIDIA", "QCOM", "Qualcomm", "1991-12-16", "nvidia_vs_qualcomm"),
    @("NVDA", "NVIDIA", "INTC", "Intel", "1993-01-29", "nvidia_vs_intel"),
    @("NVDA", "NVIDIA", "ARM", "Arm", "2023-09-14", "nvidia_vs_arm"),
    @("NVDA", "NVIDIA", "SMCI", "Super Micro Computer", "2020-01-02", "nvidia_vs_supermicro"),
    @("NVDA", "NVIDIA", "PLTR", "Palantir", "2020-09-30", "nvidia_vs_palantir"),
    @("NVDA", "NVIDIA", "TSLA", "Tesla", "2010-06-29", "nvidia_vs_tesla"),
    @("NVDA", "NVIDIA", "META", "Meta", "2012-05-18", "nvidia_vs_meta"),
    @("NVDA", "NVIDIA", "AMZN", "Amazon", "1999-01-22", "nvidia_vs_amazon"),

    @("AMZN", "Amazon", "WMT", "Walmart", "1997-05-15", "amazon_vs_walmart"),
    @("AMZN", "Amazon", "COST", "Costco", "1997-05-15", "amazon_vs_costco"),
    @("AMZN", "Amazon", "SHOP", "Shopify", "2015-05-21", "amazon_vs_shopify"),
    @("AMZN", "Amazon", "UBER", "Uber", "2019-05-10", "amazon_vs_uber"),
    @("AMZN", "Amazon", "ORCL", "Oracle", "1997-05-15", "amazon_vs_oracle"),
    @("GOOGL", "Alphabet", "META", "Meta", "2012-05-18", "alphabet_vs_meta"),
    @("GOOGL", "Alphabet", "NFLX", "Netflix", "2004-08-19", "alphabet_vs_netflix"),
    @("GOOGL", "Alphabet", "CRM", "Salesforce", "2004-08-19", "alphabet_vs_salesforce"),
    @("GOOGL", "Alphabet", "UBER", "Uber", "2019-05-10", "alphabet_vs_uber"),
    @("GOOGL", "Alphabet", "PLTR", "Palantir", "2020-09-30", "alphabet_vs_palantir"),

    @("META", "Meta", "SNAP", "Snap", "2017-03-02", "meta_vs_snap"),
    @("META", "Meta", "PINS", "Pinterest", "2019-04-18", "meta_vs_pinterest"),
    @("META", "Meta", "RDDT", "Reddit", "2024-03-21", "meta_vs_reddit"),
    @("TSLA", "Tesla", "RIVN", "Rivian", "2021-11-10", "tesla_vs_rivian"),
    @("TSLA", "Tesla", "NIO", "NIO", "2018-09-12", "tesla_vs_nio"),
    @("TSLA", "Tesla", "LCID", "Lucid", "2021-07-26", "tesla_vs_lucid"),
    @("NFLX", "Netflix", "DIS", "Disney", "2002-05-23", "netflix_vs_disney"),
    @("NFLX", "Netflix", "WBD", "Warner Bros Discovery", "2022-04-11", "netflix_vs_warnerbros"),
    @("DIS", "Disney", "CMCSA", "Comcast", "1993-01-29", "disney_vs_comcast"),
    @("KO", "Coca-Cola", "PEP", "PepsiCo", "1993-01-29", "cocacola_vs_pepsico"),

    @("V", "Visa", "MA", "Mastercard", "2008-03-19", "visa_vs_mastercard"),
    @("JPM", "JPMorgan Chase", "GS", "Goldman Sachs", "1999-05-04", "jpmorgan_vs_goldmansachs"),
    @("XOM", "Exxon Mobil", "CVX", "Chevron", "1993-01-29", "exxon_vs_chevron"),
    @("LLY", "Eli Lilly", "NVO", "Novo Nordisk", "2000-01-03", "lilly_vs_novo"),
    @("COST", "Costco", "WMT", "Walmart", "1993-01-29", "costco_vs_walmart"),
    @("HD", "Home Depot", "LOW", "Lowe's", "1993-01-29", "homedepot_vs_lowes"),
    @("CRM", "Salesforce", "NOW", "ServiceNow", "2012-06-29", "salesforce_vs_servicenow"),
    @("QCOM", "Qualcomm", "CSCO", "Cisco", "1991-12-16", "qualcomm_vs_cisco"),
    @("INTC", "Intel", "IBM", "IBM", "1993-01-29", "intel_vs_ibm"),
    @("ADBE", "Adobe", "ORCL", "Oracle", "1986-03-12", "adobe_vs_oracle")
)

# ============================================================
# Validation
# ============================================================

if ($Comparisons.Count -ne 100) {
    Write-Error "Expected exactly 100 comparisons, but found $($Comparisons.Count)."
    exit 1
}

$Names = @($Comparisons | ForEach-Object { $_[5] })

if (($Names | Select-Object -Unique).Count -ne 100) {
    Write-Error "Duplicate video directory names detected."
    exit 1
}

# Verify that each stock pair is unique regardless of ordering.
$PairKeys = @(
    $Comparisons | ForEach-Object {
        $tickers = @([string]$_[0], [string]$_[2]) | Sort-Object
        "$($tickers[0])_$($tickers[1])"
    }
)

if (($PairKeys | Select-Object -Unique).Count -ne 100) {
    Write-Error "Duplicate ticker combinations detected."
    exit 1
}

$Sp500Count = @(
    $Comparisons | Where-Object { $_[0] -eq "SPY" -or $_[2] -eq "SPY" }
).Count

if ($Sp500Count -ne 40) {
    Write-Error "Expected 40 S&P 500 comparisons, but found $Sp500Count."
    exit 1
}

if ($StartAt + $Count - 1 -gt $Comparisons.Count) {
    Write-Error "Requested range from #$StartAt for $Count comparisons exceeds the 100-comparison list."
    exit 1
}

$RunComparisons = @($Comparisons | Select-Object -Skip ($StartAt - 1) -First $Count)

Write-Host ""
Write-Host "Validated 100 unique stock comparisons." -ForegroundColor Green
Write-Host "S&P 500 comparisons: $Sp500Count"
Write-Host "Stock-versus-stock comparisons: $($Comparisons.Count - $Sp500Count)"
Write-Host "Comparisons selected: $($RunComparisons.Count) (starting at #$StartAt)"
Write-Host "Output: $OutputRoot"
Write-Host ""

# ============================================================
# Execution
# ============================================================

$SuccessCount = 0
$FailureCount = 0
$BatchManifest = Join-Path ([System.IO.Path]::GetTempPath()) (
    "stockCompare100-{0}.json" -f [guid]::NewGuid().ToString("N")
)
$BatchReport = Join-Path ([System.IO.Path]::GetTempPath()) (
    "stockCompare100-report-{0}.json" -f [guid]::NewGuid().ToString("N")
)
$BatchExitCode = 2

try {
    $BatchJobs = @(
        foreach ($Comparison in $RunComparisons) {
            [ordered]@{
                ticker_a = [string]$Comparison[0]
                label_a = [string]$Comparison[1]
                ticker_b = [string]$Comparison[2]
                label_b = [string]$Comparison[3]
                start_date = [string]$Comparison[4]
                video_name = [string]$Comparison[5]
                initial_investment = "10000"
            }
        }
    )
    $ManifestJson = ConvertTo-Json -InputObject $BatchJobs -Depth 4
    Set-Content -LiteralPath $BatchManifest -Value $ManifestJson -Encoding UTF8 -ErrorAction Stop

    $Arguments = @(
        "-u", "-m", "pipeline.stock_comparison_batch",
        "--manifest", $BatchManifest,
        "--output-root", $OutputRoot,
        "--report", $BatchReport,
        "--workers", [string]$RenderWorkers,
        "--frequency", $Frequency,
        "--background", $ChartBackground,
        "--content-scale", $ChartContentScale,
        "--audio",
        "--audio-dir", $AudioDirectory
    )

    & $Python @Arguments 2>&1 | Tee-Object -FilePath $LogFile -Append
    $BatchExitCode = $LASTEXITCODE

    if (-not (Test-Path -LiteralPath $BatchReport)) {
        throw "Batch runner did not create its result report (exit code $BatchExitCode)."
    }

    $BatchResult = Get-Content -LiteralPath $BatchReport -Raw |
        ConvertFrom-Json -ErrorAction Stop
    if ($BatchResult.total -ne $RunComparisons.Count) {
        throw "Batch report contains $($BatchResult.total) results; expected $($RunComparisons.Count)."
    }

    $SuccessCount = [int]$BatchResult.succeeded
    $FailureCount = [int]$BatchResult.failed
}
catch {
    Write-Error "Batch generation failed: $($_.Exception.Message)"
    $SuccessCount = 0
    $FailureCount = $RunComparisons.Count
}
finally {
    if (Test-Path -LiteralPath $BatchManifest) {
        Remove-Item -LiteralPath $BatchManifest -Force
    }
    if (Test-Path -LiteralPath $BatchReport) {
        Remove-Item -LiteralPath $BatchReport -Force
    }
}

# ============================================================
# Summary
# ============================================================

Write-Host "============================================================"
Write-Host "GENERATION COMPLETE"
Write-Host "============================================================"
Write-Host "Starting comparison: #$StartAt"
Write-Host "Total comparisons:  $($RunComparisons.Count)"
Write-Host "Successful:         $SuccessCount" -ForegroundColor Green
Write-Host "Failed:             $FailureCount" -ForegroundColor Red
Write-Host "Output directory:   $OutputRoot"
Write-Host "Log file:           $LogFile"
Write-Host "============================================================"

Add-Content $LogFile ""
Add-Content $LogFile "Finished: $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')"
Add-Content $LogFile "Success: $SuccessCount"
Add-Content $LogFile "Failed: $FailureCount"

if ($FailureCount -gt 0 -or $BatchExitCode -gt 1) {
    exit 1
}
