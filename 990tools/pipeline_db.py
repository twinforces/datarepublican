"""
Pipeline progress tracking database manager using SQLite.
Handles step progress, status, timing, and error tracking for resumable pipelines.
"""

import sqlite3
import os
from datetime import datetime
from typing import List, Optional, Dict, Any
from dataclasses import asdict
from models import PipelineProgress


class PipelineDatabaseManager:
    """Manages SQLite database for pipeline progress tracking."""

    def __init__(self, db_path: str = "/Volumes/Data/final/pipeline_progress.db"):
        """Initialize database connection and create tables if needed."""
        self.db_path = db_path
        self._ensure_db_exists()

    def _ensure_db_exists(self):
        """Create database and tables if they don't exist."""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute('''
                CREATE TABLE IF NOT EXISTS pipeline_progress (
                    progress_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    step_name TEXT NOT NULL,
                    start_year INTEGER NOT NULL,
                    end_year INTEGER NOT NULL,
                    status TEXT NOT NULL DEFAULT 'pending',
                    started_at TEXT,
                    completed_at TEXT,
                    records_processed INTEGER DEFAULT 0,
                    error_message TEXT,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(step_name, start_year, end_year)
                )
            ''')

            # Create indexes for better query performance
            conn.execute('CREATE INDEX IF NOT EXISTS idx_step_status ON pipeline_progress(step_name, status)')
            conn.execute('CREATE INDEX IF NOT EXISTS idx_years ON pipeline_progress(start_year, end_year)')
            conn.commit()

    def _row_to_progress(self, row) -> PipelineProgress:
        """Convert database row to PipelineProgress object."""
        return PipelineProgress(
            progress_id=row[0],
            step_name=row[1],
            start_year=row[2],
            end_year=row[3],
            status=row[4],
            started_at=datetime.fromisoformat(row[5]) if row[5] else None,
            completed_at=datetime.fromisoformat(row[6]) if row[6] else None,
            records_processed=row[7],
            error_message=row[8],
            created_at=datetime.fromisoformat(row[9]) if row[9] else None,
            updated_at=datetime.fromisoformat(row[10]) if row[10] else None,
        )

    def start_step(self, step_name: str, start_year: int, end_year: int) -> PipelineProgress:
        """Start tracking a pipeline step."""
        now = datetime.now().isoformat()

        with sqlite3.connect(self.db_path) as conn:
            # Insert or update the step record
            conn.execute('''
                INSERT INTO pipeline_progress
                (step_name, start_year, end_year, status, started_at, updated_at)
                VALUES (?, ?, ?, 'running', ?, ?)
                ON CONFLICT(step_name, start_year, end_year)
                DO UPDATE SET
                    status = 'running',
                    started_at = ?,
                    completed_at = NULL,
                    error_message = NULL,
                    updated_at = ?
            ''', (step_name, start_year, end_year, now, now, now, now))

            # Get the updated record
            cursor = conn.execute('''
                SELECT progress_id, step_name, start_year, end_year, status,
                       started_at, completed_at, records_processed, error_message,
                       created_at, updated_at
                FROM pipeline_progress
                WHERE step_name = ? AND start_year = ? AND end_year = ?
            ''', (step_name, start_year, end_year))

            row = cursor.fetchone()
            return self._row_to_progress(row)

    def update_records_processed(self, step_name: str, start_year: int, end_year: int,
                               records_processed: int):
        """Update the number of records processed for a step."""
        now = datetime.now().isoformat()

        with sqlite3.connect(self.db_path) as conn:
            conn.execute('''
                UPDATE pipeline_progress
                SET records_processed = ?, updated_at = ?
                WHERE step_name = ? AND start_year = ? AND end_year = ?
            ''', (records_processed, now, step_name, start_year, end_year))

    def complete_step(self, step_name: str, start_year: int, end_year: int,
                     records_processed: Optional[int] = None) -> PipelineProgress:
        """Mark a pipeline step as completed."""
        now = datetime.now().isoformat()

        with sqlite3.connect(self.db_path) as conn:
            if records_processed is not None:
                conn.execute('''
                    UPDATE pipeline_progress
                    SET status = 'completed', completed_at = ?, records_processed = ?,
                        updated_at = ?
                    WHERE step_name = ? AND start_year = ? AND end_year = ?
                ''', (now, records_processed, now, step_name, start_year, end_year))
            else:
                conn.execute('''
                    UPDATE pipeline_progress
                    SET status = 'completed', completed_at = ?, updated_at = ?
                    WHERE step_name = ? AND start_year = ? AND end_year = ?
                ''', (now, now, step_name, start_year, end_year))

            # Get the updated record
            cursor = conn.execute('''
                SELECT progress_id, step_name, start_year, end_year, status,
                       started_at, completed_at, records_processed, error_message,
                       created_at, updated_at
                FROM pipeline_progress
                WHERE step_name = ? AND start_year = ? AND end_year = ?
            ''', (step_name, start_year, end_year))

            row = cursor.fetchone()
            return self._row_to_progress(row)

    def fail_step(self, step_name: str, start_year: int, end_year: int,
                 error_message: str) -> PipelineProgress:
        """Mark a pipeline step as failed with an error message."""
        now = datetime.now().isoformat()

        with sqlite3.connect(self.db_path) as conn:
            conn.execute('''
                UPDATE pipeline_progress
                SET status = 'failed', completed_at = ?, error_message = ?, updated_at = ?
                WHERE step_name = ? AND start_year = ? AND end_year = ?
            ''', (now, error_message, now, step_name, start_year, end_year))

            # Get the updated record
            cursor = conn.execute('''
                SELECT progress_id, step_name, start_year, end_year, status,
                       started_at, completed_at, records_processed, error_message,
                       created_at, updated_at
                FROM pipeline_progress
                WHERE step_name = ? AND start_year = ? AND end_year = ?
            ''', (step_name, start_year, end_year))

            row = cursor.fetchone()
            return self._row_to_progress(row)

    def get_step_progress(self, step_name: str, start_year: int, end_year: int) -> Optional[PipelineProgress]:
        """Get progress for a specific step."""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute('''
                SELECT progress_id, step_name, start_year, end_year, status,
                       started_at, completed_at, records_processed, error_message,
                       created_at, updated_at
                FROM pipeline_progress
                WHERE step_name = ? AND start_year = ? AND end_year = ?
            ''', (step_name, start_year, end_year))

            row = cursor.fetchone()
            return self._row_to_progress(row) if row else None

    def get_all_progress(self, start_year: int, end_year: int) -> List[PipelineProgress]:
        """Get all progress records for a given year range."""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute('''
                SELECT progress_id, step_name, start_year, end_year, status,
                       started_at, completed_at, records_processed, error_message,
                       created_at, updated_at
                FROM pipeline_progress
                WHERE start_year = ? AND end_year = ?
                ORDER BY created_at
            ''', (start_year, end_year))

            return [self._row_to_progress(row) for row in cursor.fetchall()]

    def get_completed_steps(self, start_year: int, end_year: int) -> List[str]:
        """Get list of completed step names for a year range."""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute('''
                SELECT step_name FROM pipeline_progress
                WHERE start_year = ? AND end_year = ? AND status = 'completed'
                ORDER BY created_at
            ''', (start_year, end_year))

            return [row[0] for row in cursor.fetchall()]

    def get_failed_steps(self, start_year: int, end_year: int) -> List[PipelineProgress]:
        """Get list of failed steps for a year range."""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute('''
                SELECT progress_id, step_name, start_year, end_year, status,
                       started_at, completed_at, records_processed, error_message,
                       created_at, updated_at
                FROM pipeline_progress
                WHERE start_year = ? AND end_year = ? AND status = 'failed'
                ORDER BY created_at
            ''', (start_year, end_year))

            return [self._row_to_progress(row) for row in cursor.fetchall()]

    def get_running_steps(self, start_year: int, end_year: int) -> List[PipelineProgress]:
        """Get list of currently running steps for a year range."""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute('''
                SELECT progress_id, step_name, start_year, end_year, status,
                       started_at, completed_at, records_processed, error_message,
                       created_at, updated_at
                FROM pipeline_progress
                WHERE start_year = ? AND end_year = ? AND status = 'running'
                ORDER BY started_at
            ''', (start_year, end_year))

            return [self._row_to_progress(row) for row in cursor.fetchall()]

    def reset_step(self, step_name: str, start_year: int, end_year: int):
        """Reset a step to pending status."""
        now = datetime.now().isoformat()

        with sqlite3.connect(self.db_path) as conn:
            conn.execute('''
                UPDATE pipeline_progress
                SET status = 'pending', started_at = NULL, completed_at = NULL,
                    records_processed = 0, error_message = NULL, updated_at = ?
                WHERE step_name = ? AND start_year = ? AND end_year = ?
            ''', (now, step_name, start_year, end_year))

    def bootstrap_from_existing(self, start_year: int, end_year: int,
                              completed_steps: List[str]):
        """Bootstrap progress tracking from existing completed steps."""
        now = datetime.now().isoformat()

        with sqlite3.connect(self.db_path) as conn:
            for step_name in completed_steps:
                conn.execute('''
                    INSERT INTO pipeline_progress
                    (step_name, start_year, end_year, status, completed_at, updated_at)
                    VALUES (?, ?, ?, 'completed', ?, ?)
                    ON CONFLICT(step_name, start_year, end_year)
                    DO UPDATE SET
                        status = 'completed',
                        completed_at = ?,
                        updated_at = ?
                ''', (step_name, start_year, end_year, now, now, now, now))

    def get_resume_point(self, start_year: int, end_year: int) -> Optional[str]:
        """Determine the step to resume from based on progress."""
        completed_steps = self.get_completed_steps(start_year, end_year)

        # Define the pipeline step order
        pipeline_steps = [
            'download', 'recompress', 'extract', 'analyze', 'latest',
            'addresses', 'backfill', 'grants', 'check', 'copy', 'report'
        ]

        # Find the first incomplete step
        for step in pipeline_steps:
            if step not in completed_steps:
                return step

        return None  # All steps completed

    def clear_progress(self, start_year: int, end_year: int):
        """Clear all progress records for a year range."""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute('''
                DELETE FROM pipeline_progress
                WHERE start_year = ? AND end_year = ?
            ''', (start_year, end_year))

    def get_progress_summary(self, start_year: int, end_year: int) -> Dict[str, Any]:
        """Get a summary of pipeline progress."""
        all_progress = self.get_all_progress(start_year, end_year)

        summary = {
            'total_steps': len(all_progress),
            'completed_steps': len([p for p in all_progress if p.is_completed]),
            'running_steps': len([p for p in all_progress if p.is_running]),
            'failed_steps': len([p for p in all_progress if p.has_error]),
            'pending_steps': len([p for p in all_progress if p.status == 'pending']),
            'total_records_processed': sum(p.records_processed for p in all_progress),
            'steps': {p.step_name: {
                'status': p.status,
                'records_processed': p.records_processed,
                'started_at': p.started_at.isoformat() if p.started_at else None,
                'completed_at': p.completed_at.isoformat() if p.completed_at else None,
                'error_message': p.error_message
            } for p in all_progress}
        }

        return summary

    def close(self):
        """Close database connection (SQLite handles this automatically, but for completeness)."""
        pass