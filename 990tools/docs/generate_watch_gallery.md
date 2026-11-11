# cms_reporting/generate_watch_gallery.py

**What:** HTML gallery for `cms_puf_npi_score` — mill pins, solo outliers, one detail page per mill cell.

**Why:** A DuckDB table is not a queue a human will read. Fun already ships DOT Rogues this way: a ranked table, a map, a detail page. Same shape, with the line that this is a watch list and not a fraud determination on every page.

**How:**

```bash
python -u irs990processor.py --step cms_puf_watch --nostats -v
# or
python3 -u cms_reporting/generate_watch_gallery.py \
    --db-path /Volumes/Data/final/irs990.duckdb
```

Writes `dot_reporting/reports/cms_puf_watch/` (`index.html` mills, `solo.html`, `mill-ll-*.html`) so Fun extras and the master index pick it up next to Rogues. Column headers and jargon (`Office-year stack`, `vs specialty p90`, mill/solo flags) use the Rogues hover pattern: `title` on `th` and `<span class="term">`.

**Campus leftover** on mill pins: OSM `building`/`amenity`/`healthcare=hospital` within 80 m, or a neighbor about six stories (20 m / 8 floors), or Overture class `hospital`. That is the Brigham / cancer-center scrap-footprint pattern. A one-story medical park (Doctor Cir) stays unflagged. Live Overpass at generate time; optional `_output/cms_puf_watch_height.tsv` from the mill-pin probe. `--no-osm` skips Overpass. Pins stay on the mill list; the flag is the cut.

`--step cms_puf_watch`. Tests: `python3 -m unittest test_cms_watch_gallery.py`.

Fun wrap copies the extra suite (do not full-rsync Fun):

```bash
python3 scripts/build_site.py --copy-focus cms_puf_watch --force-copy
```

from `grumpytechbro.com`.
