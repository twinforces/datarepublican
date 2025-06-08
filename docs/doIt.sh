#!/bin/sh
bundle exec jekyll clean

bundle exec jekyll build JEKYLL_ENV=production
