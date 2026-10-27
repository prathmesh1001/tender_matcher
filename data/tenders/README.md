# Tender corpus

One folder per GeM bid, named after the bid number (`GEM/2026/B/8119613` → `GEM_2026_B_8119613/`).

```
GEM_2026_B_8119613/
  tender.yaml        provenance: bid number, GeM bid id, item category, and per file the
                     source URL, fetch time (UTC), sha256 and role
  bid.pdf            the GeM bid document
  buyer_spec.pdf     buyer-uploaded technical specification
  boq_spec.pdf       BOQ technical specification
  boq_items.csv      BOQ line items
  atc.pdf|.xlsx      buyer's Additional Terms & Conditions
  mii_mse_doc*.pdf   Make-in-India / MSE declaration formats
```

Attachments are the files linked from inside `bid.pdf`. Empty responses and duplicates (the
same spec linked once per item) are recorded in `tender.yaml` but not stored twice.

Sources: 14 tenders downloaded from the public GeM BidPlus listing
(`https://bidplus.gem.gov.in/showbidDocument/<gem_bid_id>`) on 2026-10-22; 5 provided locally by
the user (their attachment URLs are unknown and recorded as such).

See `docs/tender_analysis.md` for what the corpus contains.
