const { test, expect } = require('@playwright/test');

test('pennsylvania_index_html loads correctly', async ({ page }) => {
  const response = await page.goto(`${process.env.HOST || 'http://localhost:4000'}/pennsylvania/index.html`);
  expect(response.status()).toBe(200);
  await page.waitForFunction('document.title !== ""');
  await expect(page).toHaveTitle('Pennsylvania 2026 general election map | DataRepublican');
  // Add more assertions here
});