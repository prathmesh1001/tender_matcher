# Manufacturer documents

One folder per manufacturer. Each folder holds the downloaded PDFs plus a `sources.yaml`
recording where every file came from. A PDF that is not listed in `sources.yaml` is never
ingested.

```
data/manufacturers/
  KSB/
    sources.yaml
    ksb_etanorm_datasheet.pdf
```

## `sources.yaml`

```yaml
manufacturer: KSB
documents:
  - file: ksb_etanorm_datasheet.pdf      # filename in this folder
    category: valves_pumps               # lower_snake_case, matches backend/ontology/<category>.yaml
    product_types: [centrifugal_pump]    # every product type the document covers (ontology names)
    sub_types: [end_suction]             # optional: only if the whole document is that design
    title: Etanorm type series booklet
    doc_type: datasheet                  # datasheet | catalogue | brochure | manual | other
    url: https://...                     # exact public URL it was downloaded from
    fetched_at: 2026-10-22T10:00:00+05:30  # download time, with timezone
    publish_date: 2024-05-01             # date printed on the document, or omit if none
    sha256: <64 lowercase hex chars>     # hash of the PDF as downloaded
    notes: optional free text
```

Get the hash with `python -c "import hashlib,sys;print(hashlib.sha256(open(sys.argv[1],'rb').read()).hexdigest())" file.pdf`
(or `Get-FileHash file.pdf -Algorithm SHA256` in PowerShell, lower-cased).

`product_types` is checked by hand once per document (read the title and contents): retrieval
only looks at a document for the product types it declares, and a chunk can only be tagged with
one of them. A one-product datasheet lists one type; a "Gate, Globe & Check" catalogue lists all
three. Vendor series/brand names (e.g. "AquaSeal", "Series 50") need no entry anywhere: the
declaration maps the document to the product.

Validation rejects: unknown fields, product types or sub-types the ontology does not define, a missing timezone, a bad hash or URL, the same PDF registered
twice, and a file whose current hash no longer matches (e.g. a newer edition was saved over it;
register it as a new entry instead).
