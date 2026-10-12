// "Who is returning" view: county measures computed from the statewide voter roll, where each returned
// 2026 mail ballot is matched to the voter's 2022 and 2024 history. Every R-vs-D measure compares the
// parties inside the same county, so a county that is slow to log returns cannot look like a partisan shift.

const ROLL_GROUPS = {
	'A': ['Mail 2022 and 2024', 'habitual mail'],
	'B': ['Election Day 2022, mail 2024', 'already switched'],
	'C': ['Mail 2022, Election Day 2024', ''],
	'D': ['Election Day 2022 and 2024', 'habitual Election Day'],
	'E': ['Mail 2024, skipped 2022', '2024 surge'],
	'F': ['Election Day 2024, skipped 2022', '2024 surge'],
	'G': ['Voted 2022, skipped 2024', ''],
	'H': ['Registered after Nov 2024', 'new'],
	'I': ['No 2022 or 2024 vote', 'older registration']
};
const ROLL_AGES = { 'all': 'All ages', 'a': '18–29', 'b': '30–44', 'c': '45–64', 'd': '65+' };

const ROLL_MEASURES = {
	'ratio': { 'label': 'R vs D return rate', 'sub': 'within the group', 'kind': 'ratio' },
	'relhab': { 'label': 'R vs D, relative to habitual mail', 'sub': 'group rate ÷ habitual rate', 'kind': 'ratio' },
	'share': { 'label': 'Share of returns from the group', 'sub': 'R minus D', 'kind': 'share' },
	'rate': { 'label': 'Return rate', 'sub': 'all parties', 'kind': 'rate' },
	'pace22': { 'label': 'R vs D pace, 2026 vs 2022', 'sub': 'data.pa.gov, same point', 'kind': 'ratio' }
};

// Each question sets the switches; touching any switch afterwards turns it into a custom view.
const ROLL_PRESETS = {
	'logging': {
		'plain': 'Before reading any other question: has this county entered any 2026 returns yet? A county that has not started looks empty on every other measure.',
		'good': 'Not a partisan measure. Darker counties have enough returns logged to read.',
		'bad': 'Also not partisan. Pale counties have not logged returns yet; skip them on the other questions until they do.',
		'q': 'Has the county started logging returns?', 'measure': 'rate', 'groups': 'A', 'age': 'all',
		'read': 'Share of habitual mail voters (all parties) whose 2026 return is logged in the roll. Counties near ' +
			'zero have not entered returns yet; their other measures stay gray.'
	},
	'pace22': {
		'plain': 'Compared with the 2022 midterm at the same point, are Republicans getting their mail ballots back as fast, relative to Democrats?',
		'good': 'Above 1.00×: R ballots are coming back faster, relative to D, than in 2022. The R mail vote is more engaged than in a midterm Republicans lost badly.',
		'bad': 'Below 1.00×: R ballots are coming back slower than in 2022, which would mean softer enthusiasm even against a weak baseline.',
		'q': 'Are R ballots coming back slower than in 2022?', 'measure': 'pace22', 'groups': '', 'age': 'all',
		'read': 'R returns per approved R application, divided by the same for D, in 2026 and in 2022 at the same ' +
			'point, then 2026 over 2022. 1.00× means the R pace relative to D is unchanged from the midterm. ' +
			'Single counties are noisy because 2022 had few ballots back this early.'
	},
	'habit': {
		'plain': 'Are the Republicans who reliably vote by mail sending ballots back as fast as reliable Democratic mail voters?',
		'good': 'Close to 1.00×, or climbing from day to day. Counties above the statewide value are where the R mail base is most engaged.',
		'bad': 'Sliding further below the statewide level, or counties well under it: the core R mail base is sitting on its ballots. Some lag is normal because D mail voters return earlier every cycle.',
		'q': 'Are habitual R mail voters keeping pace?', 'measure': 'ratio', 'groups': 'A', 'age': 'all',
		'read': 'Among people who voted by mail in both 2022 and 2024, the R return rate divided by the D rate. ' +
			'D voters return earlier every cycle, so the statewide level is below 1; look for counties that ' +
			'break from it.'
	},
	'surge': {
		'plain': 'The 2024 R surge was largely voters who skipped the 2022 midterm. Are those voters coming back, or sitting this one out the way midterm voters usually do?',
		'good': 'Near or above 1.00×: R surge voters are keeping up with their own party\'s regulars about as well as D surge voters are. Republicans are holding new 2024 voters, which is their main upside in a midterm.',
		'bad': 'Well below 1.00×: R surge voters are dropping off faster than D surge voters, pointing back toward a 2022-style electorate.',
		'q': 'Are 2024 surge voters coming back?', 'measure': 'relhab', 'groups': 'E', 'age': 'all',
		'read': 'People who voted by mail in 2024 but skipped 2022, measured against habitual mail voters of the ' +
			'same party and county, R relative to D. Below 1 means R surge voters are lagging their own base ' +
			'more than D surge voters are. Surge voters who voted in person on Election Day 2024 mostly will not show up ' +
			'until Election Day.'
	},
	'switch': {
		'plain': 'Republicans are returning more mail ballots than in 2022. Are those extra votes, or the same people who used to vote in person on Election Day now voting by mail?',
		'good': 'Small or shrinking R-minus-D gap: R mail growth is coming from voters who were not Election Day voters in 2022, so it is more likely to be added votes.',
		'bad': 'Large R-heavy gap: the growth is mostly the same voters changing method, so higher R mail counts do not mean more R votes overall. Not harmful in itself, since a banked vote cannot be lost to weather or lines, but not growth either.',
		'q': 'Is R mail growth just Election Day voters switching to mail?', 'measure': 'share', 'groups': 'BD', 'age': 'all',
		'read': 'Share of each party\'s 2026 returns from people who voted in person on Election Day 2022, R minus D. A large ' +
			'R-heavy share means R mail growth vs 2022 is largely the same voters changing method, not new votes. ' +
			'Group B had already switched in 2024.'
	},
	'newv': {
		'plain': 'Are Republicans drawing in people who did not vote in 2022: 2024-only voters, new registrants and occasional voters?',
		'good': 'Above zero (R-heavy): a larger share of R returns than D returns comes from outside the 2022 electorate. Republicans are expanding the midterm electorate, not just re-running it.',
		'bad': 'Below zero (D-heavy): Democrats are doing better at bringing in voters who skipped 2022.',
		'q': 'Are voters who skipped 2022 showing up?', 'measure': 'share', 'groups': 'EFHI', 'age': 'all',
		'read': 'Share of each party\'s 2026 returns from people who did not vote in 2022, R minus D. R-heavy ' +
			'means R returns are drawing more on voters beyond the 2022 midterm electorate.'
	}
};

// Red when the value leans R relative to the center, blue when it leans D.
const ROLL_DIV = ['#9e241e', '#c4312a', '#e8837a', '#f3b5ae', '#f1f1f2', '#bccdf3', '#98b2ec', '#6d8fe2', '#2456c9'];
const ROLL_RATIO_STEPS = [1.6, 1.33, 1.15, 1.05];
const ROLL_SHARE_STEPS = [0.10, 0.06, 0.03, 0.01];
const ROLL_RATE_BINS = [[0.30, '#14532d'], [0.20, '#2f7d4f'], [0.10, '#5fa77a'], [0.05, '#9fcdae'], [0.02, '#d4ead9'], [-1, '#f5f7f5']];
// Composition bar: history groups folded into five segments.
const ROLL_SEGS = [
	['A', 'Mail 2022 and 2024', '#1f4e79', '#fff'],
	['BD', 'Election Day 2022', '#6b8fb3', '#fff'],
	['CG', 'Other 2022 voters', '#b9bcc2', 'var(--dr-ink)'],
	['EF', '2024 only', '#c9a227', 'var(--dr-ink)'],
	['HI', 'No 2022/2024 vote', '#ecd894', 'var(--dr-ink)']
];

var ROLL = null;          // the roll JSON
var ROLL_IX = null;       // county -> cohort -> age -> party -> [registered, returned]
var ROLL_LOGGING = {};    // county -> true once it is logging returns
var ROLL_PIN = null;      // county the table is scoped to, or null for statewide

function rollIndex(data) {
	ROLL = data;
	ROLL_IX = {};
	$.each(data['cells'], function (county, rows) {
		const ix = ROLL_IX[county] = {};
		$.each(rows, function (ignore, r) {
			const coh = r[0][0], age = r[0][1], p = r[0][2];
			ix[coh] = ix[coh] || {};
			ix[coh][age] = ix[coh][age] || {};
			ix[coh][age][p] = [r[1], r[2]];
		});
	});
	// Logging: at least 2% of habitual mail voters logged and 50 returns in the county.
	$.each(ROLL_IX, function (county) {
		const a = rollSum(county, 'A', 'all', 'DRO'), t = rollSum(county, 'ABCDEFGHI', 'all', 'DRO');
		ROLL_LOGGING[county] = a[0] > 0 && a[1] / a[0] >= 0.02 && t[1] >= 50;
	});
}

/** [registered, returned] summed over cohorts, an age key ('all' or one band) and parties. */
function rollSum(county, groups, age, parties) {
	var n = 0, r = 0;
	const ix = ROLL_IX[county];
	if (!ix) {
		return [0, 0];
	}
	for (var i = 0; i < groups.length; ++i) {
		const g = ix[groups[i]];
		if (!g) {
			continue;
		}
		$.each(g, function (a, byParty) {
			if (age !== 'all' && a !== age) {
				return;
			}
			for (var j = 0; j < parties.length; ++j) {
				const c = byParty[parties[j]];
				if (c) {
					n += c[0];
					r += c[1];
				}
			}
		});
	}
	return [n, r];
}

/**
 * R return rate ÷ D return rate across counties, compared inside each county: R returns over the R returns
 * expected at the same county's D rate. Equals the plain ratio for one county. Null when either party has
 * fewer than 20 returns.
 */
function rollRatio(counties, groups, age) {
	var o = 0, e = 0, dr = 0;
	$.each(counties, function (ignore, c) {
		const R = rollSum(c, groups, age, 'R'), D = rollSum(c, groups, age, 'D');
		if (D[0] > 0) {
			o += R[1];
			e += R[0] * D[1] / D[0];
			dr += D[1];
		}
	});
	return o >= 20 && dr >= 20 && e > 0 ? o / e : null;
}

function rollState() {
	return {
		'measure': $('input[name=rollMeasure]:checked').val(),
		'groups': $('input[name=rollGroup]:checked').map(function () { return this.value; }).get().join(''),
		'age': $('input[name=rollAge]:checked').val(),
		'center': $('input[name=rollCenter]:checked').val()
	};
}

/**
 * One county's value for the measure, or null when it has too few ballots or is not logging.
 * Returns {v, parts} where parts carries the numbers for the tooltip.
 */
function rollCounty(county, s) {
	const m = s['measure'], all = 'ABCDEFGHI';
	if (m === 'pace22') {
		const p = ROLL['pace'][county];
		if (!p) {
			return null;
		}
		function pace(o) {
			return p[o] >= 20 && p[o + 1] >= 50 && p[o + 2] > 0 && p[o + 3] > 0 ?
				(p[o] / p[o + 2]) / (p[o + 1] / p[o + 3]) : null;
		}
		const p22 = pace(0), p26 = pace(4);
		return { 'v': p22 && p26 ? p26 / p22 : null, 'p22': p22, 'p26': p26, 'raw': p };
	}
	const R = rollSum(county, s['groups'], s['age'], 'R'), D = rollSum(county, s['groups'], s['age'], 'D');
	const O = rollSum(county, s['groups'], s['age'], 'O');
	const out = { 'R': R, 'D': D, 'O': O, 'v': null };
	if (m === 'rate') {
		const n = R[0] + D[0] + O[0], r = R[1] + D[1] + O[1];
		out['v'] = n >= 50 ? r / n : null;
		return out;
	}
	if (!ROLL_LOGGING[county]) {
		return out;
	}
	if (m === 'ratio') {
		if (R[1] >= 20 && D[1] >= 20) {
			out['v'] = (R[1] / R[0]) / (D[1] / D[0]);
		}
	} else if (m === 'relhab') {
		const hR = rollSum(county, 'A', s['age'], 'R'), hD = rollSum(county, 'A', s['age'], 'D');
		out['hR'] = hR;
		out['hD'] = hD;
		if (R[1] >= 15 && D[1] >= 15 && hR[1] > 0 && hD[1] > 0) {
			out['relR'] = (R[1] / R[0]) / (hR[1] / hR[0]);
			out['relD'] = (D[1] / D[0]) / (hD[1] / hD[0]);
			out['v'] = out['relR'] / out['relD'];
		}
	} else if (m === 'share') {
		const tR = rollSum(county, all, s['age'], 'R'), tD = rollSum(county, all, s['age'], 'D');
		out['tR'] = tR;
		out['tD'] = tD;
		if (tR[1] >= 30 && tD[1] >= 30) {
			out['shR'] = R[1] / tR[1];
			out['shD'] = D[1] / tD[1];
			out['v'] = out['shR'] - out['shD'];
		}
	}
	return out;
}

/** Statewide value, pooled over logging counties (all counties for the return rate). */
function rollStatewide(s) {
	const m = s['measure'], all = 'ABCDEFGHI';
	const counties = Object.keys(ROLL_IX);
	var o = 0, e = 0, o2 = 0, e2 = 0, used = 0;
	if (m === 'pace22') {
		var acc = [0, 0, 0, 0];
		$.each(counties, function (ignore, c) {
			const x = rollCounty(c, s);
			if (!x || x['v'] === null) {
				return;
			}
			const p = x['raw'];
			acc[0] += p[0]; acc[1] += p[2] * p[1] / p[3];
			acc[2] += p[4]; acc[3] += p[6] * p[5] / p[7];
			used++;
		});
		const p22 = acc[0] / acc[1], p26 = acc[2] / acc[3];
		return { 'v': p26 / p22, 'p22': p22, 'p26': p26, 'used': used };
	}
	var tot = { 'R': [0, 0], 'D': [0, 0], 'O': [0, 0] };
	$.each(counties, function (ignore, c) {
		if (m !== 'rate' && !ROLL_LOGGING[c]) {
			return;
		}
		used++;
		const R = rollSum(c, s['groups'], s['age'], 'R'), D = rollSum(c, s['groups'], s['age'], 'D');
		const O = rollSum(c, s['groups'], s['age'], 'O');
		$.each({ 'R': R, 'D': D, 'O': O }, function (p, x) { tot[p][0] += x[0]; tot[p][1] += x[1]; });
		if (m === 'ratio' && D[0] > 0) {
			o += R[1]; e += R[0] * D[1] / D[0];
		} else if (m === 'relhab') {
			const hR = rollSum(c, 'A', s['age'], 'R'), hD = rollSum(c, 'A', s['age'], 'D');
			if (hR[0] > 0 && hD[0] > 0) {
				o += R[1]; e += R[0] * hR[1] / hR[0];
				o2 += D[1]; e2 += D[0] * hD[1] / hD[0];
			}
		} else if (m === 'share') {
			o += R[1]; e += rollSum(c, all, s['age'], 'R')[1];
			o2 += D[1]; e2 += rollSum(c, all, s['age'], 'D')[1];
		}
	});
	var out = { 'tot': tot, 'used': used, 'v': null };
	if (m === 'rate') {
		const n = tot['R'][0] + tot['D'][0] + tot['O'][0];
		out['v'] = n ? (tot['R'][1] + tot['D'][1] + tot['O'][1]) / n : null;
	} else if (m === 'ratio') {
		out['v'] = e ? o / e : null;
	} else if (m === 'relhab') {
		out['relR'] = e ? o / e : null;
		out['relD'] = e2 ? o2 / e2 : null;
		out['v'] = out['relR'] && out['relD'] ? out['relR'] / out['relD'] : null;
	} else if (m === 'share') {
		out['shR'] = e ? o / e : 0;
		out['shD'] = e2 ? o2 / e2 : 0;
		out['v'] = out['shR'] - out['shD'];
	}
	return out;
}

function rollFmt(kind, v) {
	if (v === null || v === undefined || !isFinite(v)) {
		return '–';
	}
	if (kind === 'ratio') {
		return v.toFixed(2) + '×';
	}
	if (kind === 'rate') {
		return (v * 100).toFixed(1) + '%';
	}
	const side = v < 0 ? 'dem' : 'rep';
	return '<span class="pa-' + side + '">' + (v < 0 ? 'D' : 'R') + ' +' + (Math.abs(v) * 100).toFixed(1) + ' pts</span>';
}

function rollNeutral(kind) {
	return kind === 'ratio' ? 1 : 0;
}

/** Bin index into ROLL_DIV (0 = most R) for a value against the center. */
function rollBin(kind, v, center) {
	const steps = kind === 'ratio' ? ROLL_RATIO_STEPS : ROLL_SHARE_STEPS;
	const x = kind === 'ratio' ? v / center : v - center;
	for (var i = 0; i < steps.length; ++i) {
		if (x >= steps[i]) {
			return i;
		}
	}
	for (var j = steps.length - 1; j >= 0; --j) {
		if (kind === 'ratio' ? x <= 1 / steps[j] : x <= -steps[j]) {
			return ROLL_DIV.length - 1 - j;
		}
	}
	return 4;
}

function rollColor(kind, v, center) {
	if (kind === 'rate') {
		for (var i = 0; i < ROLL_RATE_BINS.length; ++i) {
			if (v >= ROLL_RATE_BINS[i][0]) {
				return ROLL_RATE_BINS[i][1];
			}
		}
	}
	return ROLL_DIV[rollBin(kind, v, center)];
}

function rollLegend(kind, center, centerLabel) {
	var bar = '', ends;
	if (kind === 'rate') {
		const labels = ['30%+', '20–30%', '10–20%', '5–10%', '2–5%', 'under 2%'];
		$.each(ROLL_RATE_BINS.slice().reverse(), function (i, b) {
			bar += '<span style="background:' + b[1] + '" data-tip-title="' + labels[labels.length - 1 - i] + '"></span>';
		});
		ends = '<span>none logged</span><span>30%+ returned</span>';
		$('#paLegend').html('<div>Return rate in the selected group</div><div class="pa-legend-bar">' + bar +
			'</div><div class="pa-legend-ends">' + ends + '</div>' + rollGrayNote(kind));
		return;
	}
	const steps = kind === 'ratio' ? ROLL_RATIO_STEPS : ROLL_SHARE_STEPS;
	function step(i) {
		return kind === 'ratio' ? '×' + steps[i].toFixed(2) : (steps[i] * 100).toFixed(0) + ' pts';
	}
	const tips = [];
	for (var i = 0; i < steps.length; ++i) {
		tips.push('R-leaning, ' + step(i) + '+ from center');
	}
	tips.push('Within ' + step(steps.length - 1) + ' of center');
	for (var k = steps.length - 1; k >= 0; --k) {
		tips.push('D-leaning, ' + step(k) + '+ from center');
	}
	$.each(ROLL_DIV, function (i, c) {
		bar += '<span style="background:' + c + '" data-tip-title="' + tips[i] + '"></span>';
	});
	$('#paLegend').html('<div>Colored against ' + centerLabel + ' (' + rollFmt(kind, center).replace(/<[^>]+>/g, '') + ')</div>' +
		'<div class="pa-legend-bar">' + bar + '</div>' +
		'<div class="pa-legend-ends"><span class="pa-rep">R-leaning ' + step(0) + '</span><span>center</span>' +
		'<span class="pa-dem">D-leaning ' + step(0) + '</span></div>' + rollGrayNote(kind));
}

function rollGrayNote(kind) {
	const why = kind === 'rate' ? 'Fewer than 50 registered voters in the group' :
		'Not logging returns yet, or too few returns in the group to compare';
	return '<div><span class="pa-swatch" style="background:' + NO_DATA_COLOR + '"></span>' + why + '</div>';
}

function rollDescribe(s) {
	if (s['measure'] === 'pace22') {
		return 'all mail ballots';
	}
	const names = s['groups'].split('').map(function (g) { var t = ROLL_GROUPS[g][0]; return t.indexOf('Election Day') === 0 ? t : t.charAt(0).toLowerCase() + t.slice(1); });
	var txt = names.length === 9 ? 'all voters' : names.length ? names.join(' + ') : 'no group selected';
	return txt + (s['age'] !== 'all' ? ', age ' + ROLL_AGES[s['age']] : '');
}

function rollHover(county, s, x, kind) {
	const head = county + '<br /><small>';
	if (!x) {
		return head + 'No data</small>';
	}
	if (s['measure'] === 'pace22') {
		const p = x['raw'];
		return head + 'R pace vs D, 2026: <b>' + rollFmt('ratio', x['p26']) + '</b> (' + fmt(p[4]) + ' R / ' + fmt(p[5]) +
			' D back)<br />R pace vs D, 2022: <b>' + rollFmt('ratio', x['p22']) + '</b> (' + fmt(p[0]) + ' R / ' + fmt(p[1]) +
			' D back)<br />2026 vs 2022: <b>' + rollFmt('ratio', x['v']) + '</b></small>';
	}
	function rate(c) {
		return fmt(c[1]) + ' of ' + fmt(c[0]) + ' (' + pct1(c[1], c[0]) + ')';
	}
	var h = head + 'Group: ' + rollDescribe(s) + '<br />' +
		'R returned: <b>' + rate(x['R']) + '</b><br />D returned: <b>' + rate(x['D']) + '</b><br />';
	if (s['measure'] === 'relhab' && x['relR'] !== undefined) {
		h += 'vs habitual mail rate: R ' + x['relR'].toFixed(2) + '×, D ' + x['relD'].toFixed(2) + '×<br />';
	}
	if (s['measure'] === 'share' && x['shR'] !== undefined) {
		h += 'Share of party\'s returns: R ' + pct1(x['shR'], 1) + ', D ' + pct1(x['shD'], 1) + '<br />';
	}
	h += ROLL_MEASURES[s['measure']]['label'] + ': <b>' + rollFmt(kind, x['v']) + '</b>';
	if (!ROLL_LOGGING[county] && s['measure'] !== 'rate') {
		h += '<br />Not logging returns yet';
	}
	return h + '<br />Click to show this county in the table</small>';
}

function rollKpis(s, st, kind) {
	function kpi(label, value, sub, cls) {
		return '<div class="pa-kpi' + (cls ? ' pa-kpi--' + cls : '') + '"><div class="pa-kpi-label">' + label +
			'</div><div class="pa-kpi-value">' + value + '</div><div class="pa-kpi-sub">' + sub + '</div></div>';
	}
	function lean(v) {
		const n = rollNeutral(kind);
		return v === null || v === n ? '' : (v > n ? 'rep' : 'dem');
	}
	if (s['measure'] === 'pace22') {
		return kpi('2026 vs 2022', rollFmt('ratio', st['v']), st['used'] + ' counties with enough ballots both years', lean(st['v'])) +
			kpi('R pace vs D, 2026', rollFmt('ratio', st['p26']), 'returns per application, by ' + ROLL['pace_cutoffs']['2026']) +
			kpi('R pace vs D, 2022', rollFmt('ratio', st['p22']), 'same point, by ' + ROLL['pace_cutoffs']['2022']);
	}
	const t = st['tot'];
	const scope = s['measure'] === 'rate' ? 'all counties' : st['used'] + ' logging counties';
	return kpi(ROLL_MEASURES[s['measure']]['label'], rollFmt(kind, st['v']), scope, kind === 'rate' ? '' : lean(st['v'])) +
		kpi('R returned', pct1(t['R'][1], t['R'][0]), fmt(t['R'][1]) + ' of ' + fmt(t['R'][0]) + ' in the group') +
		kpi('D returned', pct1(t['D'][1], t['D'][0]), fmt(t['D'][1]) + ' of ' + fmt(t['D'][0]) + ' in the group');
}

/** Where each party's returns come from, by vote history; scoped like the table. */
function rollBars(s, counties) {
	function seg(p) {
		var total = 0, parts = [];
		$.each(ROLL_SEGS, function (ignore, sg) {
			var r = 0;
			$.each(counties, function (ignore2, c) { r += rollSum(c, sg[0], s['age'], p)[1]; });
			parts.push(r);
			total += r;
		});
		var html = '';
		$.each(ROLL_SEGS, function (i, sg) {
			const share = total ? 100 * parts[i] / total : 0;
			html += '<span style="flex:' + share.toFixed(3) + ' 1 0;background:' + sg[2] + ';color:' + sg[3] +
				'" data-tip-title="' + sg[1] + '" data-tip="' + fmt(parts[i]) + ' returns, ' + share.toFixed(1) + '%">' +
				(share >= 9 ? share.toFixed(0) + '%' : '') + '</span>';
		});
		return html;
	}
	var key = '';
	$.each(ROLL_SEGS, function (ignore, sg) {
		key += '<span><span class="pa-swatch" style="background:' + sg[2] + ';border:0"></span>' + sg[1] + '</span>';
	});
	return '<div class="pa-bar-name">R returns</div><div class="pa-bar">' + seg('R') + '</div>' +
		'<div class="pa-bar-name">D returns</div><div class="pa-bar">' + seg('D') + '</div>' +
		'<div class="pa-bar-key">' + key + '</div>';
}

/** Every history group for the selected age, statewide (logging counties) or one pinned county. */
function rollTable(s) {
	const counties = ROLL_PIN ? [ROLL_PIN] : Object.keys(ROLL_IX).filter(function (c) { return ROLL_LOGGING[c]; });
	function sums(groups, p) {
		var n = 0, r = 0;
		$.each(counties, function (ignore, c) {
			const x = rollSum(c, groups, s['age'], p);
			n += x[0];
			r += x[1];
		});
		return [n, r];
	}
	function cell(x) {
		return '<td>' + fmt(x[1]) + '<small>' + pct1(x[1], x[0]) + ' of ' + fmt(x[0]) + '</small></td>';
	}
	function ratio(groups) {
		const v = rollRatio(counties, groups, s['age']);
		return v === null ? '–' : v.toFixed(2) + '×';
	}
	var body = '';
	$.each(ROLL_GROUPS, function (g, lab) {
		const R = sums(g, 'R'), D = sums(g, 'D');
		const sel = s['measure'] !== 'pace22' && s['groups'].indexOf(g) >= 0 ? ' class="pa-sel"' : '';
		body += '<tr' + sel + '><th scope="row">' + lab[0] + (lab[1] ? '<small>' + lab[1] + '</small>' : '') + '</th>' +
			cell(R) + cell(D) + '<td>' + ratio(g) + '</td></tr>';
	});
	const all = 'ABCDEFGHI';
	const R = sums(all, 'R'), D = sums(all, 'D');
	body += '<tr class="pa-margin"><th scope="row">All registered</th>' + cell(R) + cell(D) + '<td>' + ratio(all) + '</td></tr>';
	if (s['groups'] && s['groups'].length < 9 && s['measure'] !== 'pace22') {
		const sR = sums(s['groups'], 'R'), sD = sums(s['groups'], 'D');
		body += '<tr class="pa-margin"><th scope="row">Selected groups</th>' + cell(sR) + cell(sD) + '<td>' + ratio(s['groups']) + '</td></tr>';
	}
	const scope = ROLL_PIN ? ROLL_PIN + ' County' : 'Statewide, ' + counties.length + ' logging counties';
	$('#paTable').html('<caption class="pa-caption">' + scope + (s['age'] !== 'all' ? ', age ' + ROLL_AGES[s['age']] : '') +
		(ROLL_PIN ? ' · <button type="button" class="pa-linkbtn" id="rollUnpin">Back to statewide</button>' : '') + '</caption>' +
		'<thead><tr><th>Voted in 2022 / 2024</th><th>R returned</th><th>D returned</th><th>R ÷ D<small>rate, within county</small></th></tr></thead>' +
		'<tbody>' + body + '</tbody>');
	$('#rollUnpin').on('click', function () { ROLL_PIN = null; doUpdate(); });
	$('#paFoot').text('Party is registration, not vote choice. A 2026 return counts once the county logs it in the voter ' +
		'roll (roll of ' + ROLL['roll_date'] + ', ' + ROLL['days_out'] + ' days out). Methods come from each voter\'s 2022 and ' +
		'2024 general election records; "mail" includes absentee.');
	return counties;
}

function rollUpdate() {
	if (!ROLL_IX) {
		$('#countMethod').text('Loading voter roll…');
		return;
	}
	const s = rollState();
	const meas = ROLL_MEASURES[s['measure']], kind = meas['kind'];
	const isPace = s['measure'] === 'pace22';
	$('#rollGroupsBox, #rollAgeBox').toggleClass('pa-off', isPace).find('input').prop('disabled', isPace);
	$('#rollCenterBox').prop('hidden', kind === 'rate');
	const preset = ROLL_PRESETS[$('input[name=rollPreset]:checked').val()];
	$('#rollRead').html(preset ? rollExplain(preset) :
		'<p class="pa-note">Custom view: ' + meas['label'].toLowerCase() + ' for ' + rollDescribe(s) + '.</p>');

	const st = rollStatewide(s);
	const center = s['center'] === 'state' && st['v'] !== null ? st['v'] : rollNeutral(kind);
	const centerLabel = s['center'] === 'state' ? 'the statewide value' : (kind === 'ratio' ? 'R = D' : 'equal shares');

	$('#countMethod').text(meas['label'] + ' · ' + rollDescribe(s));
	$('#paKpis').html(rollKpis(s, st, kind));

	$.each(name_to_fips, function (county) {
		const x = rollCounty(county, s);
		usfljsconfig[county].hover = rollHover(county, s, x, kind);
		if (x && x['v'] !== null && isFinite(x['v'])) {
			const c = rollColor(kind, x['v'], center);
			usfljsconfig[county].upColor = c;
			usfljsconfig[county].downColor = c;
			$('#' + county).attr({ fill: c });
		}
	});
	$('#mapbase path').removeClass('pa-pinned');
	if (ROLL_PIN) {
		$('#' + ROLL_PIN).addClass('pa-pinned');
	}
	rollLegend(kind, center, centerLabel);
	const scoped = rollTable(s);
	$('#paBars').html(rollBars(s, scoped));
	chRoll(s, scoped);
	rollContext();
}

/** Plain-English question, what would be good or bad for Republicans, then how the number is computed. */
function rollExplain(p) {
	return '<p class="pa-q-plain">' + p['plain'] + '</p>' +
		'<div class="pa-gb pa-gb-good"><b>Good for Republicans</b>' + p['good'] + '</div>' +
		'<div class="pa-gb pa-gb-bad"><b>Bad for Republicans</b>' + p['bad'] + '</div>' +
		'<p class="pa-note"><b>How it\'s measured:</b> ' + p['read'] + '</p>';
}

function rollContext() {
	$('#paContext').html('<div class="pa-label">How to read the history groups</div>' +
		'<p class="pa-note">Each registered voter is placed in one group by how they voted in the 2022 and 2024 ' +
		'generals. The 2024 surge groups are the voters a midterm usually loses; habitual groups show whether the ' +
		'base is keeping pace.</p>' +
		'<p class="pa-note">Counties log returns at very different speeds, so raw return counts mostly measure ' +
		'county office workload. Comparing R with D inside the same county, then pooling, removes that. Gray ' +
		'counties have not started logging or have too few returns in the group.</p>' +
		'<p class="pa-note">Surge voters who voted in person on Election Day 2024 mostly will not appear until Election Day, ' +
		'and none of this shows crossover voting.</p>');
}

/** Controls for the roll view, appended under the mode switch. */
function rollControls() {
	function chip(type, name, value, main, sub, checked) {
		return '<label class="pa-chip"><input type="' + type + '" name="' + name + '" value="' + value + '"' +
			(checked ? ' checked="checked"' : '') + '><span>' + main + '</span>' + (sub ? '<small>' + sub + '</small>' : '') + '</label>\n';
	}
	var h = '<div class="pa-group pa-mode-roll"><div class="pa-label">Question</div><div class="pa-list">\n';
	$.each(ROLL_PRESETS, function (k, p) {
		h += chip('radio', 'rollPreset', k, p['q'], '', k === 'surge');
	});
	h += chip('radio', 'rollPreset', 'custom', 'Custom', 'set the switches below', false);
	h += '</div><div class="pa-explain" id="rollRead"></div></div>\n';

	h += '<div class="pa-group pa-mode-roll"><div class="pa-label">Measure</div><div class="pa-list">\n';
	$.each(ROLL_MEASURES, function (k, m) {
		h += chip('radio', 'rollMeasure', k, m['label'], m['sub'], k === 'relhab');
	});
	h += '</div></div>\n';

	h += '<div class="pa-group pa-mode-roll" id="rollGroupsBox"><div class="pa-label">Voters who…</div><div class="pa-seg">\n';
	$.each(ROLL_GROUPS, function (k, g) {
		h += chip('checkbox', 'rollGroup', k, g[0], g[1], k === 'E');
	});
	h += '</div></div>\n';

	h += '<div class="pa-group pa-mode-roll" id="rollAgeBox"><div class="pa-label">Age in 2026</div><div class="pa-chips">\n';
	$.each(ROLL_AGES, function (k, a) {
		h += chip('radio', 'rollAge', k, a, '', k === 'all');
	});
	h += '</div></div>\n';

	h += '<div class="pa-group pa-mode-roll" id="rollCenterBox"><div class="pa-label">Color counties against</div><div class="pa-seg">\n' +
		chip('radio', 'rollCenter', 'state', 'Statewide value', 'shows outlier counties', true) +
		chip('radio', 'rollCenter', 'even', 'Parity', 'R = D', false) + '</div></div>\n';
	return h;
}

function rollApplyPreset(key) {
	const p = ROLL_PRESETS[key];
	if (!p) {
		return;
	}
	$('input[name=rollMeasure][value=' + p['measure'] + ']').prop('checked', true);
	$('input[name=rollGroup]').each(function () { this.checked = p['groups'].indexOf(this.value) >= 0; });
	$('input[name=rollAge][value=' + p['age'] + ']').prop('checked', true);
}

function rollBind() {
	$('input[name=rollPreset]').on('change', function () {
		rollApplyPreset(this.value);
	});
	$('input[name=rollMeasure], input[name=rollGroup], input[name=rollAge]').on('change', function () {
		$('input[name=rollPreset][value=custom]').prop('checked', true);
	});
	$.each(name_to_fips, function (county) {
		$('#' + county).on('click', function () {
			if ($('input[name=paMode]:checked').val() !== 'roll') {
				return;
			}
			ROLL_PIN = ROLL_PIN === county ? null : county;
			doUpdate();
		});
	});
}

/** View state in the URL hash, so a link opens the same switches: #roll/surge or #roll/custom/ratio/AE/b/state */
function rollHashWrite() {
	const mode = $('input[name=paMode]:checked').val();
	if (mode === 'dist') {
		distHashWrite();
		return;
	}
	if (mode !== 'roll') {
		history.replaceState(null, '', location.pathname + location.search);
		return;
	}
	const preset = $('input[name=rollPreset]:checked').val();
	const s = rollState();
	var h = '#roll/' + preset;
	if (preset === 'custom') {
		h += '/' + s['measure'] + '/' + (s['groups'] || '-') + '/' + s['age'];
	}
	h += s['center'] === 'even' ? '/even' : '';
	history.replaceState(null, '', h);
}

function rollHashRead() {
	const parts = location.hash.replace(/^#/, '').split('/');
	if (parts[0] !== 'roll') {
		return false;
	}
	$('input[name=paMode][value=roll]').prop('checked', true);
	const preset = parts[1] || 'surge';
	if (ROLL_PRESETS[preset]) {
		$('input[name=rollPreset][value=' + preset + ']').prop('checked', true);
		rollApplyPreset(preset);
	} else if (preset === 'custom' && ROLL_MEASURES[parts[2]]) {
		$('input[name=rollPreset][value=custom]').prop('checked', true);
		$('input[name=rollMeasure][value=' + parts[2] + ']').prop('checked', true);
		const g = (parts[3] || '').replace('-', '');
		$('input[name=rollGroup]').each(function () { this.checked = g.indexOf(this.value) >= 0; });
		if (ROLL_AGES[parts[4]]) {
			$('input[name=rollAge][value=' + parts[4] + ']').prop('checked', true);
		}
	}
	if (parts[parts.length - 1] === 'even') {
		$('input[name=rollCenter][value=even]').prop('checked', true);
	}
	return true;
}
