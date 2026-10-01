# Automated Tests
`python testBuilder.py` builds a set of script to test every index.html file.

## Setup
``` sh
npm install —save-dev @playwright/test
npx playwright install
```

## Running Tests
``` sh
npx playwright test
npx playwright test --ui
npx playwright test --reporter=list
HOST=https://57.datarepublican.com npx playwright test
npx playwright test --last-failed
npx playwright test --last-failed --headed

```