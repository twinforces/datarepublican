const { test, expect } = require('@playwright/test');

test('wordle_index_html loads correctly', async ({ page }) => {
  const response = await page.goto(`${process.env.HOST || 'http://localhost:4000'}/wordle/index.html`);
  expect(response.status()).toBe(200);
  await page.waitForFunction('document.title !== ""');
  await expect(page).toHaveTitle('wordle cheater');
  // Add more assertions here
});