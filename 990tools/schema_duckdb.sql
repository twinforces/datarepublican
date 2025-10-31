-- DuckDB Schema for IRS 990 Database
-- Comprehensive DuckDB database to replace SQLite-based data storage
-- Note: UUID extension not available on all platforms, using VARCHAR for now
-- INSTALL uuid;
-- LOAD uuid;
-- Charities table - Core charity data from IRS 990 filings
CREATE TABLE Charities (
    charity_id UUID DEFAULT uuidv7() PRIMARY KEY,
    ein VARCHAR(9) NOT NULL,
    -- Employer Identification Number (3/9 digits)
    tax_year INTEGER NOT NULL,
    -- Tax year of filing
    filer_name VARCHAR NOT NULL,
    -- Organization name (concatenated from business_name_line1 and business_name_line2)
    receipt_amt DOUBLE,
    -- Total receipts
    govt_amt DOUBLE,
    -- Government grants received
    contrib_amt DOUBLE,
    -- Contributions received
    org_type VARCHAR,
    -- Organization type (501(c)(3), etc.)
    total_exp DOUBLE,
    -- Total expenses
    prog_exp DOUBLE,
    -- Program expenses
    travel_amt DOUBLE,
    -- Travel expenses
    conferences_amt DOUBLE,
    -- Conference expenses
    officer_comp DOUBLE,
    -- Officer compensation
    comp_pct DOUBLE,
    -- Compensation as percentage
    comp_ptile DOUBLE,
    -- Compensation percentile
    comp_ptile_value DOUBLE,
    -- Compensation percentile value
    travel_pct DOUBLE,
    -- Travel as percentage
    travel_ptile DOUBLE,
    -- Travel percentile
    travel_ptile_value DOUBLE,
    -- Travel percentile value
    conferences_pct DOUBLE,
    -- Conferences as percentage
    conferences_ptile DOUBLE,
    -- Conferences percentile
    conferences_ptile_value DOUBLE,
    -- Conferences percentile value
    grants_pct DOUBLE,
    -- Grants to others as percentage
    grants_ptile DOUBLE,
    -- Grants to others percentile
    grants_ptile_value DOUBLE,
    -- Grants to others percentile value
    foreign_expenses_pct DOUBLE,
    -- Foreign expenses as percentage
    foreign_expenses_ptile DOUBLE,
    -- Foreign expenses percentile
    foreign_expenses_ptile_value DOUBLE,
    -- Foreign expenses percentile value
    grift_ratio DOUBLE,
    -- Grift ratio calculation
    total_assets DOUBLE,
    -- Total assets
    form_type VARCHAR,
    -- Form type (990, 990EZ, 990PF)
    denominator DOUBLE,
    -- Denominator for calculations
    foreign_office VARCHAR,
    -- Foreign office indicator
    foreign_expenses DOUBLE,
    -- Foreign expenses amount
    grants_to_others DOUBLE,
    -- Grants to other organizations
    domestic_misrep_flag VARCHAR,
    -- Domestic misrepresentation flag
    xml_name VARCHAR UNIQUE,
    -- XML filename reference
    colocator VARCHAR,
    -- Colocator data: LL:lat:lon, PO:box:zip, FA:country_code
    grift DOUBLE,
    -- Grift amount
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(xml_name) -- Prevent duplicate charity records per EIN per year
);
-- Grants table - Grant data from charity filings
CREATE TABLE Grants (
    grant_id UUID DEFAULT uuidv7() PRIMARY KEY,
    filer_ein CHAR(9) NOT NULL,
    -- Filer EIN (foreign key to Charities with tax_year)
    filer_name VARCHAR NOT NULL,
    -- Grantee EIN (foreign key to Charities)
    grantee_name VARCHAR NOT NULL,
    -- Filer name
    grant_ein CHAR(9),
    -- Grantee EIN (may be null for foreign)
    grant_amt DOUBLE NOT NULL,
    -- Grant amount
    tax_year INTEGER NOT NULL,
    -- Tax year
    colocator VARCHAR,
    -- Grantee colocator data
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    -- FOREIGN KEY (filer_ein, tax_year) REFERENCES Charities(ein, tax_year) -- DuckDB doesn't support CASCADE
);
-- Contributions table - Contribution data from filings
CREATE TABLE Contributions (
    contribution_id UUID DEFAULT uuidv7() PRIMARY KEY,
    filer_ein CHAR(9) NOT NULL,
    -- Filer EIN
    filer_name VARCHAR NOT NULL,
    -- Filer name
    recipient_ein CHAR(9),
    -- Recipient EIN
    amount DOUBLE NOT NULL,
    -- Contribution amount
    tax_year INTEGER NOT NULL,
    -- Tax year
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    -- FOREIGN KEY (filer_ein, tax_year) REFERENCES Charities(ein, tax_year) -- DuckDB doesn't support CASCADE
);
-- Addresses table - Address data for charities and grantees
CREATE TABLE Addresses (
    address_id UUID DEFAULT uuidv7() PRIMARY KEY,
    ein CHAR(9),
    -- EIN this address belongs to if Charity
    owner_id UUID,
    -- Owner ID for loose foreign key relationships (NULL for standalone addresses)
    master_id UUID,
    -- Master address ID for deduplication (NULL for root addresses of the tree)
    name VARCHAR NOT NULL,
    -- Organization name
    address_line1 VARCHAR,
    -- First line of street address
    address_line2 VARCHAR,
    -- Second line of street address
    city VARCHAR,
    -- City
    state VARCHAR,
    -- State
    zip_code VARCHAR,
    -- ZIP code (first 5 digits)
    zip4 VARCHAR,
    -- ZIP+4 code (last 4 digits)
    po_box VARCHAR,
    -- PO Box if applicable
    canonical_address VARCHAR,
    -- Standardized address built from components
    address_type VARCHAR NOT NULL,
    -- Address type
    geocoding_id VARCHAR,
    -- Reference to geocoding cache
    latitude DOUBLE,
    -- Latitude coordinate
    longitude DOUBLE,
    -- Longitude coordinate
    colocator VARCHAR,
    -- Colocator data: LL:lat:lon, PO:box:zip, FA:country_code
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP -- FOREIGN KEY (geocoding_id) REFERENCES Geocoding(geocoding_id) -- DuckDB doesn't support SET NULL
);
-- Geocoding table - Cached geocoding results
CREATE TABLE Geocoding (
    geocoding_id UUID DEFAULT uuidv7() PRIMARY KEY,
    normalized_address VARCHAR NOT NULL,
    -- Normalized address string
    latitude DOUBLE,
    -- Latitude coordinate
    longitude DOUBLE,
    -- Longitude coordinate
    geocoding_status VARCHAR DEFAULT 'pending',
    last_attempt TIMESTAMP,
    -- Last geocoding attempt
    attempt_count INTEGER DEFAULT 0,
    -- Number of attempts
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
-- ZipFiles table - ZIP file metadata
CREATE TABLE ZipFiles (
    zip_id UUID PRIMARY KEY,
    filename VARCHAR NOT NULL UNIQUE,
    -- ZIP filename
    file_path VARCHAR NOT NULL,
    -- Full path to ZIP file
    tax_year INTEGER NOT NULL,
    -- Tax year
    file_size BIGINT,
    -- File size in bytes
    checksum VARCHAR,
    -- File checksum for integrity
    download_date TIMESTAMP,
    -- When file was downloaded
    processed_date TIMESTAMP,
    -- When file was processed
    status VARCHAR DEFAULT 'downloaded' CHECK(status IN ('downloaded', 'processed', 'error')),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
-- XmlFiles table - XML file metadata within ZIPs
CREATE TABLE XmlFiles (
    xml_id UUID PRIMARY KEY,
    zip_id UUID NOT NULL,
    -- Reference to ZIP file
    filename VARCHAR NOT NULL,
    -- XML filename within ZIP
    internal_path VARCHAR NOT NULL,
    -- Path within ZIP archive
    file_size BIGINT,
    -- Size of XML file in bytes
    ein CHAR(9),
    -- EIN extracted from XML, starts as NULL
    tax_year INTEGER,
    -- Tax year from XML
    form_type VARCHAR,
    -- Form type (990, 990EZ, 990PF)
    processed BOOLEAN DEFAULT FALSE,
    -- Whether XML has been processed
    org_type VARCHAR,
    -- Organization type determined during parsing
    processed_at TIMESTAMP,
    -- Timestamp when processed flag was set to TRUE
    processing_version INTEGER DEFAULT 0,
    -- Version of processing pipeline used (for incremental updates)
    error_message VARCHAR,
    -- Error message if processing failed
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    -- FOREIGN KEY (zip_id) REFERENCES ZipFiles(zip_id) -- DuckDB doesn't support CASCADE
    UNIQUE(zip_id, filename) -- Unique within each ZIP
);
-- Backfill table - Additional grantee data for unknown EINs
CREATE TABLE Backfill (
    backfill_id UUID DEFAULT uuidv7() PRIMARY KEY,
    grant_id UUID,
    -- Grant originator
    grant_ein CHAR(9) NOT NULL,
    -- Grantee EIN
    name VARCHAR NOT NULL,
    -- Organization name
    colocator VARCHAR source VARCHAR DEFAULT 'xml',
    -- Source of backfill data
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(grant_ein, name, zip_code) -- Prevent duplicates
);
-- PipelineProgress table - Track processing pipeline status
CREATE TABLE PipelineProgress (
    progress_id UUID DEFAULT uuidv7() PRIMARY KEY,
    step_name VARCHAR NOT NULL,
    -- Pipeline step name
    start_year INTEGER NOT NULL,
    -- Start year being processed
    end_year INTEGER NOT NULL,
    -- End year being processed
    status VARCHAR NOT NULL CHECK(
        status IN ('pending', 'running', 'completed', 'failed')
    ),
    started_at TIMESTAMP,
    -- When step started
    completed_at TIMESTAMP,
    -- When step completed
    records_processed BIGINT DEFAULT 0,
    -- Records processed
    error_message VARCHAR,
    -- Error details if failed
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(step_name, start_year, end_year) -- One entry per step per year range
);
-- Officers table - Officer compensation data
CREATE TABLE Officers (
    officer_id UUID DEFAULT uuidv7() PRIMARY KEY,
    charity_id UUID NOT NULL,
    -- Reference to Charities
    master_id UUID,
    -- Master officer ID for deduplication (NULL for master officers)
    first_name VARCHAR NOT NULL,
    last_name VARCHAR NOT NULL,
    full_name VARCHAR,
    -- Full name as it appears in the filing (for photo lookup)
    compensation DOUBLE NOT NULL,
    tax_year INTEGER NOT NULL,
    photo_url VARCHAR,
    -- URL to officer photo from Google Knowledge Graph API
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    -- FOREIGN KEY (charity_id) REFERENCES Charities(charity_id) -- DuckDB doesn't support CASCADE
);
-- Contractors table - Contractor payment data
CREATE TABLE Contractors (
    contractor_id UUID DEFAULT uuidv7() PRIMARY KEY,
    charity_id UUID NOT NULL,
    -- owning charity
    filer_ein CHAR(9) NOT NULL,
    -- Filer EIN
    name VARCHAR NOT NULL,
    -- Contractor name
    amount DOUBLE NOT NULL,
    -- Payment amount
    ein CHAR(9),
    -- Contractor EIN if available
    tax_year INTEGER NOT NULL,
    -- Tax year
    colocator VARCHAR,
    -- Colocator data
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    -- FOREIGN KEY (filer_ein) REFERENCES Charities(ein) -- DuckDB doesn't support CASCADE
);
-- PoliticalContributions table - Political contribution data
CREATE TABLE PoliticalContributions (
    political_id UUID DEFAULT uuidv7() PRIMARY KEY,
    charity_id UUID NOT NULL,
    -- owning charity
    filer_ein CHAR(9) NOT NULL,
    -- Filer EIN
    recipient VARCHAR NOT NULL,
    -- Recipient name
    amount DOUBLE NOT NULL,
    -- Contribution amount
    recipient_ein CHAR(9),
    -- EIN if it exists
    tax_year INTEGER NOT NULL,
    -- Tax year
    colocator VARCHAR,
    -- Colocator data
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    -- FOREIGN KEY (filer_ein) REFERENCES Charities(ein) -- DuckDB doesn't support CASCADE
);
-- Indexes for performance optimization
-- Charities indexes
CREATE INDEX idx_charities_ein ON Charities(ein);
CREATE INDEX idx_charities_tax_year ON Charities(tax_year);
CREATE INDEX idx_charities_org_type ON Charities(org_type);
CREATE INDEX idx_charities_form_type ON Charities(form_type);
CREATE INDEX idx_charities_denominator ON Charities(denominator);
-- Grants indexes
CREATE INDEX idx_grants_filer_ein ON Grants(filer_ein);
CREATE INDEX idx_grants_grant_ein ON Grants(grant_ein);
CREATE INDEX idx_grants_tax_year ON Grants(tax_year);
CREATE INDEX idx_grants_filer_ein_year ON Grants(filer_ein, tax_year);
-- Contributions indexes
CREATE INDEX idx_contributions_filer_ein ON Contributions(filer_ein);
CREATE INDEX idx_contributions_recipient_ein ON Contributions(recipient_ein);
CREATE INDEX idx_contributions_tax_year ON Contributions(tax_year);
-- Addresses indexes
CREATE INDEX idx_addresses_ein ON Addresses(ein);
CREATE INDEX idx_addresses_zip_code ON Addresses(zip_code);
CREATE INDEX idx_addresses_type ON Addresses(address_type);
CREATE INDEX idx_addresses_geocoding ON Addresses(geocoding_id);
CREATE INDEX idx_addresses_master_id ON Addresses(master_id);
CREATE INDEX idx_addresses_canonical ON Addresses(canonical_address);
-- Geocoding indexes
CREATE INDEX idx_geocoding_address_hash ON Geocoding(address_hash);
CREATE INDEX idx_geocoding_status ON Geocoding(geocoding_status);
-- ZipFiles indexes
CREATE INDEX idx_zipfiles_tax_year ON ZipFiles(tax_year);
CREATE INDEX idx_zipfiles_status ON ZipFiles(status);
-- XmlFiles indexes
CREATE INDEX idx_xmlfiles_zip_id ON XmlFiles(zip_id);
CREATE INDEX idx_xmlfiles_ein ON XmlFiles(ein);
CREATE INDEX idx_xmlfiles_tax_year ON XmlFiles(tax_year);
CREATE INDEX idx_xmlfiles_processed ON XmlFiles(processed);
-- Backfill indexes
CREATE INDEX idx_backfill_grant_ein ON Backfill(grant_ein);
CREATE INDEX idx_backfill_zip_code ON Backfill(zip_code);
-- PipelineProgress indexes
CREATE INDEX idx_pipeline_step_name ON PipelineProgress(step_name);
CREATE INDEX idx_pipeline_status ON PipelineProgress(status);
CREATE INDEX idx_pipeline_years ON PipelineProgress(start_year, end_year);
-- Officers indexes
CREATE INDEX idx_officers_charity_id ON Officers(charity_id);
CREATE INDEX idx_officers_tax_year ON Officers(tax_year);
CREATE INDEX idx_officers_master_id ON Officers(master_id);
CREATE INDEX idx_officers_name ON Officers(last_name, first_name);
CREATE INDEX idx_officers_full_name ON Officers(full_name);
-- Contractors indexes
CREATE INDEX idx_contractors_filer_ein ON Contractors(filer_ein);
CREATE INDEX idx_contractors_tax_year ON Contractors(tax_year);
-- PoliticalContributions indexes
CREATE INDEX idx_political_filer_ein ON PoliticalContributions(filer_ein);
CREATE INDEX idx_political_tax_year ON PoliticalContributions(tax_year);