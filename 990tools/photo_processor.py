#!/usr/bin/env python3
"""
photo_processor.py - Google Knowledge Graph API integration for officer photos

This module handles fetching officer photos using the Google Knowledge Graph API,
with throttling, caching, and proper error handling.
"""

import os
import json
import time
import requests
import hashlib
from typing import Optional, Dict, List
from pathlib import Path

from logging_utils import get_logger, log_info, log_error, log_debug, log_warning
from config import global_config


class PhotoProcessor:
    """Handles officer photo processing using Google Knowledge Graph API"""

    def __init__(self, db_ops, cache_dir: str = None):
        self.db_ops = db_ops
        self.cache_dir = cache_dir or os.path.join(global_config.final_dir, "photo_cache")
        os.makedirs(self.cache_dir, exist_ok=True)

        self.logger = get_logger("photo_processor")
        self.api_key = os.getenv('GOOGLE_KNOWLEDGE_GRAPH_API_KEY')
        if not self.api_key:
            log_warning(self.logger, "GOOGLE_KNOWLEDGE_GRAPH_API_KEY environment variable not set")
        self.quiet = global_config.is_quiet()

        # Rate limiting: 1 request per second
        self.last_request_time = 0
        self.min_request_interval = 1.0

    def _rate_limit(self):
        """Enforce rate limiting"""
        current_time = time.time()
        time_since_last = current_time - self.last_request_time
        if time_since_last < self.min_request_interval:
            sleep_time = self.min_request_interval - time_since_last
            time.sleep(sleep_time)
        self.last_request_time = time.time()

    def _get_cache_key(self, name: str) -> str:
        """Generate cache key for a name"""
        return hashlib.md5(name.lower().encode()).hexdigest()

    def _load_cache(self, cache_key: str) -> Optional[Dict]:
        """Load cached API response"""
        cache_file = os.path.join(self.cache_dir, f"{cache_key}.json")
        if os.path.exists(cache_file):
            try:
                with open(cache_file, 'r') as f:
                    return json.load(f)
            except Exception as e:
                log_debug(self.logger, f"Failed to load cache for {cache_key}: {e}")
        return None

    def _save_cache(self, cache_key: str, data: Dict):
        """Save API response to cache"""
        cache_file = os.path.join(self.cache_dir, f"{cache_key}.json")
        try:
            with open(cache_file, 'w') as f:
                json.dump(data, f, indent=2)
        except Exception as e:
            log_warning(self.logger, f"Failed to save cache for {cache_key}: {e}")

    def _search_knowledge_graph(self, name: str) -> Optional[str]:
        """Search Google Knowledge Graph for a person's photo"""
        if not self.api_key:
            return None

        # Rate limit
        self._rate_limit()

        # Check cache first
        cache_key = self._get_cache_key(name)
        cached_result = self._load_cache(cache_key)
        if cached_result:
            log_debug(self.logger, f"Using cached result for {name}")
            return cached_result.get('photo_url')

        # Make API request
        url = "https://kgsearch.googleapis.com/v1/entities:search"
        params = {
            'query': name,
            'key': self.api_key,
            'limit': 1,
            'types': 'Person'
        }

        try:
            response = requests.get(url, params=params, timeout=10)
            response.raise_for_status()

            data = response.json()
            cache_data = {'photo_url': None, 'timestamp': time.time()}

            if 'itemListElement' in data and data['itemListElement']:
                entity = data['itemListElement'][0].get('result', {})
                if 'image' in entity and 'contentUrl' in entity['image']:
                    photo_url = entity['image']['contentUrl']
                    cache_data['photo_url'] = photo_url
                    log_debug(self.logger, f"Found photo for {name}: {photo_url}")

            # Cache the result
            self._save_cache(cache_key, cache_data)
            return cache_data['photo_url']

        except requests.RequestException as e:
            log_warning(self.logger, f"API request failed for {name}: {e}")
            # Cache empty result to avoid repeated failures
            self._save_cache(cache_key, {'photo_url': None, 'timestamp': time.time(), 'error': str(e)})
            return None
        except Exception as e:
            log_error(self.logger, f"Unexpected error searching for {name}: {e}")
            return None

    def process_officer_photos(self) -> int:
        """Process all officers and fetch their photos"""
        log_info(self.logger, "Starting officer photo processing")

        if not self.api_key:
            log_warning(self.logger, "No Google Knowledge Graph API key available, skipping photo processing")
            return 0

        # Get unique officers (master records only) without photos, including charity context
        query = """
            SELECT o.officer_id, o.first_name, o.last_name, c.filer_name
            FROM Officers o
            JOIN Charities c ON o.charity_id = c.charity_id
            WHERE o.master_id IS NULL
            AND (o.photo_url IS NULL OR o.photo_url = '')
            AND o.first_name IS NOT NULL AND o.last_name IS NOT NULL
            ORDER BY o.last_name, o.first_name
        """

        officers = self.db_ops.execute_query(query).fetchall()
        log_info(self.logger, f"Found {len(officers)} unique officers to process for photos")

        updated_count = 0
        for officer_id, first_name, last_name, charity_name in officers:
            # Create a more specific search query using both name and charity context
            full_name = f"{first_name} {last_name}".strip()
            # Use charity name to disambiguate common names
            search_query = f"{full_name} {charity_name}" if charity_name else full_name

            # Search for photo
            photo_url = self._search_knowledge_graph(search_query)

            if photo_url:
                # Update master officer
                update_query = """
                    UPDATE Officers
                    SET photo_url = ?
                    WHERE officer_id = ?
                """
                self.db_ops.execute_query(update_query, (photo_url, officer_id))

                # Also update all child officers with the same photo
                update_children_query = """
                    UPDATE Officers
                    SET photo_url = ?
                    WHERE master_id = (
                        SELECT master_id FROM Officers WHERE officer_id = ?
                    )
                """
                self.db_ops.execute_query(update_children_query, (photo_url, officer_id))

                updated_count += 1
                log_debug(self.logger, f"Updated photo for officer {officer_id}: {full_name} ({charity_name})")
            else:
                # Mark as processed even if no photo found
                update_query = """
                    UPDATE Officers
                    SET photo_url = ''
                    WHERE officer_id = ?
                """
                self.db_ops.execute_query(update_query, (officer_id,))

                # Also mark children as processed
                update_children_query = """
                    UPDATE Officers
                    SET photo_url = ''
                    WHERE master_id = (
                        SELECT master_id FROM Officers WHERE officer_id = ?
                    )
                """
                self.db_ops.execute_query(update_children_query, (officer_id,))

        self.db_ops.commit()
        log_info(self.logger, f"Photo processing complete. Updated {updated_count} unique officers with photos.")
        return updated_count