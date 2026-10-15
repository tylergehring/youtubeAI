# Local historical stock comparison

This workflow produces one reusable, typed two-ticker comparison from Yahoo Finance daily
historical data. It calculates price-only hypothetical balances and stores completed year-end
checkpoints. The video starts directly on a minimal chart with a white background by default;
`--background black` selects a black chart with a contrasting palette. Its date axis extends
with the timeline, and its logarithmic value axis starts at the initial investment and grows to
the highest value visible so far. Values below the initial investment are clipped at the
baseline. Line-end values follow the animated traces, without bottom subtitles. Audio is
optional and disabled by default. It validates a **1080×1920 H.264 MP4** and does not call an
LLM, use paid generation, create narration, upload, publish, or schedule. Output directories
are never overwritten.

## Install and run

Install the optional market-data dependency and the development tools (the latter includes a
bundled FFmpeg executable):

```powershell
pip install -e ".[market-data,dev]"
$env:FFMPEG_PATH = (& .\.venv\Scripts\python.exe -c "import imageio_ffmpeg; print(imageio_ffmpeg.get_ffmpeg_exe())")
```

Run from the repository root with a **new** output directory:

```powershell
.\.venv\Scripts\python.exe -m pipeline.stock_comparison `
    --ticker-a AMZN --label-a Amazon `
    --ticker-b WMT --label-b Walmart `
    --start-date 1997-05-15 `
    --expected-first-date-a 1997-05-15 `
    --initial-investment 10000 `
    --out workspace\stock_comparisons\amzn_wmt_first_trade_1997
```

Add `--background black` to render the black theme; white is the default.

Add `--audio` to randomly select one `.wav`, `.mp3`, `.m4a`, or `.aac` file from
`C:\Users\tyler\Downloads\AiYoutube\audio` and include it as background music. Use
`--audio-dir PATH` to choose a different folder. The selected track is copied into the output,
looped or trimmed to the video duration, faded at both ends, normalized toward -18 LUFS,
limited, and encoded as AAC. Missing folders or folders without supported tracks fail
explicitly. Without `--audio`, the video remains silent and the audio folder is not accessed.

`--ticker-a`, `--ticker-b`, `--start-date`, and `--out` are required. Labels default to ticker
symbols; `--initial-investment` defaults to 10,000. An optional inclusive `--through-date`
caps the history. Otherwise, the workflow uses a conservative 4:15 p.m. America/New_York
cutoff for a completed weekday bar and excludes an in-progress current-day bar.

When `--expected-first-date-a` is supplied, ticker A is fetched from 1900-01-01 (inclusive)
and its first valid `Close` must equal the expected date. This verifies that the requested
start is also the first observed date in the fetched history; it is not a general guarantee
that a vendor's archive is complete.

## Data and calculation method

The workflow uses the installed yfinance `PriceHistory.history` documentation and API:

- The installed yfinance **0.2.66** method documents `start` as inclusive and `end` as
  exclusive; the implementation records the exclusive `end` passed to the request.
- It requests `interval="1d"`, `auto_adjust=False`, `actions=False`, `repair=False`,
  `keepna=True`, and `raise_errors=True`. In that API, `auto_adjust=True` would adjust OHLC
  using adjusted-close data. This workflow explicitly selects the historical `Close` field
  instead of `Adj Close`.
- Yahoo's historical `Close` values reflect stock splits while excluding dividend
  adjustments; `Adj Close` includes dividend adjustments. The chosen series therefore
  represents price-only movement (with split-adjusted shares), not total return. Dividends
  and dividend reinvestment are excluded. The yfinance project is an independent,
  unofficial Yahoo Finance client—not an official Yahoo product.
- Start with an equal hypothetical lump sum in each ticker on the first common observed date.
  For each date and ticker:

  `hypothetical value = initial investment × that date's Close ÷ entry-date Close`

  Fractional shares are assumed; results use Decimal arithmetic and are rounded half-up to
  cents in the data files. The animated screen shows compact, rounded dollar amounts.
- Only exact dates with finite, positive closes for both tickers are retained. Missing close
  rows are counted and omitted. The workflow never forward-fills, interpolates, or invents a
  price. Year-end values use the last common trading observation in each completed calendar
  year. The graph reveals only observations reached so far; its x range follows the latest
  visible date, and its log-scale y range stays anchored at the initial investment while its
  upper bound follows the highest visible value, never a future point. Values below the initial
  investment are clipped at the baseline. Equal vertical distances represent equal percentage
  changes, not equal dollar changes.
- Values are nominal, not inflation-adjusted. The calculation omits fees, taxes, execution
  differences, and cash distributions. It does not represent a recommendation or predict
  future performance. Yahoo data may be corrected after retrieval.

The `history` signature, parameter documentation, and implementation were inspected from the
installed yfinance 0.2.66 package (`yfinance.scrapers.history.PriceHistory.history`); source is
also available at
[yfinance's history implementation](https://github.com/ranaroussi/yfinance/blob/main/yfinance/scrapers/history.py).
The data source links and exact request parameters are stored with every run.

## Output and review

Each successful run saves to its new directory beneath ignored `workspace/stock_comparisons/`:

- `video.mp4` — rendered MP4, silent unless `--audio` is enabled.
- `cover.png`, `contact_sheet.png`, and `review_frames/` — actual frames decoded from the
  encoded MP4 with FFmpeg, not just pre-render storyboards.
- `audio_track.*` — a copy of the randomly selected music file, when audio is enabled.
- `comparison.json`, `daily_values.csv`, and `year_end_checkpoints.csv` — calculation inputs
  and outputs, including the daily observations used.
- `acquisition.json` and `methodology.md` — source, API parameters, missing-row policy,
  attribution, and limitations.
- `review_summary.json` and `run_state.json` — technical QA and `awaiting_human_review` state.

Technical validation checks H.264 video presence, the expected audio-stream state, and—when
music is enabled—a non-silent mean volume above -60 dB. It also checks portrait dimensions,
frame rate, duration, and successful decoding of review frames. The review summary records the
selected track, its copied snapshot, and SHA-256 hash. Inspect the contact sheet, encoded video,
calculations, attribution, and framing personally before any external use.
Technical QA does not establish source-data rights, factual completeness, originality,
platform-policy compliance, or editorial approval.
