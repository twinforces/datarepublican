-- IRS 990 Database Schema
-- Comprehensive SQLite database to replace TSV-based data storage
-- Enable foreign key constraints
PRAGMA foreign_keys = ON;
-- Charities table - Core charity data from IRS 990 filings
CREATE TABLE Charities (
    charity_id INTEGER PRIMARY KEY AUTOINCREMENT,
    ein TEXT NOT NULL,
    -- Employer Identification Number (9 digits)
    tax_year INTEGER NOT NULL,
    -- Tax year of filing
    filer_name TEXT NOT NULL,
    -- Organization name
    business_name_line1 TEXT,
    -- First line of business name
    business_name_line2 TEXT,
    -- Second line of business name
    receipt_amt REAL,
    -- Total receipts
    govt_amt REAL,
    -- Government grants received
    contrib_amt REAL,
    -- Contributions received
    org_type TEXT,
    -- Organization type (501(c)(3), etc.)
    total_exp REAL,
    -- Total expenses
    prog_exp REAL,
    -- Program expenses
    travel_amt REAL,
    -- Travel expenses
    conferences_amt REAL,
    -- Conference expenses
    officer_comp REAL,
    -- Officer compensation
    comp_pct REAL,
    -- Compensation as percentage
    comp_ptile REAL,
    -- Compensation percentile
    comp_ptile_value REAL,
    -- Compensation percentile value
    travel_pct REAL,
    -- Travel as percentage
    travel_ptile REAL,
    -- Travel percentile
    travel_ptile_value REAL,
    -- Travel percentile value
    conferences_pct REAL,
    -- Conferences as percentage
    conferences_ptile REAL,
    -- Conferences percentile
    conferences_ptile_value REAL,
    -- Conferences percentile value
    grants_pct REAL,
    -- Grants to others as percentage
    grants_ptile REAL,
    -- Grants to others percentile
    grants_ptile_value REAL,
    -- Grants to others percentile value
    foreign_expenses_pct REAL,
    -- Foreign expenses as percentage
    foreign_expenses_ptile REAL,
    -- Foreign expenses percentile
    foreign_expenses_ptile_value REAL,
    -- Foreign expenses percentile value
    grift_ratio REAL,
    -- Grift ratio calculation
    total_assets REAL,
    -- Total assets
    form_type TEXT,
    -- Form type (990, 990EZ, 990PF)
    denominator REAL,
    -- Denominator for calculations
    foreign_office TEXT,
    -- Foreign office indicator
    foreign_expenses REAL,
    -- Foreign expenses amount
    grants_to_others REAL,
    -- Grants to other organizations
    domestic_misrep_flag TEXT,
    -- Domestic misrepresentation flag
    xml_name TEXT UNIQUE,
    -- XML filename reference
    grift REAL,
    -- Grift amount
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
);
-- Grants table - Grant data from charity filings
CREATE TABLE Grants (
    grant_id INTEGER PRIMARY KEY AUTOINCREMENT,
    filer_ein TEXT NOT NULL,
    -- Filer EIN (foreign key to Charities)
    filer_name TEXT NOT NULL,
    -- Filer name
    grant_ein TEXT,
    -- Grantee EIN (may be null for foreign)
    grant_amt REAL NOT NULL,
    -- Grant amount
    tax_year INTEGER NOT NULL,
    -- Tax year
    filer_colocator TEXT,
    -- Filer colocator data
    grantee_colocator TEXT,
    -- Grantee colocator data
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (filer_ein, tax_year) REFERENCES Charities(ein, tax_year) ON DELETE CASCADE
);
-- Contributions table - Contribution data from filings
CREATE TABLE Contributions (
    contribution_id INTEGER PRIMARY KEY AUTOINCREMENT,
    filer_ein TEXT NOT NULL,
    -- Filer EIN
    filer_name TEXT NOT NULL,
    -- Filer name
    recipient_ein TEXT,
    -- Recipient EIN
    amount REAL NOT NULL,
    -- Contribution amount
    tax_year INTEGER NOT NULL,
    -- Tax year
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (filer_ein, tax_year) REFERENCES Charities(ein, tax_year) ON DELETE CASCADE
);
-- Addresses table - Address data for charities and grantees
CREATE TABLE Addresses (
    address_id INTEGER PRIMARY KEY AUTOINCREMENT,
    ein TEXT NOT NULL,
    -- EIN this address belongs to
    name TEXT NOT NULL,
    -- Organization name
    address_line1 TEXT,
    -- First line of street address
    address_line2 TEXT,
    -- Second line of street address
    city TEXT,
    -- City
    state TEXT,
    -- State
    zip_code TEXT,
    -- ZIP code
    po_box TEXT,
    -- PO Box if applicable
    canonical_address TEXT,
    -- Standardized address built from components
    address_type TEXT NOT NULL CHECK(address_type IN ('filer', 'grantee')),
    -- Address type
    geocoding_id INTEGER,
    -- Reference to geocoding cache
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (geocoding_id) REFERENCES Geocoding(geocoding_id) ON DELETE
    SET NULL,
        UNIQUE(ein, canonical_address) -- Prevent duplicate addresses per EIN
);
-- Geocoding table - Cached geocoding results
CREATE TABLE Geocoding (
    geocoding_id INTEGER PRIMARY KEY AUTOINCREMENT,
    address_hash TEXT NOT NULL UNIQUE,
    -- Hash of normalized address
    normalized_address TEXT NOT NULL,
    -- Normalized address string
    latitude REAL,
    -- Latitude coordinate
    longitude REAL,
    -- Longitude coordinate
    geocoding_status TEXT DEFAULT 'pending' CHECK(
        geocoding_status IN ('pending', 'success', 'failed')
    ),
    last_attempt DATETIME,
    -- Last geocoding attempt
    attempt_count INTEGER DEFAULT 0,
    -- Number of attempts
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
);
-- ZipFiles table - ZIP file metadata
CREATE TABLE ZipFiles (
    zip_id INTEGER PRIMARY KEY AUTOINCREMENT,
    filename TEXT NOT NULL UNIQUE,
    -- ZIP filename
    file_path TEXT NOT NULL,
    -- Full path to ZIP file
    tax_year INTEGER NOT NULL,
    -- Tax year
    file_size INTEGER,
    -- File size in bytes
    checksum TEXT,
    -- File checksum for integrity
    download_date DATETIME,
    -- When file was downloaded
    processed_date DATETIME,
    -- When file was processed
    status TEXT DEFAULT 'downloaded' CHECK(status IN ('downloaded', 'processed', 'error')),
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);
-- XmlFiles table - XML file metadata within ZIPs
CREATE TABLE XmlFiles (
    xml_id INTEGER PRIMARY KEY AUTOINCREMENT,
    zip_id INTEGER NOT NULL,
    -- Reference to ZIP file
    filename TEXT NOT NULL,
    -- XML filename within ZIP
    internal_path TEXT NOT NULL,
    -- Path within ZIP archive
    ein TEXT,
    -- EIN extracted from XML
    tax_year INTEGER,
    -- Tax year from XML
    form_type TEXT,
    -- Form type (990, 990EZ, 990PF)
    processed BOOLEAN DEFAULT FALSE,
    -- Whether XML has been processed
    processing_version INTEGER DEFAULT 0,
    -- Version of processing pipeline used (for incremental updates)
    error_message TEXT,
    -- Error message if processing failed
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (zip_id) REFERENCES ZipFiles(zip_id) ON DELETE CASCADE,
    UNIQUE(zip_id, filename) -- Unique within each ZIP
);
-- Backfill table - Additional grantee data for unknown EINs
CREATE TABLE Backfill (
    backfill_id INTEGER PRIMARY KEY AUTOINCREMENT,
    grant_ein TEXT NOT NULL,
    -- Grantee EIN
    name TEXT NOT NULL,
    -- Organization name
    canonical_address TEXT,
    -- Standardized address
    po_box TEXT,
    -- PO Box
    zip_code TEXT,
    -- ZIP code
    source TEXT DEFAULT 'xml',
    -- Source of backfill data
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(grant_ein, name, zip_code) -- Prevent duplicates
);
-- PipelineProgress table - Track processing pipeline status
CREATE TABLE PipelineProgress (
    progress_id INTEGER PRIMARY KEY AUTOINCREMENT,
    step_name TEXT NOT NULL,
    -- Pipeline step name
    start_year INTEGER NOT NULL,
    -- Start year being processed
    end_year INTEGER NOT NULL,
    -- End year being processed
    status TEXT NOT NULL CHECK(
        status IN ('pending', 'running', 'completed', 'failed')
    ),
    started_at DATETIME,
    -- When step started
    completed_at DATETIME,
    -- When step completed
    records_processed INTEGER DEFAULT 0,
    -- Records processed
    error_message TEXT,
    -- Error details if failed
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(step_name, start_year, end_year) -- One entry per step per year range
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
-- Officers table - Officer compensation data
CREATE TABLE Officers (
    officer_id INTEGER PRIMARY KEY AUTOINCREMENT,
    charity_id INTEGER NOT NULL,
    -- Reference to Charities
    first_name TEXT NOT NULL,
    last_name TEXT NOT NULL,
    compensation REAL NOT NULL,
    tax_year INTEGER NOT NULL,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (charity_id) REFERENCES Charities(charity_id) ON DELETE CASCADE
);
-- Contractors table - Contractor payment data
CREATE TABLE Contractors (
    contractor_id INTEGER PRIMARY KEY AUTOINCREMENT,
    filer_ein TEXT NOT NULL,
    -- Filer EIN
    name TEXT NOT NULL,
    -- Contractor name
    amount REAL NOT NULL,
    -- Payment amount
    ein TEXT,
    -- Contractor EIN if available
    address TEXT,
    -- Contractor address
    zip_code TEXT,
    -- Contractor ZIP code
    po_box TEXT,
    -- PO Box if applicable
    tax_year INTEGER NOT NULL,
    -- Tax year
    colocator TEXT,
    -- Colocator data
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (filer_ein) REFERENCES Charities(ein) ON DELETE CASCADE
);
-- PoliticalContributions table - Political contribution data
CREATE TABLE PoliticalContributions (
    political_id INTEGER PRIMARY KEY AUTOINCREMENT,
    filer_ein TEXT NOT NULL,
    -- Filer EIN
    recipient TEXT NOT NULL,
    -- Recipient name
    amount REAL NOT NULL,
    -- Contribution amount
    recipient_address TEXT,
    -- Recipient address
    recipient_zip TEXT,
    -- Recipient ZIP code
    recipient_po_box TEXT,
    -- Recipient PO Box
    tax_year INTEGER NOT NULL,
    -- Tax year
    colocator TEXT,
    -- Colocator data
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (filer_ein) REFERENCES Charities(ein) ON DELETE CASCADE
);
-- PipelineProgress indexes
CREATE INDEX idx_pipeline_step_name ON PipelineProgress(step_name);
CREATE INDEX idx_pipeline_status ON PipelineProgress(status);
CREATE INDEX idx_pipeline_years ON PipelineProgress(start_year, end_year);
-- Officers indexes
CREATE INDEX idx_officers_charity_id ON Officers(charity_id);
CREATE INDEX idx_officers_tax_year ON Officers(tax_year);
-- Contractors indexes
CREATE INDEX idx_contractors_filer_ein ON Contractors(filer_ein);
CREATE INDEX idx_contractors_tax_year ON Contractors(tax_year);
-- PoliticalContributions indexes
CREATE INDEX idx_political_filer_ein ON PoliticalContributions(filer_ein);
CREATE INDEX idx_political_tax_year ON PoliticalContributions(tax_year);
-- Triggers for updated_at timestamps
CREATE TRIGGER update_charities_timestamp
AFTER
UPDATE ON Charities BEGIN
UPDATE Charities
SET updated_at = CURRENT_TIMESTAMP
WHERE charity_id = NEW.charity_id;
END;
CREATE TRIGGER update_geocoding_timestamp
AFTER
UPDATE ON Geocoding BEGIN
UPDATE Geocoding
SET updated_at = CURRENT_TIMESTAMP
WHERE geocoding_id = NEW.geocoding_id;
END;
CREATE TRIGGER update_pipeline_timestamp
AFTER
UPDATE ON PipelineProgress BEGIN
UPDATE PipelineProgress
SET updated_at = CURRENT_TIMESTAMP
WHERE progress_id = NEW.progress_id;
END;