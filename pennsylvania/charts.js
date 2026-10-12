// Charts under the map.
//   Pace (both modes): cumulative mail ballot returns by days before Election Day, 2026 against 2024 and 2022,
//   from the daily counts in pa_2026_general.json. Clicking a county scopes it to that county.
//   Roll (only in "Who is returning"): return rate by vote-history group, and R vs D by age, scoped like the
//   table (pinned county, or the logging counties statewide).

const CH_START = 50;      // first day shown, in days before Election Day; earlier returns fold into it
const CH_END = -7;        // military and overseas ballots count through Election Day + 7
const CH_MIN_SHARE = 300; // R share is drawn once this many R + D ballots are back
const CH_YEARS = [
	{ 'y': '2026', 'stroke': 'var(--dr-ink)', 'width': 2.5, 'dash': '' },
	{ 'y': '2024', 'stroke': '#767b84', 'width': 2, 'dash': '' },
	{ 'y': '2022', 'stroke': '#767b84', 'width': 2, 'dash': '5 4' }
];
const CH_METRICS = {
	'total': { 'label': 'All returned', 'title': 'Mail ballots returned' },
	'rep': { 'label': 'R returned', 'title': 'Republican mail ballots returned' },
	'dem': { 'label': 'D returned', 'title': 'Democratic mail ballots returned' },
	'rshare': { 'label': 'R share', 'title': 'R share of R + D ballots returned' }
};
const CH_AGE_KEYS = ['a', 'b', 'c', 'd'];
// Sequential, light to dark: older bands darker.
const CH_AGE_FILL = [['#d7e5ec', 'var(--dr-ink)'], ['#9dc0d0', 'var(--dr-ink)'], ['#4f87a2', '#fff'], ['#1f5670', '#fff']];

var CH = { 'metric': 'total', 'area': 'state', 'range': 'now' };

function chEsc(s) {
	return String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}

function chDate(year, daysOut) {
	const day = { '2026': '2026-11-03', '2024': '2024-11-05', '2022': '2022-11-08' }[year];
	const d = new Date(day + 'T12:00:00');
	d.setDate(d.getDate() - daysOut);
	return d.toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' });
}

/** Cumulative [D, R, NPA] per day from CH_START down to CH_END (or the latest 2026 day), for one area. */
function chCurve(year, area) {
	const by = DATA['daily'][year] || {};
	const n = CH_START - CH_END + 1;
	const daily = [];
	for (var i = 0; i < n; ++i) {
		daily.push([0, 0, 0]);
	}
	$.each(by, function (county, rows) {
		if ((area === 'nophl' && county === 'Philadelphia') || (area !== 'state' && area !== 'nophl' && county !== area)) {
			return;
		}
		$.each(rows, function (ignore, r) {
			const k = Math.max(0, CH_START - r[0]);
			if (k < n) {
				daily[k][0] += r[1];
				daily[k][1] += r[2];
				daily[k][2] += r[3];
			}
		});
	});
	const last = year === '2026' ? DATA['days_before_election'] : CH_END;
	const out = [];
	var c = [0, 0, 0];
	for (var k = 0; k < n && CH_START - k >= last; ++k) {
		c = [c[0] + daily[k][0], c[1] + daily[k][1], c[2] + daily[k][2]];
		out.push({ 'd': CH_START - k, 'v': c });
	}
	return out;
}

function chValue(metric, v) {
	if (metric === 'rshare') {
		return v[0] + v[1] >= CH_MIN_SHARE ? v[1] / (v[0] + v[1]) : null;
	}
	return metric === 'rep' ? v[1] : metric === 'dem' ? v[0] : v[0] + v[1] + v[2];
}

function chFmt(metric, x) {
	if (x === null || x === undefined) {
		return '–';
	}
	return metric === 'rshare' ? (100 * x).toFixed(1) + '%' : fmt(x);
}

/** Rounded axis ticks covering [lo, hi]. */
function chTicks(lo, hi, count) {
	const span = hi - lo || 1;
	const raw = span / count, mag = Math.pow(10, Math.floor(Math.log10(raw)));
	const step = [1, 2, 2.5, 5, 10].map(function (m) { return m * mag; }).filter(function (s) { return s >= raw; })[0];
	const ticks = [];
	for (var t = Math.floor(lo / step) * step; t <= hi + step * 0.999; t += step) {
		ticks.push(+t.toFixed(10));
	}
	return ticks;
}

function chPaceControls() {
	function chip(name, value, label, checked) {
		return '<label class="pa-chip"><input type="radio" name="' + name + '" value="' + value + '"' +
			(checked ? ' checked="checked"' : '') + '><span>' + label + '</span></label>';
	}
	var h = '<div class="pa-ch-filters"><div class="pa-chips" role="radiogroup" aria-label="Measure">';
	$.each(CH_METRICS, function (k, m) { h += chip('chMetric', k, m['label'], k === CH.metric); });
	h += '</div><div class="pa-chips" role="radiogroup" aria-label="Range">' +
		chip('chRange', 'now', 'To date', CH.range === 'now') + chip('chRange', 'season', 'Whole season', CH.range === 'season') +
		'</div><select id="chArea" class="pa-ch-select" aria-label="Area"><option value="state">Statewide</option>' +
		'<option value="nophl">Outside Philadelphia</option>';
	$.each(Object.keys(DATA['daily']['2024']).sort(), function (ignore, c) {
		h += '<option value="' + chEsc(c) + '">' + chEsc(c) + ' County</option>';
	});
	return h + '</select></div>';
}

function chAreaLabel() {
	return CH.area === 'state' ? 'Statewide' : CH.area === 'nophl' ? 'Outside Philadelphia' : CH.area + ' County';
}

function chPace() {
	const box = $('#chPace');
	if (!box.length || !DATA || !DATA['daily']) {
		return;
	}
	const metric = CH.metric, now = DATA['days_before_election'];
	const xEnd = CH.range === 'now' ? Math.max(now - 3, CH_END) : CH_END;
	const lines = $.map(CH_YEARS, function (yr) {
		const pts = $.map(chCurve(yr['y'], CH.area), function (p) {
			return p['d'] >= xEnd ? { 'd': p['d'], 'v': p['v'], 'y': chValue(metric, p['v']) } : null;
		});
		return [{ 'yr': yr, 'pts': pts }];
	});
	var lo = Infinity, hi = -Infinity;
	$.each(lines, function (ignore, l) {
		$.each(l['pts'], function (ignore2, p) {
			if (p['y'] !== null) {
				lo = Math.min(lo, p['y']);
				hi = Math.max(hi, p['y']);
			}
		});
	});
	$('#chPaceTitle').text(CH_METRICS[metric]['title'] + ' · ' + chAreaLabel());
	if (!isFinite(lo)) {
		box.html('<p class="pa-note">Too few ballots here to chart this.</p>');
		return;
	}
	if (metric === 'rshare') {
		lo = Math.max(0, lo - 0.02);
		hi = Math.min(1, hi + 0.02);
	} else {
		lo = 0;
	}
	const ticks = chTicks(lo, hi, 4);
	lo = ticks[0];
	hi = ticks[ticks.length - 1];

	const W = Math.max(300, box.width()), H = W < 520 ? 220 : 260;
	const M = { 'l': metric === 'rshare' ? 44 : 56, 'r': 44, 't': 14, 'b': 30 };
	const x = function (d) { return M.l + (CH_START - d) / (CH_START - xEnd) * (W - M.l - M.r); };
	const y = function (v) { return H - M.b - (v - lo) / (hi - lo) * (H - M.t - M.b); };

	var svg = '<svg class="pa-ch-svg" viewBox="0 0 ' + W + ' ' + H + '" width="' + W + '" height="' + H + '" role="img" aria-label="' +
		chEsc(CH_METRICS[metric]['title'] + ' by days before Election Day, 2026, 2024 and 2022') + '">';
	$.each(ticks, function (ignore, t) {
		svg += '<line class="pa-ch-grid" x1="' + M.l + '" x2="' + (W - M.r) + '" y1="' + y(t) + '" y2="' + y(t) + '"/>' +
			'<text class="pa-ch-tick" x="' + (M.l - 6) + '" y="' + (y(t) + 4) + '" text-anchor="end">' +
			(metric === 'rshare' ? Math.round(100 * t) + '%' : t >= 1e6 ? (t / 1e6) + 'M' : t >= 1e3 ? (t / 1e3) + 'k' : t) + '</text>';
	});
	const step = CH_START - xEnd > 30 ? 10 : 5;
	for (var d = CH_START; d >= xEnd; d -= step) {
		svg += '<text class="pa-ch-tick" x="' + x(d) + '" y="' + (H - M.b + 16) + '" text-anchor="middle">' +
			(d === 0 ? 'E-Day' : d) + '</text>';
	}
	svg += '<text class="pa-ch-tick" x="' + (W - M.r) + '" y="' + (H - 2) + '" text-anchor="end">days before Election Day</text>';
	svg += '<line class="pa-ch-now" x1="' + x(now) + '" x2="' + x(now) + '" y1="' + M.t + '" y2="' + (H - M.b) + '"/>' +
		'<text class="pa-ch-tick" x="' + (x(now) - 4) + '" y="' + (M.t + 8) + '" text-anchor="end">2026 data</text>';

	// Baselines first so 2026 draws on top.
	var labels = [];
	$.each(lines.slice().reverse(), function (ignore, l) {
		var path = '', pen = false, lastPt = null;
		$.each(l['pts'], function (ignore2, p) {
			if (p['y'] === null) {
				pen = false;
				return;
			}
			path += (pen ? 'L' : 'M') + x(p['d']).toFixed(1) + ' ' + y(p['y']).toFixed(1);
			pen = true;
			lastPt = p;
		});
		svg += '<path d="' + path + '" fill="none" stroke="' + l['yr']['stroke'] + '" stroke-width="' + l['yr']['width'] + '"' +
			(l['yr']['dash'] ? ' stroke-dasharray="' + l['yr']['dash'] + '"' : '') + ' stroke-linejoin="round" stroke-linecap="round"/>';
		if (lastPt) {
			labels.push({ 'y': l['yr']['y'], 'x': x(lastPt['d']), 'py': y(lastPt['y']) });
		}
	});
	// Direct labels at line ends, nudged apart.
	labels.sort(function (a, b) { return a['py'] - b['py']; });
	for (var i = 1; i < labels.length; ++i) {
		if (labels[i]['x'] > W - M.r - 2 && labels[i - 1]['x'] > W - M.r - 2) {
			labels[i]['py'] = Math.max(labels[i]['py'], labels[i - 1]['py'] + 13);
		}
	}
	$.each(labels, function (ignore, lb) {
		svg += '<text class="pa-ch-end' + (lb['y'] === '2026' ? ' pa-ch-end--now' : '') + '" x="' + (lb['x'] + 6) + '" y="' +
			(lb['py'] + 4) + '">' + lb['y'] + '</text>';
	});
	svg += '<g class="pa-ch-hover" hidden><line class="pa-ch-cross" y1="' + M.t + '" y2="' + (H - M.b) + '"/></g>' +
		'<rect class="pa-ch-hit" x="' + M.l + '" y="' + M.t + '" width="' + (W - M.l - M.r) + '" height="' + (H - M.t - M.b) + '"/></svg>' +
		'<div class="pa-ch-tip" hidden></div>';
	box.html(svg);

	const hover = box.find('.pa-ch-hover'), tip = box.find('.pa-ch-tip');
	function show(evt) {
		const rect = box.find('svg')[0].getBoundingClientRect();
		const px = (evt.clientX - rect.left) * W / rect.width;
		const d = Math.round(CH_START - (px - M.l) / (W - M.l - M.r) * (CH_START - xEnd));
		if (d > CH_START || d < xEnd) {
			return;
		}
		var dots = '<line class="pa-ch-cross" x1="' + x(d) + '" x2="' + x(d) + '" y1="' + M.t + '" y2="' + (H - M.b) + '"/>';
		var rows = '';
		$.each(lines, function (ignore, l) {
			const p = $.grep(l['pts'], function (q) { return q['d'] === d; })[0];
			if (p && p['y'] !== null) {
				dots += '<circle cx="' + x(d) + '" cy="' + y(p['y']) + '" r="4" fill="' + l['yr']['stroke'] + '" class="pa-ch-dot"/>';
			}
			rows += '<div><b>' + l['yr']['y'] + '</b> <span>' + chDate(l['yr']['y'], d) + '</span> ' +
				'<span class="pa-ch-tipv">' + (p ? chFmt(metric, p['y']) : 'not yet') + '</span></div>';
		});
		hover.html(dots).prop('hidden', false);
		tip.html('<div class="pa-ch-tiphead">' + (d === 0 ? 'Election Day' : d > 0 ? d + ' days before Election Day' :
			-d + ' days after Election Day') + '</div>' + rows).prop('hidden', false);
		const left = x(d) * rect.width / W;
		tip.css({ 'left': left > rect.width / 2 ? left - tip.outerWidth() - 12 : left + 12, 'top': 8 });
	}
	box.find('.pa-ch-hit').on('pointermove pointerdown', show).on('pointerleave', function () {
		hover.prop('hidden', true);
		tip.prop('hidden', true);
	});

	var note = '2026 is through ' + chDate('2026', now) + '; 2024 and 2022 are lined up by days before their Election Days. ';
	if (metric === 'rshare') {
		note += 'Two-party share, drawn once ' + fmt(CH_MIN_SHARE) + ' R + D ballots are back. ';
	}
	if (CH.area === 'state') {
		note += 'Statewide 2026 numbers lean on Philadelphia, which is logging returns earlier than in past years; ' +
			'"Outside Philadelphia" removes it.';
	} else if (CH.area !== 'nophl') {
		note += 'Click the county again, or pick Statewide, to go back.';
	}
	$('#chPaceNote').text(note);
}

/** Return rate by history group, R and D, for the table's scope. */
function chHistory(s, counties) {
	const age = s['age'];
	const rows = [];
	var max = 0;
	$.each(ROLL_GROUPS, function (g, lab) {
		var R = [0, 0], D = [0, 0];
		$.each(counties, function (ignore, c) {
			const r = rollSum(c, g, age, 'R'), d = rollSum(c, g, age, 'D');
			R = [R[0] + r[0], R[1] + r[1]];
			D = [D[0] + d[0], D[1] + d[1]];
		});
		const rr = R[0] ? R[1] / R[0] : 0, dr = D[0] ? D[1] / D[0] : 0;
		max = Math.max(max, rr, dr);
		rows.push({ 'g': g, 'lab': lab, 'R': R, 'D': D, 'rr': rr, 'dr': dr });
	});
	max = max || 1;
	function bar(cls, rate, x, who) {
		const w = 100 * rate / max;
		return '<div class="pa-ch-hbar"><span class="' + cls + '" style="width:' + w.toFixed(2) + '%" data-tip-title="' + who +
			'" data-tip="' + fmt(x[1]) + ' of ' + fmt(x[0]) + ' returned (' + (100 * rate).toFixed(1) + '%)"></span>' +
			'<em>' + (100 * rate).toFixed(1) + '%</em></div>';
	}
	var h = '';
	$.each(rows, function (ignore, r) {
		const sel = s['groups'] && s['groups'].indexOf(r['g']) >= 0 && s['measure'] !== 'pace22';
		const rv = rollRatio(counties, r['g'], age), ratio = rv === null ? '–' : rv.toFixed(2) + '×';
		h += '<div class="pa-ch-hrow' + (sel ? ' pa-sel' : '') + '"><div class="pa-ch-hlab">' + r['lab'][0] +
			(r['lab'][1] ? '<small>' + r['lab'][1] + '</small>' : '') + '</div><div class="pa-ch-hbars">' +
			bar('pa-ch-r', r['rr'], r['R'], 'R · ' + r['lab'][0]) + bar('pa-ch-d', r['dr'], r['D'], 'D · ' + r['lab'][0]) +
			'</div><div class="pa-ch-hratio" data-tip="R return rate ÷ D return rate, compared inside each county">' + ratio + '</div></div>';
	});
	$('#chHistory').html(h);
}

/** R ÷ D return rate in each age band (selected groups and habitual mail), plus each party's age mix. */
function chAge(s, counties) {
	const groups = s['measure'] === 'pace22' || !s['groups'] ? 'ABCDEFGHI' : s['groups'];
	function sums(g, a, p) {
		var n = 0, r = 0;
		$.each(counties, function (ignore, c) {
			const x = rollSum(c, g, a, p);
			n += x[0];
			r += x[1];
		});
		return [n, r];
	}
	function ratio(g, a) {
		const v = rollRatio(counties, g, a);
		return v === null ? null : { 'v': v, 'R': sums(g, a, 'R'), 'D': sums(g, a, 'D') };
	}
	const series = [{ 'g': groups, 'name': groups === 'ABCDEFGHI' ? 'All registered' : 'Selected groups', 'cls': 'pa-ch-pt' }];
	if (groups !== 'A') {
		series.push({ 'g': 'A', 'name': 'Habitual mail', 'cls': 'pa-ch-pt pa-ch-pt--ref' });
	}
	const bands = CH_AGE_KEYS.concat(['all']);
	var lo = 0.8, hi = 1.25;
	const vals = {};
	$.each(series, function (i, sr) {
		$.each(bands, function (ignore, a) {
			const v = vals[i + a] = ratio(sr['g'], a);
			if (v) {
				lo = Math.min(lo, v['v']);
				hi = Math.max(hi, v['v']);
			}
		});
	});
	lo = Math.max(0.1, lo / 1.08);
	hi = hi * 1.08;
	const box = $('#chAge');
	const W = Math.max(280, box.width()), rowH = 30, M = { 'l': 64, 'r': 16, 't': 8, 'b': 26 };
	const H = M.t + M.b + rowH * bands.length;
	const x = function (v) { return M.l + (Math.log(v) - Math.log(lo)) / (Math.log(hi) - Math.log(lo)) * (W - M.l - M.r); };
	var svg = '<svg class="pa-ch-svg" viewBox="0 0 ' + W + ' ' + H + '" width="' + W + '" height="' + H + '" role="img" ' +
		'aria-label="R return rate divided by D return rate, by age band">';
	$.each([0.25, 0.33, 0.5, 0.67, 0.8, 1, 1.25, 1.5, 2, 3, 4], function (ignore, t) {
		if (t < lo || t > hi) {
			return;
		}
		svg += '<line class="' + (t === 1 ? 'pa-ch-ref' : 'pa-ch-grid') + '" x1="' + x(t) + '" x2="' + x(t) + '" y1="' + M.t +
			'" y2="' + (H - M.b) + '"/><text class="pa-ch-tick" x="' + x(t) + '" y="' + (H - M.b + 15) + '" text-anchor="middle">' +
			(t === 1 ? 'R = D' : t + '×') + '</text>';
	});
	$.each(bands, function (bi, a) {
		const cy = M.t + rowH * bi + rowH / 2;
		svg += '<text class="pa-ch-rowlab' + (a === 'all' ? ' pa-ch-rowlab--all' : '') + '" x="' + (M.l - 8) + '" y="' + (cy + 4) +
			'" text-anchor="end">' + ROLL_AGES[a] + '</text>';
		$.each(series.slice().reverse(), function (ignore, sr) {
			const i = series.indexOf(sr), v = vals[i + a];
			if (!v) {
				return;
			}
			svg += '<circle class="' + sr['cls'] + '" cx="' + x(v['v']) + '" cy="' + cy + '" r="5.5" data-tip-title="' + sr['name'] +
				', ' + ROLL_AGES[a] + ': ' + v['v'].toFixed(2) + '×" data-tip="R ' + fmt(v['R'][1]) + ' of ' + fmt(v['R'][0]) + ' (' +
				(100 * v['R'][1] / v['R'][0]).toFixed(1) + '%) · D ' + fmt(v['D'][1]) + ' of ' + fmt(v['D'][0]) + ' (' +
				(100 * v['D'][1] / v['D'][0]).toFixed(1) + '%)"/>';
		});
	});
	box.html(svg + '</svg>');
	var key = '';
	$.each(series, function (ignore, sr) {
		key += '<span><svg width="14" height="14" aria-hidden="true"><circle class="' + sr['cls'] + '" cx="7" cy="7" r="5"/></svg>' +
			sr['name'] + '</span>';
	});
	$('#chAgeKey').html(key);

	// Age mix: each party's returns against everyone in the same groups.
	function mix(p, idx) {
		const parts = $.map(CH_AGE_KEYS, function (a) { return sums(groups, a, p)[idx]; });
		const total = parts.reduce(function (t, v) { return t + v; }, 0);
		var h = '';
		$.each(parts, function (i, v) {
			const share = total ? 100 * v / total : 0;
			h += '<span style="flex:' + share.toFixed(3) + ' 1 0;background:' + CH_AGE_FILL[i][0] + ';color:' + CH_AGE_FILL[i][1] +
				'" data-tip-title="' + ROLL_AGES[CH_AGE_KEYS[i]] + '" data-tip="' + fmt(v) + ', ' + share.toFixed(1) + '%">' +
				(share >= 9 ? share.toFixed(0) + '%' : '') + '</span>';
		});
		return h;
	}
	var mk = '';
	$.each(CH_AGE_KEYS, function (i, a) {
		mk += '<span><span class="pa-swatch" style="background:' + CH_AGE_FILL[i][0] + ';border:0"></span>' + ROLL_AGES[a] + '</span>';
	});
	$('#chMix').html('<div class="pa-bar-name">R returned</div><div class="pa-bar">' + mix('R', 1) + '</div>' +
		'<div class="pa-bar-name">R in groups</div><div class="pa-bar">' + mix('R', 0) + '</div>' +
		'<div class="pa-bar-name">D returned</div><div class="pa-bar">' + mix('D', 1) + '</div>' +
		'<div class="pa-bar-name">D in groups</div><div class="pa-bar">' + mix('D', 0) + '</div>' +
		'<div class="pa-bar-key">' + mk + '</div>');
	$('#chAgeScope').text(series[0]['name'] === 'All registered' ? 'All registered voters' : 'Selected groups: ' + rollDescribe(s));
}

/** Called from rollUpdate with the table's counties. */
function chRoll(s, counties) {
	const scope = ROLL_PIN ? ROLL_PIN + ' County' : 'Statewide, ' + counties.length + ' logging counties';
	$('#chHistoryTitle').text('Return rate by vote history · ' + scope + (s['age'] !== 'all' ? ', age ' + ROLL_AGES[s['age']] : ''));
	$('#chAgeTitle').text('R vs D by age · ' + scope);
	chHistory(s, counties);
	chAge(s, counties);
}

function chRedrawRoll() {
	if (ROLL_IX && $('input[name=paMode]:checked').val() === 'roll') {
		const s = rollState();
		chRoll(s, ROLL_PIN ? [ROLL_PIN] : Object.keys(ROLL_IX).filter(function (c) { return ROLL_LOGGING[c]; }));
	}
}

/** Once, after DATA loads. */
function chInit() {
	$('#chPaceControls').html(chPaceControls());
	$('#chPaceControls').on('change', 'input, select', function () {
		CH.metric = $('input[name=chMetric]:checked').val();
		CH.range = $('input[name=chRange]:checked').val();
		CH.area = $('#chArea').val();
		chPace();
	});
	$.each(name_to_fips, function (county) {
		$('#' + county).on('click', function () {
			if (!DATA['daily']['2024'][county]) {
				return;
			}
			CH.area = CH.area === county ? 'state' : county;
			$('#chArea').val(CH.area);
			chPace();
		});
	});
	var timer = null, lastW = $('#chPace').width();
	$(window).on('resize', function () {
		clearTimeout(timer);
		timer = setTimeout(function () {
			if ($('#chPace').width() !== lastW) {
				lastW = $('#chPace').width();
				chPace();
				chRedrawRoll();
			}
		}, 150);
	});
	chPace();
}
