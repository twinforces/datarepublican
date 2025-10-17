-- Migration to add address components and business name columns
-- This migration adds separate columns for address components and business name lines while preserving existing data
-- Add new columns to Addresses table
ALTER TABLE Addresses
ADD COLUMN address_line1 TEXT;
ALTER TABLE Addresses
ADD COLUMN address_line2 TEXT;
-- Add new columns to Charities table
ALTER TABLE Charities
ADD COLUMN business_name_line1 TEXT;
ALTER TABLE Charities
ADD COLUMN business_name_line2 TEXT;
-- Update the canonical_address column comment to reflect that it's built from components
-- (SQLite doesn't support changing comments, but the schema.sql has been updated)
-- Note: Existing data in canonical_address will remain as-is
-- New records will have canonical_address built from the separate components
-- For existing records, the components (address_line1, address_line2, business_name_line1, business_name_line2) will be NULL
-- This is acceptable since the canonical_address and filer_name fields still contain the full address and name strings
-- Indexes for the new columns (optional, add if needed for performance)
CREATE INDEX IF NOT EXISTS idx_addresses_address_line1 ON Addresses(address_line1);
CREATE INDEX IF NOT EXISTS idx_addresses_address_line2 ON Addresses(address_line2);
CREATE INDEX IF NOT EXISTS idx_charities_business_name_line1 ON Charities(business_name_line1);
CREATE INDEX IF NOT EXISTS idx_charities_business_name_line2 ON Charities(business_name_line2);