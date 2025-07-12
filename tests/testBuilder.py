import os
from pathlib import Path
import re
import frontmatter
from html import unescape

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

def get_title_content(file_path, docs_root="../docs"):
    """Extract the <title> content from corresponding docs/ folder index.html or source file."""
    try:
        project_root = os.path.abspath(os.path.join(os.getcwd(), ".."))
        relative_path = os.path.relpath(file_path, start=project_root).replace(os.sep, '/')
        docs_html_path = os.path.join(docs_root, relative_path.replace('.md', '.html'))
        
        if os.path.exists(docs_html_path):
            with open(docs_html_path, 'r', encoding='utf-8') as f:
                content = f.read()
            match = re.search(r'<title>(.*?)</title>', content, re.IGNORECASE | re.DOTALL)
            if match:
                return unescape(match.group(1).strip())
        
        # Fallback to source file title
        if file_path.endswith('.md'):
            post = frontmatter.load(file_path)
            title = post.get('title', None)
            return unescape(title) if title else None
        else:
            with open(file_path, 'r', encoding='utf-8') as f:
                content = f.read()
            match = re.search(r'<title>(.*?)</title>', content, re.IGNORECASE | re.DOTALL)
            if match:
                return unescape(match.group(1).strip())
        
        return None
    except Exception as e:
        print(f"Error reading title from {file_path}: {e}")
        return None

def generate_test_content(file_path):
    """Generate test content for a given index.html or index.md file."""
    project_root = os.path.abspath(os.path.join(os.getcwd(), ".."))
    relative_path = os.path.relpath(file_path, start=project_root)
    url_path = relative_path.replace(os.sep, '/')
    if file_path.endswith('.md'):
        url_path = url_path.replace('.md', '.html')  # Serve .md as .html in Jekyll
    full_url = f"`${{process.env.HOST || '{DEFAULT_HOST}'}}/{url_path}`"

    test_name = sanitize_filename(relative_path.replace(os.sep, '_'))
    test_filename = f"test_{test_name}.spec.js"  # Use .spec.js for Playwright

    title = get_title_content(file_path)
    title_assertion = ""
    if title:
        title = title.replace("'", "\\'")  # Escape single quotes
        # Add wait for title to handle dynamic updates
        title_assertion = f"  await page.waitForFunction('document.title !== \"\"');\n  await expect(page).toHaveTitle('{title}');\n"

    test_content = f"{PLAYWRIGHT_IMPORT}test('{test_name} loads correctly', async ({{ page }}) => {{\n  const response = await page.goto({full_url});\n  expect(response.status()).toBe(200);\n{title_assertion}  // Add more assertions here\n}});"

    return test_filename, test_content

def find_and_generate_tests(root_dir="../"):
    """Scan parent directory for index.html and index.md files and generate tests, skipping /docs and /node_modules."""
    create_test_dir()
    test_files_created = 0
    project_root = os.path.abspath(os.path.join(os.getcwd(), ".."))

    for root, _, files in os.walk(root_dir):
        norm_root = os.path.normpath(os.path.abspath(root)).replace(os.sep, '/')
        root_rel = os.path.relpath(norm_root, start=project_root).replace(os.sep, '/')
        if root_rel == 'docs' or root_rel.startswith('docs/') or \
           root_rel == 'node_modules' or root_rel.startswith('node_modules/'):
            print(f"Skipping directory: {norm_root}")
            continue

        print(f"Scanning directory: {norm_root}")
        for file_name in ("index.html", "index.md"):
            if file_name in files:
                file_path = os.path.join(root, file_name)
                print(f"Found {file_name}: {file_path}")
                test_filename, test_content = generate_test_content(file_path)
                test_filepath = os.path.join(TEST_DIR, test_filename)
                
                with open(test_filepath, "w", encoding="utf-8") as f:
                    f.write(test_content)
                print(f"Created test file: {test_filepath}")
                test_files_created += 1

    if test_files_created == 0:
        print("No index.html or index.md files found in the parent directory hierarchy (excluding /docs and /node_modules).")
    else:
        print(f"Generated {test_files_created} test files in {TEST_DIR}/")

if __name__ == "__main__":
    print("Scanning for index.html and index.md files in parent directory (excluding /docs and /node_modules)...")
    find_and_generate_tests()
    print("Done. To run tests, use: HOST=https://preview.datarepublican.com npx playwright test")