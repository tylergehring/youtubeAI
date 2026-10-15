```powershell
$ErrorActionPreference = "Continue"

# ============================================================
# CONFIGURATION
# ============================================================

$Python = ".\.venv\Scripts\python.exe"
$OutputRoot = "C:\user\tyler\downloads\compare100stocks"
$LogFile = Join-Path $OutputRoot "generation.log"

# Fixed starting point requested:
# October 15, 2026, 12:58 PM Pacific Time.
#
# This script uses the computer's local time zone.
$StartTime = [datetime]::new(2026, 10, 15, 12, 58, 0)

# ============================================================
# To Run:
# ============================================================
#
# pip install -e ".[market-data,dev]"
#
# $env:FFMPEG_PATH = (& .\.venv\Scripts\python.exe -c "import imageio_ffmpeg; print(imageio_ffmpeg.get_ffmpeg_exe())")
#
# Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
#
# .\stockCompare100.ps1
#

# ============================================================
# PRE-FLIGHT CHECKS
# ============================================================

if (-not (Test-Path $Python)) {
    Write-Error "Python executable not found: $Python"
    Write-Error "Run this script from your repository root."
    exit 1
}

# Avoid mixing this batch with previous outputs.
if (Test-Path $OutputRoot) {
    $ExistingItems = @(Get-ChildItem -Path $OutputRoot -Force)

    if ($ExistingItems.Count -gt 0) {
        Write-Error "Output directory is not empty: $OutputRoot"
        Write-Error "Move or remove existing contents before running."
        exit 1
    }
}
else {
    New-Item -ItemType Directory -Path $OutputRoot -Force |
        Out-Null
}

"Stock comparison batch started: $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')" |
    Set-Content $LogFile

# ============================================================
# 100 STOCK COMPARISONS
#
# Format:
# TickerA, LabelA, TickerB, LabelB, StartDate, VideoName
# ============================================================

$Comparisons = @(

    # --------------------------------------------------------
    # PART 1: S&P 500 ETF VS. 40 POPULAR STOCKS
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
    # PART 2: 60 COMPARISONS BETWEEN TWO INDIVIDUAL STOCKS
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
    @("NVDA", "NVIDIA", "AVGO", "Broadcom", "2009-08-06", "nvidia_vs_broadcom_2"),

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
# VALIDATION
# ============================================================

if ($Comparisons.Count -ne 100) {
    Write-Error "Expected 100 comparisons; found $($Comparisons.Count)."
    exit 1
}

$Names = @($Comparisons | ForEach-Object { [string]$_.Get(5) })

if (($Names | Select-Object -Unique).Count -ne 100) {
    Write-Error "Duplicate video directory names detected."
    exit 1
}

$PairKeys = @(
    $Comparisons | ForEach-Object {
        $Pair = @([string]$_[0], [string]$_[2]) | Sort-Object
        "$($Pair[0])_$($Pair[1])"
    }
)

if (($PairKeys | Select-Object -Unique).Count -ne 100) {
    Write-Error "Duplicate ticker combinations detected."
    exit 1
}

$Sp500Count = @(
    $Comparisons | Where-Object {
        $_[0] -eq "SPY" -or $_[2] -eq "SPY"
    }
).Count

if ($Sp500Count -ne 40) {
    Write-Error "Expected 40 S&P 500 comparisons; found $Sp500Count."
    exit 1
}

Write-Host "Validated $($Comparisons.Count) comparisons." `
    -ForegroundColor Green

# ============================================================
# CREATE PUBLISHING SCHEDULE
# ============================================================

# First scheduled slot after the specified starting time.
$PublishTime = $StartTime.Date.AddHours(17)

if ($StartTime.Hour -ge 17) {
    $PublishTime = $StartTime.Date.AddDays(1).AddHours(5)
}

# ============================================================
# EXECUTION
# ============================================================

$SuccessCount = 0
$FailureCount = 0
$Index = 0

foreach ($Comparison in $Comparisons) {

    $Index++

    $TickerA = [string]$Comparison[0]
    $LabelA = [string]$Comparison[1]
    $TickerB = [string]$Comparison[2]
    $LabelB = [string]$Comparison[3]
    $HistoricalStartDate = [string]$Comparison[4]
    $VideoName = [string]$Comparison[5]

    $OutputDirectory = Join-Path $OutputRoot $VideoName

    # ISO-style local timestamp.
    $PublishAt = $PublishTime.ToString("yyyy-MM-ddTHH:mm:ss")

    Write-Host ""
    Write-Host "[$Index/100] $LabelA vs $LabelB" `
        -ForegroundColor Cyan

    Write-Host "  Historical start: $HistoricalStartDate"
    Write-Host "  Publish at:       $($PublishTime.ToString('yyyy-MM-dd h:mm tt'))"
    Write-Host "  Output:           $OutputDirectory"

    $Arguments = @(
        "-m", "pipeline.stock_comparison",
        "--ticker-a", $TickerA,
        "--label-a", $LabelA,
        "--ticker-b", $TickerB,
        "--label-b", $LabelB,
        "--start-date", $HistoricalStartDate,
        "--initial-investment", "10000",
        "--out", $OutputDirectory,
        "--publish",
        "--publish-at", $PublishAt
    )

    # Run one comparison.
    $RunOutput = & $Python @Arguments 2>&1
    $ExitCode = $LASTEXITCODE

    $RunOutput | Out-File `
        -FilePath $LogFile `
        -Append `
        -Encoding utf8

    if ($ExitCode -eq 0) {

        $SuccessCount++

        Write-Host "  SUCCESS" -ForegroundColor Green

        Add-Content $LogFile `
            "SUCCESS: $VideoName | Publish: $PublishAt"
    }
    else {

        $FailureCount++

        Write-Host "  FAILED: exit code $ExitCode" `
            -ForegroundColor Red

        Add-Content $LogFile `
            "FAILED: $VideoName | Publish: $PublishAt | Exit: $ExitCode"
    }

    # Advance to the next 5 AM / 5 PM slot.
    if ($PublishTime.Hour -eq 17) {
        $PublishTime = $PublishTime.Date.AddDays(1).AddHours(5)
    }
    else {
        $PublishTime = $PublishTime.Date.AddHours(17)
    }
}

# ============================================================
# SUMMARY
# ============================================================

Write-Host ""
Write-Host "============================================================"
Write-Host "GENERATION COMPLETE"
Write-Host "============================================================"
Write-Host "Total comparisons: $($Comparisons.Count)"
Write-Host "S&P 500 comparisons: $Sp500Count"
Write-Host "Stock vs. stock comparisons: $($Comparisons.Count - $Sp500Count)"
Write-Host "Successful: $SuccessCount"
Write-Host "Failed: $FailureCount"
Write-Host "Output directory: $OutputRoot"
Write-Host "Log file: $LogFile"
Write-Host "============================================================"

Add-Content $LogFile ""
Add-Content $LogFile "Finished: $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')"
Add-Content $LogFile "Success: $SuccessCount"
Add-Content $LogFile "Failed: $FailureCount"

if ($FailureCount -gt 0) {
    exit 1
}
```
