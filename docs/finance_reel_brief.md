# Brief: the short-form finance "facts" Reel this project makes

**Audience for this file:** an LLM (or a person) who will be given topic ideas and must turn
them into video specs for this program. Read it fully before proposing anything. It explains what
the video looks like, what the program can and cannot do, the hard limits, and the rules that keep
the numbers honest.

---

## 1. What we are making

A **short vertical (9:16) video, about 9-15 seconds, with optional music/narration**. The text on screen *is*
the content. One background video (or image) plays behind a readable text overlay.

Think of a social "quick facts" Reel: a question at the top, a one-line assumption, a short list of
labeled figures that appear one at a time, then a closing question that invites comments.

```
┌──────────────────────────┐
│  (status-bar area, empty)│  top ~10% kept clear
│                          │
│  How Much Do You Need    │  <- TITLE: a question, large bold white
│  to Retire?              │
│  Using a 4.7% withdrawal │  <- METHODOLOGY: one small line of assumptions
│  rate                    │
│ ┌──────────────────────┐ │
│ │ $30K/year     $638K  │ │  <- ROWS: label (left) + value (right, accent colour)
│ │ $40K/year     $851K  │ │     appear one at a time, ~0.9 s apart
│ │ $50K/year     $1.06M │ │
│ │ ...                  │ │
│ └──────────────────────┘ │
│ ┌──────────────────────┐ │
│ │ How much annual       │ │  <- CTA: closing question in a bordered box
│ │ retirement income...? │ │
│ └──────────────────────┘ │
│  Hypothetical illustration│  <- DISCLOSURE: small, visible the whole time
│  for educational ...      │
│  (platform buttons area,  │  bottom ~20% kept clear
│   empty)                  │
└──────────────────────────┘
        background video visible behind everything, under a subtle dark scrim
```

Reveal order: **title -> methodology -> rows one by one -> CTA**, then everything holds on screen
for about 3 seconds. The disclosure is visible from the start. Animation is only a short fade and
slide; nothing flashes or races.

The optional **editorial** preset adds a short eyebrow label, a large left-aligned headline and
ruled comparison rows. Start with 4-6 rows, not the maximum. Explicit title line breaks are
preserved. Classic remains the default for old specs.

### Typical length

`duration = 2.2 s + 0.9 s x (rows - 1) + 1.4 s + 0.35 s + 3.0 s`

| Rows | ~Length |
|---|---|
| 4 | 9.6 s |
| 6 | 11.5 s |
| 8 | 13.3 s |
| 12 | 16.9 s |

6-8 rows is the sweet spot: dense enough to feel useful, short enough to read.

---

## 2. What the program does (and does not do)

**Does:**
- Takes one structured spec (YAML or JSON) and renders a finished MP4 at 1080x1920, 30 fps.
- Calculates figures itself, deterministically, for supported formulas, and records the inputs,
  formula, assumptions and rounding next to the video (`final/provenance.json`).
- Crops/scales/loops one background clip to fill the frame; keeps text inside safe margins;
  darkens the footage just enough for legibility while keeping it visible.
- Validates the result (resolution, text inside the safe area, figures re-derived from the spec,
  background file unchanged, provenance recorded).
- Supports local licensed music, fades, narration ducking and local font files copied into the job.
- Writes a publishing draft from the actual reel: title, description, relevant hashtags,
  assumptions, source citations and supplied credits. No automatic publishing or SEO guarantees.

**Does not:**
- Download, scrape, search for, or generate background footage. **The user supplies a local
  video/image** and states its licence in the spec.
- Generate speech or music. Voiceover/music must be local supplied files; the default is silent.
- Make charts, graphs, icons, emojis, multi-scene edits, cuts, transitions, stickers, per-word
  captions, multiple columns, or more than one background.
- Use an LLM to compute or verify numbers. **LLMs never supply financial figures** (see section 6).
- Give personalised advice or promise outcomes.

Separately, the repo also has an older **explainer** format (transcripts -> script -> narrated
landscape video). That is a different path and is not what this brief is about.

---

## 3. The spec you produce

One YAML (or JSON) file per video. All fields:

| Field | Required | Limits / rules |
|---|---|---|
| `schema_version` | no | `1` |
| `template` | no | `finance_facts` |
| `title` | **yes** | 3-90 chars. Make it a **question** or a hook. Keep ~60 or fewer so it fits in 2 lines |
| `methodology` | manual: **yes**; calculated: optional | 3-160 chars. The assumptions line. For calculated figures **leave it out**: it is generated from the assumptions. If you write one it must still contain every assumption (rate, years, etc.) |
| `figures` | **yes** | One of the kinds in section 4. 1-12 rows (aim for 5-8) |
| `cta` | **yes** | 3-140 chars. A closing **question** that invites comments. Aim for 90 or fewer |
| `disclosure` | **yes** | 10-220 chars. Must say the figures are **hypothetical** or **illustrative** unless they are sourced (section 6) |
| `background` | **yes** | `path` (local file), `origin`, `license` (required text), optional `attribution` |
| `voiceover` | no | `path` to a user-supplied audio file. Leave out for the default text-only reel |
| `music` | no | Local `path`, required `license`, optional `attribution`, `start_s`, `gain_db`, `fade_in_s`, `fade_out_s` |
| `style` | no | `preset: classic` (default) or `editorial`; optional eyebrow, alignment, row format, font files and sizes |
| `pacing` | no | Optional `row_interval_s` (0.6-3) and `end_hold_s` (2-10), in seconds |
| `provenance` | no | `verification: illustrative` (default) or `sourced`, `sources[]`, `notes` |
| `accent_color` | no | `#RRGGBB` for the value text and accents. Default warm yellow `#FFD166` |

`background.origin` is one of `user_supplied`, `licensed_project_asset`, `generated_placeholder`.

### Writing guidance (what makes these perform and stay safe)

- **Title:** a question people have a gut answer to ("How Much Do You Need to Retire?", "What Does
  $10,000 Become in 30 Years?"). No clickbait promises.
- **Rows:** short labels, scannable values. Labels <= 32 chars, values <= 16 chars. Keep the label
  pattern identical across rows (all `$X/year`, all `After N years`, all `Start at N`).
- **CTA:** an open question the viewer answers about *themselves* ("How much annual retirement
  income would make you feel financially secure?"). Not a command to buy or invest.
- **Disclosure:** short and plain: "Hypothetical illustration for educational purposes only. Not
  financial advice."
- **Tone:** neutral, educational. Describe what a calculation shows under stated assumptions.

### Forbidden wording (the validator rejects it in title, CTA, methodology and manual rows)

`guarantee`, `risk-free`, `can't lose`, `cannot lose`, `sure thing`, `get rich quick`,
`you must invest`, `you should invest`, `you should buy`, `you should sell`.
(The disclosure may say "no guarantee"; the other text may not promise outcomes.)

---

## 4. Figure kinds

Prefer a **calculated** kind whenever one fits. They are computed with exact decimal arithmetic and
fully documented. You supply only the assumptions.

### 4.1 `withdrawal_rate`: "How much do you need to retire?"

Capital needed to fund each annual income: `annual_income / (withdrawal_rate_pct / 100)`.

```yaml
figures:
  kind: withdrawal_rate
  withdrawal_rate_pct: 4.7          # an assumption, not a researched fact
  annual_incomes: [30000, 40000, 50000, 60000, 70000, 80000, 100000, 120000]
```
Rows display as `$30K/year -> $638K`, `$50K/year -> $1.06M`. Auto methodology:
"Using a 4.7% withdrawal rate".

### 4.2 `compound_growth`: "What does a lump sum become?"

`value = initial_amount x (1 + r/m)^(m x years)`. No added contributions.

```yaml
figures:
  kind: compound_growth
  initial_amount: 10000
  annual_return_pct: 7              # nominal annual rate, held constant
  compounding: annual               # annual | semiannual | quarterly | monthly
  years: [5, 10, 15, 20, 25, 30]
```
Rows display as `After 10 years -> $19,672`. All four assumptions are **required**.

### 4.3 `age_target`: "What would it take to reach a target by a certain age?"

The level contribution per period needed from each starting age to reach a target by a target age.
**Every assumption is required and has no default.**

```yaml
figures:
  kind: age_target
  target_amount: 1000000
  target_age: 65
  starting_ages: [25, 30, 35, 40, 45, 50]
  annual_return_pct: 7
  contribution_frequency: monthly   # annual | semiannual | quarterly | monthly
  compounding: monthly              # annual | semiannual | quarterly | monthly
  contribution_timing: end          # end (default) | start of each period
  label_template: "Start at {age}"  # optional, <= 24 chars, must contain {age}
```
Rows display as `Start at 25 -> $381/mo` (rounded **up** so the target is actually met). Starting
ages must be below `target_age`. **Never** present a withdrawal-rate table as "what to invest at
each age"; use this kind for age-based contribution questions.

### 4.4 `manual`: typed-in rows

Use only when the numbers come from a human-provided source or a plain rule of thumb, never from
the LLM's memory. `methodology` is **required**.

```yaml
methodology: The 50/30/20 budgeting guideline
figures:
  kind: manual
  rows:
    - {label: "Needs",   value: "50%"}
    - {label: "Wants",   value: "30%"}
    - {label: "Savings", value: "20%"}
```
Manual figures are only as trustworthy as their provenance (section 6).

### Topics that need a new calculator

Additional supported calculated kinds:

| Kind | Required inputs | Output |
|---|---|---|
| `contribution_growth` | `contribution_amount`, `contribution_frequency`, `annual_return_pct`, `compounding`, `years[]`; optional `contribution_timing: end` or `start` | Future value of level contributions from a zero balance |
| `loan_payment` | `principal`, `term_years`, `annual_rates_pct[]` | Monthly amortized principal-and-interest payment at each fixed rate |
| `inflation_erosion` | `amount`, `inflation_rate_pct`, `years[]` | Purchasing power in today's dollars; cash earns no interest |
| `doubling_time` | `annual_returns_pct[]` | `ln(2) / ln(1 + rate/100)`, annually compounded, rounded to 0.1 years |

These are hypothetical scenarios, not measured rates or forecasts. Loan figures exclude taxes,
insurance and fees. Inflation is assumed constant. Contribution growth excludes taxes, fees
and inflation. Include those limitations in the disclosure.

If a topic needs math the supported kinds do not do (extra-payment loan payoff,
debt snowball, tax brackets...), **do not approximate it in your head and type the
results as manual rows.** Say "needs a new calculator kind", describe the formula and the
assumptions it would require, and let the developer add it.

---

## 5. Background video

One clip, supplied by the user as a local file:

- **Vertical 9:16 is best** (e.g. 1080x1920 or 2160x3840). Landscape works but is centre-cropped.
- **At least as long as the reel (aim for 12-20 s).** Shorter clips loop; a 4 s clip loops
  ~3 times and the repetition is noticeable.
- Formats: `.mp4 .mov .m4v .webm` (video), `.jpg .jpeg .png .webp` (image). Up to 500 MB.
- The clip's own audio is discarded.
- **Good backgrounds:** calm, slow, low-detail footage with some visual interest that supports the
  topic's mood: ocean/beach, a home or garden, city skyline at dusk, coffee/desk with no faces,
  hands writing, nature, a car driving on a road, a retirement-style lifestyle scene.
- **Avoid:** fast cuts, flashing, very bright/white scenes, busy high-contrast detail in the
  bottom third (the small disclosure sits there), on-screen text or logos in the footage, music
  you do not own, recognisable people or brands you do not have rights to.
- **Licence:** the user must have the right to use the clip. The spec records it:

```yaml
background:
  path: C:\Users\tyler\Downloads\clip.mp4   # or a path relative to the spec file
  origin: user_supplied                     # or licensed_project_asset
  license: Pexels License                   # the exact licence on the page it came from
```

Suggested match of mood to topic: beach/ocean -> retirement, freedom, long-term growth;
home interior/exterior -> mortgages, saving for a house, budgeting; city/skyline -> investing,
careers, income; nature/time-lapse -> compounding, patience, long horizons.

---

## 6. Financial correctness rules (non-negotiable)

1. **No invented figures.** The LLM must never produce statistics, rates of return, prices,
   averages, rankings or "facts" from memory and present them as true. Facts and rates are either
   (a) clearly labelled assumptions inside a calculated kind, or (b) supplied by a human with a
   citation.
2. **Default to `verification: illustrative`.** That is the honest label for assumptions such as
   "7% return" or "4.7% withdrawal rate". With this setting the disclosure **must** contain the word
   "hypothetical" or "illustrative".
3. **`verification: sourced`** is allowed only if a human has provided the real source. Then
   `sources` needs at least one entry with `title`, a real `http(s)` `url`, and `as_of` (the date
   of the data). Never fabricate a citation or URL.
   ```yaml
   provenance:
     verification: sourced
     sources:
       - title: <exact title of the document or dataset>
         url: https://...
         as_of: 2024-06-30
         publisher: <optional>
   ```
4. **Show the assumptions on screen.** For calculated kinds this is automatic. Never remove them.
5. **No personalised advice, no guarantees.** Describe what a calculation shows under assumptions.
   Do not tell viewers what to buy, sell or invest in.
6. **Be honest about limits.** Constant returns, no taxes, fees or inflation. The disclosure should
   say returns vary (the compound example adds: "real returns vary and can be negative").
7. If you are unsure whether a number is real, make it an assumption, or leave the topic out and
   say why.

---

## 7. Topic ideas: what to hand back

When given a topic, respond with:

1. **Fit:** which figure kind fits (or "needs a new calculator" / "needs a human-sourced fact").
2. **A complete spec** following section 3, ready to save as `.yaml`.
3. **Suggested background:** one sentence describing the ideal footage and why.
4. **Open questions for the human:** anything that needs a real source or a decision (e.g. which
   return rate to assume).

### Topics that fit today

| Topic | Kind | Notes |
|---|---|---|
| How much do you need to retire? | `withdrawal_rate` | vary the rate and incomes |
| What does $X become over N years? | `compound_growth` | vary return, compounding, years |
| What if you started investing at 25 vs 45? | `age_target` | target, age, return all explicit |
| How much monthly to reach $X by age Y? | `age_target` | monthly contributions |
| Compounding frequency compared | `compound_growth` | one spec per frequency, same other inputs |
| A budgeting rule of thumb (50/30/20) | `manual` | illustrative, methodology names the rule |

### Output template

```yaml
schema_version: 1
template: finance_facts

title: <question, <= 60 chars>
# methodology: omit for calculated kinds

figures:
  kind: <a supported calculated kind, or manual>
  # ...assumptions for that kind (all required ones)...

cta: <closing question about the viewer, <= 90 chars>
disclosure: >-
  Hypothetical illustration for educational purposes only. Not financial advice.

background:
  path: <local file the user will supply>
  origin: user_supplied
  license: <exact licence text the user confirms>

provenance:
  verification: illustrative
  notes: <what is assumed and why>
```

---

## 8. How it is run (for context)

```powershell
# Preferred single-video workflow: render + QA + publishing draft + cover + contact sheet
python -m pipeline.reel --spec my_spec.yaml

# Or run stages individually
python -m pipeline.jobs create --format finance_facts --reel-spec my_spec.yaml
python -m pipeline.run --job <job_id>
python -m pipeline.preview --job <job_id>     # optional key frames + contact sheet
```

The finished video is `workspace/jobs/<job_id>/final/video.mp4`; the calculation and licence record
is `final/provenance.json`. Invalid specs fail immediately with a message that names the field, so
if a validation message appears, fix that field and retry.

Working examples are in `examples/finance/` (`retirement_withdrawal.yaml`,
`compound_growth_example.yaml`, `age_target_example.yaml`, `editorial_compound.yaml`).
Full reference: `docs/pipeline.md`
("Finance reels").

### Editorial styling and music

```yaml
title: |-
  What could
  $10,000 become?
style:
  preset: editorial
  eyebrow: THE COMPOUND EFFECT
  title_align: left         # left | center
  row_format: rules         # rules | panels
  title_size: 92            # 48-110 reference pixels at 1080 width
  row_size: 54              # 36-64 reference pixels
  # font_regular: Local-Regular.ttf
  # font_bold: Local-Bold.ttf
  # font_license: <required when supplying fonts>
pacing:
  row_interval_s: 1.2
  end_hold_s: 4
# music:
#   path: music.wav
#   license: <actual licence statement>
#   attribution: <required credit>
#   start_s: 0
#   gain_db: -12
#   fade_in_s: 0.5
#   fade_out_s: 1.5
```

Local font/music paths resolve relative to the spec. No downloads or licence checks are
performed; the owner must confirm usage rights. Music loops to the reel duration, fades and is
ducked under any supplied voiceover. Listen to the finished mix before publishing.
Optional `background.crop_x`/`crop_y` values (0..1, default 0.5) adjust framing without stretching.
Only explicit line breaks and the supported style fields are interpreted; no Markdown/HTML
formatting. Editorial fails rather than shrinking text below 85%; reduce density if it fails.
The generated cover is a suggested frame; platform thumbnail selection and safe areas vary.
