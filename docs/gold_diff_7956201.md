# Gold diff: GEM_2026_B_7956201

Gold source: docs/manual_match_check.md (section 3)

| Run | agree | not decided | weaker | stronger | other catalogue | other page |
|---|---|---|---|---|---|---|
| ontology | 10 | 13 | 3 | 0 | 0 | 3 |
| no ontology | 1 | 3 | 1 | 0 | 21 | 0 |

## Item 1, LnT_Valves, `lt_gate_globe_check_api600.pdf`

| Req | Tender | Gold | ontology | no ontology |
|---|---|---|---|---|
| 1.1.1 | Size : 6 inch | full_match (p12) | full_match (p7, p12, p17): **agree** | - (-): **other catalogue** |
| 1.1.2 | CLASS : # 300 Class | full_match (p9) | full_match (p1, p7, p12): **agree, other page** | - (-): **other catalogue** |
| 1.1.3 | Flanged Ended | partial_match (p9) | unsupported (-): **not decided**; statement without a label: needs the LLM judge (not enabled) | - (-): **other catalogue** |
| 1.s1.p6.1 | Valve Type. : Globe Valve | full_match (p9) | unsupported (-): **not decided**; no catalogue field names this attribute | - (-): **other catalogue** |
| 1.s1.p6.2 | Rating : # 300 | full_match (p9) | full_match (p1, p7, p12): **agree, other page** | - (-): **other catalogue** |
| 1.s1.p6.3 | Body / Bonnet Construction : Bolted Bonnet | full_match (p6) | unsupported (-): **not decided**; no catalogue field names this attribute | - (-): **other catalogue** |
| 1.s1.p6.4 | Service Fluid/Working Fluid : Liquid Ammonia | unsupported (-) | unsupported (-): **agree**; no catalogue field names this attribute | - (-): **other catalogue** |
| 1.s1.p6.5 | Design Standard : BS-1873 | full_match (p5) | unsupported (-): **not decided**; no catalogue field names this attribute | - (-): **other catalogue** |
| 1.s1.p6.6 | Testing Standard : API 598 | full_match (p5) | unsupported (-): **not decided**; no catalogue field names this attribute | - (-): **other catalogue** |
| 1.s1.p6.7 | Body & Bonnet MOC : LCB | full_match (p9) | partial_match (p9, p12, p13): **weaker**; fails in a table, but the document's options list offers it | - (-): **other catalogue** |
| 1.s1.p6.8 | Trim : 12 -> 16 | full_match (p9) | unsupported (-): **not decided**; free text: needs the LLM judge (not enabled) | - (-): **other catalogue** |
| 1.s1.p6.9 | Disc : LCB + HF | partial_match (p12) | unsupported (-): **not decided**; free text: needs the LLM judge (not enabled) | - (-): **other catalogue** |
| 1.s1.p6.10 | Gasket : Graphite or equivalent | partial_match (p6) | unsupported (-): **not decided**; free text: needs the LLM judge (not enabled) | - (-): **other catalogue** |
| 1.s1.p6.11 | Packing type : Graphite or equivalent | full_match (p12) | unsupported (-): **not decided**; free text: needs the LLM judge (not enabled) | - (-): **other catalogue** |
| 1.s1.p6.12 | Operator : Gear/HW | full_match (p9, p12) | unsupported (-): **not decided**; no catalogue field names this attribute | - (-): **other catalogue** |
| 1.s1.p6.13 | Valve Ends : Flanged. | partial_match (p9) | unsupported (p17): **not decided**; quote not found on the cited page (was full_match) | - (-): **other catalogue** |
| 1.s1.p6.14 | Bolting : B7/2H | full_match (p12) | partial_match (p12, p13): **weaker**; 1 of 2 options found | - (-): **other catalogue** |
| 1.s1.p6.15 | Stem/Hinge pin : SS316 | full_match (p9) | partial_match (p9, p12, p13, p19): **weaker**; fails in a table, but the document's options list offers it | - (-): **other catalogue** |
| 1.s1.p6.16 | Design Temp. : -40 Deg. C to +40 Deg. C | full_match (p5) | unsupported (p5, p6, p16): **not decided**; printed value is impossible, so it was misread: ignored | - (-): **other catalogue** |
| 1.s1.p6.17 | Low temperature/Cyro : Low Temperature Test | unsupported (-) | unsupported (-): **agree**; free text: needs the LLM judge (not enabled) | - (-): **other catalogue** |
| 1.s1.p6.18 | Design Pressure (Bar) : 40 | unsupported (-) | unsupported (-): **agree**; no catalogue field names this attribute | - (-): **other catalogue** |

Manual check: fit 0.92, coverage 15/18 (weighted).
System, ontology: compatibility 75.0, coverage 28.6 % (6/21, unweighted).
System, no ontology: compatibility 100.0, coverage 9.5 % (2/21, unweighted).

Notes from gold:

- 1.s1.p6.8: Manual check marks this for review.

## Item 7, LnT_Valves, `lt_gate_globe_check_api602.pdf`

| Req | Tender | Gold | ontology | no ontology |
|---|---|---|---|---|
| 7.1.2 | CLASS : # 800 Class | full_match (p3, p4, p5, p6) | full_match (p1, p3, p6): **agree** | full_match (p1, p3): **agree** |
| 7.s2.p6.2 | Rating : #800 | full_match (p3, p4, p5, p6) | full_match (p1, p3, p6): **agree** | unsupported (p3): **not decided**; dimension table: its numbers do not state the attribute |
| 7.s2.p6.5 | Design Standard : API 602 | full_match (p1) | full_match (p3): **agree, other page** | unsupported (-): **not decided**; no catalogue field names this attribute |
| 7.s2.p6.7 | Seat ring : LF2 + Stellite Hard Facing | full_match (p4) | unsupported (p6): **not decided**; quote not found on the cited page (was partial_match) | unsupported (p6): **not decided**; quote not found on the cited page (was partial_match) |
| 7.s2.p6.8 | Body & Bonnet MOC : LCB -> LF2 | partial_match (p3) | partial_match (p3): **agree**; near miss: only part of the code found | missing_capability (p4): **weaker** |

Manual check: fit None, coverage None (weighted).
System, ontology: compatibility 77.77777777777777, coverage 40.9 % (9/22, unweighted).
System, no ontology: compatibility 66.66666666666667, coverage 13.6 % (3/22, unweighted).

Not labelled in gold, so not compared:

- 7.1.1 `Size : 3/4 inch` (ontology: full_match, no ontology: unsupported)
- 7.1.3 `Socket Weld Ended` (ontology: unsupported, no ontology: unsupported)
- 7.s2.p6.1 `Valve Type. : Gate Valve` (ontology: unsupported, no ontology: unsupported)
- 7.s2.p6.3 `Body / Bonnet Construction : Bolted Bonnet` (ontology: unsupported, no ontology: unsupported)
- 7.s2.p6.4 `Service Fluid/Working Fluid : Liquid Ammonia` (ontology: unsupported, no ontology: unsupported)
- 7.s2.p6.6 `Testing Standard : API 598` (ontology: full_match, no ontology: unsupported)
- 7.s2.p6.9 `Trim : 12 -> 16` (ontology: unsupported, no ontology: unsupported)
- 7.s2.p6.10 `Disc : LCB + HF` (ontology: unsupported, no ontology: unsupported)
- 7.s2.p6.11 `Gasket : Graphite or equivalent` (ontology: unsupported, no ontology: unsupported)
- 7.s2.p6.12 `Packing type : Graphite or equivalent` (ontology: unsupported, no ontology: unsupported)
- 7.s2.p6.13 `Operator : Gear/HW` (ontology: unsupported, no ontology: unsupported)
- 7.s2.p7.1 `Valve Ends : Flanged.` (ontology: full_match, no ontology: full_match)
- 7.s2.p7.2 `Bolting : B7/2H` (ontology: unsupported, no ontology: unsupported)
- 7.s2.p7.3 `Stem/Hinge pin : SS316` (ontology: missing_capability, no ontology: unsupported)
- 7.s2.p7.4 `Design Temp. : -40 Deg. C to +40 Deg. C` (ontology: partial_match, no ontology: unsupported)
- 7.s2.p7.5 `Low temperature/Cyro : Low Temperature Test` (ontology: unsupported, no ontology: unsupported)
- 7.s2.p7.6 `Design Pressure (Bar) : 40` (ontology: unsupported, no ontology: unsupported)

Items without gold: 2, 3, 4, 5, 6.

## Catalogues: product label vs sub-type fit

| Item | Manufacturer | Catalogue | Gold | ontology | no ontology |
|---|---|---|---|---|---|
| 1 | LnT_Valves | `lt_gate_globe_check_api600.pdf` | full | unknown, chosen: **differs** | unknown: **differs** |
| 7 | LnT_Valves | `lt_gate_globe_check_api602.pdf` | partial | unknown, chosen: **differs** | unknown, chosen: **differs** |
| 7 | Delval | `(none)` | not_found | not a candidate: agree | not a candidate: agree |

## Ranking (not in gold, shown only)

ontology:

1. LnT_Valves: compatibility 70.8 %, coverage 31.8 %, low coverage
2. Delval: compatibility -, coverage 0.0 %, low coverage
3. KSB: compatibility -, coverage 0.0 %, low coverage
4. Kirloskar Brothers: compatibility -, coverage 0.0 %, low coverage

no ontology:

1. LnT_Valves: compatibility 69.4 %, coverage 11.9 %, low coverage
2. Delval: compatibility -, coverage 0.0 %, low coverage
3. KSB: compatibility -, coverage 0.0 %, low coverage
4. Kirloskar Brothers: compatibility -, coverage 0.0 %, low coverage
