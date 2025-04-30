import os
import json
import psycopg2
from datetime import date
import csv

def main():
    # Connect to Postgres using environment variables or fall back to a default
    db_conn_str = os.getenv("CONN", "postgresql://root:password@127.0.0.1:5432/data_store_api") \
                  + "/" + os.getenv("DBNAME", "data_store_api")
    print(f"Connecting to: {db_conn_str}")
    conn = psycopg2.connect(db_conn_str)

    try:
        # We want to fetch only awards with a period_of_performance_current_end_date in calendar year 2024
        start_2024 = date(2024, 1, 1)
        end_2024 = date(2024, 12, 31)

        with conn.cursor(name="award_cursor_2024") as cur:
            cur.itersize = 2000  # fetch 2k rows at a time

            cur.execute("""
                SELECT
                    generated_unique_award_id,
                    description,
                    period_of_performance_current_end_date,
                    ordering_period_end_date AS ordering_end_date, -- For procurement, the date on which, for the award referred to by the action being reported, no additional orders referring to it may be placed
                    base_and_all_options_value AS potential, -- For the Award it is the mutually agreed upon total contract value including all options (if any)
                    base_exercised_options_val AS current_award_amount, -- The contract value for the base contract and any options that have been exercised.
                    total_obligation AS total_obligated,
                    total_outlays AS outlays
                FROM rpt.award_search
                WHERE
                    total_obligation > 1000000 -- Arbitrary restriction to larger awards
                    AND period_of_performance_current_end_date >= %s -- Start of 2024  
                    AND period_of_performance_current_end_date <= %s -- End of 2024
                    AND (
                        ordering_period_end_date IS NULL
                        OR ordering_period_end_date <= %s -- No options remaining to be exercised past 2024
                    )                    
            """, (start_2024, end_2024, end_2024))

            with open('awards_2024.csv', mode="w", newline="", encoding="utf-8") as csvfile:
                writer = csv.writer(csvfile)

                # Write header row
                writer.writerow([
                    "generated_unique_award_id",
                    "description",
                    "period_of_performance_current_end_date",
                    "ordering_end_date",
                    "potential",               # base_and_all_options_value
                    "current_award_amount",    # base_exercised_options_val
                    "total_obligated",         # total_obligation
                    "outlays"                  # total_outlays
                ])

                row_count = 0
                for row in cur:
                    if (row_count % 10000)==0:
                        print("Found %s rows" % row_count)
                    row_count += 1
                    (
                        gen_award_id,
                        desc,
                        end_date,
                        ordering_end_date,
                        potential,
                        current_award_amount,
                        total_obligated,
                        outlays
                    ) = row

                    writer.writerow([
                        gen_award_id,
                        desc,
                        end_date,
                        ordering_end_date,
                        potential,
                        current_award_amount,
                        total_obligated,
                        outlays
                    ])

                print(f"Found {row_count} awards ending in 2024.")
    finally:
        conn.close()
        print("Connection closed.")

if __name__ == "__main__":
    main()
