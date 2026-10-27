# Tender corpus analysis

What real GeM tenders contain, where the requirements live, and what that means for the
pipeline. Based on 19 tenders in `data/tenders/` (14 downloaded from GeM BidPlus on
2026-10-22, 5 provided locally). All quotes below were read from the cited file and page.

## Corpus

| Group | Tenders | Notes |
|---|---|---|
| Valves | 7907627 (cryogenic globe), 7956201 (globe/gate, ammonia), 8017367 (check/ball/globe/butterfly BOQ), 8063368 (pneumatic globe control), 8105855 (cast CS globe/NRV), 8119613 (triple-offset butterfly), 8123915 (globe ASA150), 8132165 (gate A217 WC9), 8157057 (butterfly IVGK), 8175587 (globe with flanges) | core evaluation set |
| Pumps | 7959356 (60 HP centrifugal), 8011173 (SS submersible), 8026686 (cooling-water centrifugal), 8071357 (submersible sewerage), 8167974 (HCl/NaOH centrifugal), 8169451 (water circulation) | core evaluation set |
| Out of category | 7865644 (thermocouples), 7951144 (forging per drawing), 8054155 (aluminium phosphide) | negative tests: most requirements should be `unsupported` |

## Where requirements live

1. **GeM category spec table** in `bid.pdf` ("Specification / Bid Requirement (Allowed Values)").
   Only 2 of 19 tenders (8054155 p5, 8071357). Clean (group, parameter, value) rows, e.g.
   `Physical Characteristics | Aluminium Phosphide % as per IS 6438 | 56%`.
2. **Item description string** (custom/BOQ bids), repeated in `bid.pdf` under "Item Category" and
   per consignee. One line packs many requirements, e.g. 7907627: `SIZE: 2", PN50, BW ENDS, STEM
   LENGTH 220 MM MIN, TEMP DESIGN (-196 DEG C) TO 120 DEG C, BODY A351CF8, ...`.
3. **Buyer specification attachment** (`buyer_spec*.pdf`, `boq_spec.pdf`, `boq_items.csv`): the
   most detailed source in 15 of 16 in-category tenders.
4. **ATC attachment** (`atc.*`): mostly commercial/legal, but sometimes holds the real technical
   annexure (7956201 `atc.pdf` p6: Annexure-2 item details and spec table).

The same requirement often appears in 2–3 of these places; requirements must be de-duplicated
across sources, keeping the most specific one and citing where it was found.

## Document formats (why OCR and layout matter)

| Format | Where seen | Effect today |
|---|---|---|
| Text layer | every `bid.pdf`; 8119613, 8157057, 8017367, 8132165 specs | fine |
| Image scan | 8011173, 8123915 (×3), 8167974, 8169451 specs; 8105855 MII doc | OCR works (conf 0.84–0.94) |
| **Outlined text** (glyphs drawn as vector paths, no images, no text) | 7907627 spec; 7956201 ATC (24/26 pages); 7865644 ATC (82/95) | **missed**: Step 3 only treats image-covered pages as scans |
| Fixed-width "key : value" text | 8119613 (`PRESSURE RATING : CLASS #150`) | easy to split deterministically |

Problems found on real pages:
- **`eng+hin` OCR corrupts material grades**: `SS316` read as `55316` (7956201 atc p6, 7907627
  spec p1), `SS304` as `55304` (8011173 spec p1–2). English-only OCR reads the same page correctly
  (`Stem/Hinge pin SS316`).
- **Scanned tables lose their columns**: 8011173 spec p1 OCR returns labels (`Rated Capacity`,
  `Head @ duty point`) and values (`1500 lpm`, `54 mwe`) as separate runs, so the label–value
  pairing is lost.
- **Hindi in the GeM text layer is mojibake** (`बड संAया/Bid Number`); the English half of each
  bilingual label is clean and should be used.

## Parameters to match

**Valves**: valve type; design standard (BS 1873, API 600/602/594/609/6D, BS EN ISO 15761/17292,
EN 593, IS 13095); size (inch, NB, DN, mm: `3/4"`, `40 NB`, `DN125`, `250 MM`); pressure rating
(`Class #150/300/800`, `PN10/16/50`, `ASA 150`); end connection (flanged RF/RTJ, BW, SW, wafer,
NPT/BSP thread); bonnet/operation (bolted, extended bonnet, hand wheel, gear, pneumatic ATO/ATC);
material per part (body `ASTM A216 WCB`, `LCB`, `A217 WC9`, `A351 CF8M`, `A105`, `CI`, `SGI`;
disc; trim; stem; seat `EPDM`/`PTFE`/stellite; gasket; packing; bolting `B7/2H`); service fluid
(water, liquid ammonia, oxygen, steam); design temperature/pressure (`-196 DEG C TO 120 DEG C`,
`40 bar`); testing (API 598, EN 12266, IBR); leakage class; dimensions (`Max. face to face dim:
60mm`); torque (`820 Nm`); max flow.

**Pumps**: pump type (centrifugal horizontal, submersible, monoblock, stages); duty point flow
(`110.160 m3/hr`, `30.60 L/S`, `1500 lpm`) and head (`32.20 MTR`, `54 mwe`); speed (`2930 RPM`,
`2900 +/- 5%`); power (`15 KW (20 HP)`); motor (IP55, insulation class F, 415 V 3-phase 50 Hz);
suction/delivery size (`Not more than 40 NB`); material per part (casing `CAST IRON IS 210: FG
260`, impeller `BRONZE IS:318 GR-LTB2`, shaft `SS ASTM A276-410`, non-metallic for 33 % HCl);
seal (`SiC Vs SiC`); fluid, specific gravity, temperature, solids size; test standards (IS 14220
/ ISO 9906 Grade II, IS 5120, IS 9283).

**Not technically verifiable from catalogues** (classified, not matched): EMD/ePBG, turnover and
experience criteria, MII/MSE, delivery period, warranty, documents to submit (MTC EN 10204 3.1,
TPI by Lloyd/TUV), packing, invoicing.

## Hard cases seen in the corpus

- **Direction words**: `min`, `OR HIGHER` (8017367), `Not more than` (8167974), `Up to 80° C`,
  `Max.`, tolerances `2900 +/- 5%` (7959356), ranges `-40 Deg. C to +40 Deg. C`.
- **Unit variety**: inch ↔ NB ↔ DN ↔ mm; `kg/cm²` ↔ bar; m³/h ↔ L/s ↔ lpm; HP ↔ kW; `mwe`/`MTR`
  for head. `Class` and `PN` are different systems and must not be converted into each other.
- **"Or equivalent" materials**: `ASTM A216 Grade WCB or equivelent`, `SS304 or superior`; needs
  material equivalence in the ontology, otherwise the honest result is `partial_match`.
- **Multiple line items per tender**, each with its own size/rating (8017367 has 28 items):
  requirements must be attached to a line item, and a manufacturer can match some items and not
  others.
- **Named makes**: `Make: M/s. Intervalve Poonawalla Ltd.` (8157057), `Make KSB` (7959356),
  `Kirloskar (KBL), BE, Crompton greaves or equivalent` (8169451), acceptable-make list
  `L&T Valves, BHEL, Key-Tech, BHDT, KSB` (7956201 atc p8).
- **Out-of-category tenders** must not produce confident matches.

## Ingestion: follow the links inside the bid

`bid.pdf` is only the wrapper. The documents that carry the specs are reached through links
inside it (and, rarely, inside its attachments). Following every link up to depth 3 on all 19
tenders (`data/tenders/*/tender.yaml` records each URL, depth and the file it was found in):

| Link pattern | Content | Matching value |
|---|---|---|
| `mkp…/CatalogAttrs/SpecificationDocument/…` | buyer technical specification | core |
| `mkp…/OrderItem/BoqDocument/…` | BOQ technical specification / NIT | core |
| `mkp…/OrderItem/BoqLineItemsDocument/…` | BOQ line items (CSV: item no., title, qty) | defines line items |
| `bidplus…/bid/showCatalogue/…` | GeM category spec page (HTML) | same rows as the bid table, without units |
| `fulfilment…/contract/slafds?…` | buyer ATC (PDF or XLSX) | sometimes holds the technical annexure |
| `…downloadMseMiiDoc…`, `documentdownload` | MII/MSE formats, buyer letters | compliance only |
| `…downloadOmppdfile/`, `admin…/apis/v1/gtc` | always empty / generic GTC | skip |

Findings:
- **Use the GeM originals, not re-saved copies.** The user's local copies of 7907627's spec and
  7956201's NIT were outlined (no text layer); the same documents fetched from the links have a
  normal text layer (`buyer_spec_2.pdf`, `boq_spec.pdf`).
- The 5 local tenders were missing 6 linked documents (BOQ specs, BOQ CSVs, buyer specs, ATC).
- **GeM spec tables continue across pages.** 8071357's table splits at the page break (p3–p4);
  on p4 the repeated header is not recognised, so the performance rows (`Capacity, liter/hour |
  16000`, `Power | 2.0 (horsepower)`, `Head | 14.0 (meter)`) were missed by a header-based filter.
  The PDF gives units in brackets; the `showCatalogue` HTML drops them, so the PDF stays primary.

## Catalogue coverage of tender parameters

Official catalogues collected so far (`data/manufacturers/`): L&T Valves (6) and DelVal (3), all
with a text layer and table-heavy (~1,300 table rows). Kirloskar's site blocks scripted downloads
and no reachable official KSB/Intervalve PDFs were found. Each tender requirement below was
checked by hand against the cited page (FULL / PARTIAL / NONE = no evidence in that document).

**8119613 triple-offset butterfly vs `lt_triple_offset_butterfly.pdf`** (10 full, 1 partial, 3 none)

| Requirement | Result | Evidence |
|---|---|---|
| Triple-offset, metal seated, torque seated | FULL | p1 "API 609 Triple-offset Butterfly Valves"; p9 "torque-seated valves" |
| Size 250 / 300 mm | FULL (range) | p1 "3" to 100" (80 mm to 2500 mm)" |
| Class #150 | FULL | p1 "ASME Class 150 to 1500" |
| API 609 design | FULL | p4 table "Design · API 609 (Category B), ASME B16.34" |
| Wafer ends | FULL | p4 table "Ends · Wafer/ Lugged" |
| Body ASTM A216 WCB | FULL | p4 materials "Body · A 216 Gr. WCB/ WCC" |
| Disc A217 CA15 / CF8M | PARTIAL | WCB-body variant has a WCB disc (p4); CF8M disc only in the stainless variant (p5) |
| Shaft A479 SS410 / 17-4PH | FULL (equivalence) | p4 "A 479 Type SS 410/ A 564 Type 630" (Type 630 = 17-4PH) |
| Body seat Stellite 21 | FULL | p4 "Body seat hard-faced with Stellite 21 or 6" |
| Hand gear operated | FULL | p4 "Gear Unit · Worm type (self-locking)" |
| Zero leakage | FULL (synonym) | p9 "Bi-directional bubble-tight sealing" |
| Torque at shaft 820 Nm | NONE | torque is only described ("reduces operating torque"), no values |
| Max flow 650 m3/h | NONE | no flow/Cv data |
| Fluid: water | weak | p3 lists water among industries, not a rating |

**8157057 butterfly, 450 mm, PN10, CI body, SGI disc, EPDM, EN 593**
- `lt_aquaseal_butterfly_check.pdf` p4: "sizes from 50 mm (2") to 600 mm (24"), in PN 10" → size and
  rating FULL by range; body cast iron, EPDM liner, EN 593 on p4; disc in this model is CI/CF8M
  (SG iron only in other models, p8) → PARTIAL.
- `delval_series_50_52_centric_bfv_spec.pdf` p1: "14"-24" (DN 350 – DN 600) 150 psi (10.0 barg)" →
  FULL only after psi→bar and PN10≈10 bar; body "ASTM A126 Gr B" (= cast iron), disc "ASTM A536 Gr.
  65-45-12" (= SG/ductile iron) → FULL by material equivalence; gear/handwheel not in this bulletin
  → NONE. Same page: "*Max. pressure rating limited to 6 Bar" for some options (conditional limit).
- The tender also names a make: "Make: M/s. Intervalve Poonawalla Ltd." Neither catalogue can meet a
  make requirement; it must be classified separately and shown to the bid manager.

**7956201 globe BS 1873 #300, ammonia vs `lt_gate_globe_check_api600.pdf`**: BS 1873 (p5–6), class
300 (p6), bolted bonnet (p6), body A352 LCB rated "-46°C to 343°C" (p5, covers the tender's -40 to
+40 °C by range), trim no. 16 (p9), graphite gasket (p6), studs A193 B7 / nuts A194 2H (p10), API 598
(p4), flanged ends (p9). **Not stated**: liquid-ammonia suitability, the 40 bar design pressure as a
value. **`lt_gate_globe_check_api602.pdf`** covers the forged class 800 gate (API 602 p1, class 800
p3–6, LF2 p3–4, Stellite #6 p4).

**7907627 cryogenic globe vs `lt_cryogenic_valves.pdf`** (3-page brochure): only the design standard
(p2 "Design: BS 6364") is stated. Extended bonnet, -196 °C, CF8 body, PN50, BW ends, oxygen
cleaning and EN 10204 3.1 are all absent → brochure-level documents give very low coverage.

**8017367 dual-plate check / ball valves**: API 594 dual-plate check (Aquaseal p24–25), ISO 17292 /
API 6D, class 800 for DN 8–50, WCB body, CF8M ball, API 598 testing (process ball valves p1, p3, p6).

### Which parameters are worth matching

1. **Reliably in catalogues, usually in tables**: design standard, pressure class/PN, size range,
   end connection, material per component per grade, operator type, test standard, temperature
   range per material, fire-safe/fugitive-emission standards.
2. **Matchable only with logic**: size inside a range; pressure with unit conversion (psi, barg,
   kg/cm², PN); material equivalence (A564 Type 630 = 17-4PH, A126 = cast iron, A536 = SG iron);
   synonyms and spelling (bubble-tight = zero leakage, dual-plate = dual plate, Triple-offset).
3. **Rarely in catalogues → expect `unsupported`**: duty-specific values (torque, flow/Cv, stem
   length, face-to-face maxima unless a dimension table exists), service suitability (ammonia,
   oxygen cleaning), certificates (EN 10204 3.1, IBR, TPI), make lists, warranty, delivery.
4. **Variant-dependent specs**: catalogues give materials per grade column and limits per option;
   a match must pick one variant and take all its values from that column (never mix columns).

## Implications for the build

1. Scanned-page detection must also catch outlined text (many vector paths, no text layer).
2. OCR: run `eng` first and use `eng+hin` only for lines that contain Devanagari, so Latin spec
   codes keep the English reading.
3. OCR'd tables need row reconstruction from line boxes (pair label and value on the same row).
4. Tender parsing is extraction, not retrieval: GeM spec tables, BOQ CSV and `key : value` lines
   are parsed deterministically; only free-text items and annexure prose need an LLM.
5. The data model needs **tender line items**; requirements and results hang off an item.
6. Manufacturers to collect first, because tenders name them: KSB, Kirloskar Brothers,
   Intervalve Poonawalla, L&T Valves, Crompton Greaves; plus the assignment's Duncan and Delval.
7. Tender ingestion must follow the links in `bid.pdf` (depth ≤ 3), skip the generic ones, and
   prefer GeM originals over re-saved copies.
8. Table extraction must merge tables that continue across pages and drop repeated header rows.
9. **Bug**: merged-cell fill-down must only fill *vertical* spans. A cell spanning several
   columns (L&T TOBV p4 "Packing · Graphite – 5 braided rings", one cell across all four grade
   columns) currently gets the shaft values copied into the other columns, which invents
   material claims. Horizontal spans must repeat the spanning cell's own text.
10. The ontology needs value logic (ranges, units incl. psi/barg/PN, material equivalence,
    synonyms) and variant-aware matching; keyword search alone gives false hits ("torque").
