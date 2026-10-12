// "House districts" mode: congressional district outlines over the county map, the roll's mail read for each
// district, and a margin range that moves with the national-environment switch. Inputs come from
// pa_2026_districts.json; the margin math is below so the switch can rerun it.

var DIST = null;
var DIST_PIN = null;

// Incumbency, in margin points per side: a seat whose incumbent party changed since the baseline year moves 2× this.
const DIST_INC_PTS = 2;
// District swing as a share of the national swing; the middle case uses the midpoint.
const DIST_ELASTICITY = [0.75, 1];
// Generic-ballot polling error allowed for in the range, in points either way.
const DIST_POLL_ERR = 3;
const DIST_STEPS = [20, 12, 6, 2];
// Below this share of habitual mail voters in logging counties, a district's mail read is shown as not readable.
const DIST_MIN_LOGGED = 0.4;
const DIST_LOGGED_BINS = [[0.7, '#2f7d4f'], [0.4, '#5fa77a'], [0.1, '#9fcdae'], [-1, '#f5f7f5']];
const DIST_MEASURES = {
	'forecast': { 'label': 'Margin range', 'sub': 'model, moves with the switch below' },
	'hab': { 'label': 'Habitual mail R vs D', 'sub': 'R ÷ D return rate' },
	'surge': { 'label': '2024 surge voters', 'sub': 'R ÷ D, relative to habitual mail' },
	'logged': { 'label': 'Logging', 'sub': 'share of habitual mail voters in logging counties' }
};

function distLoad(data) {
	DIST = data;
	const svg = document.querySelector('#mapbase svg');
	const g = document.createElementNS('http://www.w3.org/2000/svg', 'g');
	g.setAttribute('id', 'paDistricts');
	g.setAttribute('transform', $('#Pennsylvania').attr('transform') || '');
	$.each(data['districts'], function (ignore, d) {
		const p = document.createElementNS('http://www.w3.org/2000/svg', 'path');
		p.setAttribute('d', d['path']);
		p.setAttribute('id', 'paCd' + d['cd']);
		p.setAttribute('class', 'pa-cd');
		g.appendChild(p);
	});
	$.each(data['districts'], function (ignore, d) {
		const t = document.createElementNS('http://www.w3.org/2000/svg', 'text');
		t.setAttribute('x', d['label'][0]);
		t.setAttribute('y', d['label'][1]);
		t.setAttribute('class', 'pa-cd-label' + ([2, 3, 5].indexOf(d['cd']) >= 0 ? ' pa-cd-label--sm' : ''));
		t.textContent = d['cd'];
		g.appendChild(t);
	});
	g.style.display = 'none';
	svg.appendChild(g);
	$('#paDistricts .pa-cd').each(function () {
		const cd = Number(this.id.slice(4));
		$(this).on('mouseenter touchstart', function (e) {
			$('#usfljstip').show().html(distHover(cd));
			distTipMove(e);
		}).on('mousemove', distTipMove).on('mouseleave', function () {
			$('#usfljstip').hide();
		}).on('click', function () {
			DIST_PIN = DIST_PIN === cd ? null : cd;
			doUpdate();
		});
	});
}

function distTipMove(e) {
	const pt = e.originalEvent && e.originalEvent.touches ? e.originalEvent.touches[0] : e;
	const tip = $('#usfljstip');
	var x = pt.pageX + 10, y = pt.pageY + 15;
	if (x + tip.outerWidth() > $(document).scrollLeft() + $(window).width()) {
		x -= tip.outerWidth() + 40;
	}
	if (y + tip.outerHeight() > $(document).scrollTop() + $(window).height()) {
		y = $(document).scrollTop() + $(window).height() - tip.outerHeight() - 10;
	}
	tip.css({ left: x, top: y });
}

function distState() {
	return {
		'measure': $('input[name=distMeasure]:checked').val(),
		'env': Number($('#distEnv').val())
	};
}

/** Two-party R−D margin (points) of an electorate [D, R, other] at district lean delta. */
function distLogitMargin(comp, delta) {
	const beta = DIST['beta'];
	var r = 0;
	$.each(['D', 'R', 'O'], function (i, p) {
		r += comp[i] / (1 + Math.exp(-(beta[p] + delta)));
	});
	return 100 * (2 * r / (comp[0] + comp[1] + comp[2]) - 1);
}

/**
 * Margin from one baseline year: that year's district lean on the expected 2026 electorate (turnout case k), plus
 * the national swing from that year to an environment of D +env, plus any change in which party holds the seat.
 */
function distMargin(d, year, k, env, el) {
	const inc = d['inc'][2] - d['inc'][year === '2022' ? 0 : 1];
	return distLogitMargin(d['electorate'][k], d['delta'][year]) +
		el * (-env - DIST['national'][year]) + DIST_INC_PTS * inc;
}

/** {mid, lo, hi} R−D margin, or null when neither baseline year had a contested race. */
function distForecast(d, env) {
	const years = Object.keys(d['delta']);
	if (!years.length) {
		return null;
	}
	const elMid = (DIST_ELASTICITY[0] + DIST_ELASTICITY[1]) / 2;
	var mid = 0, lo = Infinity, hi = -Infinity;
	$.each(years, function (ignore, y) {
		mid += distMargin(d, y, 'm', env, elMid) / years.length;
		$.each(['d', 'r'], function (ignore2, k) {
			$.each([env - DIST_POLL_ERR, env + DIST_POLL_ERR], function (ignore3, e) {
				$.each(DIST_ELASTICITY, function (ignore4, el) {
					const m = distMargin(d, y, k, e, el);
					lo = Math.min(lo, m);
					hi = Math.max(hi, m);
				});
			});
		});
	});
	return { 'mid': mid, 'lo': lo, 'hi': hi };
}

function distFmt(m) {
	if (m === null || m === undefined || !isFinite(m)) {
		return '–';
	}
	const side = m < 0 ? 'dem' : 'rep';
	return '<span class="pa-' + side + '">' + (m < 0 ? 'D' : 'R') + ' +' + Math.abs(m).toFixed(1) + '</span>';
}

function distEnvLabel(env) {
	return env === 0 ? 'even' : (env > 0 ? 'D +' : 'R +') + Math.abs(env).toFixed(1);
}

function distMarginColor(m) {
	for (var i = 0; i < DIST_STEPS.length; ++i) {
		if (m >= DIST_STEPS[i]) {
			return ROLL_DIV[i];
		}
	}
	for (var j = DIST_STEPS.length - 1; j >= 0; --j) {
		if (m <= -DIST_STEPS[j]) {
			return ROLL_DIV[ROLL_DIV.length - 1 - j];
		}
	}
	return ROLL_DIV[4];
}

function distValue(d, s) {
	if (s['measure'] === 'forecast') {
		const f = distForecast(d, s['env']);
		return f ? f['mid'] : null;
	}
	if (s['measure'] !== 'logged' && d['logged'] < DIST_MIN_LOGGED) {
		return null;
	}
	return d[s['measure']];
}

/** Mail read for display, or a dash when too little of the district is logging. */
function distRead(d, k) {
	return d['logged'] < DIST_MIN_LOGGED ? '–' : rollFmt('ratio', d[k]);
}

function distColor(d, s) {
	const v = distValue(d, s);
	if (s['measure'] === 'forecast') {
		return v === null ? ROLL_DIV[ROLL_DIV.length - 1] : distMarginColor(v);
	}
	if (s['measure'] === 'logged') {
		for (var i = 0; i < DIST_LOGGED_BINS.length; ++i) {
			if (v >= DIST_LOGGED_BINS[i][0]) {
				return DIST_LOGGED_BINS[i][1];
			}
		}
	}
	return v === null ? NO_DATA_COLOR : rollColor('ratio', v, DIST['state'][s['measure']]);
}

function distName(d) {
	const n = d['nominees'];
	function who(i, p) {
		if (!n[i]) {
			return '';
		}
		const inc = d['inc'][2] === (p === 'R' ? 1 : -1);
		return '<span class="pa-' + (p === 'R' ? 'rep' : 'dem') + '">' + n[i] + '</span> (' + p + (inc ? ', inc.' : '') + ')';
	}
	return [who(0, 'D'), who(1, 'R')].filter(Boolean).join(' vs ');
}

function distHover(cd) {
	const d = DIST['districts'][cd - 1], s = distState();
	const f = distForecast(d, s['env']);
	var h = 'PA-' + cd + '<br /><small>' + distName(d) + '<br />';
	h += f ? 'Margin range: <b>' + distFmt(f['lo']) + ' to ' + distFmt(f['hi']) + '</b>, middle ' + distFmt(f['mid']) +
		' (environment ' + distEnvLabel(s['env']) + ')<br />' : 'No contested House race in 2022 or 2024 to fit<br />';
	h += 'House result: 2022 ' + distFmt(d['actual']['2022']) + ', 2024 ' + distFmt(d['actual']['2024']) + '<br />';
	h += 'Habitual mail R ÷ D: <b>' + distRead(d, 'hab') + '</b> · surge vs habitual: <b>' +
		distRead(d, 'surge') + '</b><br />';
	h += pct1(d['logged'], 1) + ' of habitual mail voters are in logging counties' +
		(d['logged'] < DIST_MIN_LOGGED ? ' (too few to read)' : '');
	if (d['unlogged'].length) {
		h += '<br />Not logging yet: ' + d['unlogged'].map(distCounty).join(', ');
	}
	return h + '<br />Click to show the breakdown</small>';
}

function distCounty(c) {
	return c === 'MCKEAN' ? 'McKean' : c.charAt(0) + c.slice(1).toLowerCase();
}

function distKpis(s, rows) {
	function kpi(label, value, sub, cls) {
		return '<div class="pa-kpi' + (cls ? ' pa-kpi--' + cls : '') + '"><div class="pa-kpi-label">' + label +
			'</div><div class="pa-kpi-value">' + value + '</div><div class="pa-kpi-sub">' + sub + '</div></div>';
	}
	var dSure = 0, rSure = 0, dLean = 0, rLean = 0;
	$.each(rows, function (ignore, x) {
		if (!x.f) {
			dSure++;
		} else if (x.f['hi'] < 0) {
			dSure++;
		} else if (x.f['lo'] > 0) {
			rSure++;
		} else if (x.f['mid'] < 0) {
			dLean++;
		} else {
			rLean++;
		}
	});
	return kpi('D favored', String(dSure + dLean), dSure + ' with the whole range D, ' + dLean + ' leaning', 'dem') +
		kpi('R favored', String(rSure + rLean), rSure + ' with the whole range R, ' + rLean + ' leaning', 'rep') +
		kpi('Now held', '10 R · 7 D', 'after the 2024 election');
}

function distBar(f) {
	const W = 240, lim = 30;
	function x(m) { return W / 2 + Math.max(-lim, Math.min(lim, m)) / lim * (W / 2); }
	if (!f) {
		return '';
	}
	const color = f['mid'] < 0 ? 'var(--pa-dem)' : 'var(--pa-rep)';
	return '<svg class="pa-cd-bar" viewBox="0 0 ' + W + ' 14" width="' + W + '" height="14" aria-hidden="true">' +
		'<line x1="' + W / 2 + '" x2="' + W / 2 + '" y1="0" y2="14" stroke="#9a9a96" stroke-width="1"/>' +
		'<rect x="' + x(f['lo']).toFixed(1) + '" y="4" width="' + Math.max(1, x(f['hi']) - x(f['lo'])).toFixed(1) +
		'" height="6" rx="3" fill="' + color + '" opacity="0.35"/>' +
		'<circle cx="' + x(f['mid']).toFixed(1) + '" cy="7" r="3.5" fill="' + color + '"/></svg>';
}

function distTable(s, rows) {
	var body = '';
	$.each(rows, function (ignore, x) {
		const d = x.d, f = x.f;
		const cls = DIST_PIN === d['cd'] ? ' class="pa-sel"' : '';
		body += '<tr' + cls + ' data-cd="' + d['cd'] + '"><th scope="row">PA-' + d['cd'] + '<small>' + distName(d) + '</small></th>' +
			'<td>' + (f ? distFmt(f['mid']) + '<small>' + distFmt(f['lo']) + ' to ' + distFmt(f['hi']) + '</small>' : 'Safe D<small>no contested baseline</small>') + '</td>' +
			'<td class="pa-cd-barcell">' + distBar(f) + '<small>2024 House ' + distFmt(d['actual']['2024']) + '</small></td>' +
			'<td>' + distRead(d, 'hab') + '<small>surge ' + distRead(d, 'surge') + '<br />' +
			pct1(d['logged'], 1) + ' logged</small></td></tr>';
	});
	$('#paTable').html('<caption class="pa-caption">Sorted closest race first · environment ' + distEnvLabel(s['env']) +
		(DIST_PIN ? ' · <button type="button" class="pa-linkbtn" id="distUnpin">Clear selection</button>' : '') + '</caption>' +
		'<thead><tr><th>District</th><th>Margin<small>middle, range</small></th><th class="pa-cd-barcell">Range<small>D +30 to R +30</small></th>' +
		'<th>Mail R ÷ D<small>habitual, surge</small></th></tr></thead>' +
		'<tbody>' + body + '</tbody>');
	$('#paTable tbody tr').on('click', function () {
		const cd = Number($(this).data('cd'));
		DIST_PIN = DIST_PIN === cd ? null : cd;
		doUpdate();
	});
	$('#distUnpin').on('click', function () { DIST_PIN = null; doUpdate(); });
	$('#paFoot').html('Margins are two-party, R minus D. Party is registration, not vote choice; "mail R ÷ D" ' +
		'compares return rates inside each county piece of the district (roll of ' + DIST['roll_date'] +
		'), counting only counties that are logging returns; "–" means under ' + DIST_MIN_LOGGED * 100 +
		'% of the district is logging. 2026 nominees per Spotlight PA.');
}

/** The pinned district's margin, term by term, for each baseline year. */
function distDetail(s) {
	if (!DIST_PIN) {
		return '<p class="pa-note">Click a district on the map or in the table to see how its range is built.</p>';
	}
	const d = DIST['districts'][DIST_PIN - 1];
	const years = Object.keys(d['delta']);
	if (!years.length) {
		return '<p class="pa-note"><b>PA-' + d['cd'] + ':</b> no contested House race in 2022 or 2024, so there is ' +
			'nothing to fit. It is safely Democratic.</p>';
	}
	const el = (DIST_ELASTICITY[0] + DIST_ELASTICITY[1]) / 2;
	var rows = '';
	$.each(years, function (ignore, y) {
		const base = d['actual'][y];
		const turnout = distLogitMargin(d['electorate']['m'], d['delta'][y]) - base;
		const swing = el * (-s['env'] - DIST['national'][y]);
		const inc = DIST_INC_PTS * (d['inc'][2] - d['inc'][y === '2022' ? 0 : 1]);
		rows += '<tr><th scope="row">From ' + y + '</th><td>' + distFmt(base) + '</td><td>' + pts(turnout) + '</td><td>' +
			pts(swing) + '</td><td>' + pts(inc) + '</td><td>' + distFmt(base + turnout + swing + inc) + '</td></tr>';
	});
	function pts(v) {
		return Math.abs(v) < 0.05 ? '0' : '<span class="pa-' + (v < 0 ? 'dem' : 'rep') + '">' + (v < 0 ? 'D' : 'R') +
			' +' + Math.abs(v).toFixed(1) + '</span>';
	}
	return '<div class="pa-label">PA-' + d['cd'] + ': how the middle estimate is built</div>' +
		'<table class="pa-table pa-cd-detail"><thead><tr><th></th><th>House result</th><th>Midterm turnout</th>' +
		'<th>National swing</th><th>Incumbency</th><th>2026</th></tr></thead><tbody>' + rows + '</tbody></table>' +
		'<p class="pa-note">The middle estimate averages the rows. The range also tries the D-best and R-best turnout ' +
		'cases, the environment ±' + DIST_POLL_ERR + ' points, and a district swing of ' +
		DIST_ELASTICITY[0] * 100 + '–' + DIST_ELASTICITY[1] * 100 + '% of the national swing.</p>';
}

function distLegend(s) {
	var bar = '';
	if (s['measure'] === 'forecast') {
		const tips = ['R +20 or more', 'R +12 to +20', 'R +6 to +12', 'R +2 to +6', 'Within 2', 'D +2 to +6', 'D +6 to +12',
			'D +12 to +20', 'D +20 or more'];
		$.each(ROLL_DIV, function (i, c) {
			bar += '<span style="background:' + c + '" data-tip-title="' + tips[i] + '"></span>';
		});
		$('#paLegend').html('<div>Middle estimate, two-party margin</div><div class="pa-legend-bar">' + bar + '</div>' +
			'<div class="pa-legend-ends"><span class="pa-rep">R +20</span><span>even</span><span class="pa-dem">D +20</span></div>');
		return;
	}
	if (s['measure'] === 'logged') {
		const labels = ['70%+', '40–70%', '10–40%', 'under 10%'];
		$.each(DIST_LOGGED_BINS.slice().reverse(), function (i, b) {
			bar += '<span style="background:' + b[1] + '" data-tip-title="' + labels[labels.length - 1 - i] + '"></span>';
		});
		$('#paLegend').html('<div>Habitual mail voters in counties that are logging returns</div><div class="pa-legend-bar">' +
			bar + '</div><div class="pa-legend-ends"><span>not readable</span><span>readable</span></div>');
		return;
	}
	rollLegend('ratio', DIST['state'][s['measure']], 'the statewide value');
}

function distUpdate() {
	if (!DIST) {
		$('#countMethod').text('Loading districts…');
		return;
	}
	const s = distState();
	$('#distEnvOut').html(distEnvLabel(s['env']));
	$('#distEnvBox').toggleClass('pa-off', s['measure'] !== 'forecast');
	$('#distRead').html(distExplain(s['measure']));
	$('#countMethod').text('U.S. House · ' + DIST_MEASURES[s['measure']]['label'] +
		(s['measure'] === 'forecast' ? ' at a national environment of ' + distEnvLabel(s['env']) : ''));
	const rows = $.map(DIST['districts'], function (d) { return { d: d, f: distForecast(d, s['env']) }; });
	rows.sort(function (a, b) {
		return (a.f ? Math.abs(a.f['mid']) : 999) - (b.f ? Math.abs(b.f['mid']) : 999);
	});
	$('#paKpis').html(distKpis(s, rows));
	$('#paBars').empty();
	$('#distDetail').html(distDetail(s));
	$('#paDistricts').show();
	$('#paDistricts .pa-cd').each(function () {
		const cd = Number(this.id.slice(4));
		this.setAttribute('fill', distColor(DIST['districts'][cd - 1], s));
		$(this).toggleClass('pa-pinned', cd === DIST_PIN);
	});
	distLegend(s);
	distTable(s, rows);
	distContext();
}

function distHide() {
	$('#paDistricts').hide();
}

function distExplain(m) {
	const t = {
		'forecast': ['Who wins each seat if the national mood is what the polls say?',
			'Ranges that stay on the R side, especially in PA-7, PA-8 and PA-10, which were all within 2 points in 2024.',
			'Ranges centered on the D side. A midterm against the president\'s party moves every seat; the mail data ' +
			'only nudges the margins.',
			'Each district\'s 2022 and 2024 House results set its lean, refit to the electorate a midterm is expected to ' +
			'bring out (from who voted in 2022 after skipping or voting in 2018 and 2020). The national swing from those ' +
			'years to the environment you pick is added, plus 2 points per side for a change in which party holds the seat.'],
		'hab': ['Are the people who always vote by mail returning ballots at their usual R-to-D pace in this district?',
			'Near or above the statewide value (0.84×).',
			'Well below it: the R mail base in that district is holding ballots.',
			'R return rate ÷ D return rate among voters who mailed in 2022 and 2024, compared inside each county piece of ' +
			'the district and pooled, logging counties only.'],
		'surge': ['Are the voters Republicans gained in 2024 coming back in this district?',
			'Near 1.00×: R surge voters return as reliably, relative to the R base, as D surge voters do.',
			'Low: the district\'s electorate is drifting back to 2022. The low end of the margin range assumes the ' +
			'statewide lag holds everywhere.',
			'Return rate of 2024 mail voters who skipped 2022 ÷ habitual mail rate, R over D, inside each county piece.'],
		'logged': ['Can this district be read yet?',
			'Not partisan.',
			'Not partisan. Below about 40%, the mail columns lean on whichever county logs first.',
			'Share of the district\'s habitual mail voters who live in counties that have started logging returns.']
	}[m];
	return '<p class="pa-q-plain">' + t[0] + '</p>' +
		'<div class="pa-gb pa-gb-good"><b>Good for Republicans</b>' + t[1] + '</div>' +
		'<div class="pa-gb pa-gb-bad"><b>Bad for Republicans</b>' + t[2] + '</div>' +
		'<p class="pa-note"><b>How it\'s measured:</b> ' + t[3] + '</p>';
}

function distContext() {
	const g = DIST['generic'];
	$('#paContext').html('<div class="pa-label">What this range is and is not</div>' +
		'<p class="pa-note">It is a scenario range, not a poll and not a probability. The national environment does most ' +
		'of the work: generic-ballot averages on ' + g['as_of'] + ' ran from D +' + g['lo'] + ' to D +' + g['hi'] +
		', against R +' + DIST['national']['2022'] + ' and R +' + DIST['national']['2024'] + ' in the 2022 and 2024 ' +
		'House popular vote. The mail data moves the margins by a point or two, through who is expected to turn out.</p>' +
		'<p class="pa-note">Candidate quality, money and crossover voting are only in it as far as they were in the ' +
		'2022 and 2024 results. Shapiro at the top of the ticket is not modeled.</p>');
}

function distControls() {
	function chip(value, main, sub, checked) {
		return '<label class="pa-chip"><input type="radio" name="distMeasure" value="' + value + '"' +
			(checked ? ' checked="checked"' : '') + '><span>' + main + '</span><small>' + sub + '</small></label>\n';
	}
	var h = '<div class="pa-group pa-mode-dist" hidden><div class="pa-label">Color districts by</div><div class="pa-list">\n';
	$.each(DIST_MEASURES, function (k, m) {
		h += chip(k, m['label'], m['sub'], k === 'forecast');
	});
	h += '</div><div class="pa-explain" id="distRead"></div></div>\n';
	h += '<div class="pa-group pa-mode-dist" id="distEnvBox" hidden><div class="pa-label">National environment (generic ballot): ' +
		'<b id="distEnvOut">D +8.0</b></div>' +
		'<input type="range" id="distEnv" min="-4" max="14" step="0.5" value="8" aria-label="National environment, D lead in points">' +
		'<div class="pa-legend-ends"><span class="pa-rep">R +4</span><span>even</span><span class="pa-dem">D +14</span></div>' +
		'<p class="pa-note" id="distEnvNote"></p></div>\n';
	return h;
}

function distBind() {
	$('#distEnv').on('input', doUpdate);
}

function distReady() {
	const g = DIST['generic'];
	if (!distHashEnv) {
		$('#distEnv').val(g['default']);
	}
	$('#distEnvNote').html('Starts at D +' + g['default'].toFixed(1) + ', inside the range of generic-ballot averages on ' +
		g['as_of'] + ' (D +' + g['lo'] + ' to D +' + g['hi'] + '). The 2022 and 2024 House popular vote was R +' +
		DIST['national']['2022'] + ' and R +' + DIST['national']['2024'] + '.');
}

var distHashEnv = false;

/** #districts/<measure>/<env>, e.g. #districts/forecast/8 */
function distHashWrite() {
	const s = distState();
	history.replaceState(null, '', '#districts/' + s['measure'] + '/' + s['env']);
}

function distHashRead() {
	const parts = location.hash.replace(/^#/, '').split('/');
	if (parts[0] !== 'districts') {
		return false;
	}
	$('input[name=paMode][value=dist]').prop('checked', true);
	if (DIST_MEASURES[parts[1]]) {
		$('input[name=distMeasure][value=' + parts[1] + ']').prop('checked', true);
	}
	if (parts[2] !== undefined && isFinite(Number(parts[2])) && parts[2] !== '') {
		$('#distEnv').val(Number(parts[2]));
		distHashEnv = true;
	}
	return true;
}
