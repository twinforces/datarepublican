# cms_puf_score_processor.py

**What:** `cms_puf_npi_score` and `cms_puf_watch_cells` — a **ranked watch list**, not a fraud determination.

**Why:** Raw `hours_timed > 2080` at a house-sized pin is 5,816 individuals after dropping labs. That queue is mostly facility rounding, 15-minute therapy, cataract units, and hospital campuses. Paley’s 83k hours were 99% facility (99309 / 31525) while NPPES pointed at a house. Kvitle’s 12,080 units of 66984 is optometry volume. SC House Calls stacked 50 individuals with >2,080 **office** hours at `LL:34.0309:-81.0361` (2,743 sqft).

**Rules**

| Flag | Meaning |
|---|---|
| `office_heavy` | ≥70% of timed hours have Place_Of_Srvc `O` |
| `extreme_office` | office hours > 2,000 and ≥ 2× specialty 90th percentile |
| `small_site` | nearby building max sqft < 4,000 (±3 cells) |
| `stacked` | ≥5 individuals with **office** hours > 2,080 at the same `LL:` cell |
| `pin_collision` | that stack spans ≥2 states or ≥3 cities (geocode garbage) |
| `therapy_type` / `optometry_type` / `lab_type` | PT/OT/speech/chiro; optometry; clinical lab/pathology |
| `watch_solo` | I + office_heavy + extreme_office + small_site + not therapy/optometry/lab |
| `watch_mill` | I + stacked + max nearby sqft < 3,000 + not pin_collision + not lab + office hours > 2,080 |
| `watch` | solo or mill |
| `priority` | `office_vs_p90` (full if solo, ×0.25 if mill) + stack/5 + 4000/sqft + 10 if LEIE |

Hospital campuses stack too (Cedars, Brigham 75 Francis St). Mill uses a tighter 3,000 sqft cut than solo, and counts **office** hours so nursing-home rounders at a shared pin do not make a mill. Facility-heavy NPIs drop out of solo watch even if the pin is a house.

**How:** `python -u irs990processor.py --step cms_puf_score --nostats -v` (needs `cms_puf_npi_hours` + `cms_puf_service_hours`). Tests: `python3 -m unittest test_cms_puf_score.py`.

```sql
SELECT npi, name, first_name, provider_type, hours_office, office_vs_p90,
       max_area_near, npi_high_at_cell, watch_solo, watch_mill, priority, street, city, state
FROM cms_puf_npi_score
WHERE watch
ORDER BY priority DESC
LIMIT 50;

SELECT * FROM cms_puf_watch_cells
WHERE n_mill > 0
ORDER BY n_mill DESC;
```

**Production (2026-11-05):** 1,197,445 scored NPIs. `watch=1,338` (solo 1,242, mill 198 NPIs at 25 cells). Of the 5,816 raw-hour queue, 1,531 still watch. Zero LEIE overlap. Paley and Kvitle are off the list. SC House Calls is the top mill cell.

This is a queue for a human. It is not an accusation.

HTML: `python3 -u cms_reporting/generate_watch_gallery.py` → `dot_reporting/reports/cms_puf_watch/`. Doc: `docs/generate_watch_gallery.md`.

## How the 5,816 became a watch list

1. Split Place_Of_Srvc. Hours billed as facility did not happen at the NPPES pin.
2. Compare office hours to the specialty 90th percentile, not to a 2,080-hour year.
3. Drop therapy, optometry, lab, pathology from **solo** watch (unit grain is not a clinician-year).
4. A mill is five or more people with >2,080 **office** hours in a building under 3,000 sqft, at a pin that is one city.
5. Rank by how far above the specialty p90, how small the building, and how stacked the pin.
6. Still on the mill list and worth a human look: hospital pins that happen to have a small nearby footprint (75 Francis St / Brigham). Next filter is “is this a known campus?” not a louder hours number.

**Not fraud:** leftover PPP, SAM, or USASpending at the pin is a prior for the dossier, not a determination. Allcare NPI `1235424128` is not in the 2024 Physician PUF (home-care agency, Medicaid grain).
