const { test, expect } = require('@playwright/test');

test('nokings_index_html loads correctly', async ({ page }) => {
  const response = await page.goto(`${process.env.HOST || 'http://localhost:4000'}/nokings/index.html`);
  expect(response.status()).toBe(200);
  await page.waitForFunction('document.title !== ""');
  await expect(page).toHaveTitle('NoKings → Federal‑Grant Links | DataRepublican');
  // Add more assertions here
});