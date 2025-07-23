const { test, expect } = require('@playwright/test');

test('ned_index_html loads correctly', async ({ page }) => {
  const response = await page.goto(`${process.env.HOST || 'http://localhost:4000'}/ned/index.html`);
  expect(response.status()).toBe(200);
  await page.waitForFunction('document.title !== ""');
  await expect(page).toHaveTitle('NED Journal of Democracy Index | DataRepublican');
  // Add more assertions here
});