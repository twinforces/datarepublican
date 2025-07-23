const { test, expect } = require('@playwright/test');

test('clark_index_html loads correctly', async ({ page }) => {
  const response = await page.goto(`${process.env.HOST || 'http://localhost:4000'}/clark/index.html`);
  expect(response.status()).toBe(200);
  await page.waitForFunction('document.title !== ""');
  await expect(page).toHaveTitle('Clark Mail-Ins');
  // Add more assertions here
});