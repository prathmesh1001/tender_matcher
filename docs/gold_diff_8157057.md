# Gold diff: GEM_2026_B_8157057

Gold source: docs/manual_match_check.md (section 2)

| Run | agree | not decided | weaker | stronger | other catalogue | other page |
|---|---|---|---|---|---|---|
| ontology | 2 | 5 | 0 | 0 | 7 | 2 |
| no ontology | 0 | 7 | 0 | 0 | 7 | 0 |

## Item 1, LnT_Valves, `lt_aquaseal_butterfly_check.pdf`

| Req | Tender | Gold | ontology | no ontology |
|---|---|---|---|---|
| 1.1.2 | Valve Size - : 450MM | full_match (p4) | full_match (p6, p12, p18): **agree, other page** | unsupported (-): **not decided**; statement without a label: needs the LLM judge (not enabled) |
| 1.1.3 | CLASS - : PN10 | full_match (p4) | full_match (p3): **agree, other page** | unsupported (-): **not decided**; statement without a label: needs the LLM judge (not enabled) |
| 1.1.4 | Body- CI | full_match (p4) | unsupported (-): **not decided**; statement without a label: needs the LLM judge (not enabled) | unsupported (-): **not decided**; statement without a label: needs the LLM judge (not enabled) |
| 1.1.5 | Disc- SGI | partial_match (p4, p8) | unsupported (-): **not decided**; statement without a label: needs the LLM judge (not enabled) | unsupported (-): **not decided**; statement without a label: needs the LLM judge (not enabled) |
| 1.1.6 | Seat- EPDM | full_match (p4) | unsupported (-): **not decided**; statement without a label: needs the LLM judge (not enabled) | unsupported (-): **not decided**; statement without a label: needs the LLM judge (not enabled) |
| 1.1.7 | DESIGN/MFG STD- EN593 | full_match (p4) | unsupported (-): **not decided**; statement without a label: needs the LLM judge (not enabled) | unsupported (-): **not decided**; statement without a label: needs the LLM judge (not enabled) |
| 1.1.8 | Service- water | full_match (p4) | unsupported (-): **not decided**; statement without a label: needs the LLM judge (not enabled) | unsupported (-): **not decided**; statement without a label: needs the LLM judge (not enabled) |

Manual check: fit 0.9, coverage 9/9 (weighted).
System, ontology: compatibility 100.0, coverage 22.2 % (2/9, unweighted).
System, no ontology: compatibility None, coverage 0.0 % (0/9, unweighted).

Not labelled in gold, so not compared:

- 1.1.1 `IVGK` (ontology: unsupported, no ontology: unsupported)
- 1.make `MAKE : Intervalve Poonawalla Ltd` (ontology: unsupported, no ontology: unsupported)

## Item 1, Delval, `delval_series_50_52_centric_bfv_spec.pdf`

| Req | Tender | Gold | ontology | no ontology |
|---|---|---|---|---|
| 1.1.2 | Valve Size - : 450MM | full_match (p1) | - (-): **other catalogue** | - (-): **other catalogue** |
| 1.1.3 | CLASS - : PN10 | full_match (p1) | - (-): **other catalogue** | - (-): **other catalogue** |
| 1.1.4 | Body- CI | full_match (p2) | - (-): **other catalogue** | - (-): **other catalogue** |
| 1.1.5 | Disc- SGI | full_match (p2) | - (-): **other catalogue** | - (-): **other catalogue** |
| 1.1.6 | Seat- EPDM | full_match (p2) | - (-): **other catalogue** | - (-): **other catalogue** |
| 1.1.7 | DESIGN/MFG STD- EN593 | full_match (p1) | - (-): **other catalogue** | - (-): **other catalogue** |
| 1.1.8 | Service- water | unsupported (-) | - (-): **other catalogue** | - (-): **other catalogue** |

Manual check: fit 1.0, coverage 7/9 (weighted).
System, ontology: compatibility None, coverage 0.0 % (0/9, unweighted).
System, no ontology: compatibility None, coverage 0.0 % (0/9, unweighted).

Notes from gold:

- 1.1.3: Manual check needs review: p2 "*Max. pressure rating limited to 6 Bar" applies to some options.

Not labelled in gold, so not compared:

- 1.1.1 `IVGK` (ontology: unsupported, no ontology: unsupported)
- 1.make `MAKE : Intervalve Poonawalla Ltd` (ontology: unsupported, no ontology: unsupported)

## Catalogues: product label vs sub-type fit

| Item | Manufacturer | Catalogue | Gold | ontology | no ontology |
|---|---|---|---|---|---|
| 1 | LnT_Valves | `lt_aquaseal_butterfly_check.pdf` | full | unknown, chosen: **differs** | unknown, chosen: **differs** |
| 1 | Delval | `delval_series_50_52_centric_bfv_spec.pdf` | full | unknown: **differs** | unknown: **differs** |

## Ranking (not in gold, shown only)

ontology:

1. LnT_Valves: compatibility 100.0 %, coverage 22.2 %, low coverage
2. Delval: compatibility -, coverage 0.0 %, low coverage
3. KSB: compatibility -, coverage 0.0 %, low coverage
4. Kirloskar Brothers: compatibility -, coverage 0.0 %, low coverage

no ontology:

1. Delval: compatibility -, coverage 0.0 %, low coverage
2. KSB: compatibility -, coverage 0.0 %, low coverage
3. Kirloskar Brothers: compatibility -, coverage 0.0 %, low coverage
4. LnT_Valves: compatibility -, coverage 0.0 %, low coverage
