# 990 Tools
In order of application use —help with any script for additional params:

`python download_IRS_990_zips.py 2017 2025 --dest irs_zips` will download all the zip files from the IRS website the rest of the tools can work directly from the zips, saving a lot of disk spaces and clutter. This is safe to re-run, as it will skip files it already has. 

`python recompress_irs_zips` one of the IRS zip files is in a format that can’t be read by python, so this recompresses it. 

`python extract_charities.py 2017 2025 --input-dir /Volumes/Data/irs_zips --output-dir /Volumes/Data/tsvs` this will read all the XML files, and produce .tsv files by org type `—quiet` makes it go faster. Takes about an hour. This will also output an officer_mapping.json which is anticipating a future feature to find charities by person, it maps last/first/charity.  

`python analyze_charities.py 2017 2025 --output-dir /Volumes/Data/tsvs --input-dir /Volumes/Data/tsvs` this will read the previous set of .tsv files, fill in the percentile columns, and do some other analysis looking for grift (the grift part is mostly WIP)

`python get_latest.py 2017 2025 —minimumD 0 —source-dir analyzed —zip-dir irs_zips —output-dir output` go through the previous set of .tsv files, and find the most recent filing for every charity. These can be filtered by orgTypes you want, orgTypes you don’t want, and a minimum denominator (assets+income used for the percentages). This will write both .tsv and .csv files. After it extracts the latest tax files, it will go back to the zip files and get the grant data, and write that to a .tsv and .csv file. charity_latest and grants_latest will be the files. 

` python extract_addresses.py 2017 2025 --zip-dir /Volumes/Data/irs_zips --cache-dir /Volumes/Data/atsvs/_cache --output-dir /Volumes/Data/atsvs/output --force-reproces --sample-xml /Volumes/Data/badxml` Because private foundations generally don’t report EINs, we have to match addresses to the charities. That's not too bad a problem, as name+ZIP is enough to match most charities, just like last name and Zip is enough to match most people. (Dividing a large number by 42,000 makes it smaller.) PO Box/Zip/Name is even better. So this step extracts the addresses to build the lookup database. 

`python charity_filter.py  --input-file /Volumes/Data/atsvs/charity_latest.tsv --output-file /Volumes/Data/atsvs/charity_truncated.tsv` Trim down the list from charity_latest to just orgs with >1M in assets+income. This also trims out a lot of the columns we aren't using let, like the percentiles and percentages.

`python extract_grants.py 2017 2025 --zip-dir /Volumes/Data/irs_zips --cache-dir /Volumes/Data/atsvs/_cache --output-dir /Volumes/Data/atsvs/output --force-reproces --charity-source /Volumes/Data/atsvs/charity_truncated.tsv` Ok, so we have the latest tax filings from two scripts ago, and we have our address database, now we can go pull the grants to match. Grants to foreign entities are mapped by country, since we don't have data for other countries, deal with it. 


`combine_grants.sh input output` this will aggregate grants by the same filer to the same grantee in the same tax year, which usually cuts the file in half to a third. 







 
