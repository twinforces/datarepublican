#!/bin/bash

# Get list of all directories in root, excluding docs
dirs=$(find . -maxdepth 1 -type d -not -path './docs' -not -path . -not -path './.git' | sed 's|^\./||')

# Check if any directories exist
if [ -z "$dirs" ]; then
    echo "No directories found in root (excluding docs and .git)."
    exit 1
fi

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
        fi
    done
done