import requests
import time
import json

def get_toptier_agencies():
    url = "https://api.usaspending.gov/api/v2/references/toptier_agencies/"
    response = requests.get(url)
    print(f"Fetching top-tier agencies: Status {response.status_code}")
    return response.json()['results'] if response.status_code == 200 else []

def get_federal_accounts(toptier_code):
    url = f"https://api.usaspending.gov/api/v2/agency/{toptier_code}/federal_account/"
    response = requests.get(url)
    print(f"Fetching federal accounts for {toptier_code}: Status {response.status_code}")
    return response.json()['results'] if response.status_code == 200 else []

def get_account_spending(account_number, fiscal_year):
    url = f"https://api.usaspending.gov/api/v2/federal_accounts/{account_number}/"
    params = {"fiscal_year": fiscal_year, "limit": 100}
    response = requests.get(url, params=params)
    print(f"Fetching spending for {account_number} (FY {fiscal_year}): Status {response.status_code}")
    return response.json() if response.status_code == 200 else {}

def get_function_spending(account_number, fiscal_year, account_title, toptier_code):
    # Step 1: Get subfunctions to find relevant budget_function and budget_subfunction
    subfunction_url = "https://api.usaspending.gov/api/v2/spending/"
    subfunction_payload = {
        "type": "budget_subfunction",
        "filters": {
            "fy": str(fiscal_year),
            "period": "12"
        }
    }
    subfunction_response = requests.post(subfunction_url, json=subfunction_payload)
    print(f"Fetching budget subfunctions (FY {fiscal_year}): Status {subfunction_response.status_code}")
    if subfunction_response.status_code == 200:
        subfunction_data = subfunction_response.json()
        print(f"Budget subfunction response: {json.dumps(subfunction_data, indent=2)}")

        # Step 2: Fetch federal account data for each subfunction
        account_data = get_account_spending(account_number, fiscal_year)
        total = float(account_data.get('total_obligated_amount', 0.0))
        function_totals = {}
        for subfunc in subfunction_data.get('results', []):
            subfunc_name = subfunc.get('name')
            subfunc_code = subfunc.get('code')
            # Map toptier_code to budget_function (simplified for DoD)
            budget_function = "050" if toptier_code == "097" else "000"  # Adjust for other agencies
            account_payload = {
                "type": "federal_account",
                "filters": {
                    "fy": str(fiscal_year),
                    "period": "12",
                    "budget_function": budget_function,
                    "budget_subfunction": subfunc_code
                }
            }
            account_response = requests.post(subfunction_url, json=account_payload)
            if account_response.status_code == 200:
                account_data_response = account_response.json()
                print(f"Federal account response for subfunction {subfunc_code}: {json.dumps(account_data_response, indent=2)}")
                for item in account_data_response.get('results', []):
                    if item.get('account_number') == account_number:
                        function_totals[subfunc_name] = total  # Use FY 2024 total
                        break
        if function_totals:
            return function_totals

    print(f"Error: {subfunction_response.text if 'subfunction_response' in locals() else 'No subfunction response'}")
    # Fallback
    account_data = get_account_spending(account_number, fiscal_year)
    amount = float(account_data.get('total_obligated_amount', 0.0))
    title_lower = account_title.lower()
    if account_number.startswith(("097", "017", "021")):
        if "retirement" in title_lower:
            return {"Income Security": amount}
        elif "operation" in title_lower:
            return {"National Defense": amount}
        return {"National Defense": amount}
    elif "salaries and expenses" in title_lower:
        return {"General Government": amount}
    return {"Other": amount}

def main(fiscal_year=2024, max_agencies=4):
    toptier_agencies = get_toptier_agencies()
    dod = next((a for a in toptier_agencies if a['toptier_code'] == "097"), None)
    selected_agencies = toptier_agencies[:max_agencies-1] + ([dod] if dod else [])
    spending_data = {}
    
    dod_subtier_map = {
        "021": "Department of the Army",
        "017": "Department of the Navy",
        "057": "Department of the Air Force",
        "097": "Defense-Wide"
    }

    for agency in selected_agencies:
        agency_name = agency['agency_name']
        toptier_code = agency['toptier_code']
        print(f"\nProcessing {agency_name} (Top-tier Code: {toptier_code})")
        spending_data[agency_name] = {"subagencies": {}, "federal_accounts": []}

        federal_accounts = get_federal_accounts(toptier_code)
        time.sleep(1)
        for account in federal_accounts[:5]:
            account_number = account['code']
            account_title = account['name']
            agency_code = account_number.split('-')[0]
            account_spending = get_account_spending(account_number, fiscal_year)
            functions = get_function_spending(account_number, fiscal_year, account_title, toptier_code)
            time.sleep(1)
            fy_obligated = account_spending.get('total_obligated_amount', account.get('obligated_amount', 0.0))

            if toptier_code == "097" and agency_code in dod_subtier_map:
                subtier_name = dod_subtier_map[agency_code]
                spending_data[agency_name]["subagencies"][subtier_name] = spending_data[agency_name]["subagencies"].get(subtier_name, 0.0) + fy_obligated

            spending_data[agency_name]["federal_accounts"].append({
                "account_number": account_number,
                "account_title": account_title,
                "obligated_amount": fy_obligated,
                "functions": functions
            })

    print("\n=== Spending Breakdown ===")
    for agency, data in spending_data.items():
        print(f"\nAgency: {agency}")
        print("  Subagencies:")
        for subtier, amount in data["subagencies"].items():
            print(f"    {subtier}: Obligated: ${amount:,.2f}")
        print("  Federal Accounts (FY 2024):")
        for account in data["federal_accounts"]:
            print(f"    {account['account_number']} - {account['account_title']} (Obligated: ${account['obligated_amount']:,.2f})")
            if account['functions']:
                print("      Budget Functions:")
                for func, amount in account['functions'].items():
                    print(f"        {func}: ${amount:,.2f}")

if __name__ == "__main__":
    main(2024)