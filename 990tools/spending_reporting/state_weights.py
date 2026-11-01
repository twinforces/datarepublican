#!/usr/bin/env python3
"""State population and Electoral College weights for federal $ heatmaps.

Population: Census Vintage 2024 resident estimates as of 2024-07-01
(NST-EST2024-POP). Electoral votes: 2024 College (2020 Census
apportionment; DC gets 3 under the 23rd Amendment). PR and territories
have no EV and are omitted from the Albers map.
"""

from __future__ import annotations

CENSUS_POP_VINTAGE = "2024"
CENSUS_POP_AS_OF = "2024-07-01"
CENSUS_POP_SOURCE = (
    "U.S. Census Bureau, Vintage 2024 NST-EST2024-POP, resident population "
    "as of 2024-07-01"
)

# Integer headcount (FRED / Census thousands × 1000).
POP_2024: dict[str, int] = {
    "AL": 5_157_699,
    "AK": 740_133,
    "AZ": 7_582_384,
    "AR": 3_088_354,
    "CA": 39_431_263,
    "CO": 5_957_493,
    "CT": 3_675_069,
    "DE": 1_051_917,
    "DC": 702_250,
    "FL": 23_372_215,
    "GA": 11_180_878,
    "HI": 1_446_146,
    "ID": 2_001_619,
    "IL": 12_710_158,
    "IN": 6_924_275,
    "IA": 3_241_488,
    "KS": 2_970_606,
    "KY": 4_588_372,
    "LA": 4_597_740,
    "ME": 1_405_012,
    "MD": 6_263_220,
    "MA": 7_136_171,
    "MI": 10_140_459,
    "MN": 5_793_151,
    "MS": 2_943_045,
    "MO": 6_245_466,
    "MT": 1_137_233,
    "NE": 2_005_465,
    "NV": 3_267_467,
    "NH": 1_409_032,
    "NJ": 9_500_851,
    "NM": 2_130_256,
    "NY": 19_867_248,
    "NC": 11_046_024,
    "ND": 796_568,
    "OH": 11_883_304,
    "OK": 4_095_393,
    "OR": 4_272_371,
    "PA": 13_078_751,
    "RI": 1_112_308,
    "SC": 5_478_831,
    "SD": 924_669,
    "TN": 7_227_750,
    "TX": 31_290_831,
    "UT": 3_503_613,
    "VT": 648_493,
    "VA": 8_811_195,
    "WA": 7_958_180,
    "WV": 1_769_979,
    "WI": 5_960_975,
    "WY": 587_618,
}

EV_YEAR = 2024
EV_SOURCE = (
    "2024 Electoral College: 2020 Census House apportionment plus two "
    "senators; DC 3 votes (23rd Amendment). 538 total."
)

ELECTORAL_VOTES_2024: dict[str, int] = {
    "AL": 9,
    "AK": 3,
    "AZ": 11,
    "AR": 6,
    "CA": 54,
    "CO": 10,
    "CT": 7,
    "DE": 3,
    "DC": 3,
    "FL": 30,
    "GA": 16,
    "HI": 4,
    "ID": 4,
    "IL": 19,
    "IN": 11,
    "IA": 6,
    "KS": 6,
    "KY": 8,
    "LA": 8,
    "ME": 4,
    "MD": 10,
    "MA": 11,
    "MI": 15,
    "MN": 10,
    "MS": 6,
    "MO": 10,
    "MT": 4,
    "NE": 5,
    "NV": 6,
    "NH": 4,
    "NJ": 14,
    "NM": 5,
    "NY": 28,
    "NC": 16,
    "ND": 3,
    "OH": 17,
    "OK": 7,
    "OR": 8,
    "PA": 19,
    "RI": 4,
    "SC": 9,
    "SD": 3,
    "TN": 11,
    "TX": 40,
    "UT": 6,
    "VT": 3,
    "VA": 13,
    "WA": 12,
    "WV": 4,
    "WI": 10,
    "WY": 3,
}


def attach_federal_weights(stats: list[dict]) -> list[dict]:
    """Add pop, electoral_votes, dollars_per_pop, dollars_per_ev on each row."""
    for s in stats:
        st = str(s.get("state") or "").upper()
        pop = POP_2024.get(st)
        ev = ELECTORAL_VOTES_2024.get(st)
        dollars = float(s.get("dollars") or 0)
        s["pop"] = pop
        s["electoral_votes"] = ev
        s["dollars_per_pop"] = (dollars / pop) if pop else 0.0
        s["dollars_per_ev"] = (dollars / ev) if ev else 0.0
    return stats
