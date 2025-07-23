const { test, expect } = require('@playwright/test');

test('officers_index_html loads correctly', async ({ page }) => {
  const response = await page.goto(`${process.env.HOST || 'http://localhost:4000'}/officers/index.html`);
  expect(response.status()).toBe(200);
  await page.waitForFunction('document.title !== ""');
  await expect(page).toHaveTitle('Search results for william kristol - Government NGO tracking');
  // Add more assertions here
});