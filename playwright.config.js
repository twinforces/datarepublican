/** @type {import('@playwright/test').PlaywrightTestConfig} */
const config = {
  testDir: './tests',
  testMatch: ['**/*.spec.js'],
  timeout: 60000,
  retries: 2, // Retry failed tests twice
  use: {
    headless: true,
    viewport: { width: 1280, height: 720 },
  },
};

module.exports = config;