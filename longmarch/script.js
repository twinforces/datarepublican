// longmarch/script.js
import { ingestTSV, searchPeople, getSummary } from './model.js';

// For scaffold: use a small inline sample TSV until real .tsv.gz is provided
const SAMPLE_TSV = `lastname	firstname	fullname	organization	title	year
Wetter	Pierce	Pierce T. Wetter III	Radius	Engineer	1995
Wetter	Pierce	Pierce T. Wetter III	SuperMac	Engineer	1996
Wetter	Pierce	Pierce T. Wetter III	SomeOrg	VP	2005
Smith	John	John Smith	SomeOrg	Analyst	2005
Smith	John	John Smith	AnotherOrg	Director	2010
`;

let dataLoaded = false;

async function init() {
  const lastInput = document.getElementById('lastNameInput');
  const firstInput = document.getElementById('firstNameInput');
  const searchBtn = document.getElementById('searchBtn');
  const summaryDiv = document.getElementById('summary');

  // Load sample data on init (replace with real fetch to .tsv.gz later)
  if (!dataLoaded) {
    await ingestTSV(SAMPLE_TSV);
    dataLoaded = true;
    console.log('Sample data loaded for longmarch scaffold');
  }

  searchBtn.addEventListener('click', () => doSearch(lastInput, firstInput, summaryDiv));
  lastInput.addEventListener('keypress', (e) => { if (e.key === 'Enter') doSearch(lastInput, firstInput, summaryDiv); });
  firstInput.addEventListener('keypress', (e) => { if (e.key === 'Enter') doSearch(lastInput, firstInput, summaryDiv); });

  // Optional: auto-search on type (debounced in real version)
}

function doSearch(lastInput, firstInput, summaryDiv) {
  const last = lastInput.value.trim();
  const first = firstInput.value.trim();

  if (!last) {
    summaryDiv.classList.add('hidden');
    return;
  }

  const matches = searchPeople(last, first);
  const summaryText = getSummary(matches);

  summaryDiv.innerHTML = `<strong>Results:</strong> ${summaryText}<br><small class="opacity-60">(Full timeline + packed-circle viz + arcs coming next. This is the model + summary scaffold.)</small>`;
  summaryDiv.classList.remove('hidden');

  // TODO: trigger D3 viz render with matches
  console.log('Search matches:', matches);
}

// Boot
if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', init);
} else {
  init();
}
