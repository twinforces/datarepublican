from queue import Queue, Empty
import time
from tqdm import tqdm
import threading
import psutil


class QueueStatusDisplay:
    """
    Secondary progress bar for displaying queue status without conflicting with main progress bar.

    Shows real-time queue utilization, throughput, and backpressure statistics.
    Supports standard queue.Queue and custom queues with get_queue_stats().
    Estimates memory usage based on item count and estimated size per item.
    """

    def __init__(self, tracking_queue: Queue, update_interval: float = 1.0, estimated_size_per_item_mb: float = 0.002):
        self.tracking_queue = tracking_queue
        self.update_interval = update_interval
        self.estimated_size_per_item_bytes = estimated_size_per_item_mb * 1024 * 1024  # Convert MB to bytes
        self._stop_event = threading.Event()
        self._display_thread = None
        self._last_stats = {}
        self._memory_gauge = None
        self._memory_peak = 0.0
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

    def _display_loop(self):
        """Main display loop for updating queue status"""
        while not self._stop_event.is_set():
            try:
                self._update_display()
                self._update_memory_display()
                time.sleep(self.update_interval)
            except Exception as e:
                # Don't let display errors crash the main process
                if not global_config.is_quiet():
                    log_debug(get_logger("queue_display"), f"Queue display error: {e}")
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
        level_colors = {
            'low': '🟢',      # Green
            'medium': '🟡',   # Yellow
            'high': '🟠',     # Orange
            'critical': '🔴'  # Red
        }
        color = level_colors.get(level, '⚪')
        postfix_parts.append(f"{color} {level.upper()}")

        # Queue size info
        current_size = stats['current_size']
        max_size = stats['max_size'] or '∞'
        postfix_parts.append(f"Size: {current_size}/{max_size}")

        # Pending files count (queue size is pending contexts, each context = 1 file)
        postfix_parts.append(f"Pending Files: {current_size}")

        # Estimated memory usage
        estimated_memory_gb = (current_size * self.estimated_size_per_item_bytes) / (1024 ** 3)
        postfix_parts.append(f"Est Mem: {estimated_memory_gb:.1f}GB")

        # Throughput info
        put_attempts = stats['total_put_attempts']
        put_successes = stats['total_put_successes']
        get_attempts = stats['total_get_attempts']
        get_successes = stats['total_get_successes']

        if put_attempts > 0:
            put_success_rate = (put_successes / put_attempts) * 100
            postfix_parts.append(f"Put: {put_success_rate:.1f}%")

        if get_attempts > 0:
            get_success_rate = (get_successes / get_attempts) * 100
            postfix_parts.append(f"Get: {get_success_rate:.1f}%")

        # Backpressure stats
        adaptive_delays = stats['adaptive_delays']
        emergency_blocks = stats['emergency_blocks']
        max_utilization = stats['max_utilization'] * 100

        if adaptive_delays > 0:
            postfix_parts.append(f"Delays: {adaptive_delays}")

        if emergency_blocks > 0:
            postfix_parts.append(f"Blocks: {emergency_blocks}")

        postfix_parts.append(f"Peak: {max_utilization:.1f}%")

        # Adaptive delay info
        delay = stats['adaptive_delay']
        if delay > 0:
            postfix_parts.append(f"Delay: {delay:.1f}s")

        postfix = " | ".join(postfix_parts)
        self.pbar.set_postfix_str(postfix)
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
            postfix_parts.append(f"Current: {current_memory_gb:.1f}GB")
            postfix_parts.append(f"Peak: {self._memory_peak:.1f}GB")
            postfix_parts.append(f"Available: {available_memory_gb:.1f}GB")

            # Memory status indicator
            if memory_pct > 90:
                status = "🔴 CRITICAL"
            elif memory_pct > 75:
                status = "🟠 HIGH"
            elif memory_pct > 50:
                status = "🟡 MEDIUM"
            else:
                status = "🟢 LOW"

            postfix_parts.append(f"Status: {status}")

            postfix = " | ".join(postfix_parts)
            self._memory_gauge.set_postfix_str(postfix)
            self._memory_gauge.refresh()

        except Exception as e:
            # Don't let memory monitoring errors crash the process
            if not global_config.is_quiet():
                log_debug(get_logger("memory_display"), f"Memory display error: {e}")

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