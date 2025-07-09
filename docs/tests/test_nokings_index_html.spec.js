const { test, expect } = require("@playwright/test");

// Helper function to escape regex special characters
function escapeRegExp(string) {
  return string.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

test("nokings_index_html loads correctly and handles browse navigation", async ({
  page,
}) => {
  const baseUrl = process.env.HOST || "http://localhost:4000";
  const response = await page.goto(`${baseUrl}/nokings/index.html`);

  expect(response.status()).toBe(200);
  await expect(page).toHaveTitle(/.+/);

  await page.click("text=Explore Top 100");

  // Verify URL: Match /browse/ with query parameters
  await expect(page).toHaveURL(
    new RegExp(`${escapeRegExp(baseUrl)}/browse/\\?e=.*`),
    { timeout: 10000 }
  );

  await page.waitForSelector(".loading", {
    state: "hidden",
    timeout: 300 * 1000,
  });

  await page.waitForFunction(
    () => {
      const graphDiv = document.querySelector(".graph");
      return graphDiv && graphDiv.querySelectorAll("svg").length > 0;
    },
    { timeout: 30000 }
  );

  await expect(page.locator(".graph svg")).toBeVisible();
});
