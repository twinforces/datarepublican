from queue import Queue, Empty
import time
from tqdm import tqdm
import threading
import psutil
from logging_utils import get_logger
from config import global_config


class QueueStatusDisplay:
    """
    Secondary progress bar for displaying queue status without conflicting with main progress bar.

    Shows real-time queue utilization, throughput, and backpressure statistics.
    Supports standard queue.Queue and custom queues with get_queue_stats().
    Estimates memory usage based on item count and estimated size per item.
    """

    def __init__(self, tracking_queue: Queue, update_interval: float = 1.0, estimated_size_per_item_mb: float = 0.002, custom_metrics_func=None):
        self.tracking_queue = tracking_queue
        self.update_interval = update_interval
        self.estimated_size_per_item_bytes = estimated_size_per_item_mb * 1024 * 1024  # Convert MB to bytes
        self.custom_metrics_func = custom_metrics_func  # Function to get custom metrics
        self._stop_event = threading.Event()
        self._display_thread = None
        self._last_stats = {}
        self._memory_gauge = None
        self._memory_peak = 0.0
        self._parsed_files_gauge = None
        self._parsed_files_peak = 0
        self._pdc_operations_gauge = None
        self._pdc_operations_peak = 0
        self._pdc_updates_gauge = None
        self._pdc_updates_peak = 0
        self._zip_cache_gauge = None
        self._zip_cache_peak = 0
        self._outstanding_geocode_gauge = None
        self._outstanding_geocode_peak = 0
        self._geocoded_addresses_gauge = None
        self._geocoded_addresses_peak = 0
        # API call gauges
        self._census_calls_gauge = None
        self._photon_calls_gauge = None
        self._librestreet_calls_gauge = None
        self._nominatim_calls_gauge = None
        self._opencage_calls_gauge = None
        self._grok_calls_gauge = None
        self._google_maps_calls_gauge = None
        self._name_search_calls_gauge = None
        self._process = psutil.Process()

        # Initialize tqdm progress bar for queue status
        if tqdm:
            self.pbar = tqdm(
                total=100,  # Percentage
                desc="Queue Status",
                unit="%",
                bar_format='{desc}: {percentage:3.0f}%|{bar}| {postfix}',
                position=1,  # Position below main progress bar
                leave=True
            )
        else:
            self.pbar = None

    def start(self):
        """Start the queue status display thread"""
        if self.pbar and not self._display_thread:
            self._stop_event.clear()
            # Initialize memory gauge
            self._memory_gauge = tqdm(
                total=100,  # Percentage
                desc="Memory Usage",
                unit="%",
                bar_format='{desc}: {percentage:3.0f}%|{bar}| {postfix}',
                position=2,  # Position below queue status
                leave=True
            )
            # Initialize parsed files gauge
            self._parsed_files_gauge = tqdm(
                total=100,  # Percentage
                desc="Parsed Files",
                unit="%",
                bar_format='{desc}: {percentage:3.0f}%|{bar}| {postfix}',
                position=3,  # Position below memory gauge
                leave=True
            )
            # Initialize PDC Operations gauge
            self._pdc_operations_gauge = tqdm(
                total=100,  # Percentage
                desc="PDC Operations",
                unit="%",
                bar_format='{desc}: {percentage:3.0f}%|{bar}| {postfix}',
                position=4,  # Position below parsed files gauge
                leave=True
            )
            # Initialize PDC Updates gauge
            self._pdc_updates_gauge = tqdm(
                total=100,  # Percentage
                desc="PDC Updates",
                unit="%",
                bar_format='{desc}: {percentage:3.0f}%|{bar}| {postfix}',
                position=5,  # Position below PDC operations gauge
                leave=True
            )
            # Initialize Zip Cache gauge
            self._zip_cache_gauge = tqdm(
                total=100,  # Percentage
                desc="Zip Cache Size",
                unit="%",
                bar_format='{desc}: {percentage:3.0f}%|{bar}| {postfix}',
                position=6,  # Position below PDC updates gauge
                leave=True
            )
            # Initialize Outstanding Geocode Requests gauge
            self._outstanding_geocode_gauge = tqdm(
                total=100,  # Percentage
                desc="Outstanding Geocode",
                unit="%",
                bar_format='{desc}: {percentage:3.0f}%|{bar}| {postfix}',
                position=7,  # Position below zip cache gauge
                leave=True
            )
            # Initialize Outstanding API Calls gauge
            self._geocoded_addresses_gauge = tqdm(
                total=100,  # Percentage
                desc="Outstanding API Calls",
                unit="%",
                bar_format='{desc}: {percentage:3.0f}%|{bar}| {postfix}',
                position=8,  # Position below outstanding geocode gauge
                leave=True
            )
            # Initialize API call gauges
            self._census_calls_gauge = tqdm(
                total=100,
                desc="Census Calls",
                unit="%",
                bar_format='{desc}: {percentage:3.0f}%|{bar}| {postfix}',
                position=9,
                leave=True
            )
            self._grok_calls_gauge = tqdm(
                total=100,
                desc="Grok Calls",
                unit="%",
                bar_format='{desc}: {percentage:3.0f}%|{bar}| {postfix}',
                position=13,
                leave=True
            )
            self._photon_calls_gauge = tqdm(
                total=100,
                desc="Photon Calls",
                unit="%",
                bar_format='{desc}: {percentage:3.0f}%|{bar}| {postfix}',
                position=10,
                leave=True
            )
            self._librestreet_calls_gauge = tqdm(
                total=100,
                desc="LibreStreet Calls",
                unit="%",
                bar_format='{desc}: {percentage:3.0f}%|{bar}| {postfix}',
                position=11,
                leave=True
            )
            self._nominatim_calls_gauge = tqdm(
                total=100,
                desc="Nominatim Calls",
                unit="%",
                bar_format='{desc}: {percentage:3.0f}%|{bar}| {postfix}',
                position=12,
                leave=True
            )
            self._opencage_calls_gauge = tqdm(
                total=100,
                desc="OpenCage Calls",
                unit="%",
                bar_format='{desc}: {percentage:3.0f}%|{bar}| {postfix}',
                position=13,
                leave=True
            )
            self._google_maps_calls_gauge = tqdm(
                total=100,
                desc="Google Maps Calls",
                unit="%",
                bar_format='{desc}: {percentage:3.0f}%|{bar}| {postfix}',
                position=14,
                leave=True
            )
            self._name_search_calls_gauge = tqdm(
                total=100,
                desc="Name Search Calls",
                unit="%",
                bar_format='{desc}: {percentage:3.0f}%|{bar}| {postfix}',
                position=15,
                leave=True
            )
            self._display_thread = threading.Thread(target=self._display_loop, daemon=True)
            self._display_thread.start()

    def stop(self):
        """Stop the queue status display"""
        if self._display_thread:
            self._stop_event.set()
            self._display_thread.join(timeout=2.0)
            if self.pbar:
                self.pbar.close()
            if self._memory_gauge:
                self._memory_gauge.close()
            if self._parsed_files_gauge:
                self._parsed_files_gauge.close()
            if self._pdc_operations_gauge:
                self._pdc_operations_gauge.close()
            if self._pdc_updates_gauge:
                self._pdc_updates_gauge.close()
            if self._zip_cache_gauge:
                self._zip_cache_gauge.close()
            if self._outstanding_geocode_gauge:
                self._outstanding_geocode_gauge.close()
            if self._geocoded_addresses_gauge:
                self._geocoded_addresses_gauge.close()
            if self._census_calls_gauge:
                self._census_calls_gauge.close()
            if self._photon_calls_gauge:
                self._photon_calls_gauge.close()
            if self._librestreet_calls_gauge:
                self._librestreet_calls_gauge.close()
            if self._nominatim_calls_gauge:
                self._nominatim_calls_gauge.close()
            if self._grok_calls_gauge:
                self._grok_calls_gauge.close()
            if self._opencage_calls_gauge:
                self._opencage_calls_gauge.close()
            if self._google_maps_calls_gauge:
                self._google_maps_calls_gauge.close()
            if self._name_search_calls_gauge:
                self._name_search_calls_gauge.close()

    def _display_loop(self):
        """Main display loop for updating queue status"""
        while not self._stop_event.is_set():
            try:
                self._update_display()
                self._update_memory_display()
                self._update_parsed_files_display()
                self._update_pdc_operations_display()
                self._update_pdc_updates_display()
                self._update_zip_cache_display()
                self._update_outstanding_geocode_display()
                self._update_geocoded_addresses_display()
                self._update_census_calls_display()
                self._update_photon_calls_display()
                self._update_librestreet_calls_display()
                self._update_nominatim_calls_display()
                self._update_opencage_calls_display()
                self._update_grok_calls_display()
                self._update_google_maps_calls_display()
                self._update_name_search_calls_display()
                time.sleep(self.update_interval)
            except Exception as e:
                # Don't let display errors crash the main process
                break

    def _update_display(self):
        """Update the queue status display"""
        if not self.pbar:
            return

        # Get current queue statistics - support both custom queues and standard Queue
        try:
            stats = self.tracking_queue.get_queue_stats()
            has_custom_stats = True
        except AttributeError:
            # Standard queue.Queue - compute basic stats
            current_size = self.tracking_queue.qsize()
            stats = {
                'current_size': current_size,
                'max_size': None,  # Unbounded
                'current_utilization': 1.0 if current_size > 0 else 0.0,  # Assume full if items present
                'utilization_level': self._get_utilization_level(current_size),
                'total_put_attempts': 0,  # Not tracked
                'total_put_successes': 0,
                'total_get_attempts': 0,
                'total_get_successes': 0,
                'adaptive_delays': 0,
                'emergency_blocks': 0,
                'max_utilization': 1.0,
                'adaptive_delay': 0.0
            }
            has_custom_stats = False

        # Calculate utilization percentage
        utilization_pct = stats['current_utilization'] * 100

        # Calculate utilization percentage
        utilization_pct = stats['current_utilization'] * 100

        # Update progress bar
        self.pbar.n = int(utilization_pct)
        self.pbar.total = 100

        # Create postfix with detailed statistics
        postfix_parts = []

        # Current status
        level = stats['utilization_level']
        postfix_parts.append(f"{level.upper()}")

        # Queue size info
        current_size = stats['current_size']
        max_size = stats['max_size'] or '∞'
        postfix_parts.append(f"Size {current_size}/{max_size}")

        # Pending files count (queue size is pending contexts, each context = 1 file)
        postfix_parts.append(f"Pending Files {current_size}")

        # Estimated memory usage
        estimated_memory_gb = (current_size * self.estimated_size_per_item_bytes) / (1024 ** 3)
        postfix_parts.append(f"Est Mem {estimated_memory_gb:.1f}GB")


        # Throughput info
        put_attempts = stats['total_put_attempts']
        put_successes = stats['total_put_successes']
        get_attempts = stats['total_get_attempts']
        get_successes = stats['total_get_successes']

        if put_attempts > 0:
            put_success_rate = (put_successes / put_attempts) * 100
            postfix_parts.append(f"Put {put_success_rate:.1f}%")

        if get_attempts > 0:
            get_success_rate = (get_successes / get_attempts) * 100
            postfix_parts.append(f"Get {get_success_rate:.1f}%")

        # Backpressure stats
        adaptive_delays = stats['adaptive_delays']
        emergency_blocks = stats['emergency_blocks']
        max_utilization = stats['max_utilization'] * 100

        if adaptive_delays > 0:
            postfix_parts.append(f"Delays {adaptive_delays}")

        if emergency_blocks > 0:
            postfix_parts.append(f"Blocks {emergency_blocks}")

        postfix_parts.append(f"Peak {max_utilization:.1f}%")

        # Adaptive delay info
        delay = stats['adaptive_delay']
        if delay > 0:
            postfix_parts.append(f"Delay {delay:.1f}s")

        postfix = " · ".join(postfix_parts)
        self.pbar.set_postfix({"info": postfix})
        self.pbar.refresh()

    def _update_memory_display(self):
        """Update the memory usage display"""
        if not self._memory_gauge:
            return

        try:
            # Get current memory usage
            current_memory_gb = self._process.memory_info().rss / (1024 ** 3)
            self._memory_peak = max(self._memory_peak, current_memory_gb)

            # Get system memory info
            system_memory = psutil.virtual_memory()
            total_memory_gb = system_memory.total / (1024 ** 3)
            available_memory_gb = system_memory.available / (1024 ** 3)

            # Calculate percentage of total system memory
            memory_pct = (current_memory_gb / total_memory_gb) * 100

            # Update gauge
            self._memory_gauge.n = int(memory_pct)
            self._memory_gauge.total = 100

            # Create postfix with memory details
            postfix_parts = []
            postfix_parts.append(f"Current {current_memory_gb:.1f}GB")
            postfix_parts.append(f"Peak {self._memory_peak:.1f}GB")
            postfix_parts.append(f"Available {available_memory_gb:.1f}GB")

            # Memory status indicator
            if memory_pct > 90:
                status = "CRITICAL"
            elif memory_pct > 75:
                status = "HIGH"
            elif memory_pct > 50:
                status = "MEDIUM"
            else:
                status = "LOW"

            postfix_parts.append(f"Status {status}")

            postfix = " · ".join(postfix_parts)
            self._memory_gauge.set_postfix({"info": postfix})
            self._memory_gauge.refresh()

        except Exception as e:
            # Don't let memory monitoring errors crash the process
            pass

    def _update_parsed_files_display(self):
        """Update the parsed files count display"""
        if not self._parsed_files_gauge:
            return

        try:
            # Get current parsed files count from custom metrics
            if self.custom_metrics_func:
                custom_metrics = self.custom_metrics_func()
                if custom_metrics and 'parsed_files_count' in custom_metrics:
                    current_count = custom_metrics['parsed_files_count']
                    total_count = custom_metrics.get('parsed_files_total', 0)
                    self._parsed_files_peak = max(self._parsed_files_peak, current_count)

                    # Set total and current for progress bar
                    self._parsed_files_gauge.total = total_count
                    self._parsed_files_gauge.n = current_count
                    # Calculate percentage for display
                    if total_count > 0:
                        percentage = (current_count / total_count) * 100
                        self._parsed_files_gauge.n = int(percentage)
                    else:
                        self._parsed_files_gauge.n = 0

                    # Create postfix with parsed files details
                    postfix = f"Count {current_count} · Peak {self._parsed_files_peak}"
                    self._parsed_files_gauge.set_postfix({"info": postfix})
                    self._parsed_files_gauge.refresh()

        except Exception as e:
            # Don't let parsed files monitoring errors crash the process
            pass

    def _update_pdc_operations_display(self):
        """Update the PDC operations count display"""
        if not self._pdc_operations_gauge:
            return

        try:
            # Get current PDC operations count from custom metrics
            if self.custom_metrics_func:
                custom_metrics = self.custom_metrics_func()
                if custom_metrics and 'pdc_operations_count' in custom_metrics:
                    current_count = custom_metrics['pdc_operations_count']
                    total_count = custom_metrics.get('pdc_operations_total', 0)
                    self._pdc_operations_peak = max(self._pdc_operations_peak, current_count)

                    # Set total and current for progress bar
                    if total_count > 0:
                        self._pdc_operations_gauge.total = total_count
                        percentage = (current_count / total_count) * 100
                        self._pdc_operations_gauge.n = int(percentage)
                    else:
                        # No total available, use FLUSH_EVERY_N_ROWS as denominator for percentage
                        from pending_database_context import PendingDatabaseContext
                        flush_threshold = PendingDatabaseContext._updated_counter  # This might not be accessible
                        # For now, use a reasonable default
                        self._pdc_operations_gauge.total = 10000  # FLUSH_EVERY_N_ROWS
                        percentage = min((current_count / 10000) * 100, 100)
                        self._pdc_operations_gauge.n = int(percentage)

                    # Create postfix with PDC operations details
                    postfix = f"Count {current_count} · Peak {self._pdc_operations_peak}"
                    self._pdc_operations_gauge.set_postfix({"info": postfix})
                    self._pdc_operations_gauge.refresh()

        except Exception as e:
            # Don't let PDC operations monitoring errors crash the process
            pass

    def _update_pdc_updates_display(self):
        """Update the PDC updates count display"""
        if not self._pdc_updates_gauge:
            return

        try:
            # Get current PDC updates count from custom metrics
            if self.custom_metrics_func:
                custom_metrics = self.custom_metrics_func()
                if custom_metrics and 'pdc_updates_count' in custom_metrics:
                    current_count = custom_metrics['pdc_updates_count']
                    total_count = custom_metrics.get('pdc_updates_total', 0)
                    self._pdc_updates_peak = max(self._pdc_updates_peak, current_count)

                    # Set total and current for progress bar
                    if total_count > 0:
                        self._pdc_updates_gauge.total = total_count
                        percentage = (current_count / total_count) * 100
                        self._pdc_updates_gauge.n = int(percentage)
                    else:
                        # No total available, use FLUSH_EVERY_N_ROWS as denominator for percentage
                        self._pdc_updates_gauge.total = 10000  # FLUSH_EVERY_N_ROWS
                        percentage = min((current_count / 10000) * 100, 100)
                        self._pdc_updates_gauge.n = int(percentage)

                    # Create postfix with PDC updates details
                    postfix = f"Count {current_count} · Peak {self._pdc_updates_peak}"
                    self._pdc_updates_gauge.set_postfix({"info": postfix})
                    self._pdc_updates_gauge.refresh()

        except Exception as e:
            # Don't let PDC updates monitoring errors crash the process
            pass

    def _update_zip_cache_display(self):
        """Update the zip cache size display"""
        if not self._zip_cache_gauge:
            return

        try:
            # Get current zip cache size from custom metrics
            if self.custom_metrics_func:
                custom_metrics = self.custom_metrics_func()
                if custom_metrics and 'zip_cache_size' in custom_metrics:
                    current_size = custom_metrics['zip_cache_size']
                    total_size = 36  # Fixed denominator for zip cache
                    self._zip_cache_peak = max(self._zip_cache_peak, current_size)

                    # Set total and current for progress bar
                    self._zip_cache_gauge.total = total_size
                    self._zip_cache_gauge.n = current_size
                    # Calculate percentage for display
                    if total_size > 0:
                        percentage = (current_size / total_size) * 100
                        self._zip_cache_gauge.n = int(percentage)
                    else:
                        self._zip_cache_gauge.n = 0

                    # Create postfix with zip cache details
                    postfix = f"Count {current_size} · Peak {self._zip_cache_peak}"
                    self._zip_cache_gauge.set_postfix({"info": postfix})
                    self._zip_cache_gauge.refresh()

        except Exception as e:
            # Don't let zip cache monitoring errors crash the process
            pass

    def _update_outstanding_geocode_display(self):
        """Update the outstanding geocode requests display"""
        if not self._outstanding_geocode_gauge:
            return

        try:
            # Get current outstanding geocode requests count from custom metrics
            if self.custom_metrics_func:
                custom_metrics = self.custom_metrics_func()
                if custom_metrics and 'outstanding_geocode_requests' in custom_metrics:
                    current_count = custom_metrics['outstanding_geocode_requests']
                    self._outstanding_geocode_peak = max(self._outstanding_geocode_peak, current_count)

                    # For outstanding requests, use a reasonable total for percentage display
                    # Since we don't know the total, use a fixed denominator
                    total_requests = 10000  # Arbitrary large number for percentage calculation
                    percentage = min((current_count / total_requests) * 100, 100)
                    self._outstanding_geocode_gauge.n = int(percentage)
                    self._outstanding_geocode_gauge.total = 100

                    # Create postfix with outstanding geocode details
                    postfix = f"Count {current_count} · Peak {self._outstanding_geocode_peak}"
                    self._outstanding_geocode_gauge.set_postfix({"info": postfix})
                    self._outstanding_geocode_gauge.refresh()

        except Exception as e:
            # Don't let outstanding geocode monitoring errors crash the process
            pass

    def _update_geocoded_addresses_display(self):
        """Update the outstanding API calls display"""
        if not self._geocoded_addresses_gauge:
            return

        try:
            # Get current outstanding geocode requests count from custom metrics
            if self.custom_metrics_func:
                custom_metrics = self.custom_metrics_func()
                if custom_metrics and 'outstanding_geocode_requests' in custom_metrics:
                    current_records = custom_metrics['outstanding_geocode_requests']
                    self._geocoded_addresses_peak = max(self._geocoded_addresses_peak, current_records)

                    # Calculate API calls (assuming ~1000 records per API call)
                    api_calls = (current_records + 999) // 1000  # Round up division
                    self._geocoded_addresses_peak = max(self._geocoded_addresses_peak, api_calls)

                    # For API calls, use a reasonable total for percentage display
                    total_calls = 100  # Expect up to 100 API calls
                    percentage = min((api_calls / total_calls) * 100, 100)
                    self._geocoded_addresses_gauge.n = int(percentage)
                    self._geocoded_addresses_gauge.total = 100

                    # Create postfix with API calls details
                    postfix = f"Calls {api_calls} · Records {current_records:,}"
                    self._geocoded_addresses_gauge.set_postfix({"info": postfix})
                    self._geocoded_addresses_gauge.refresh()

        except Exception as e:
            # Don't let outstanding geocode monitoring errors crash the process
            pass

    def _update_census_calls_display(self):
        """Update the Census calls display"""
        if not self._census_calls_gauge:
            return

        try:
            if self.custom_metrics_func:
                custom_metrics = self.custom_metrics_func()
                if custom_metrics and 'census_calls' in custom_metrics:
                    count = custom_metrics['census_calls']
                    self._census_calls_gauge.n = 0
                    self._census_calls_gauge.total = 100
                    postfix = f"Count {count}"
                    self._census_calls_gauge.set_postfix({"info": postfix})
                    self._census_calls_gauge.refresh()
        except Exception as e:
            pass

    def _update_photon_calls_display(self):
        """Update the Photon calls display"""
        if not self._photon_calls_gauge:
            return

        try:
            if self.custom_metrics_func:
                custom_metrics = self.custom_metrics_func()
                if custom_metrics and 'photon_calls' in custom_metrics:
                    count = custom_metrics['photon_calls']
                    self._photon_calls_gauge.n = 0
                    self._photon_calls_gauge.total = 100
                    postfix = f"Count {count}"
                    self._photon_calls_gauge.set_postfix({"info": postfix})
                    self._photon_calls_gauge.refresh()
        except Exception as e:
            pass
        
    def _update_grok_calls_display(self):
        """Update the Grok calls display"""
        if not self._grok_calls_gauge:
            return

        try:
            if self.custom_metrics_func:
                custom_metrics = self.custom_metrics_func()
                if custom_metrics and 'grok_calls' in custom_metrics:
                    count = custom_metrics['photon_calls']
                    self._grok_calls_gauge.n = 0
                    self._grok_calls_gauge.total = 100
                    postfix = f"Count {count}"
                    self._grok_calls_gauge.set_postfix({"info": postfix})
                    self._grok_calls_gauge.refresh()
        except Exception as e:
            pass


    def _update_librestreet_calls_display(self):
        """Update the LibreStreet calls display"""
        if not self._librestreet_calls_gauge:
            return

        try:
            if self.custom_metrics_func:
                custom_metrics = self.custom_metrics_func()
                if custom_metrics and 'librestreet_calls' in custom_metrics:
                    count = custom_metrics['librestreet_calls']
                    self._librestreet_calls_gauge.n = 0
                    self._librestreet_calls_gauge.total = 100
                    postfix = f"Count {count}"
                    self._librestreet_calls_gauge.set_postfix({"info": postfix})
                    self._librestreet_calls_gauge.refresh()
        except Exception as e:
            pass

    def _update_nominatim_calls_display(self):
        """Update the Nominatim calls display"""
        if not self._nominatim_calls_gauge:
            return

        try:
            if self.custom_metrics_func:
                custom_metrics = self.custom_metrics_func()
                if custom_metrics and 'nominatim_calls' in custom_metrics:
                    count = custom_metrics['nominatim_calls']
                    self._nominatim_calls_gauge.n = 0
                    self._nominatim_calls_gauge.total = 100
                    postfix = f"Count {count}"
                    self._nominatim_calls_gauge.set_postfix({"info": postfix})
                    self._nominatim_calls_gauge.refresh()
        except Exception as e:
            pass

    def _update_opencage_calls_display(self):
        """Update the OpenCage calls display"""
        if not self._opencage_calls_gauge:
            return

        try:
            if self.custom_metrics_func:
                custom_metrics = self.custom_metrics_func()
                if custom_metrics and 'opencage_calls' in custom_metrics:
                    count = custom_metrics['opencage_calls']
                    self._opencage_calls_gauge.n = 0
                    self._opencage_calls_gauge.total = 100
                    postfix = f"Count {count}"
                    self._opencage_calls_gauge.set_postfix({"info": postfix})
                    self._opencage_calls_gauge.refresh()
        except Exception as e:
            pass

    def _update_google_maps_calls_display(self):
        """Update the Google Maps calls display"""
        if not self._google_maps_calls_gauge:
            return

        try:
            if self.custom_metrics_func:
                custom_metrics = self.custom_metrics_func()
                if custom_metrics and 'google_maps_calls' in custom_metrics:
                    count = custom_metrics['google_maps_calls']
                    self._google_maps_calls_gauge.n = 0
                    self._google_maps_calls_gauge.total = 100
                    postfix = f"Count {count}"
                    self._google_maps_calls_gauge.set_postfix({"info": postfix})
                    self._google_maps_calls_gauge.refresh()
        except Exception as e:
            pass

    def _update_name_search_calls_display(self):
        """Update the Name Search calls display"""
        if not self._name_search_calls_gauge:
            return

        try:
            if self.custom_metrics_func:
                custom_metrics = self.custom_metrics_func()
                if custom_metrics and 'name_search_calls' in custom_metrics:
                    count = custom_metrics['name_search_calls']
                    self._name_search_calls_gauge.n = 0
                    self._name_search_calls_gauge.total = 100
                    postfix = f"Count {count}"
                    self._name_search_calls_gauge.set_postfix({"info": postfix})
                    self._name_search_calls_gauge.refresh()
        except Exception as e:
            pass

    def _get_utilization_level(self, current_size: int) -> str:
        """Determine utilization level based on queue size"""
        if current_size == 0:
            return 'low'
        elif current_size < 5:
            return 'low'
        elif current_size < 10:
            return 'medium'
        elif current_size < 20:
            return 'high'
        else:
            return 'critical'