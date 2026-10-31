# Write-up

The full write-up is plan step 17. This records decisions as they are made.

## Scoring decisions

**Ranking.** Manufacturers are ranked by compatibility, with a minimum evidence coverage
(`RANKING_MIN_COVERAGE_PCT`, starting at 50 %). A manufacturer below the minimum is listed after
the others and clearly marked "low coverage", so a high score on little evidence cannot lead the
ranking. Coverage is always shown. If every manufacturer is below the minimum, all are marked and
the order within that group is still by compatibility.

**Options written with "/".** A tender value such as "CA15/CF8M" does not say whether one option
is enough. If the catalogue shows only some of the options, the result is `partial_match` with
`needs_review`. If the tender writes "or" between options ("NITRILE OR SS"), one option found is a
`full_match`. Qualifiers such as "or equivalent" and "or higher" are not options.
