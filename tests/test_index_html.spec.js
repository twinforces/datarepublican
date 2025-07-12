const { test, expect } = require('@playwright/test');

test('index_html loads correctly', async ({ page }) => {
  const response = await page.goto(`${process.env.HOST || 'http://localhost:4000'}/index.html`);
  expect(response.status()).toBe(200);
  await page.waitForFunction('document.title !== ""');
  await expect(page).toHaveTitle('Exposing where the money flows | DataRepublican');
  // Add more assertions here
});