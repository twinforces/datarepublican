// longmarch/model.js
// Person class and data model for long march visualization

export class Person {
  constructor(lastname, firstname, fullname) {
    this.lastname = lastname;
    this.firstname = firstname;
    this.fullname = fullname || `${firstname} ${lastname}`;
    this.orgsByYear = {}; // year -> array of {organization, title}
    this.years = new Set();
  }

  addOrg(year, organization, title) {
    if (!this.orgsByYear[year]) this.orgsByYear[year] = [];
    this.orgsByYear[year].push({ organization, title });
    this.years.add(year);
  }

  get sortedYears() {
    return Array.from(this.years).sort((a, b) => a - b);
  }

  get firstYear() {
    return this.sortedYears[0];
  }

  get lastYear() {
    return this.sortedYears[this.sortedYears.length - 1];
  }

  get orgCount() {
    return Object.keys(this.orgsByYear).length;
  }
}

// Simple in-memory store
let people = [];
let peopleByLastName = new Map(); // lastname lower -> array of Person

// Ingest TSV (for now expects array of objects or raw text; gz support later via DecompressionStream)
export async function ingestTSV(tsvTextOrUrl) {
  let rows;
  if (typeof tsvTextOrUrl === 'string' && tsvTextOrUrl.startsWith('http')) {
    const res = await fetch(tsvTextOrUrl);
    const text = await res.text();
    rows = parseTSV(text);
  } else if (typeof tsvTextOrUrl === 'string') {
    rows = parseTSV(tsvTextOrUrl);
  } else {
    rows = tsvTextOrUrl; // assume pre-parsed
  }

  people = [];
  peopleByLastName.clear();

  for (const row of rows) {
    const { lastname, firstname, fullname, organization, title, year } = row;
    if (!lastname || !organization || !year) continue;

    const key = lastname.toLowerCase();
    if (!peopleByLastName.has(key)) {
      peopleByLastName.set(key, []);
    }

    let person = peopleByLastName.get(key).find(p => 
      (p.firstname || '').toLowerCase() === (firstname || '').toLowerCase() &&
      p.fullname.toLowerCase() === (fullname || '').toLowerCase()
    );

    if (!person) {
      person = new Person(lastname, firstname, fullname);
      peopleByLastName.get(key).push(person);
      people.push(person);
    }

    person.addOrg(parseInt(year, 10), organization, title || '');
  }

  console.log(`Ingested ${people.length} people across ${new Set(people.flatMap(p => Array.from(p.years))).size} years`);
  return people;
}

function parseTSV(text) {
  const lines = text.trim().split(/\r?\n/);
  if (lines.length === 0) return [];
  const headers = lines[0].split('\t').map(h => h.trim().toLowerCase());
  const rows = [];
  for (let i = 1; i < lines.length; i++) {
    const values = lines[i].split('\t');
    const row = {};
    headers.forEach((h, idx) => {
      row[h] = values[idx] ? values[idx].trim() : '';
    });
    rows.push(row);
  }
  return rows;
}

export function searchPeople(lastname, firstname = '') {
  const key = lastname.toLowerCase();
  const candidates = peopleByLastName.get(key) || [];
  if (!firstname) return candidates;

  const fn = firstname.toLowerCase();
  return candidates.filter(p => (p.firstname || '').toLowerCase().includes(fn));
}

export function getSummary(peopleList) {
  if (!peopleList || peopleList.length === 0) return 'No matches found.';

  const totalOrgs = new Set();
  let totalFellows = 0;
  const allYears = new Set();

  peopleList.forEach(person => {
    Object.values(person.orgsByYear).forEach(orgs => {
      orgs.forEach(o => totalOrgs.add(o.organization));
    });
    totalFellows += Object.keys(person.orgsByYear).length; // simplistic count of person-year-org entries
    person.years.forEach(y => allYears.add(y));
  });

  const yearsSorted = Array.from(allYears).sort((a,b)=>a-b);
  return `Search found ${peopleList.length} matching person(s) across ${totalOrgs.size} organizations with ${totalFellows} fellow-traveler entries over ${yearsSorted.length} years (${yearsSorted[0]}–${yearsSorted[yearsSorted.length-1]}).`;
}

export { people, peopleByLastName };
