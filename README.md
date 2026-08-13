# Warhammer 40k Factions Test — reverse-engineered weights

Unofficial analysis of the [IDRlabs Warhammer 40k Factions Test](https://www.idrlabs.com/warhammer-40k-factions/test.php).

**Not affiliated with Games Workshop or IDRlabs.** Warhammer 40,000 and all related names are trademarks of Games Workshop Group PLC.

## What we found

- **40 questions**, each mapped to **exactly one** faction
- **8 factions × 5 questions** each
- Slider scale: `0` Disagree … `2` Neutral … `4` Agree (shown below as answers **1–5**)
- All-neutral baseline → every faction **50%**
- Full Agree (or reverse Disagree) on an item → **±10 percentage points** on that faction
- Final score ≈ `50 + sum of deltas` for the faction (range ~0–100%)

| Faction | Question IDs | Notes |
|---|---|---|
| Imperium of Man | 1–5 | |
| Chaos | 6–10 | |
| Eldar | 11–15 | Q14–15 are **reverse** |
| Dark Eldar | 16–20 | |
| Orks | 21–25 | |
| Tyranids | 26–30 | |
| Necrons | 31–35 | |
| T’au Empire | 36–40 | |

Full per-answer table: [`out/scoring_table.csv`](out/scoring_table.csv)

---

## Example: how to get **Eldar (100%)**, everyone else **0%**

Say **Agree** on only **3** Eldar questions; say **Disagree** on **all** remaining questions (including reverse Eldar items and every other faction).

| # | Question | Answer |
|---|---|---|
| **11** | I am deeply interested in the study of culture and history. | **5** Agree |
| **12** | I tend to be suspicious of people I do not know. | **5** Agree |
| **13** | People are sometimes startled by the keenness of my memory. | **5** Agree |
| **14** | Philosophical discussions are mostly boring. | **1** Disagree *(reverse → still boosts Eldar)* |
| **15** | I dislike going to art museums. | **1** Disagree *(reverse → still boosts Eldar)* |
| *all other questions* | — | **1** Disagree |

Why this zeros the rest: each other faction has 5 items; Disagree on all five = `50 − 5×10 = 0%`.  
Eldar: Agree on Q11–13 (+30) + Disagree on reverse Q14–15 (+20) = `50 + 50 = 100%`.

![Eldar 100%, others 0%](docs/images/eldar-result-graphic.png)

Verified live result: *You are Eldar (100%).* Chart query: `p=0,0,100,0,0,0,0,0`.

> Tip: questions are **shuffled** in the UI. Match by question text / internal id, not by “Question 1 of 40”.

---

## Answer scale (1–5)

| UI label | This repo | Site slider | Typical delta (non-reverse) |
|---|---|---|---|
| Disagree | 1 | 0 | −10 |
| | 2 | 1 | −5 |
| Neutral | 3 | 2 | 0 |
| | 4 | 3 | +5 |
| Agree | 5 | 4 | +10 |

For **reverse** items (Eldar Q14–15), signs flip.

---

## Reproduce / scrape weights

Requires Python 3.10+ and Playwright.

```bash
python3 -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
playwright install chromium

# Probe all 40 questions (Agree one-hot vs Neutral baseline)
python reverse_weights.py -o ./out

# Watch the browser
python reverse_weights.py --no-headless --mode ui -o ./out
```

Outputs in `out/`:

| File | Contents |
|---|---|
| `results.json` | Compact scores per question |
| `weights.csv` | Scores + deltas vs baseline |
| `scoring_table.csv` | Question → faction → deltas for answers 1–5 |
| `results_detailed.json` | Catalog, baseline, probe metadata |

### CLI options

```text
--mode fast|ui     fast = cookie inject + ?finish (default); ui = click through
--start N --end N  question id range (1–40)
--no-headless      show Chromium
--resume           skip probes already in results_detailed.json
--no-baseline      skip all-neutral baseline run
```

---

## Method (short)

1. Load the test and read `TEST.questions` (ids 1–40).
2. For each question id `i`: set answer `i` = Agree (`4`), all others = Neutral (`2`).
3. Submit via the same cookie the site uses (`answers-warhammer-40k-factionsENv1`) and open `?finish`.
4. Read faction % from the result chart (`p=` query / bars).
5. Subtract the all-neutral baseline to get per-item deltas.
6. Interpolate answers 2/4 as half-steps (linear Likert).

Probes use **stable question ids**, not shuffled UI order.

---

## Disclaimer

For research / curiosity only. IDRlabs terms restrict automated access; use responsibly and at your own risk. This project does not redistribute Games Workshop IP beyond quoting publicly available quiz item text for analysis.
