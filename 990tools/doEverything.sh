#!/bin/sh

# grab all the zip files
python download_IRS_990_zips.py 2017 2025 --test /Volumes/Data/irs_zips

# only necessary once
#cd /Volumes/Data/irs_zips; python recompress_irs_zips

python extract_charities.py 2017 2025 --quiet --input-dir /Volumes/Data/irs_zips --output-dir /Volumes/Data/tsvs
python analyze_charities.py --start-year 2017 --stop-year 2025 --quiet --input-dir /Volumes/Data/tsvs --output-dir /Volumes/Data/tsvs 
python get_latest.py 2017 2025 --zip-dir /Volumes/Data/irs_zips --source-dir /Volumes/Data/tsvs --output-dir ./latest
cp latest/*.tsv /Volumes/Data/tsvs
sh combine_grants.sh /Volumes/Data/tsvs/grants_latest.tsv /Volumes/Data/tsvs/grants_combined.tsv
python extract_addresses_and_grants.py 2017 2025 --zip-dir /Volumes/Data/irs_zips --source-dir /Volumes/Data/tsvs --output-dir ./latest --quiet
sh combine_grants.sh ./latest/inferred_grants.tsv ./latest/inferred_combined.tsv

