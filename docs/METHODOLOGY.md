# Methodology

How the models were chosen, how they are prompted, how they are scored, and what
this benchmark cannot tell you.

---

## 1. Model selection

The brief named `gemini-1.5-flash`, `claude-3-haiku-20240307` and `gpt-4o-mini`.
Those generations have since been retired or deprecated by their providers, so
code written against those exact strings returns a 404 on its first call. Rather
than ship a codebase that cannot run, each is replaced by **the current model in
the same price and latency tier** from the same provider:

| Role in the comparison | Model | Provider | In $/Mtok | Out $/Mtok | Why it is here |
|---|---|---|---|---|---|
| Cost floor | `gemini-2.5-flash` | Google | 0.30 | 2.50 | Free tier, no card required |
| Accuracy reference | `claude-haiku-4-5` | Anthropic | 1.00 | 5.00 | Most willing to return `null` rather than guess |
| Ecosystem baseline | `gpt-5-mini` | OpenAI | 0.25 | 2.00 | The default most teams reach for first |
| Latency reference | `qwen/qwen3.6-27b` | Groq | 0.60 | 3.00 | Fastest serving; open weights |
| Serving control | `Llama-4-Maverick-17B-128E` | SambaNova | 0.63 | 1.80 | Second open-weights datapoint |
| Document specialist | `pixtral-12b` | Mistral | 0.15 | 0.15 | Trained for document understanding specifically |
| Generation control | `llama-3.2-11b-vision` | NVIDIA NIM | 0.06* | 0.06* | Older, smaller Llama against SambaNova's Llama 4 |
| Training-mix control | `kimi-k2.6` | Moonshot | 0.60* | 2.50* | Chinese/English document-heavy corpus, not Western-web-heavy |

\* NVIDIA meters in credits rather than publishing a per-token rate, so its
figures are estimates from what the same open weights cost elsewhere;
`MODEL_PRICE_OVERRIDES` makes them exact.

Eight providers, five free and one on trial credits. Which of them a given deployment actually offers is an allowlist (`ENABLED_PROVIDERS`), not a code change: the point of building a harness rather than a one-off script is that swapping the field of competitors costs one line. The open-weight three are not filler: running
Llama 4 on SambaNova alongside Qwen on Groq separates *model* quality from
*serving* quality, which a benchmark limited to one vendor per model cannot do.
Pixtral is the only model here trained with document understanding as a primary
objective rather than as a by-product of general vision, so it is the most
likely to behave differently on handwriting. Running Llama 3.2 11B on NVIDIA
against Llama 4 Maverick on SambaNova isolates a generational delta within one
model family, which is the cleanest available read on how much recent progress
in handwriting OCR came from scale versus from data.

The substitution is a one-line `.env` change, not a code change: every
`*_MODEL` variable is configuration, and `scripts/list_models.py` asks each
provider what it currently serves. That was
the actual design goal — a vision model that exists today is a 404 in eighteen
months, and a benchmark harness that has to be edited to survive a deprecation
is a harness nobody re-runs.

All three are the *small* tier of their family. On handwriting, the frontier
models are meaningfully better, but a bookkeeping pipeline processing thousands
of bills a month cannot afford them, so the interesting question is which cheap
model is good enough — not which expensive model is best.

---

## 2. Prompt engineering

One prompt is shared by all three providers (`app/utils/constants.py`). Sharing
it is not laziness: per-model prompt tuning would confound the comparison, since
a measured accuracy gap could then reflect prompt effort rather than model
capability.

Four decisions carry most of the weight:

**Null is explicitly rewarded.** The prompt states that a `null` is scored as an
honest miss while a fabrication is scored as a wrong answer *and is strictly
worse*. Vision models default to producing plausible output; on a smudged total
that means inventing a number. For an accounting pipeline, a blank field routes
to human review while a confident wrong number posts to the ledger.

**Day-first dates are stated, not assumed.** `03/04/2024` is 3 April in India and
3 March nowhere. Models trained predominantly on US data reliably transpose it.
One sentence in the prompt is far cheaper than post-hoc correction.

**Domain framing over generic OCR.** "Indian handwritten bills — kirana stores,
chemists, auto-rickshaw receipts" plus the physical context (ballpoint on a
rubber-stamped pad, mixed Devanagari and English) measurably reduces attempts to
impose a formal-invoice structure that these documents do not have.

**"Grand total, not a line item"** is called out because it is the single most
common extraction error: models grab the largest visible number or the last
line, which on a bill with a discount row is the subtotal.

Structured output is requested at the decoder where available
(`response_mime_type="application/json"` for Gemini, `response_format` for
OpenAI) rather than relying on the prompt alone. Anthropic has no equivalent
flag, so `parse_llm_json` repairs output in three escalating passes: strip
markdown fences, slice the outermost braces, remove trailing commas. Penalising
a model for packaging rather than comprehension would measure the wrong thing.

---

## 3. The evaluation rubric

Exact-match scoring is the wrong instrument for handwriting. `Sharma Genral
Store` versus `Sharma General Store` is a dropped `e` — a human bookkeeper
accepts it without looking up. `Verma Medicals` is a different shop. Exact
matching scores both 0 and reports the two models as equally bad, which is
false.

Every field is therefore scored independently on a documented scale:

| Outcome | Score | `match_type` |
|---|---|---|
| Identical after normalisation | 1.0 | `exact` |
| `thefuzz.ratio` ≥ 90 | 0.9 | `fuzzy` |
| `thefuzz.ratio` ≥ 70 | 0.7 | `partial` |
| `thefuzz.ratio` < 70 | 0.0 | `missing` |
| Both null | 1.0 | `not_applicable` |
| Expected a value, model returned null | 0.0 | `missing` |
| Expected null, model returned a value | 0.0 | `missing` |

Four refinements matter more than the thresholds:

**Dates and amounts never touch string matching.** Both sides are parsed to
`datetime.date` / `Decimal` and compared semantically. `15/03/2024`,
`2024-03-15` and `15 Mar 2024` all score 1.0 — we are grading reading
comprehension, not output formatting. Amounts get a 1% tolerance band scoring
0.9, which covers a misread final digit or a dropped paisa while still failing
a model that read the subtotal instead of the total.

**Hallucination scores 0.0 with no partial credit.** Inventing a bill number the
bill does not have is the most damaging failure mode for a bookkeeping pipeline,
because it is invisible downstream. Recording a value where truth is null is
scored as a miss and labelled as a hallucination in the notes.

**GSTINs are compared as identifiers, not prose.** When both sides contain a
well-formed 15-character GSTIN they are compared exactly. A GSTIN with one wrong
character is useless, so `fuzz.ratio = 93` must not earn 0.9. Free-text tax
descriptions still fall back to fuzzy matching.

**Normalisation is deliberately conservative.** Case, accents, whitespace and
edge punctuation are folded; internal punctuation is not. Stripping all
punctuation would let `S.G.Store` match `SG Store` at 100% and inflate every
score.

`overall_accuracy` is the unweighted mean of the six fields. Weighting `amount`
higher would produce a prettier headline number and hide a model that is losing
vendor names — which is exactly what the pipeline needs to know. Consumers can
re-weight from the per-field breakdown, which is always returned.

Thresholds are constructor arguments, so the whole benchmark can be re-run under
a stricter rubric without editing source.

---

## 4. Cost analysis

Token counts come from the provider's own usage metadata whenever it is
returned; each result records `token_source` as `provider` or `estimated` so the
distinction is never silently lost. The estimated path (prompt length ÷ 4, plus
a flat per-image estimate) is a fallback, and it is the least trustworthy number
in the report — image tokenisation differs substantially between providers.

Images are downscaled to a 1600 px long edge before transmission. Vision models
bill per tile, so a 12 MP phone photo costs several times what the same bill
costs at 1600 px, and handwriting is already comfortably legible at that size.

Per-100-bill figures are `mean cost per bill × 100`. This is deliberately naive
and slightly **understates** real cost at volume: it excludes retries on
malformed JSON, excludes the human review pass that low-confidence extractions
require, and assumes bill complexity matches the sample. Treat it as a floor for
comparing models against each other, not as a budget.

---

## 5. Limitations

**Sample size dominates everything.** At 10–15 bills, the 95% confidence
interval on any accuracy figure is roughly ±15 points. A 3-point gap between two
models is noise. The report says so explicitly rather than printing a ranking
that will not replicate — below 30 bills it appends a caveat to the
recommendation.

**Single run, no variance measurement.** Temperature is 0 where the provider
allows it, but these models are not deterministic. Each bill is extracted once,
so run-to-run variance is unmeasured. Three runs per bill with reported standard
deviation would be the honest version and was cut for scope.

**Ground truth is one annotator, unvalidated.** No inter-annotator agreement was
measured. On genuinely smudged fields, a second annotator would disagree with
the first, and that disagreement rate is the real ceiling on measurable model
accuracy.

**Field-level scoring cannot see structure.** A model that returns the correct
six fields for the wrong bill in a multi-bill photo scores well.

**Zoho coupling is shallow.** Expenses are created against a resolved account
id, but vendor records are not created or matched, tax is not itemised, and the
expense is not reconciled. A production integration would need all three.

---

## 6. Recommendation framework

Which model to pick depends on what the wrong answer costs you.

**Volume with human review in the loop → the cheapest model that clears ~85%
overall.** If a reviewer sees every bill anyway, the marginal value of accuracy
above that is small and the cost difference compounds across thousands of bills.
Gemini Flash's free tier makes it the default here.

**Straight-through posting with no review → the model with the best `amount` and
`date` accuracy, and specifically the lowest hallucination rate.** Read the
per-field `match_type_counts`, not `overall_accuracy`. A model that returns null
on 10% of amounts is *safer* than one that guesses on all of them, because nulls
route to a human and wrong numbers do not.

**Latency-sensitive interactive use → the fastest model that clears your accuracy
bar**, with the rest reserved for a background re-check.

**When two models are within ~5 points on your sample, pick the cheaper one.**
That gap is inside the noise floor at this sample size, so paying more buys
measurement error. The report applies exactly this rule when it generates its
recommendation.

**Regardless of choice, re-run this benchmark quarterly.** Model endpoints get
deprecated, prices move, and a new small model appears roughly every quarter.
The entire point of building the harness rather than doing a one-off comparison
is that re-running it costs one command.
