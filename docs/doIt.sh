#!/bin/sh
bundle exec jekyll clean

bundle exec jekyll build JEKYLL_ENV=production

# docs copies WIP blindly even files not checked into git, 
./clean_docs.sh