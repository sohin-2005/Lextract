# Building the bill dataset

The benchmark is only as good as this folder. Fifteen varied bills with a
careful answer key will tell you more than fifty sloppy ones.

---

## 1. Collect 10–15 handwritten bills

Aim for **variety**, not volume. The point is to find where models break, and a
model that reads fifteen photos of the same neat shopkeeper's handwriting tells
you nothing.

Mix across these axes:

| Axis | Get a spread of |
|---|---|
| **Bill type** | Kirana/grocery, chemist, auto-rickshaw, small restaurant, hardware, tailor |
| **Handwriting** | Neat and careful, fast and cursive, mixed script (Devanagari + English) |
| **Paper** | Carbon copy, rubber-stamped pad, plain paper, thermal printout with handwritten total |
| **Photo quality** | Bright daylight, indoor tube light, slight blur, angled, shadowed |
| **Structure** | Some with a GSTIN, some without; some with a bill number, some without |

Deliberately include 2–3 **hard** bills — a smudged total, a date written
`१५/०३/२४`, a torn corner. Those are where the models actually differ. A dataset
of only easy bills produces a three-way tie and no useful conclusion.

---

## 2. Redact before you upload

**Do this before the image leaves your phone.** These are real receipts.

**Cover or blur:**

- Phone numbers (the shop's *and* any customer's)
- Individual people's full names — a customer name written on the bill
- Bank account numbers, UPI IDs, cheque numbers
- Aadhaar and PAN numbers
- Vehicle registration numbers on auto/taxi receipts
- Any address more precise than the locality

**Keep visible** — the benchmark needs these:

- Shop / business name
- Amounts and line items
- Date and bill number
- GSTIN (a business tax ID, not personal data)

A black rectangle drawn in any photo editor is fine. Blur can sometimes be
reversed; a solid fill cannot.

> The images are git-ignored (`dataset/bills/*`) so they never reach a remote
> repository. Redaction still matters: these images are sent to third-party LLM
> APIs during extraction.

---

## 3. Name the files

    bill_01_grocery.jpg
    bill_02_medical.jpg
    bill_03_auto.jpg
    bill_04_restaurant.jpg
    bill_05_hardware.jpg

Zero-padded numbers keep them sorted; the suffix reminds you what each one is
when you are entering ground truth an hour later.

Place them in `dataset/bills/`, or just drag them into the web UI — the uploader
copies them there and gives each a collision-proof stored name.

---

## 4. Write the answer key

For each bill, **look at the image and type what you see** into
`ground_truth.json` (or the ground-truth form on the bill detail page, which is
faster and validates as you go).

```json
[
  {
    "bill_id": "bill_01_grocery",
    "vendor_name": "Sharma General Store",
    "bill_number": null,
    "date": "2024-03-15",
    "amount": "245.50",
    "currency": "INR",
    "tax_gst_details": "GSTIN: 07AABCU9603R1ZX"
  }
]
```

### Rules that keep the scores honest

1. **`null` means the bill genuinely does not have it.** Not "I cannot read it",
   not "I did not bother". If a field is present but illegible, that bill is a
   bad benchmark item for that field — either drop the bill or accept that every
   model will score 0 there.
2. **`date` is always `YYYY-MM-DD`.** Indian bills are written day-first, so
   `15/3/24` becomes `2024-03-15`. Get this backwards and you will "discover"
   that every model transposes dates.
3. **`amount` is the final payable total**, as a string, no currency symbol and
   no commas: `"1245.50"`, never `"Rs. 1,245.50/-"`.
4. **Transcribe the vendor name exactly as written.** Do not expand `Genl.` to
   `General`, do not fix spelling, do not add `Pvt Ltd`. You are recording what
   is on the paper.
5. **Never prefill from a model and accept it unchecked.** The UI offers a
   prefill button because typing six fields fifteen times is tedious — but if
   you rubber-stamp a model's output, you are scoring that model against itself
   and it will win.

Getting the answer key wrong is the single most likely way for this benchmark to
produce a confident, wrong conclusion. It is worth the twenty minutes.

---

## 5. Sanity check

Before drawing conclusions:

- [ ] Every bill in `dataset/bills/` has a ground-truth entry
- [ ] Every `date` is `YYYY-MM-DD` and matches the image day-first
- [ ] Every `amount` is a bare number
- [ ] No template rows from this repo are left in `ground_truth.json`
- [ ] You re-checked 2–3 entries against the images after a break

Then: `GET /api/evaluation/report`.
