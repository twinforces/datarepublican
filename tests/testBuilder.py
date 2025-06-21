import os
from pathlib import Path
import re

# Configuration
DEFAULT_HOST = "http://localhost:4000"  # Default for tests if process.env.HOST is unset
TEST_DIR = "."  # Save tests in current directory (tests/)
PLAYWRIGHT_IMPORT = "const { test, expect } = require('@playwright/test');\n\n"

def create_test_dir():
    """Create the tests directory if it doesn't exist."""
    Path(TEST_DIR).mkdir(exist_ok=True)

def sanitize_filename(name):
    """Convert a path into a valid filename."""
    name = re.sub(r'[^\w\-]', '_', name)
    return name.strip('_')

def generate_test_content(html_path):
    """Generate test content for a given index.html file."""
    # Calculate relative path from project root (parent of tests/)
    project_root = os.path.abspath(os.path.join(os.getcwd(), ".."))
    relative_path = os.path.relpath(html_path, start=project_root)
    url_path = relative_path.replace(os.sep, '/')
    # Use JavaScript template literal for dynamic host
    full_url = f"`${{process.env.HOST || '{DEFAULT_HOST}'}}/{url_path}`"

    test_name = sanitize_filename(relative_path.replace(os.sep, '_'))
    test_filename = f"test_{test_name}.spec.js"  # Use .spec.js for Playwright

    test_content = f"""{PLAYWRIGHT_IMPORT}
test('{test_name} loads correctly', async ({{ page }}) => {{
  const response = await page.goto({full_url});
  expect(response.status()).toBe(200);
  await expect(page).toHaveTitle(/.+/);
  // Add more assertions here (e.g., await expect(page.locator('h1')).toBeVisible());
}});
"""

    return test_filename, test_content

def find_and_generate_tests(root_dir="../"):
    """Scan parent directory for index.html files and generate tests, skipping /docs and /node_modules."""
    create_test_dir()
    test_files_created = 0
    project_root = os.path.abspath(os.path.join(os.getcwd(), ".."))

    for root, _, files in os.walk(root_dir):
        # Normalize root path for consistent comparison
        norm_root = os.path.normpath(os.path.abspath(root)).replace(os.sep, '/')
        # Check if /docs or /node_modules is a parent directory in the path
        root_rel = os.path.relpath(norm_root, start=project_root).replace(os.sep, '/')
        if root_rel == 'docs' or root_rel.startswith('docs/') or \
           root_rel == 'node_modules' or root_rel.startswith('node_modules/'):
            print(f"Skipping directory: {norm_root}")
            continue

        print(f"Scanning directory: {norm_root}")
        if "index.html" in files:
            html_path = os.path.join(root, "index.html")
            print(f"Found index.html: {html_path}")
            test_filename, test_content = generate_test_content(html_path)
            test_filepath = os.path.join(TEST_DIR, test_filename)
            
            with open(test_filepath, "w", encoding="utf-8") as f:
                f.write(test_content)
            print(f"Created test file: {test_filepath}")
            test_files_created += 1

    if test_files_created == 0:
        print("No index.html files found in the parent directory hierarchy (excluding /docs and /node_modules).")
    else:
        print(f"Generated {test_files_created} test files in {TEST_DIR}/")

if __name__ == "__main__":
    print("Scanning for index.html files in parent directory (excluding /docs and /node_modules)...")
    find_and_generate_tests()
    print("Done. To run tests, use: HOST=https://preview.datarepublican.com npx playwright test")