#!/bin/bash

# Get list of all directories in root, excluding docs, excluding docs, assets, and .git
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
        is_tracked=0

        # Check if the exact root_file is tracked
        if echo "$tracked_files" | grep -Fx "$root_file" > /dev/null; then
            is_tracked=1
        fi

        # If not tracked and it's an .html file, check for corresponding .md
        if [ $is_tracked -eq 0 ] && [[ "$doc_file" == *.html ]]; then
            root_md="${root_file%.html}.md"
            if echo "$tracked_files" | grep -Fx "$root_md" > /dev/null; then
                is_tracked=1
            fi
        fi

        # If still not tracked, remove
        if [ $is_tracked -eq 0 ]; then
            echo "Removing $doc_file (untracked in $root_file)"
            # Delete the file from the filesystem
            if [ -f "$doc_file" ]; then
                rm -f "$doc_file"
                # Check if the file was previously tracked in Git
                if git ls-files --error-unmatch "$doc_file" > /dev/null 2>&1; then
                    # Remove from Git index if it was tracked
                    git rm --cached "$doc_file"
                fi
                removed=1
            else
                echo "Warning: File $doc_file does not exist, skipping."
            fi
        fi
    done

    # Remove empty directories in docs/$dir
    find "docs/$dir" -type d -empty -delete
done

# Verify no large files remain in the index
echo "Verifying no large files remain in the index..."
# Check for files >50MB in the index
git ls-files --stage | while read -r mode sha size path; do
    # Convert size from bytes to MB (approx)
    size_mb=$((size / 1024 / 1024))
    if [ $size_mb -gt 50 ]; then
        echo "Warning: Large file ($size_mb MB) still in index: $path"
    fi
done

# If files were removed, suggest committing
if [ $removed -eq 1 ]; then
    echo "Files removed. Please commit changes:"
    echo "  git commit -m 'Remove large untracked files from docs'"
    echo "  git push"
    echo "If push fails due to large files in history, use git-filter-repo (see instructions)."
else
    echo "No files were removed."
fi