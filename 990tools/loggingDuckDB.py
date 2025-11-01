import duckdb
from logging_utils import get_logger, log_info, log_debug

class LoggingDuckDBConnection:
    """
    Proxy for duckdb.DuckDBPyConnection that logs SQL queries before execution,
    including bulk operations via executemany().
    
    Usage:
        conn = LoggingDuckDBConnection('my_database.db')
        conn.executemany("INSERT INTO test VALUES (?, ?)", [(1, 'a'), (2, 'b')])
        # Logs: "Executing SQL (bulk): INSERT INTO test VALUES (?, ?)"
        #        "With 2 parameter sets"
    """
    
    def __init__(self, *args, **kwargs):
        """
        Initialize the proxy by creating the underlying DuckDB connection.
        Supports all args/kwargs from duckdb.connect().
        """
        logger = get_logger(__name__)
        log_debug(logger, f"connecting: {args} {kwargs}")
        print("Using Logging DuckDB")
        self._conn = duckdb.connect(*args, **kwargs)
    
    def execute(self, query, parameters=None, **kwargs):
        """
        Execute a SQL query, logging it first.
        Logs the query with placeholders intact (for security); params are not logged.
        """
        logger = get_logger(__name__)
        log_debug(logger, f"Executing SQL: {query}")
        if parameters:
            log_debug(logger, f"With params: {parameters}")
        return self._conn.execute(query, parameters, **kwargs)

    def sql(self, query, parameters=None, **kwargs):
        """
        Execute a SQL query via the sql() method, logging it first.
        Similar to execute(), but returns a DuckDBPyRelation.
        """
        logger = get_logger(__name__)
        log_debug(logger, f"Executing SQL via sql(): {query}")
        if parameters:
            log_debug(logger, f"With params: {parameters}")
        return self._conn.sql(query, parameters, **kwargs)
    
    def executemany(self, query, parameters=None, **kwargs):
        """
        Execute a SQL query multiple times with different parameter sets, logging once.
        Logs the query with placeholders; reports batch size but not individual params.
        """
        logger = get_logger(__name__)
        log_debug(logger, f"Executing SQL (bulk): {query}")
        if parameters:
            log_debug(logger, f"With {len(parameters)} parameter sets")
        return self._conn.executemany(query, parameters, **kwargs)
    
    def __getattr__(self, name):
        """
        Delegate all other attribute access to the underlying connection.
        This handles methods like cursor(), commit(), rollback(), close(), etc.
        """
        return getattr(self._conn, name)
    
    def close(self):
        """
        Explicitly close the underlying connection.
        """
        return self._conn.close()