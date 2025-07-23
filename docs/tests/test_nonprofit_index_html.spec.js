const { test, expect } = require('@playwright/test');

test('nonprofit_index_html loads correctly', async ({ page }) => {
  const response = await page.goto(`${process.env.HOST || 'http://localhost:4000'}/nonprofit/index.html`);
  expect(response.status()).toBe(200);
  await page.waitForFunction('document.title !== ""');
  await expect(page).toHaveTitle('Nonprofits | DataRepublican');
  // Add more assertions here
});