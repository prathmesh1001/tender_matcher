# Gold diff: GEM_2026_B_8017367

Gold source: docs/manual_match_check.md (section 5)

| Run | agree | not decided | weaker | stronger | other catalogue | other page |
|---|---|---|---|---|---|---|
| ontology | 9 | 17 | 3 | 2 | 0 | 5 |
| no ontology | 4 | 21 | 0 | 3 | 3 | 3 |

## Item 1, LnT_Valves, `lt_aquaseal_butterfly_check.pdf`

| Req | Tender | Gold | ontology | no ontology |
|---|---|---|---|---|
| 1.1.1 | DUAL PLATE WAFER TYPE CHECK VALVE | full_match (p24) | unsupported (-): **not decided**; statement without a label: needs the LLM judge (not enabled) | unsupported (-): **not decided**; statement without a label: needs the LLM judge (not enabled) |
| 1.1.2 | SIZE : DN50 | full_match (p24) | partial_match (p3): **weaker** | full_match (p8, p14): **agree, other page** |
| 1.1.3 | CLASS : PN16 OR HIGHER | full_match (p24) | full_match (p3): **agree, other page** | unsupported (p14): **not decided**; label found but no value in a comparable unit |
| 1.2.1 | DUAL PLATE CHECK VALVE | full_match (p24) | unsupported (-): **not decided**; statement without a label: needs the LLM judge (not enabled) | unsupported (-): **not decided**; statement without a label: needs the LLM judge (not enabled) |
| 1.3 | Rating/Class : PN16 or higher, | full_match (p24) | full_match (p3): **agree, other page** | unsupported (-): **not decided**; no catalogue field names this attribute |
| 1.4 | Product Standard : API 594 | full_match (p24) | unsupported (-): **not decided**; no catalogue field names this attribute | unsupported (-): **not decided**; no catalogue field names this attribute |
| 1.5.1 | Body : ASTM A216 Grade WCB or equivelent | missing_capability (p25) | missing_capability (p25): **agree** | full_match (p14, p19): **stronger** |
| 1.5.2 | DISC : A351 CF8M or equivelent | partial_match (p25) | unsupported (p26): **not decided**; stated only for another product in this document, not Aquaseal Chek Dual-plate Check Valve | full_match (p8, p14): **stronger** |
| 1.5.3 | Type of Seat : nitrile or SS | missing_capability (p25) | unsupported (-): **not decided**; free text: needs the LLM judge (not enabled) | unsupported (-): **not decided**; free text: needs the LLM judge (not enabled) |
| 1.5.4 | Spring | partial_match (p25) | unsupported (-): **not decided**; statement without a label: needs the LLM judge (not enabled) | unsupported (-): **not decided**; statement without a label: needs the LLM judge (not enabled) |
| 1.5.5 | Stop pin & hinge pin retainer : SS316 or equivelent | partial_match (p25) | unsupported (-): **not decided**; no catalogue field names this attribute | unsupported (-): **not decided**; no catalogue field names this attribute |
| 1.6 | End details : Wafer Type suitable for Clamping between flanges as per ASME B16.5 CL-150 | partial_match (p25) | missing_capability (p25): **weaker** | unsupported (-): **not decided**; no catalogue field names this attribute |
| 1.7.1 | Max. face to face dim : 60mm | full_match (p25) | unsupported (-): **not decided**; no catalogue field names this attribute | unsupported (-): **not decided**; no catalogue field names this attribute |
| 1.7.2 | Max. OD : 109mm | full_match (p25) | unsupported (-): **not decided**; no catalogue field names this attribute | unsupported (-): **not decided**; no catalogue field names this attribute |

Manual check: fit 0.72, coverage None (weighted).
System, ontology: compatibility 50.0, coverage 31.2 % (5/16, unweighted).
System, no ontology: compatibility 100.0, coverage 18.8 % (3/16, unweighted).

Not labelled in gold, so not compared:

- 1.2.2 `WAFER TYPE SPRING LOADED` (ontology: unsupported, no ontology: unsupported)
- 1.5.6 `Bearing/Bush : SS316 or equivelent` (ontology: unsupported, no ontology: unsupported)

## Item 5, LnT_Valves, `lt_aquaseal_butterfly_check.pdf`

| Req | Tender | Gold | ontology | no ontology |
|---|---|---|---|---|
| 5.1.2 | SIZE : DN300 | missing_capability (p24) | partial_match (p3): **stronger** | partial_match (p3, p22): **stronger** |

Manual check: fit None, coverage None (weighted).
System, ontology: compatibility 50.0, coverage 31.2 % (5/16, unweighted).
System, no ontology: compatibility 83.33333333333333, coverage 18.8 % (3/16, unweighted).

Not labelled in gold, so not compared:

- 5.1.1 `DUAL PLATE WAFER TYPE CHECK VALVE` (ontology: unsupported, no ontology: unsupported)
- 5.1.3 `CLASS : PN16 OR HIGHER` (ontology: full_match, no ontology: unsupported)
- 5.2.1 `DUAL PLATE CHECK VALVE` (ontology: unsupported, no ontology: unsupported)
- 5.2.2 `WAFER TYPE SPRING LOADED` (ontology: unsupported, no ontology: unsupported)
- 5.3 `Rating/Class : PN16 or higher,` (ontology: full_match, no ontology: unsupported)
- 5.4 `Product Standard : API 594` (ontology: unsupported, no ontology: unsupported)
- 5.5.1 `Body : ASTM A216 Grade WCB or equivelent` (ontology: missing_capability, no ontology: full_match)
- 5.5.2 `DISC : A351 CF8M or equivelent` (ontology: unsupported, no ontology: full_match)
- 5.5.3 `Type of Seat : nitrile or SS` (ontology: unsupported, no ontology: unsupported)
- 5.5.4 `Spring` (ontology: unsupported, no ontology: unsupported)
- 5.5.5 `Stop pin & hinge pin retainer : SS316 or equivelent` (ontology: unsupported, no ontology: unsupported)
- 5.5.6 `Bearing/Bush : SS316 or equivelent` (ontology: unsupported, no ontology: unsupported)
- 5.6 `End details : Wafer Type suitable for Clamping between flanges as per ASME B16.5 CL-150` (ontology: missing_capability, no ontology: unsupported)
- 5.7.1 `Max. face to face dim : 181mm` (ontology: unsupported, no ontology: unsupported)
- 5.7.2 `Max. OD : 404mm` (ontology: unsupported, no ontology: unsupported)

## Item 11, LnT_Valves, `lt_process_ball_valves.pdf`

| Req | Tender | Gold | ontology | no ontology |
|---|---|---|---|---|
| 11.title.1 | SIZE : dn25 mm | full_match (p3) | full_match (p3, p10): **agree** | - (-): **other catalogue** |
| 11.1.2 | CLASS : 800 OR HIGHER | full_match (p3) | full_match (p1, p11, p12): **agree, other page** | full_match (p1, p11, p12): **agree, other page** |
| 11.2.2 | Rating/Class : Class 800 or higher | full_match (p3) | full_match (p1, p11, p12): **agree, other page** | unsupported (-): **not decided**; no catalogue field names this attribute |
| 11.3.1 | Product Standard : BS EN ISO 17292 | full_match (p1) | unsupported (-): **not decided**; no catalogue field names this attribute | unsupported (-): **not decided**; no catalogue field names this attribute |
| 11.3.2 | Testing Standard : API 598 | full_match (p6) | unsupported (-): **not decided**; no catalogue field names this attribute | full_match (p6): **agree** |
| 11.4 | Body : ASTM A216 Grade WCB or equivelent, | full_match (p6) | unsupported (-): **not decided**; no catalogue field names this attribute | unsupported (p6): **not decided**; quote not found on the cited page (was full_match) |
| 11.5 | Ball & Stem : SS (ASTM A 182 or equivelent), | full_match (p6) | unsupported (-): **not decided**; free text: needs the LLM judge (not enabled) | unsupported (-): **not decided**; free text: needs the LLM judge (not enabled) |
| 11.6 | End details : Screwed female NPT Threads both ends | partial_match (p3) | unsupported (-): **not decided**; free text: needs the LLM judge (not enabled) | unsupported (-): **not decided**; free text: needs the LLM judge (not enabled) |

Manual check: fit 0.95, coverage 8/8 (weighted).
System, ontology: compatibility 100.0, coverage 30.0 % (3/10, unweighted).
System, no ontology: compatibility 100.0, coverage 22.2 % (2/9, unweighted).

Not labelled in gold, so not compared:

- 11.1.1 `BALL VALVE DN25 MM` (ontology: unsupported, no ontology: unsupported)
- 11.2.1 `BALL VALVE` (ontology: unsupported, no ontology: unsupported)

## Item 11, Delval, `delval_series_65_72_ball_valve_spec.pdf`

| Req | Tender | Gold | ontology | no ontology |
|---|---|---|---|---|
| 11.1.2 | CLASS : 800 OR HIGHER | full_match (p2) | full_match (p2): **agree** | unsupported (-): **not decided**; no catalogue field names this attribute |
| 11.2.2 | Rating/Class : Class 800 or higher | full_match (p2) | full_match (p2): **agree** | unsupported (-): **not decided**; no catalogue field names this attribute |
| 11.4 | Body : ASTM A216 Grade WCB or equivelent, | full_match (p1) | full_match (p2): **agree, other page** | full_match (p2): **agree, other page** |
| 11.5 | Ball & Stem : SS (ASTM A 182 or equivelent), | full_match (p1) | unsupported (-): **not decided**; free text: needs the LLM judge (not enabled) | unsupported (-): **not decided**; free text: needs the LLM judge (not enabled) |
| 11.6 | End details : Screwed female NPT Threads both ends | missing_capability (p1) | unsupported (-): **not decided**; free text: needs the LLM judge (not enabled) | unsupported (-): **not decided**; free text: needs the LLM judge (not enabled) |

Manual check: fit None, coverage None (weighted).
System, ontology: compatibility 100.0, coverage 40.0 % (4/10, unweighted).
System, no ontology: compatibility 100.0, coverage 11.1 % (1/9, unweighted).

Notes from gold:

- 11.1.2: Corrected from unsupported (manual "class 800 not stated"). The tender says "800 or higher", and this one-series bulletin lists Class 900 for a size range holding DN25. It gives no limit by size.
- 11.2.2: Corrected from unsupported, as 11.1.2.

Not labelled in gold, so not compared:

- 11.1.1 `BALL VALVE DN25 MM` (ontology: unsupported, no ontology: unsupported)
- 11.2.1 `BALL VALVE` (ontology: unsupported, no ontology: unsupported)
- 11.3.1 `Product Standard : BS EN ISO 17292` (ontology: unsupported, no ontology: unsupported)
- 11.3.2 `Testing Standard : API 598` (ontology: unsupported, no ontology: unsupported)
- 11.title.1 `SIZE : dn25 mm` (ontology: full_match, no ontology: -)

## Item 20, LnT_Valves, `lt_aquaseal_butterfly_check.pdf`

| Req | Tender | Gold | ontology | no ontology |
|---|---|---|---|---|
| 20.title.1 | SIZE : dn 100mm | unsupported (-) | full_match (p6): **stronger**; catalogues tie, none picked: lt_triple_offset_butterfly.pdf, lt_aquaseal_butterfly_check.pdf | - (-): **other catalogue** |
| 20.1.2 | CLASS : PN 16 Bar | full_match (p8) | partial_match (p3): **weaker**; product heading unclear: capped at partial; catalogues tie, none picked: lt_triple_offset_butterfly.pdf, lt_aquaseal_butterfly_check.pdf | - (-): **other catalogue** |

Manual check: fit None, coverage None (weighted).
System, ontology: compatibility 75.0, coverage 50.0 % (2/4, unweighted).
System, ontology: catalogues tie, none picked: lt_triple_offset_butterfly.pdf, lt_aquaseal_butterfly_check.pdf.
System, no ontology: compatibility 100.0, coverage 33.3 % (1/3, unweighted).

Not labelled in gold, so not compared:

- 20.1.1 `BUTTERFLY VALVE DN 100MM` (ontology: unsupported, no ontology: unsupported)
- 20.2 `Standard IS 13095 Application Tight Shut Off Valve End Connection Flangeless Wafer Valve Face to Face Dimension Wafer Short (PN 2.5) Class 125/150 Material of body Spheroidal Graphite Iron Material of Disc Spheroidal Graphite Iron Material of Shaft Stainless Steel Material of Seating Ring/ Seal Retaining Ring Deposited metal-suitable for duty or resilient material Material of Seat Elastomers Material of Internal Fastenings Stainless Steel Material of External Bolting Carbon Steel` (ontology: unsupported, no ontology: unsupported)

## Item 20, Delval, `delval_series_50_52_centric_bfv_spec.pdf`

| Req | Tender | Gold | ontology | no ontology |
|---|---|---|---|---|
| 20.1.2 | CLASS : PN 16 Bar | partial_match (p1) | unsupported (p2): **not decided**; label found but no value in a comparable unit; catalogues tie, none picked: delval_series_50_52_centric_bfv_spec.pdf, delval_series_44_49_hpbfv_spec.pdf | unsupported (-): **not decided**; no catalogue field names this attribute; catalogues tie, none picked: delval_series_50_52_centric_bfv_spec.pdf, delval_series_44_49_hpbfv_spec.pdf |

Manual check: fit None, coverage None (weighted).
System, ontology: compatibility None, coverage 0.0 % (0/4, unweighted).
System, ontology: catalogues tie, none picked: delval_series_50_52_centric_bfv_spec.pdf, delval_series_44_49_hpbfv_spec.pdf.
System, no ontology: compatibility None, coverage 0.0 % (0/3, unweighted).
System, no ontology: catalogues tie, none picked: delval_series_50_52_centric_bfv_spec.pdf, delval_series_44_49_hpbfv_spec.pdf.

Not labelled in gold, so not compared:

- 20.1.1 `BUTTERFLY VALVE DN 100MM` (ontology: unsupported, no ontology: unsupported)
- 20.2 `Standard IS 13095 Application Tight Shut Off Valve End Connection Flangeless Wafer Valve Face to Face Dimension Wafer Short (PN 2.5) Class 125/150 Material of body Spheroidal Graphite Iron Material of Disc Spheroidal Graphite Iron Material of Shaft Stainless Steel Material of Seating Ring/ Seal Retaining Ring Deposited metal-suitable for duty or resilient material Material of Seat Elastomers Material of Internal Fastenings Stainless Steel Material of External Bolting Carbon Steel` (ontology: unsupported, no ontology: unsupported)
- 20.title.1 `SIZE : dn 100mm` (ontology: unsupported, no ontology: -)

Items without gold: 10, 12, 13, 14, 15, 16, 17, 18, 19, 2, 21, 22, 23, 24, 25, 26, 27, 28, 3, 4, 6, 7, 8, 9.

## Catalogues: product label vs sub-type fit

| Item | Manufacturer | Catalogue | Gold | ontology | no ontology |
|---|---|---|---|---|---|
| 1 | LnT_Valves | `lt_aquaseal_butterfly_check.pdf` | least | match, chosen: **differs** | unknown, chosen: **differs** |
| 1 | Delval | `(none)` | not_found | not a candidate: agree | not a candidate: agree |
| 5 | LnT_Valves | `lt_aquaseal_butterfly_check.pdf` | least | match, chosen: **differs** | unknown, chosen: **differs** |
| 11 | LnT_Valves | `lt_process_ball_valves.pdf` | full | unknown, chosen: **differs** | unknown, chosen: **differs** |
| 11 | Delval | `delval_series_65_72_ball_valve_spec.pdf` | partial | unknown, chosen: **differs** | unknown, chosen: **differs** |
| 20 | LnT_Valves | `lt_aquaseal_butterfly_check.pdf` | partial | unknown: **differs** | unknown: **differs** |
| 20 | Delval | `delval_series_50_52_centric_bfv_spec.pdf` | partial | unknown: **differs** | unknown: **differs** |

## Ranking (not in gold, shown only)

ontology:

1. Delval: compatibility 77.8 %, coverage 12.8 %, low coverage
2. LnT_Valves: compatibility 66.5 %, coverage 37.4 %, low coverage
3. KSB: compatibility -, coverage 0.0 %, low coverage
4. Kirloskar Brothers: compatibility -, coverage 0.0 %, low coverage

no ontology:

1. LnT_Valves: compatibility 79.6 %, coverage 25.4 %, low coverage
2. Delval: compatibility 71.4 %, coverage 3.6 %, low coverage
3. KSB: compatibility -, coverage 0.0 %, low coverage
4. Kirloskar Brothers: compatibility -, coverage 0.0 %, low coverage
