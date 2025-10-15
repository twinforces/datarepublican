"""
Migration utilities to convert existing JSON-based indexes to database storage.
"""

import os
import json
import sqlite3
from typing import Dict, Any, List, Optional
from zip_xml_db import ZipFileManager, XmlFileManager, IndexingManager
from datetime import datetime


def migrate_json_indexes(zips_dir: str, db_path: str = "/Volumes/Data/final/pipeline_progress.db",
                        xml_index_file: Optional[str] = None,
                        ein_index_file: Optional[str] = None) -> Dict[str, Any]:
    """
    Migrate existing JSON indexes to database storage.

    Args:
        zips_dir: Directory containing ZIP files
        db_path: Path to SQLite database
        xml_index_file: Path to existing XML index JSON file
        ein_index_file: Path to existing EIN index JSON file

    Returns:
        Migration results summary
    """
    if not xml_index_file:
        xml_index_file = os.path.join(zips_dir, 'xml_zip_index.json')
    if not ein_index_file:
        ein_index_file = os.path.join(zips_dir, 'ein_xml_index.json')

    results = {
        'xml_index_migrated': False,
        'ein_index_migrated': False,
        'zip_files_created': 0,
        'xml_files_created': 0,
        'errors': []
    }

    zip_manager = ZipFileManager(db_path)
    xml_manager = XmlFileManager(db_path)

    # Migrate XML index
    if os.path.exists(xml_index_file):
        try:
            with open(xml_index_file, 'r') as f:
                xml_index = json.load(f)

            zip_files_created = 0
            xml_files_created = 0

            # Group XML files by ZIP path
            zip_groups = {}
            for xml_file, zip_path in xml_index.items():
                if zip_path not in zip_groups:
                    zip_groups[zip_path] = []
                zip_groups[zip_path].append(xml_file)

            # Process each ZIP file
            for zip_path, xml_files in zip_groups.items():
                if not os.path.exists(zip_path):
                    results['errors'].append(f"ZIP file not found: {zip_path}")
                    continue

                try:
                    # Create ZIP file record
                    filename = os.path.basename(zip_path)
                    file_size = os.path.getsize(zip_path)

                    # Extract tax year from filename
                    import re
                    year_match = re.search(r'(\d{4})', filename)
                    tax_year = int(year_match.group(1)) if year_match else None

                    if not tax_year:
                        results['errors'].append(f"Could not extract tax year from: {filename}")
                        continue

                    zip_file = zip_manager.add_zip_file(
                        filename=filename,
                        file_path=zip_path,
                        tax_year=tax_year,
                        file_size=file_size,
                        download_date=datetime.now()
                    )
                    zip_files_created += 1

                    # Create XML file records
                    for xml_filename in xml_files:
                        xml_file = xml_manager.add_xml_file(
                            zip_id=zip_file.zip_id,
                            filename=xml_filename,
                            internal_path=xml_filename
                        )
                        xml_files_created += 1

                except Exception as e:
                    results['errors'].append(f"Error processing {zip_path}: {str(e)}")

            results['xml_index_migrated'] = True
            results['zip_files_created'] = zip_files_created
            results['xml_files_created'] = xml_files_created

        except Exception as e:
            results['errors'].append(f"Error migrating XML index: {str(e)}")

    # Migrate EIN index
    if os.path.exists(ein_index_file):
        try:
            with open(ein_index_file, 'r') as f:
                ein_index = json.load(f)

            ein_updates = 0

            for ein, entries in ein_index.items():
                for entry in entries:
                    xml_filename = entry['xml_file']
                    zip_path = entry['zip_path']

                    # Find the XML file in database and update EIN
                    with sqlite3.connect(db_path) as conn:
                        cursor = conn.execute('''
                            SELECT x.xml_id
                            FROM XmlFiles x
                            JOIN ZipFiles z ON x.zip_id = z.zip_id
                            WHERE x.filename = ? AND z.file_path = ?
                        ''', (xml_filename, zip_path))

                        row = cursor.fetchone()
                        if row:
                            xml_id = row[0]
                            # Extract additional metadata if available
                            tax_year = entry.get('tax_year')
                            form_type = entry.get('form_type')

                            conn.execute('''
                                UPDATE XmlFiles
                                SET ein = ?, tax_year = ?, form_type = ?
                                WHERE xml_id = ?
                            ''', (ein, tax_year, form_type, xml_id))
                            ein_updates += 1

            results['ein_index_migrated'] = True
            results['ein_updates'] = ein_updates

        except Exception as e:
            results['errors'].append(f"Error migrating EIN index: {str(e)}")

    return results


def validate_migration(db_path: str = "/Volumes/Data/final/pipeline_progress.db",
                      xml_index_file: Optional[str] = None,
                      ein_index_file: Optional[str] = None) -> Dict[str, Any]:
    """
    Validate that migration preserved all data correctly.

    Returns validation results.
    """
    results = {
        'xml_files_match': False,
        'ein_files_match': False,
        'total_xml_files': 0,
        'total_ein_entries': 0,
        'db_xml_files': 0,
        'db_ein_entries': 0,
        'discrepancies': []
    }

    # Load original indexes
    original_xml_index = {}
    original_ein_index = {}

    if xml_index_file and os.path.exists(xml_index_file):
        with open(xml_index_file, 'r') as f:
            original_xml_index = json.load(f)
        results['total_xml_files'] = len(original_xml_index)

    if ein_index_file and os.path.exists(ein_index_file):
        with open(ein_index_file, 'r') as f:
            original_ein_index = json.load(f)
        results['total_ein_entries'] = sum(len(entries) for entries in original_ein_index.values())

    # Check database counts
    with sqlite3.connect(db_path) as conn:
        cursor = conn.execute('SELECT COUNT(*) FROM XmlFiles')
        results['db_xml_files'] = cursor.fetchone()[0]

        cursor = conn.execute('SELECT COUNT(*) FROM XmlFiles WHERE ein IS NOT NULL')
        results['db_ein_entries'] = cursor.fetchone()[0]

    # Validate XML files
    if original_xml_index:
        results['xml_files_match'] = (results['total_xml_files'] == results['db_xml_files'])

    # Validate EIN entries
    if original_ein_index:
        results['ein_files_match'] = (results['total_ein_entries'] == results['db_ein_entries'])

    return results


def cleanup_json_indexes(zips_dir: str, backup: bool = True) -> List[str]:
    """
    Remove or backup old JSON index files after successful migration.

    Args:
        zips_dir: Directory containing the index files
        backup: Whether to backup files before removal

    Returns:
        List of actions taken
    """
    actions = []
    index_files = ['xml_zip_index.json', 'ein_xml_index.json']

    for filename in index_files:
        filepath = os.path.join(zips_dir, filename)

        if os.path.exists(filepath):
            if backup:
                backup_path = filepath + '.backup'
                os.rename(filepath, backup_path)
                actions.append(f"Backed up {filename} to {filename}.backup")
            else:
                os.remove(filepath)
                actions.append(f"Removed {filename}")

    return actions


def migrate_and_validate(zips_dir: str, db_path: str = "/Volumes/Data/final/pipeline_progress.db") -> Dict[str, Any]:
    """
    Complete migration workflow: migrate indexes, validate, and optionally cleanup.

    Returns comprehensive results.
    """
    xml_index_file = os.path.join(zips_dir, 'xml_zip_index.json')
    ein_index_file = os.path.join(zips_dir, 'ein_xml_index.json')

    results = {
        'migration': None,
        'validation': None,
        'cleanup': None,
        'success': False
    }

    # Perform migration
    results['migration'] = migrate_json_indexes(zips_dir, db_path, xml_index_file, ein_index_file)

    # Validate migration
    results['validation'] = validate_migration(db_path, xml_index_file, ein_index_file)

    # Check if migration was successful
    migration_ok = (results['migration']['xml_index_migrated'] or
                   results['migration']['ein_index_migrated'])
    validation_ok = (results['validation']['xml_files_match'] and
                    results['validation']['ein_files_match'])

    if migration_ok and validation_ok:
        results['success'] = True
        # Cleanup old files
        results['cleanup'] = cleanup_json_indexes(zips_dir, backup=True)

    return results


# Command-line interface
if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Migrate JSON indexes to database")
    parser.add_argument("zips_dir", help="Directory containing ZIP files and indexes")
    parser.add_argument("--db-path", default="/Volumes/Data/final/pipeline_progress.db", help="Database path")
    parser.add_argument("--validate-only", action="store_true", help="Only validate existing migration")
    parser.add_argument("--cleanup", action="store_true", help="Remove old JSON files after migration")

    args = parser.parse_args()

    if args.validate_only:
        results = validate_migration(args.db_path)
        print("Validation Results:")
        print(f"XML files match: {results['xml_files_match']}")
        print(f"EIN entries match: {results['ein_files_match']}")
        print(f"Original XML files: {results['total_xml_files']}")
        print(f"DB XML files: {results['db_xml_files']}")
        print(f"Original EIN entries: {results['total_ein_entries']}")
        print(f"DB EIN entries: {results['db_ein_entries']}")
    else:
        results = migrate_and_validate(args.zips_dir, args.db_path)

        print("Migration Results:")
        print(f"Success: {results['success']}")

        migration = results['migration']
        print(f"XML index migrated: {migration['xml_index_migrated']}")
        print(f"EIN index migrated: {migration['ein_index_migrated']}")
        print(f"ZIP files created: {migration['zip_files_created']}")
        print(f"XML files created: {migration['xml_files_created']}")

        if migration.get('ein_updates'):
            print(f"EIN updates: {migration['ein_updates']}")

        validation = results['validation']
        print(f"XML files match: {validation['xml_files_match']}")
        print(f"EIN entries match: {validation['ein_files_match']}")

        if results['cleanup']:
            print("Cleanup actions:")
            for action in results['cleanup']:
                print(f"  {action}")

        if migration['errors']:
            print("Errors:")
            for error in migration['errors']:
                print(f"  {error}")