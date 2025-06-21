const { test, expect } = require('@playwright/test');


test('convex_index_html loads correctly', async ({ page }) => {
  const response = await page.goto(`${process.env.HOST || 'http://localhost:4000'}/convex/index.html`);
  expect(response.status()).toBe(200);
  await expect(page).toHaveTitle(/.+/);
  // Add more assertions here (e.g., await expect(page.locator('h1')).toBeVisible());
});
