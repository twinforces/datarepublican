const { test, expect } = require('@playwright/test');

test('policies_index_md loads correctly', async ({ page }) => {
  const response = await page.goto(`${process.env.HOST || 'http://localhost:4000'}/policies/index.html`);
  expect(response.status()).toBe(200);
  await page.waitForFunction('document.title !== ""');
  await expect(page).toHaveTitle('Policies | DataRepublican');
  // Add more assertions here
});