const { test, expect } = require('@playwright/test');

test('browse_index_html loads correctly', async ({ page }) => {
  const response = await page.goto(`${process.env.HOST || 'http://localhost:4000'}/browse/index.html`);
  expect(response.status()).toBe(200);
  await page.waitForFunction('document.title !== ""');
  await expect(page).toHaveTitle('Charity explorer - Sankey chart of NGOs and their flows | DataRepublican');
  // Add more assertions here
});