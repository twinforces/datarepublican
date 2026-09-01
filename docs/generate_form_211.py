```python
import pandas as pd
from mako.template import Template
import subprocess
import os
from datetime import datetime

# Paths
CSV_PATH = "/Volumes/Data/irs_zips/grift_candidates_2021.csv"
TEMPLATE_PATH = "/Volumes/Data/irs_zips/evidence/form_211_template.md"
EVIDENCE_DIR = "/Volumes/Data/irs_zips/evidence"
OUTPUT_PREFIX = "/Volumes/Data/irs_zips/evidence/form_211_batch"

# Your info
YOUR_NAME = "Pierce Wetter"
YOUR_ADDRESS = "123 Main St, City, State, ZIP"
YOUR_PHONE = "555-123-4567"
YOUR_EMAIL = "pierce@example.com"
ANONYMITY_REQUEST = "I request anonymity to protect my identity under IRS confidentiality rules."

def generate_form_211(batch, batch_number):
    # Load template
    template = Template(filename=TEMPLATE_PATH)

    # Render Markdown
    submission_date = datetime.now().strftime("%B %d, %Y")
    rendered_md = template.render(
        submission_date=submission_date,
        ngos=batch.to_dict("records"),
        your_name=YOUR_NAME,
        your_address=YOUR_ADDRESS,
        your_phone=YOUR_PHONE,
        your_email=YOUR_EMAIL,
        anonymity_request=ANONYMITY_REQUEST
    )

    # Save rendered Markdown
    md_path = f"{OUTPUT_PREFIX}_{batch_number}.md"
    with open(md_path, "w", encoding="utf-8") as f:
        f.write(rendered_md)

    # Convert to PDF
    pdf_path = f"{OUTPUT_PREFIX}_{batch_number}.pdf"
    subprocess.run(
        ["pandoc", md_path, "-o", pdf_path, "--pdf-engine=wkhtmltopdf"],
        check=True
    )

    print(f"Generated {pdf_path}")
    return pdf_path

def main():
    # Load CSV
    df = pd.read_csv(CSV_PATH)
    
    # Sort by comp_pct and select top NGOs
    df_sorted = df.sort_values("comp_pct", ascending=False)
    
    # Process in batches of 5
    batch_size = 5
    for batch_number, start in enumerate(range(0, len(df_sorted), batch_size), 1):
        batch = df_sorted.iloc[start:start + batch_size]
        if batch.empty:
            break
        
        # Generate Form 211 PDF
        pdf_path = generate_form_211(batch, batch_number)
        
        # Save CSV subset
        csv_subset_path = f"{EVIDENCE_DIR}/grift_candidates_2021_batch_{batch_number}.csv"
        batch.to_csv(csv_subset_path, index=False)
        print(f"Saved batch CSV: {csv_subset_path}")
        
        # Extract XMLs
        for ein in batch["filer_ein"]:
            for year in ["2020", "2021"]:
                zip_file = "/Volumes/Data/irs_zips/recompressed_zips/2020_TEOS_XML_CT1.zip" if year == "2020" else "/Volumes/Data/irs_zips/2021_TEOS_XML_01A.zip"
                xml_file = f"{ein}_public.xml"
                subprocess.run(
                    ["7z", "x", zip_file, f"-o{EVIDENCE_DIR}", xml_file, "-y"],
                    check=True,
                    capture_output=True
                )
                print(f"Extracted {xml_file} for EIN {ein} from {year}")

if __name__ == "__main__":
    main()
```