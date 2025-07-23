const { test, expect } = require('@playwright/test');

test('donations2024_index_html loads correctly', async ({ page }) => {
  const response = await page.goto(`${process.env.HOST || 'http://localhost:4000'}/donations2024/index.html`);
  expect(response.status()).toBe(200);
  await page.waitForFunction('document.title !== ""');
  await expect(page).toHaveTitle('ActBlue / WinRed 2024 donor lookup | DataRepublican');
  // Add more assertions here
});