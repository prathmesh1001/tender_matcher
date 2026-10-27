# Manual match check: tender line items vs collected catalogues

Hand evaluation of the proposed two-layer logic, done by reading the documents (no code).
Catalogue page numbers refer to the PDFs in `data/manufacturers/`; tender references to
`data/tenders/`. These results are a first set of gold labels for `eval/`.

## Rules applied

**Layer 1, per requirement**: FULL = 1.0, PARTIAL = 0.5, MISSING = 0.0 (the catalogue covers the
attribute and the value fails), NONE = no evidence in the documents (excluded from the score).

**Weights**: A identity (type, design standard) 3 · B rating (size, pressure, temperature) 3 ·
C construction (ends, materials, operator, testing) 2 · D secondary 1. Partial = half weight.

**Fit** = Σ(score × weight) / Σ(weight) over requirements with evidence.
**Coverage** = requirements with evidence / technical requirements.

**Layer 2, product label per line item**
- FULL: same family and sub-type, every critical requirement FULL, fit ≥ 0.9, coverage ≥ 70 %
- PARTIAL: same family and sub-type, no critical MISSING, fit ≥ 0.6
- LEAST: same family but other sub-type, or any critical requirement MISSING
- NOT FOUND: no product of that family in the collected documents
- Flags: INSUFFICIENT EVIDENCE (coverage < 70 % or a critical requirement NONE), NAMED MAKE,
  NEEDS REVIEW (conditional limits, ambiguous requirement)

Critical (valves): size, pressure class/PN, design standard, body material.

## Results

### 1. GEM/2026/B/8119613 · item 10 · triple-offset butterfly 250 mm, Class 150 (buyer_spec p1)

**vs L&T `lt_triple_offset_butterfly.pdf` → FULL** (fit 0.96, coverage 11/14 = 79 %)

| Requirement | W | Status | Evidence |
|---|---|---|---|
| Triple-offset metal seated, torque seated | A | FULL | p1 "API 609 Triple-offset Butterfly Valves"; p9 "torque-seated valves" |
| Valve design API 609 | A | FULL | p4 "Design · API 609 (Category B), ASME B16.34" |
| Size 250 mm | B | FULL | p1 "3" to 100" (80 mm to 2500 mm)" |
| Class #150 | B | FULL | p1 "ASME Class 150 to 1500" |
| End connection wafer | C | FULL | p4 "Ends · Wafer/ Lugged" |
| Body ASTM A216 Gr. WCB | C | FULL | p4 "Body · A 216 Gr. WCB/ WCC" |
| Disc ASTM A217 CA15 / CF8M | C | PARTIAL | WCB variant disc is WCB (p4); CF8M only in the stainless variant (p5); CA15 not listed |
| Shaft A479 SS410 / 17-4PH | C | FULL | p4 "A 479 Type SS 410/ A 564 Type 630" (Type 630 = 17-4PH) |
| Body seat Stellite 21 | C | FULL | p4 "Body seat hard-faced with Stellite 21 or 6" |
| Hand gear operated | C | FULL | p4 "Gear Unit · Worm type (self-locking)" |
| Zero leakage | D | FULL | p9 "Bi-directional bubble-tight sealing" (synonym) |
| Fluid water | D | NONE | water named only as an industry (p3) |
| Max flow 650 m3/h | D | NONE | no flow / Cv data |
| Torque at shaft 820 Nm | D | NONE | torque described, no values (p8) |

vs L&T Aquaseal (centric) and DelVal 44/49 (double offset, p1 "Double Offset High
Performance") and DelVal 50/52 (centric) → **LEAST** (same family, other sub-type).

### 2. GEM/2026/B/8157057 · butterfly 450 mm, PN10, CI body, SGI disc, EPDM, EN 593, worm gear + handwheel, water; "Make: M/s. Intervalve Poonawalla Ltd." (buyer_spec p1)

**vs L&T `lt_aquaseal_butterfly_check.pdf` (Aquaseal 10) → FULL + NAMED MAKE** (fit 0.90, coverage 9/9)

| Requirement | W | Status | Evidence |
|---|---|---|---|
| Centric rubber-lined butterfly | A | FULL | p4 "Aquaseal10 Integrally-moulded Wafer-type Butterfly Valve" |
| Design EN 593 | A | FULL | p4 "Valve Design EN 593 (BS 5155)" |
| Size 450 mm | B | FULL | p4 "sizes from 50 mm (2") to 600 mm (24"), in PN 10" (range) |
| PN10 | B | FULL | p4 same sentence |
| Body cast iron | C | FULL | p4 "Body Cast Iron EN 1561 (BS 1452) Gr. 200, Gr. 250" |
| Disc SG iron | C | PARTIAL | Aquaseal 10 disc is cast iron / CF8M (p4); SG iron disc only in Aquaseal 16 (p8), another model |
| Seat EPDM | C | FULL | p4 "Body Liner Nitrile, EPDM" |
| Worm gear + handwheel | C | PARTIAL | p5 only "direct mounting of actuators and gear units" |
| Service water | D | FULL | p4 "reliable sealing in water and air lines" |

**vs DelVal `delval_series_50_52_centric_bfv_spec.pdf` → FULL + NAMED MAKE + NEEDS REVIEW** (fit 1.00, coverage 7/9)

| Requirement | Status | Evidence |
|---|---|---|
| Centric butterfly, EN 593 | FULL | p1 "Series 50 Wafer or Series 52 Lugged"; "Design: BS EN 593" |
| 450 mm, PN10 | FULL (range + unit) | p1 "14"-24" (DN 350 – DN 600) 150 psi (10.0 barg)" |
| Body cast iron | FULL (equivalence) | p2 "Body: ASTM A126 Gr B" (grey cast iron) |
| Disc SG iron | FULL (equivalence) | p2 "Disc: ASTM A536 Gr. 65-45-12" (ductile = SG iron) |
| Seat EPDM | FULL | p2 "Seat: EPDM / WHITE EPDM …" |
| Worm gear + handwheel, service water | NONE | not in this bulletin |

Needs review: p2 "*Max. pressure rating limited to 6 Bar" applies to some options.
Fit 1.00 only because two requirements have no evidence (see observation 2).

### 3. GEM/2026/B/7956201 · item 1 · globe valve 6", #300, BS 1873, liquid ammonia (boq_spec p6–7)

Buyer's acceptable makes include "M/s. L&T Valves" (boq_spec p8), so L&T is eligible.

**vs L&T `lt_gate_globe_check_api600.pdf` → FULL + NEEDS REVIEW** (fit 0.92, coverage 15/18)

| Requirement | W | Status | Evidence |
|---|---|---|---|
| Globe valve, bolted bonnet | A | FULL | p9 "4 Globe"; p6 "bolted-bonnet construction" |
| Design BS 1873 | A | FULL | p5 standards table "BS 1873" |
| Size 6" | B | FULL | p12 globe dimensions table, 50 (2") to 300 (12") |
| Rating #300 | B | FULL | p9 "3 Class 300" |
| Design temp -40 to +40 °C | B | FULL (range) | p5 "ASTM A352 Gr. LCB/LCC -46°C to 343°C" |
| Design pressure 40 bar | B | NONE | p5 points to "pages 26" for P-T ratings; the PDF has 24 pages |
| Body & bonnet LCB | C | FULL | p9 "LCB for ASTM A352 Gr. LCB" |
| Trim "12 -> 16" | C | FULL, review | p9 trim nos. 12 and 16 offered; the tender wording is ambiguous |
| Disc LCB + HF | C | PARTIAL | standard disc "ASTM A216 Gr. WCB + 13% Cr. Steel" (p12); LCB disc not shown |
| Gasket graphite | C | PARTIAL | p6 graphite gasket stated for Class 150 joints; Class 300 joint differs |
| Packing graphite | C | FULL | p12 "Graphite with braided end rings" |
| Operator gear / handwheel | C | FULL | p9 "GO for Gear Operation"; p12 "Handwheel" |
| Flanged ends + tongue & groove companion flanges | C | PARTIAL | p9 only "Flanged RF / RTJ / Butt-weld" |
| Bolting B7 / 2H | C | FULL | p12 "ASTM A193 Gr. B7", "ASTM A194 Gr. 2H" |
| Stem SS316 | C | FULL | p9 trim 12/16 stem "SS 316" |
| Testing API 598 | C | FULL | p5 "API 598" |
| Liquid ammonia service | D | NONE | not stated |
| Low-temperature test | D | NONE | not stated |

Item 7 (forged gate, API 602, #800, LF2) vs `lt_gate_globe_check_api602.pdf` → **PARTIAL**:
API 602 (p1), class 800 (p3–6), Stellite seat (p4) FULL; LF2 appears only in a test-pressure
note (p3), not in the body-material codes (A105, F11, F22, F304, F316, F347) → PARTIAL.
DelVal → **NOT FOUND** (no gate/globe valves in the collected DelVal documents).

### 4. GEM/2026/B/7907627 · cryogenic globe 2", PN50, BW ends, extended bonnet, -196 °C, body A351 CF8; "reputed make like STRACK/HEROSE/BESTOBELL/MACK" (buyer_spec_2 p1)

**vs L&T `lt_gate_globe_check_api602.pdf` → PARTIAL + INSUFFICIENT EVIDENCE + NAMED MAKE** (fit 0.82, coverage 8/15)

| Requirement | Status | Evidence |
|---|---|---|
| Cryogenic globe valve | FULL | p1 standards "BS 6364"; "Cryo Gate & Globe Valves · BS 6364"; option "CR - Cryogenic" |
| Size 2" (also 1.5", 1", 3/4") | FULL | size codes 15, 20, 25, 40, 50 mm |
| PN50 | PARTIAL | classes 150–2500 listed; PN50 ↔ Class 300 only via the EN 1759 designation |
| BW ends | FULL | "3 - Butt-weld" |
| Body A351 CF8 (cast) | PARTIAL | forged "F304 / F304L" offered: same alloy, different product form |
| Bonnet DIN 1.4308 | PARTIAL | same as body |
| Stem A276 Gr 304 | FULL | "ASTM A479 Type 304" (equivalent bar spec) |
| Testing API 598 | FULL | "API 598" |
| Temp -196 °C to 120 °C | NONE | BS 6364 implies cryogenic service, but no temperature is stated |
| Extended bonnet, stem length ≥ 220 mm | NONE | not stated in this catalogue |
| PTFE gasket, SS304 insert bush, B8 bolts, packing | NONE | not stated |
| EN 12266-1/-2 testing, oxygen cleaning (EN 12300) | NONE | not stated |

`lt_cryogenic_valves.pdf` (3-page brochure) adds only "Design: BS 6364" (p2).

### 5. GEM/2026/B/8017367 · multi-item valve BOQ (boq_spec p1)

**Dual-plate check DN50, PN16, API 594; body WCB "or equivalent", disc CF8M, seat nitrile or SS,
spring/pins SS316, wafer to ASME B16.5 CL150, max F-F 60 mm, max OD 109 mm**

vs L&T Aquaseal Chek (`lt_aquaseal_butterfly_check.pdf` p24–25) → **LEAST** (fit 0.72, critical MISSING)

| Requirement | Status | Evidence |
|---|---|---|
| Dual-plate check, API 594 | FULL | p24 "soft-seated dual-plate check valve that conforms to API 594" |
| DN50, PN16 | FULL | p24 "2" (50 mm) to 8" (200 mm), in PN 10, PN 16" |
| Body WCB or equivalent | **MISSING** | p25 "Cast Iron EN1561 (BS 1452) Gr. 200" (cast iron is not equivalent to cast steel) |
| Disc CF8M or equivalent | PARTIAL | p25 "Closure Plate · Stainless Steel A 217 Gr. CA15" |
| Seat nitrile or SS | MISSING | p25 "Seat · EPDM" |
| Spring / pins SS316 | PARTIAL | p25 "Spring · Stainless Steel 316"; pins "A 276 Type 410" |
| Wafer for ASME B16.5 CL150 | PARTIAL | p25 end connection "EN 1092" (PN) / "ASME B16.1" (Class 125) |
| Max F-F 60 mm, max OD 109 mm | FULL | p25 DN50 "54 · 109" |

DN125: no dimension row (p25 lists 50, 80, 100, 150, 200). DN300: outside 50–200 mm →
size MISSING. All dual-plate items → LEAST. DelVal → NOT FOUND.

**Ball valve DN25, Class 800 or higher, ISO 17292, WCB body, SS ball/stem (A182), screwed female
NPT, API 598**

vs L&T `lt_process_ball_valves.pdf` → **FULL** (fit 0.95, coverage 8/8): ISO 17292 (p1); "sizes DN 8
to DN 50 are offered in Class 800" (p3); body "ASTM A216 Gr. WCB", ball "ASTM A351 Gr. CF8M/ ASTM
A182 Gr. F316" (p6); "Pressure testing ISO 5208/ API 598" (p6); ends "Screwed/ Socket-weld" (p3;
NPT not named → PARTIAL).

vs DelVal 65/72 → **PARTIAL**: floating ball, WCB body, CF8M ball (p1) FULL, but "two-piece body
flanged" (p1) vs screwed ends → MISSING; class 800 not stated → NONE.

**Butterfly DN100, PN16, centric rubber-lined, SG iron body/disc, SS shaft, IS 13095, leakage
class VI, up to 80 °C, working pressure up to 10 kg/cm², wafer to suit 150#**

vs L&T Aquaseal 16 (p8) → **PARTIAL + INSUFFICIENT EVIDENCE**: PN16, SG iron body and disc, SS
stem, nitrile/EPDM liner, "ASME B16.5 Class150/ BS 4504 PN 16" FULL; size range for Aquaseal 16
not stated (critical NONE); IS 13095, leakage class VI and 80 °C not stated.

vs DelVal 50/52 → **PARTIAL**: body "ASTM A395 Gr 60-40-18" (ductile iron) and disc A536 FULL;
seat EPDM FULL; but DN 50–300 rated "175 psi (12.0 barg)" (p1) < PN16 rating → PARTIAL (the
10 kg/cm² ≈ 9.8 bar working pressure is met, the PN16 rating is not).

### 6. Pump tenders (7959356, 8011173, 8026686, 8071357, 8167974, 8169451)

**Not evaluated**: no pump manufacturer documents were collected (Kirloskar blocks scripted
download; no reachable official KSB documents). Out-of-category tenders (7865644 thermocouples,
7951144 forging, 8054155 aluminium phosphide): every manufacturer → NOT FOUND.

## Observations on the logic

1. **The critical-requirement gate is necessary.** Dual-plate check vs Aquaseal Chek has fit 0.72,
   which a threshold alone would call PARTIAL, but the body is cast iron where cast steel is
   required. The gate correctly makes it LEAST.
2. **Excluding NONE inflates the fit.** DelVal 50/52 scores 1.00 on 7 of 9 requirements. Fit must
   always be shown with coverage, and a critical NONE must cap the label at PARTIAL.
3. **"Not offered" must mean "not found in the collected documents".** Absence from our documents
   is not proof that the manufacturer does not make the product.
4. **Variant mixing is a real risk.** The SG iron disc exists in Aquaseal 16, the PN10 rating in
   Aquaseal 10. Taking both from the same PDF would claim a product L&T does not list.
5. **End connection should probably be critical.** A flanged ball valve cannot go into a screwed
   line, whatever the rest of the score.
6. **Equivalence and conversions decide many results**: A395 = ductile iron, A564 Type 630 =
   17-4PH, psi → bar, PN50 ↔ Class 300 (EN 1759 designation only).
7. **Brochures give little evidence**, and the coverage gate catches it (cryogenic case 8/15).
8. **Named makes split three ways**: a hard make requirement (8157057: only Intervalve), a soft
   "make like …" list (7907627), and an approved-make list that includes the manufacturer
   (7956201: L&T eligible).
9. **Catalogues can be incomplete in themselves**: L&T API 600 points to P-T tables on "pages 26"
   that are not in the 24-page PDF.
10. **Some tender requirements are ambiguous** ("Trim 12 -> 16") and need NEEDS REVIEW rather than
    a guess.
