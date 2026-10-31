# Gold diff: GEM_2026_B_8157057

Gold source: docs/manual_match_check.md (section 2)

| Run | agree | not decided | weaker | stronger | other catalogue | other page |
|---|---|---|---|---|---|---|
| ontology | 9 | 4 | 1 | 0 | 0 | 1 |
| no ontology | 3 | 11 | 0 | 0 | 0 | 1 |

## Item 1, LnT_Valves, `lt_aquaseal_butterfly_check.pdf`

| Req | Tender | Gold | ontology | no ontology |
|---|---|---|---|---|
| 1.1.2 | Valve Size : 450MM | full_match (p4) | full_match (p6): **agree, other page** | unsupported (-): **not decided**; statement without a label: needs the LLM judge (not enabled) |
| 1.1.3 | CLASS : PN10 | full_match (p4) | partial_match (p3): **weaker**; product heading unclear: capped at partial | unsupported (-): **not decided**; statement without a label: needs the LLM judge (not enabled) |
| 1.1.4 | Body : CI | full_match (p4) | full_match (p4): **agree**; same material, named another way | unsupported (p3): **not decided**; no material equivalents: cannot tell if these are the same |
| 1.1.5 | Disc : SGI | partial_match (p4, p8) | unsupported (p8, p14, p19): **not decided**; stated only for another product in this document, not Aquaseal 10 Integrally-moulded Butterﬂy Valve - PN 10 | unsupported (p4, p8, p14): **not decided**; tender names words, catalogue names codes: not like-for-like |
| 1.1.6 | Seat : EPDM | full_match (p4) | full_match (p4): **agree** | full_match (p8, p14, p25): **agree, other page** |
| 1.1.7 | DESIGN/MFG STD : EN593 | full_match (p4) | full_match (p4): **agree** | unsupported (-): **not decided**; no catalogue field names this attribute |
| 1.1.8 | Service : water | full_match (p4) | unsupported (-): **not decided**; no catalogue field names this attribute | unsupported (-): **not decided**; no catalogue field names this attribute |

Manual check: fit 0.9, coverage 9/9 (weighted).
System, ontology: compatibility 90.0, coverage 55.6 % (5/9, unweighted).
System, no ontology: compatibility 100.0, coverage 11.1 % (1/9, unweighted).

Not labelled in gold, so not compared:

- 1.1.1 `IVGK` (ontology: unsupported, no ontology: unsupported)
- 1.make `MAKE : Intervalve Poonawalla Ltd` (ontology: unsupported, no ontology: unsupported)

## Item 1, Delval, `delval_series_50_52_centric_bfv_spec.pdf`

| Req | Tender | Gold | ontology | no ontology |
|---|---|---|---|---|
| 1.1.2 | Valve Size : 450MM | full_match (p1) | unsupported (-): **not decided**; no catalogue field names this attribute | unsupported (-): **not decided**; statement without a label: needs the LLM judge (not enabled) |
| 1.1.3 | CLASS : PN10 | full_match (p1) | unsupported (p2): **not decided**; label found but no value in a comparable unit | unsupported (-): **not decided**; statement without a label: needs the LLM judge (not enabled) |
| 1.1.4 | Body : CI | full_match (p2) | full_match (p2): **agree**; same material, named another way | unsupported (p1, p2): **not decided**; tender names words, catalogue names codes: not like-for-like |
| 1.1.5 | Disc : SGI | full_match (p2) | full_match (p2): **agree**; same material, named another way | unsupported (p1, p2): **not decided**; no material equivalents: cannot tell if these are the same |
| 1.1.6 | Seat : EPDM | full_match (p2) | full_match (p2): **agree** | full_match (p2): **agree** |
| 1.1.7 | DESIGN/MFG STD : EN593 | full_match (p1) | full_match (p1): **agree** | unsupported (-): **not decided**; no catalogue field names this attribute |
| 1.1.8 | Service : water | unsupported (-) | unsupported (p1): **agree**; tender names words, catalogue names codes: not like-for-like | unsupported (p1): **agree**; tender names words, catalogue names codes: not like-for-like |

Manual check: fit 1.0, coverage 7/9 (weighted).
System, ontology: compatibility 100.0, coverage 44.4 % (4/9, unweighted).
System, no ontology: compatibility 100.0, coverage 11.1 % (1/9, unweighted).

Notes from gold:

- 1.1.3: Manual check needs review: p2 "*Max. pressure rating limited to 6 Bar" applies to some options.

Not labelled in gold, so not compared:

- 1.1.1 `IVGK` (ontology: unsupported, no ontology: unsupported)
- 1.make `MAKE : Intervalve Poonawalla Ltd` (ontology: unsupported, no ontology: unsupported)

## Catalogues: product label vs sub-type fit

| Item | Manufacturer | Catalogue | Gold | ontology | no ontology |
|---|---|---|---|---|---|
| 1 | LnT_Valves | `lt_aquaseal_butterfly_check.pdf` | full | unknown, chosen: **differs** | unknown, chosen: **differs** |
| 1 | Delval | `delval_series_50_52_centric_bfv_spec.pdf` | full | unknown, chosen: **differs** | unknown, chosen: **differs** |

## Ranking (not in gold, shown only)

ontology:

1. LnT_Valves: compatibility 90.0 %, coverage 55.6 %
2. Delval: compatibility 100.0 %, coverage 44.4 %, low coverage
3. KSB: compatibility -, coverage 0.0 %, low coverage
4. Kirloskar Brothers: compatibility -, coverage 0.0 %, low coverage

no ontology:

1. Delval: compatibility 100.0 %, coverage 11.1 %, low coverage
2. LnT_Valves: compatibility 100.0 %, coverage 11.1 %, low coverage
3. KSB: compatibility -, coverage 0.0 %, low coverage
4. Kirloskar Brothers: compatibility -, coverage 0.0 %, low coverage
