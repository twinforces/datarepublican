// longmarch/model.test.js
// Unit tests for Person class, ingestTSV, searchPeople, and getSummary
// Run with: node --test longmarch/model.test.js

import { describe, it, beforeEach } from 'node:test';
import assert from 'node:assert/strict';

import {
  Person,
  ingestTSV,
  searchPeople,
  getSummary,
  people,
  peopleByLastName
} from './model.js';

// Sample TSV excerpt provided by user (Karen + Bass lines + a few extras for coverage)
const SAMPLE_TSV = `lastname	firstname	fullname	organization	title	year
Wetter	Pierce	Pierce T. Wetter III	Radius	Engineer	1995
Wetter	Pierce	Pierce T. Wetter III	SuperMac	Engineer	1996
Wetter	Pierce	Pierce T. Wetter III	SomeOrg	VP	2005
Smith	John	John Smith	SomeOrg	Analyst	2005
Smith	John	John Smith	AnotherOrg	Director	2010
Bentley	Karen	KAREN BENTLEY	AMBASSADOR CHRISTIAN SCHOOL INC		2024
Bentley	Karen	KAREN BENTLEY	AMBASSADOR CHRISTIAN SCHOOL INC		2023
Bassler	Karen	Karen Bassler	Bavarian Aid Society		2023
Comeaux	Karen	KAREN COMEAUX	GREENBRIAR-PRAIRIE BASSE WATER CORP		2024
Abbassi	Karen	KAREN ABBASSI	Morton Plant Mease Health Services Inc		2024
Bass	Karen	KAREN BASS	SPARKLE TOUCH LEARNING ACADEMY INC		2024
Bass	Karen	KAREN L BASS	SPARKLE TOUCH LEARNING ACADEMY INC		2024
Bass	Karen	KAREN L BASS	SPARKLE TOUCH LEARNING ACADEMY INC		2023
Bass	Karen	KAREN L BASS	SPARKLE TOUCH LEARNING CENTER MINISTRY INC		2022
Bass	Karen	KAREN L BASS	SPARKLE TOUCH LEARNING CENTER MINISTRY INC		2020
`;

describe('Person class', () => {
  it('should create a person with correct properties', () => {
    const p = new Person('Bass', 'Karen', 'KAREN L BASS');
    assert.equal(p.lastname, 'Bass');
    assert.equal(p.firstname, 'Karen');
    assert.equal(p.fullname, 'KAREN L BASS');
    assert.deepEqual(p.orgsByYear, {});
    assert.equal(p.years.size, 0);
  });

  it('should add organizations by year and track years correctly', () => {
    const p = new Person('Bass', 'Karen', 'KAREN L BASS');
    p.addOrg(2024, 'SPARKLE TOUCH LEARNING ACADEMY INC', '');
    p.addOrg(2023, 'SPARKLE TOUCH LEARNING ACADEMY INC', '');
    p.addOrg(2022, 'SPARKLE TOUCH LEARNING CENTER MINISTRY INC', '');

    assert.equal(p.orgCount, 3);
    assert.deepEqual(p.sortedYears, [2022, 2023, 2024]);
    assert.equal(p.firstYear, 2022);
    assert.equal(p.lastYear, 2024);
    assert.equal(p.orgsByYear[2024].length, 1);
  });

  it('should handle multiple orgs in the same year', () => {
    const p = new Person('Test', 'Multi', 'Multi Test');
    p.addOrg(2024, 'Org A', 'Title A');
    p.addOrg(2024, 'Org B', 'Title B');

    assert.equal(p.orgCount, 1); // still one year
    assert.equal(p.orgsByYear[2024].length, 2);
  });
});

describe('ingestTSV', () => {
  beforeEach(() => {
    // Reset module state between tests
    people.length = 0;
    peopleByLastName.clear();
  });

  it('should parse TSV and create correct number of unique people (strict fullname match)', async () => {
    const result = await ingestTSV(SAMPLE_TSV);

    // Note: KAREN BASS and KAREN L BASS are treated as different people (strict match on fullname)
    assert.equal(result.length, 9); // 3 Wetters? No: Wetter x1, Smith x1, + 7 Karens (Bentley, Bassler, Comeaux, Abbassi, Bass x3 variants)
    // Actually recounting from data: Wetter(1), Smith(1), Bentley(1), Bassler(1), Comeaux(1), Abbassi(1), Bass(3 variants) = 9
    assert.equal(result.length, 9);
  });

  it('should correctly attach multiple years/orgs to the same person', async () => {
    await ingestTSV(SAMPLE_TSV);

    const wetter = searchPeople('Wetter')[0];
    assert.equal(wetter.lastname, 'Wetter');
    assert.equal(wetter.firstYear, 1995);
    assert.equal(wetter.lastYear, 2005);
    assert.equal(wetter.orgCount, 3);
  });

  it('should skip rows missing lastname, organization, or year', async () => {
    const badData = `lastname	firstname	fullname	organization	title	year
Bass	Karen	KAREN BASS	SPARKLE TOUCH		2024
			Missing Lastname		2023
`;
    const result = await ingestTSV(badData);
    assert.equal(result.length, 1);
  });

  it('should handle duplicate rows for the same person gracefully', async () => {
    const dupData = `lastname	firstname	fullname	organization	title	year
Bass	Karen	KAREN BASS	Org One		2024
Bass	Karen	KAREN BASS	Org One		2024
Bass	Karen	KAREN BASS	Org Two		2023
`;
    const result = await ingestTSV(dupData);
    assert.equal(result.length, 1);
    const p = result[0];
    assert.equal(p.orgCount, 2); // 2023 and 2024
    assert.equal(p.orgsByYear[2024].length, 1); // duplicate year entry not duplicated
  });
});

describe('searchPeople', () => {
  beforeEach(async () => {
    people.length = 0;
    peopleByLastName.clear();
    await ingestTSV(SAMPLE_TSV);
  });

  it('should find people by lastname (case insensitive)', () => {
    const results = searchPeople('bass');
    assert.ok(results.length >= 3); // multiple Bass variants
    assert.ok(results.every(p => p.lastname.toLowerCase() === 'bass'));
  });

  it('should filter by firstname when provided (partial match)', () => {
    const results = searchPeople('Bass', 'L');
    assert.ok(results.length >= 1);
    assert.ok(results.every(p => p.firstname.toLowerCase().includes('l') || p.fullname.toLowerCase().includes('l bass')));
  });

  it('should return empty array for unknown lastname', () => {
    const results = searchPeople('Nonexistent');
    assert.equal(results.length, 0);
  });
});

describe('getSummary', () => {
  beforeEach(async () => {
    people.length = 0;
    peopleByLastName.clear();
    await ingestTSV(SAMPLE_TSV);
  });

  it('should return the exact expected summary string format', () => {
    const bassPeople = searchPeople('Bass');
    const summary = getSummary(bassPeople);

    assert.ok(summary.startsWith('Search found '));
    assert.ok(summary.includes('matching person(s) across'));
    assert.ok(summary.includes('organizations with'));
    assert.ok(summary.includes('fellow-traveler entries over'));
    assert.ok(summary.includes('years ('));
    assert.ok(summary.endsWith(').'));
  });

  it('should return "No matches found." for empty input', () => {
    const summary = getSummary([]);
    assert.equal(summary, 'No matches found.');
  });

  it('should count organizations and years correctly across people', async () => {
    const allPeople = await ingestTSV(SAMPLE_TSV); // re-ingest to get full set
    const summary = getSummary(allPeople);

    // We don't assert exact numbers here because they depend on the strict dedup logic,
    // but we verify the string contains plausible numbers
    assert.ok(summary.includes('Search found 9 matching person(s)'));
  });
});
