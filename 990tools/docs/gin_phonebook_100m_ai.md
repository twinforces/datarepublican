# $100M ghost → phonebook EIN thumbs

**What:** 663 GIN (ghost name) → suggested 9-digit EIN pairs whose grants sum to ≥ $100M. After Charity-history GIN backfill (`charity_name_gin_backfill.py`).

**Why:** No backend votes. Thumbs-up/down “do these two orgs plausibly map?” on the expensive slice only.

**File:** `gin_phonebook_100m_pairs.tsv` (dollars, grant_rows, ghost_name, suggested_ein, suggested_name, gin).

**Prompt (one pair):** You are checking a 990-PF grantee string (ghost) against an IRS EIN the phone book suggested. Answer only `yes` or `no`: is it plausible that **ghost_name** is the same organization as **suggested_name** (EIN **suggested_ein**)? If the ghost is a generic/redacted/pass-through string, answer `no`.

Approved → default-collapse in UI; rejected → stay a ghost. Does not replace the Trust-the-phone-book toggle for the long tail.
