const { test, expect } = require('@playwright/test');

test('officers_bulk_index_html loads correctly', async ({ page }) => {
  const response = await page.goto(`${process.env.HOST || 'http://localhost:4000'}/officers/bulk/index.html`);
  expect(response.status()).toBe(200);
  await page.waitForFunction('document.title !== ""');
  await expect(page).toHaveTitle('NGO bulk officer search | DataRepublican');
  // Add more assertions here
});