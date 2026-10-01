/** @type {import('@playwright/test').PlaywrightTestConfig} */
const config = {
  testDir: "./tests",
  testMatch: ["**/*.spec.js"],
  timeout: 60000,
  retries: 2,
  use: {
    headless: true,
    viewport: { width: 1280, height: 720 },
    baseURL: process.env.HOST || "http://localhost:4000",
  },
  projects: [
    {
      name: "default",
      // Needs $10M chunks, IndexedDB profile, and 10-minute timeout.
      // Local: npx playwright test tests/browse_sankey.spec.js --project=demo
      testIgnore: ["**/browse_sankey.spec.js"],
    },
    {
      name: "demo",
      testMatch: ["**/browse_sankey.spec.js"],
      timeout: 600000,
      retries: 0,
      use: {
        headless: true,
        video: "on",
        screenshot: "on",
        viewport: { width: 1280, height: 720 },
      },
    },
  ],
};

module.exports = config;