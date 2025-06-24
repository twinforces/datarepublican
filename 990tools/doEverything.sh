#!/bin/sh

# 990 Tools
#In order of application use —help with any script for additional params:

export ZIPS_DIR="/Volumes/Data/irs_zips"
export OUT_DIR="/Volumes/Data/tsvs"
export ANAL_DIR="/Volumes/Data/atsvs"
export FINAL_DIR= "/Volumes/Data/final"
export DR_ROOT="~/Development/datarepublican/"

# will download all the zip files from the IRS website the rest of the tools can work directly from the zips, saving a lot of disk spaces and clutter. This is safe to re-run, as it will skip files it already has. 
python download_IRS_990_zips.py 2017 2025 --dest $ZIPS_DIR

#one of the IRS zip files is in a format that can’t be read by python, so this recompresses it. You'll have to move the fixed ones back.
cd $ZIPS_DIR; python recompress_irs_zips

cd $DR_ROOT/990tools

# this will read all the XML files, and produce .tsv files by org type —quiet makes it go faster. Takes about an hour. This will also output an officer_mapping.json which is anticipating a future feature to find charities by person, it maps last/first/charity.
python extract_charities.py 2017 2025 --input-dir $ZIPS_DIR --output-dir $OUT_DIR  

# this will read the previous set of .tsv files, fill in the percentile columns, and do some other analysis looking for grift (the grift part is mostly WIP)
python analyze_charities.py --start-year 2017 --stop-year 2025 --output-dir $ANAL_DIR --input-dir $OUT_DIR

# go through the previous set of .tsv files, and find the most recent filing for every charity. These can be filtered by orgTypes you want, orgTypes you don’t want, and a minimum denominator (assets+income used for the percentages). This will write both .tsv and .csv files. After it extracts the latest tax files, it will go back to the zip files and get the grant data, and write that to a .tsv and .csv file. charity_latest and grants_latest will be the files.
python get_latest.py 2017 2025 --minimumD 0 --source-dir $ANAL_DIR --zip-dir $ZIPS_DIR --output-dir $FINAL_DIR 

#  Because private foundations generally don’t report EINs, we have to match addresses to the charities. That's not too bad a problem, as name+ZIP is enough to match most charities, just like last name and Zip is enough to match most people. (Dividing a large number by 42,000 makes it smaller.) PO Box/Zip/Name is even better. So this step extracts the addresses to build the lookup database.
python extract_addresses.py 2017 2025 --zip-dir $ZIPS_DIR --cache-dir $ANAL_DIR/_cache --output-dir $FINAL_DIR --sample-xml $FINAL_DIR/badxml 


#  Trim down the list from charity_latest to just orgs with >1M in assets+income. This also trims out a lot of the columns we aren't using let, like the percentiles and percentages.
python charity_filter.py  --input-file $FINAL_DIR/charity_latest.tsv --output-file $FINAL_DIR/charites_1M.tsv

# Ok, so we have the latest tax filings from two scripts ago, and we have our address database, now we can go pull the grants to match. Grants to foreign entities are mapped by country, since we don't have data for other countries, deal with it. 
python extract_grants.py 2017 2025 --zip-dir /Volumes/Data/irs_zips --cache-dir $ANAL_DIR/_cache/ --output-dir $FINAL_DIR --charity-source $FINAL_DIR/charites_1M.tsv
#python grant_filters.py --input-file $FINAL_DIR/inferred_grants.tsv --output-file $FINAL_DIR/grants_pf.tsv

# this will aggregate grants by the same filer to the same grantee in the same tax year, which usually cuts the file by a half to a third.
./combine_grants.sh $FINAL_DIR/grants_latest.tsv $FINAL_DIR/grants_final.tsv
./combine_grants.sh $FINAL_DIR/inferred_grants.tsv $FINAL_DIR/grants_pf.tsv
./grant_report.py --input-file $FINAL_DIR/grants_final.tsv --report-file $FINAL_DIR/final_report.md 
./grant_report.py --input-file $FINAL_DIR/grants_pf.tsv --report-file $FINAL_DIR/pf_report.md

# so all that work to filtered out the charities by size? guess what? We over filtered have to copy some charities back so the grants have a destination to go to! Need to do that for both sets of grants, thanks for playing!

./unfilter_from_grants.py --master $FINAL_DIR/charity_latest.tsv --filtered $FINAL_DIR/charites_1M.tsv --grants $FINAL_DIR/grants_pf.tsv --output $FINAL_DIR/charity_semifinal.tsv
./extract_rows.sh 
./unfilter_from_grants.py --master $FINAL_DIR/charity_latest.tsv --filtered $FINAL_DIR/charity_semifinal.tsv --grants $FINAL_DIR/grants_final.tsv --output $FINAL_DIR/charity_final.tsv
./extract_rows.sh 
wc -l $FINAL_DIR/*.tsv 

# zip everything and move it into place

cd $FINAL_DIR
zip -o grants_final.tsv.zip grants_final.tsv
zip -o grants.pf.tsv.zip grants_pf.tsv
zip -o charities.tsv.zip charity_final.tsv

mv grants_final.tsv.zip grants.pf.tsv.zip charities.tsv.zip $DR_ROOT/browse/




 
