# 990 Tools
In order of application use —help with any script for additional params:

`python download_IRS_990_zips.py 2017 2025 --dest irs_zips` will download all the zip files from the IRS website the rest of the tools can work directly from the zips, saving a lot of disk spaces and clutter. This is safe to re-run, as it will skip files it already has. 

`python recompress_irs_zips` one of the IRS zip files is in a format that can’t be read by python, so this recompresses it. 

`python extract_charities.py 2017 2025` this will read all the XML files, and produce .tsv files by org type `—quiet` makes it go faster. Takes about an hour. This will also output an officer_mapping.json which is anticipating a future feature to find charities by person, it maps last/first/charity.  

`python analyze_charities.py 2017 2025` this will read the previous set of .tsv files, fill in the percentile columns, and do some other analysis looking for grift (the grift part is mostly WIP)

`python get_latest.py 2017 2025 —minimumD 0 —source-dir analyzed —zip-dir irs_zips —output-dir output` go through the previous set of .tsv files, and find the most recent filing for every charity. These can be filtered by orgTypes you want, orgTypes you don’t want, and a minimum denominator (assets+income used for the percentages). This will write both .tsv and .csv files. After it extracts the latest tax files, it will go back to the zip files and get the grant data, and write that to a .tsv and .csv file. charity_latest and grants_latest will be the files. 

`python extract_addresses_and_grants.py` Because private foundations don’t report EINs, we have to match addresses to the charities. So this produces two files: `charity_addresses.tsv` which is the list of charities from the charity_latest file, we go back to the zips and grab their address, and canonicalize it with pypostal, and output this file with ein name, address. `inferred_grants.tsv` is then produced by matching the name/addresses listed in the 990PF forms to those addresses. This also produces some debugging files, like zip_errors.tsv for bad zip codes, po_box_matches.tsv that tracks pobox+zip+name combinations, invalid_eins which mostly have happened when upstream code has lost a leading zero on an ein. This takes awhile, because it goes back and processes all the original tsvs to track address changes. 

`combine_grants.sh input output` this will aggregate grants by the same filer to the same grantee in the same tax year, which usually cuts the file in half to a third. 







 
