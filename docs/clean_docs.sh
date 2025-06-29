#!/bin/bash

# Get list of all directories in root, excluding docs, assets, and .git
dirs=$(find . -maxdepth 1 -type d -not -path './docs' -not -path './assets' -not -path . -not -path './.git' | sed 's|^\./||')

# Check if any directories exist
if [ -z "$dirs" ]; then
    echo "No directories found in root (excluding docs, assets, and .git)."
    exit 1
fi

# Track if any files are removed
removed=0

# Iterate over each directory
for dir in $dirs; do
    # Verify the directory exists in /docs
    if [ ! -d "docs/$dir" ]; then
        echo "Warning: Directory 'docs/$dir' does not exist, skipping."
        continue
    fi

    # Get list of tracked files in the directory (recursively)
    tracked_files=$(git ls-files "$dir" | sort)

    # Find all files in docs/$dir recursively
    find "docs/$dir" -type f | while read -r doc_file; do
        # Convert /docs/$dir path to equivalent root $dir path
        root_file=${doc_file/docs\//}
        # Check if the file exists in the tracked files list
        if ! echo "$tracked_files" | grep -Fx "$root_file" > /dev/null; then
            echo "Removing $doc_file (untracked in $root_file)"
            git rm -f "$doc_file"
            removed=1
        fi
    done
done

# Verify no large files remain in the index
if [ $removed -eq 1 ]; then
    echo "Verifying no large files remain in the index..."
    # Check for files >50MB in the index
    git ls-files --stage | while read -r mode sha size path; do
        # Convert size from bytes to MB (approx)
        size_mb=$((size / 1024 / 1024))
        if [ $size_mb -gt 50 ]; then
            echo "Warning: Large file ($size_mb MB) still in index: $path"
        fi
    done
fi

# If files were removed, suggest committing
if [ $removed -eq 1 ]; then
    echo "Files removed. Please commit changes:"
    echo "  git commit -m 'Remove large untracked files from docs'"
    echo "  git push"
    echo "If push fails due to large files in history, use git-filter-repo (see instructions)."
else
    echo "No files were removed."
fi