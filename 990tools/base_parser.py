#!/usr/bin/env python3
"""
base_parser.py - Base parser class for IRS 990 form parsing

This module provides a base class for parsing IRS 990 forms (990, 990EZ, 990PF)
with common functionality and reduced code duplication.
"""

import sys
from lxml import etree  # type: ignore
from io import BytesIO
from nameparser import HumanName
from parse_utils import parse_int_field, parse_string_field, clean_name, MONEY_PATTERN, parse_float_field, parse_name_fast, split_zip_code
from models import Charity, Officer, Grant, Contractor, PoliticalContribution, Address
from typing import Optional, List, Tuple, Dict, Any, Callable
from logging_utils import log_info, log_error, log_debug, log_warning
from constants import DEBUG_EINS, ORG_TYPE_SUFFIXES
from config import global_config


class BaseParser:
    """Base class for IRS 990 form parsers"""

    def __init__(self, form_type: str, xpaths_dict: Dict[str, Any], namespaces: Dict[str, str]) -> None:
        self.form_type: str = form_type
        self.XPATHS: Dict[str, Any] = xpaths_dict
        self.NAMESPACES: Dict[str, str] = namespaces


    def get_xpaths_for_form(self, form_type: str) -> Dict[str, Any]:
        """Get the appropriate XPath dictionary based on form type"""
        if form_type == "990":
            from xpaths_990 import XPATHS_990
            return XPATHS_990
        elif form_type == "990EZ":
            from xpaths_990ez import XPATHS_990EZ
            return XPATHS_990EZ
        elif form_type == "990PF":
            from xpaths_990pf import XPATHS_990PF
            return XPATHS_990PF
        else:
            return self.XPATHS  # fallback



    def parse_org_type(
        self,
        root: etree._Element,
        field: str,
        namespaces: Dict[str, str],
        xml_filename: str,
        context: Dict[str, Any],
        xpath_cache: Dict[str, Any],
        form_type: str,
        xpath_match_stats: Optional[Dict[str, int]] = None
    ) -> str:
        """Parse organization type - to be implemented by subclasses"""
        raise NotImplementedError("Subclasses must implement parse_org_type")

    def parse_officer_comp(
        self,
        root: etree._Element,
        field: str,
        namespaces: Dict[str, str],
        xml_filename: str,
        context: Dict[str, Any],
        xpath_cache: Dict[str, Any],
        form_type: str,
        xpath_match_stats: Optional[Dict[str, int]] = None
    ) -> Tuple[int, List[Dict[str, Any]]]:
        """Parse officer compensation - optimized implementation"""
        total: int = 0
        officer_entries: List[Dict[str, Any]] = []

        xpaths_for_form = self.get_xpaths_for_form(form_type)

        # Check if this form type has officer compensation elements
        if not xpaths_for_form.get("officer_comp_elements"):
            return total, officer_entries  # No officer compensation for this form type

        # Use XPath union for better performance - get all officer elements at once
        from xpaths import ORG_TYPE_UNION_XPATH
        officer_xpath = etree.XPath(f".//irs:IRS{form_type}/irs:Form990PartVIISectionAGrp | .//irs:Form990PartVIISectionAGrp", namespaces=namespaces)
        elements: List[etree._Element] = officer_xpath(root)

        # Pre-resolve charity to avoid repeated getCharity() calls
        charity = context.getCharity()
        if not charity:
            return total, officer_entries

        for elem in elements:
            # Direct element access instead of parse_string_field for better performance
            name_elem = elem.find("irs:PersonNm", namespaces)
            if name_elem is None:
                name_elem = elem.find("PersonNm")
            comp_elem = elem.find("irs:ReportableCompFromOrgAmt", namespaces)
            if comp_elem is None:
                comp_elem = elem.find("ReportableCompFromOrgAmt")

            if name_elem is not None and comp_elem is not None:
                name_text = name_elem.text.strip()
                try:
                    comp_value = int(float(comp_elem.text.strip().replace(',', '')))
                    if comp_value > 0:
                        cleaned_name = clean_name(name_text)
                        first_name, last_name = parse_name_fast(cleaned_name)

                        officer_entries.append({
                            "first_name": first_name,
                            "last_name": last_name,
                            "full_name": name_text,  # Store original name for photo lookup
                            "amount": comp_value,
                            "ein": charity.ein,
                            "charity_name": charity.filer_name or 'Unknown',
                            "tax_year": charity.tax_year
                        })
                        total += comp_value

                        if not global_config.is_quiet():
                            log_info("Parsed officer {0} {1} compensation: ${2} for EIN {3} in {4}",
                                      first_name, last_name, comp_value, charity.ein, xml_filename)
                except (ValueError, AttributeError):
                    continue

        return total, officer_entries

    def parse_grants_to_others(
        self,
        root: etree._Element,
        field: str,
        namespaces: Dict[str, str],
        xml_filename: str,
        context: Dict[str, Any],
        xpath_cache: Dict[str, Any],
        form_type: str,
        xpath_match_stats: Optional[Dict[str, int]] = None
    ) -> int:
        """Parse grants to others - to be implemented by subclasses"""
        raise NotImplementedError("Subclasses must implement parse_grants_to_others")

    def parse_travel(
        self,
        root: etree._Element,
        field: str,
        namespaces: Dict[str, str],
        xml_filename: str,
        context: Dict[str, Any],
        xpath_cache: Dict[str, Any],
        form_type: str,
        xpath_match_stats: Optional[Dict[str, int]] = None
    ) -> int:
        """Parse travel expenses from TravelGrp/TotalAmt"""
        xpaths_for_form = self.get_xpaths_for_form(form_type)
        return parse_int_field(root, xpaths_for_form, field, namespaces, xml_filename, context, xpath_cache, xpath_match_stats=xpath_match_stats)

    def parse_conferences(
        self,
        root: etree._Element,
        field: str,
        namespaces: Dict[str, str],
        xml_filename: str,
        context: Dict[str, Any],
        xpath_cache: Dict[str, Any],
        form_type: str,
        xpath_match_stats: Optional[Dict[str, int]] = None
    ) -> int:
        """Parse conference expenses using XPath union for better performance"""
        from xpaths import CONFERENCES_UNION_XPATH

        # Get all conference elements in one query
        conference_elements: List[etree._Element] = CONFERENCES_UNION_XPATH(root)

        # Return the first valid element's text as integer
        for elem in conference_elements:
            if elem.text and elem.text.strip():
                try:
                    return int(elem.text.strip())
                except ValueError:
                    continue

        # Fallback to original method if union fails
        try:
            xpaths_for_form = self.get_xpaths_for_form(form_type)
            return parse_int_field(root, xpaths_for_form, field, namespaces, xml_filename, context, xpath_cache, xpath_match_stats=xpath_match_stats)
        except KeyError:
            # Field not available for this form type
            return 0

    def parse_receipt(
        self,
        root: etree._Element,
        field: str,
        namespaces: Dict[str, str],
        xml_filename: str,
        context: Dict[str, Any],
        xpath_cache: Dict[str, Any],
        form_type: str,
        xpath_match_stats: Optional[Dict[str, int]] = None
    ) -> int:
        """Parse receipt amount using XPath union for better performance"""
        from xpaths import RECEIPT_UNION_XPATH

        # Get all receipt elements in one query
        receipt_elements: List[etree._Element] = RECEIPT_UNION_XPATH(root)

        # Return the first valid element's text as integer
        for elem in receipt_elements:
            if elem.text and elem.text.strip():
                try:
                    return int(elem.text.strip())
                except ValueError:
                    continue

        # Fallback to original method if union fails
        xpaths_for_form = self.get_xpaths_for_form(form_type)
        return parse_int_field(root, xpaths_for_form, "receipt", namespaces, xml_filename, context, xpath_cache, xpath_match_stats=xpath_match_stats)

    def parse_govt_grants(
        self,
        root: etree._Element,
        field: str,
        namespaces: Dict[str, str],
        xml_filename: str,
        context: Dict[str, Any],
        xpath_cache: Dict[str, Any],
        form_type: str,
        xpath_match_stats: Optional[Dict[str, int]] = None
    ) -> int:
        """Parse government grants"""
        xpaths_for_form = self.get_xpaths_for_form(form_type)
        return parse_int_field(root, xpaths_for_form, field, namespaces, xml_filename, context, xpath_cache, xpath_match_stats=xpath_match_stats)

    def parse_contributions(
        self,
        root: etree._Element,
        field: str,
        namespaces: Dict[str, str],
        xml_filename: str,
        context: Dict[str, Any],
        xpath_cache: Dict[str, Any],
        form_type: str,
        xpath_match_stats: Optional[Dict[str, int]] = None
    ) -> int:
        """Parse contributions"""
        xpaths_for_form = self.get_xpaths_for_form(form_type)
        return parse_int_field(root, xpaths_for_form, field, namespaces, xml_filename, context, xpath_cache, xpath_match_stats=xpath_match_stats)

    def parse_total_exp(
        self,
        root: etree._Element,
        field: str,
        namespaces: Dict[str, str],
        xml_filename: str,
        context: Dict[str, Any],
        xpath_cache: Dict[str, Any],
        form_type: str,
        xpath_match_stats: Optional[Dict[str, int]] = None
    ) -> int:
        """Parse total expenses using XPath union for better performance"""
        from xpaths import TOTAL_EXP_UNION_XPATH

        # Get all total expenses elements in one query
        exp_elements: List[etree._Element] = TOTAL_EXP_UNION_XPATH(root)

        # Return the first valid element's text as integer
        for elem in exp_elements:
            if elem.text and elem.text.strip():
                try:
                    return int(elem.text.strip())
                except ValueError:
                    continue

        # Fallback to original method if union fails
        xpaths_for_form = self.get_xpaths_for_form(form_type)
        return parse_int_field(root, xpaths_for_form, field, namespaces, xml_filename, context, xpath_cache, xpath_match_stats=xpath_match_stats)

    def parse_prog_exp(
        self,
        root: etree._Element,
        field: str,
        namespaces: Dict[str, str],
        xml_filename: str,
        context: Dict[str, Any],
        xpath_cache: Dict[str, Any],
        form_type: str,
        xpath_match_stats: Optional[Dict[str, int]] = None
    ) -> int:
        """Parse program expenses using XPath union for better performance"""
        from xpaths import PROG_EXP_UNION_XPATH

        # Get all program expenses elements in one query
        prog_elements: List[etree._Element] = PROG_EXP_UNION_XPATH(root)

        # Return the first valid element's text as integer
        for elem in prog_elements:
            if elem.text and elem.text.strip():
                try:
                    return int(elem.text.strip())
                except ValueError:
                    continue

        # Fallback to original method if union fails
        xpaths_for_form = self.get_xpaths_for_form(form_type)
        return parse_int_field(root, xpaths_for_form, field, namespaces, xml_filename, context, xpath_cache, xpath_match_stats=xpath_match_stats)

    def parse_total_assets(
        self,
        root: etree._Element,
        field: str,
        namespaces: Dict[str, str],
        xml_filename: str,
        context: Dict[str, Any],
        xpath_cache: Dict[str, Any],
        form_type: str,
        xpath_match_stats: Optional[Dict[str, int]] = None
    ) -> int:
        """Parse total assets using XPath union for better performance"""
        from xpaths import TOTAL_ASSETS_UNION_XPATH

        # Get all total assets elements in one query
        assets_elements: List[etree._Element] = TOTAL_ASSETS_UNION_XPATH(root)

        # Return the first valid element's text as integer
        for elem in assets_elements:
            if elem.text and elem.text.strip():
                try:
                    return int(elem.text.strip())
                except ValueError:
                    continue

        # Fallback to original method if union fails
        xpaths_for_form = self.get_xpaths_for_form(form_type)
        return parse_int_field(root, xpaths_for_form, field, namespaces, xml_filename, context, xpath_cache, xpath_match_stats=xpath_match_stats)

    def parse_foreign_office(
        self,
        root: etree._Element,
        field: str,
        namespaces: Dict[str, str],
        xml_filename: str,
        context: Dict[str, Any],
        xpath_cache: Dict[str, Any],
        form_type: str,
        xpath_match_stats: Optional[Dict[str, int]] = None
    ) -> bool:
        """Parse foreign office indicator using XPath union for better performance"""
        from xpaths import FOREIGN_OFFICE_UNION_XPATH

        # Get all foreign office elements in one query
        office_elements: List[etree._Element] = FOREIGN_OFFICE_UNION_XPATH(root)

        # Return the first valid element's text
        for elem in office_elements:
            if elem.text and elem.text.strip():
                return elem.text.strip().upper() == 'X'

        # Fallback to original method if union fails
        try:
            xpaths_for_form = self.get_xpaths_for_form(form_type)
            elem = parse_string_field(root, xpaths_for_form, field, namespaces, xml_filename, context, xpath_cache, xpath_match_stats=xpath_match_stats, default=None)
            return elem.strip().upper() == 'X' if elem is not None else False
        except Exception as e:
            # 990PF forms don't have a 'foreign_office' field, so catch the exception and set to False
            log_debug(f"DEBUG: foreign_office field not found for {form_type} form, setting to False: {str(e)}")
            return False

    def parse_filer_name(
        self,
        root: etree._Element,
        field: str,
        namespaces: Dict[str, str],
        xml_filename: str,
        context: Dict[str, Any],
        xpath_cache: Dict[str, Any],
        form_type: str,
        xpath_match_stats: Optional[Dict[str, int]] = None
    ) -> str:
        """Parse filer name"""
        xpaths_for_form = self.get_xpaths_for_form(form_type)
        return parse_string_field(root, xpaths_for_form, field, namespaces, xml_filename, context, xpath_cache, xpath_match_stats=xpath_match_stats, default="Unknown")

    def parse_schedule_i(
        self,
        root: etree._Element,
        xml_filename: str,
        context: Any,
        xpath_cache: Dict[str, Any],
        charity: Optional[Charity] = None,
        form_type: Optional[str] = None,
        xpath_match_stats: Optional[Dict[str, int]] = None
    ) -> None:
        """Parse Schedule I (Grants to Organizations) - optimized with XPath unions for better performance"""
        from xpaths import GRANT_UNION_XPATH, GRANT_NAME_UNION_XPATH, GRANT_AMOUNT_UNION_XPATH

        # Get all grant elements in one query
        grant_elements: List[etree._Element] = GRANT_UNION_XPATH(root)

        # Process each grant element
        for grant_elem in grant_elements:
            name_elements: List[etree._Element] = GRANT_NAME_UNION_XPATH(grant_elem)
            amount_elements: List[etree._Element] = GRANT_AMOUNT_UNION_XPATH(grant_elem)

            grant_name: Optional[str] = None
            grant_amount: Optional[int] = None

            # Get first valid name
            for name_elem in name_elements:
                if name_elem.text and name_elem.text.strip():
                    grant_name = name_elem.text.strip()
                    break

            # Get first valid amount
            for amt_elem in amount_elements:
                if amt_elem.text and amt_elem.text.strip():
                    try:
                        grant_amount = int(amt_elem.text.strip())
                        break
                    except ValueError:
                        continue

            # Create grant if we have valid data
            if grant_name and grant_amount is not None:
                from models import Grant
                grant = Grant(
                    recipient_name=grant_name,
                    amount=grant_amount,
                    tax_year=charity.tax_year if charity else 0,
                    charity_id=charity.id if charity else None
                )
                context.addObjectToDatabase(grant)

        # Fallback to original method if no grants found via union
        if not grant_elements:
            from parse_schedule_i import parse_grants
            grants_data = parse_grants(root, xml_filename, charity.ein if charity else '', charity.filer_name if charity else '', charity.tax_year if charity else 0, set(), form_type, context=context)
    def parse_schedule_c(
        self,
        root: etree._Element,
        xml_filename: str,
        context: Any,
        xpath_cache: Dict[str, Any],
        charity: Optional[Charity] = None,
        form_type: Optional[str] = None,
        xpath_match_stats: Optional[Dict[str, int]] = None
    ) -> None:
        """Parse Schedule C (Political Contributions) - optimized to skip if no Schedule C exists"""
        # Quick check: if no Schedule C element exists, skip entirely
        schedule_c_check = etree.XPath(".//irs:ScheduleC", namespaces={'irs': 'http://www.irs.gov/efile'})
        if not schedule_c_check(root):
            return  # No Schedule C, nothing to parse

        # Only parse if Schedule C actually exists
        from parse_schedule_c import parse_contributions
        contributions_data = parse_contributions(root, xml_filename, charity.ein if charity else '', charity.filer_name if charity else '', charity.tax_year if charity else 0, form_type, context=context)
        # parse_contributions now adds contributions directly to context
    def parse_schedule_l(
        self,
        root: etree._Element,
        xml_filename: str,
        context: Any,
        xpath_cache: Dict[str, Any],
        charity: Optional[Charity] = None,
        form_type: Optional[str] = None,
        xpath_match_stats: Optional[Dict[str, int]] = None
    ) -> None:
        """Parse Schedule L (Contractors) - optimized direct element access for better performance"""
        # Use direct element access for contractor parsing - much faster than XPath unions
        # Contractors are typically in the top-level form data, not in separate schedules

        # Pre-resolve charity to avoid repeated getCharity() calls
        charity_obj = context.getCharity()
        if not charity_obj:
            return

        # Direct element access for contractor compensation groups
        contractor_groups = []
        contractor_groups.extend(root.findall(".//irs:ContractorCompensationGrp", namespaces={'irs': 'http://www.irs.gov/efile'}))
        contractor_groups.extend(root.findall(".//ContractorCompensationGrp"))
        contractor_groups.extend(root.findall(".//irs:CompensationOfHghstPdCntrctGrp", namespaces={'irs': 'http://www.irs.gov/efile'}))
        contractor_groups.extend(root.findall(".//CompensationOfHghstPdCntrctGrp"))

        for contractor_elem in contractor_groups:
            # Direct element access for name and compensation
            name_elem = contractor_elem.find("irs:ContractorName/irs:BusinessName/irs:BusinessNameLine1Txt", namespaces={'irs': 'http://www.irs.gov/efile'})
            if name_elem is None:
                name_elem = contractor_elem.find("ContractorName/BusinessName/BusinessNameLine1Txt")
            if name_elem is None:
                name_elem = contractor_elem.find("irs:ContractorName/irs:BusinessNameLine1Txt", namespaces={'irs': 'http://www.irs.gov/efile'})
            if name_elem is None:
                name_elem = contractor_elem.find("ContractorName/BusinessNameLine1Txt")
            if name_elem is None:
                name_elem = contractor_elem.find("irs:BusinessName/irs:BusinessNameLine1Txt", namespaces={'irs': 'http://www.irs.gov/efile'})
            if name_elem is None:
                name_elem = contractor_elem.find("BusinessName/BusinessNameLine1Txt")

            comp_elem = contractor_elem.find("irs:ContractorCompensationAmt", namespaces={'irs': 'http://www.irs.gov/efile'})
            if comp_elem is None:
                comp_elem = contractor_elem.find("ContractorCompensationAmt")
            if comp_elem is None:
                comp_elem = contractor_elem.find("irs:CompensationAmt", namespaces={'irs': 'http://www.irs.gov/efile'})
            if comp_elem is None:
                comp_elem = contractor_elem.find("CompensationAmt")

            if name_elem is not None and comp_elem is not None:
                name_text = name_elem.text.strip() if name_elem.text else None
                comp_text = comp_elem.text.strip() if comp_elem.text else None

                if name_text and comp_text:
                    try:
                        comp_value = int(float(comp_text.replace(',', '')))
                        if comp_value > 0:
                            from models import Contractor
                            contractor = Contractor(
                                name=name_text,
                                amount=comp_value,
                                tax_year=charity_obj.tax_year,
                                charity_id=charity_obj.id
                            )
                            context.addObjectToDatabase(contractor)

                            if not global_config.is_quiet():
                                log_info("Parsed contractor {0} compensation: ${1} for EIN {2} in {3}",
                                          name_text, comp_value, charity_obj.ein, xml_filename)
                    except (ValueError, AttributeError):
                        continue
    def parse_address(
        self,
        root: etree._Element,
        xml_filename: str,
        context: Dict[str, Any],
        xpath_cache: Dict[str, Any],
        charity: Optional[Charity] = None,
        form_type: Optional[str] = None,
        xpath_match_stats: Optional[Dict[str, int]] = None
    ) -> Optional[Address]:
        """Parse address information using XPath union for better performance"""
        try:
            from xpaths import ADDRESS_UNION_XPATH

            # Get all address elements in one query
            address_elements: List[etree._Element] = ADDRESS_UNION_XPATH(root)

            # Extract values by tag name
            address_line1: Optional[str] = None
            address_line2: Optional[str] = None
            city: Optional[str] = None
            state: Optional[str] = None
            zip_code_raw: Optional[str] = None

            for elem in address_elements:
                tag_name = elem.tag.split('}')[-1]  # Remove namespace prefix
                if tag_name in ('AddressLine1Txt', 'AddressLine1'):
                    address_line1 = elem.text.strip() if elem.text else None
                elif tag_name in ('AddressLine2Txt', 'AddressLine2'):
                    address_line2 = elem.text.strip() if elem.text else None
                elif tag_name == 'CityNm':
                    city = elem.text.strip() if elem.text else None
                elif tag_name == 'StateAbbreviationCd':
                    state = elem.text.strip() if elem.text else None
                elif tag_name == 'ZIPCd':
                    zip_code_raw = elem.text.strip() if elem.text else None

            # Split ZIP code into zip_code and zip4
            zip_code, zip4 = split_zip_code(zip_code_raw or '')

            # Check if we have at least some address components
            if any([address_line1, address_line2, city, state, zip_code]):
                # Charity must be available to build address - restructure if needed
                if charity is not None:
                    return charity.build_address(
                        address_line1=address_line1,
                        address_line2=address_line2,
                        city=city,
                        state=state,
                        zip_code=zip_code,
                        zip4=zip4
                    )
            return None
        except Exception as e:
            log_error("Failed to parse address for EIN {0} in {1}: {2}",
                      charity.ein if charity else 'Unknown', xml_filename, str(e))
            return None

    def calculate_percentage(self, value: Optional[float], denom: Optional[float]) -> float:
        """Calculate percentage safely"""
        if denom == 0 or value is None or denom is None:
            return 0.0
        return round((value / denom) * 100, 2)

    def get_field_parsers(self) -> List[Tuple[str, Callable]]:
        """Get list of field parsers - to be implemented by subclasses"""
        raise NotImplementedError("Subclasses must implement get_field_parsers")

    def parse_form(
        self,
        root: etree._Element,
        xml_filename: str,
        xpath_cache: Dict[str, Any],
        context: Any,
        xpath_match_stats: Optional[Dict[str, int]] = None,
        cached_charity: Optional[Charity] = None
    ) -> None:
        """Main parsing method - now uses context instead of returning tuples"""
        from pending_database_context import PendingDatabaseContext

        if not isinstance(context, PendingDatabaseContext):
            raise ValueError("context must be a PendingDatabaseContext instance")

        namespaces: Dict[str, str] = {'irs': 'http://www.irs.gov/efile'}

        # Use cached charity if provided, otherwise get from context
        charity = cached_charity if cached_charity is not None else context.getCharity()
        if not charity or not charity.ein or charity.ein == "Unknown":
            raise ValueError(f"Invalid EIN '{charity.ein if charity else 'None'}' for file {xml_filename}")

        # Ensure charity is always available throughout the method
        if charity is None:
            raise ValueError(f"No charity object available for file {xml_filename}")

        if charity.form_type != self.form_type:
            log_error("XML {0} is not a Form {1} (form_type: {2}), skipping",
                      xml_filename, self.form_type, charity.form_type)
            return

        # Re-raise exceptions after logging for better error handling
        try:
            # Get form-appropriate XPath collection
            xpaths_for_form = self.get_xpaths_for_form(charity.form_type)

            # Parse filer name components
            business_name_line1 = parse_string_field(root, xpaths_for_form, "business_name_line1", namespaces, xml_filename, context, xpath_cache, xpath_match_stats=xpath_match_stats, default=None)
            business_name_line2 = parse_string_field(root, xpaths_for_form, "business_name_line2", namespaces, xml_filename, context, xpath_cache, xpath_match_stats=xpath_match_stats, default=None)

            # Combine business name lines
            filer_name = f"{business_name_line1 or ''} {business_name_line2 or ''}".strip() or "Unknown"
            charity.filer_name = filer_name

            # Get field parsers from subclass
            fields = self.get_field_parsers()
            data: Dict[str, Any] = {}
            officer_entries: List[Dict[str, Any]] = []
            for field, func in fields:
                argcount = func.__code__.co_argcount
                if argcount == 9:
                    call_args = (root, field, namespaces, xml_filename, context, xpath_cache, charity.form_type)
                    call_kwargs = {'xpath_match_stats': xpath_match_stats}
                else:
                    call_args = (root, field, namespaces, xml_filename, context, xpath_cache)
                    call_kwargs = {'xpath_match_stats': xpath_match_stats}
                if field == "officer_comp":
                    total, entries = func(*call_args, **call_kwargs)
                    data[field] = total
                    officer_entries.extend(entries)
                else:
                    data[field] = func(*call_args, **call_kwargs)

            # Update XPathS to use form-appropriate collection for subsequent operations
            self.XPATHS = xpaths_for_form

            # Calculate percentages
            data["comp_pct"] = self.calculate_percentage(data["officer_comp"], data["total_exp"])
            data["travel_pct"] = self.calculate_percentage(data["travel"], data["total_exp"])
            data["conferences_pct"] = self.calculate_percentage(data.get("conferences", 0), data["total_exp"])
            data["grants_pct"] = self.calculate_percentage(data["grants_to_others"], data["total_exp"])
            data["foreign_expenses_pct"] = self.calculate_percentage(data.get("foreign_expenses", 0), data["total_exp"])
            data["grift_ratio"] = self.calculate_percentage(data["officer_comp"] + data["travel"] + data.get("conferences", 0), data["total_exp"])

            # Set common fields
            data["denominator"] = data["total_assets"] + data["receipt"]
            data["comp_ptile"] = None
            data["travel_ptile"] = None
            data["conferences_ptile"] = None
            data["grants_ptile"] = None

            # Set form-specific fields (to be overridden by subclasses)
            data = self.set_form_specific_fields(data)

            # Ensure all required fields are present with defaults
            required_fields = [
                "foreign_office", "foreign_expenses", "domestic_misrep_flag",
                "govt_grants", "contributions", "foreign_expenses_pct", "foreign_expenses_ptile"
            ]
            for field in required_fields:
                if field not in data:
                    if field in ["foreign_office", "domestic_misrep_flag"]:
                        data[field] = False
                    elif field in ["foreign_expenses_pct", "foreign_expenses_ptile"]:
                        data[field] = None
                    else:
                        data[field] = None

            # Update charity with parsed data
            charity.receipt_amt = data["receipt"]
            charity.govt_amt = data["govt_grants"]
            charity.contrib_amt = data["contributions"]
            charity.org_type = data["org_type"]
            charity.total_exp = data["total_exp"]
            charity.prog_exp = data["prog_exp"]
            charity.travel_amt = data["travel"]
            charity.conferences_amt = data.get("conferences", 0)
            charity.officer_comp = data["officer_comp"]
            charity.comp_pct = data["comp_pct"]
            charity.comp_ptile = data["comp_ptile"]
            charity.travel_pct = data["travel_pct"]
            charity.travel_ptile = data["travel_ptile"]
            charity.conferences_pct = data["conferences_pct"]
            charity.conferences_ptile = data["conferences_ptile"]
            charity.grants_pct = data["grants_pct"]
            charity.grants_ptile = data["grants_ptile"]
            charity.foreign_expenses_pct = data["foreign_expenses_pct"]
            charity.foreign_expenses_ptile = data["foreign_expenses_ptile"]
            charity.grift_ratio = data["grift_ratio"]
            charity.total_assets = data["total_assets"]
            charity.denominator = data["denominator"]
            charity.foreign_office = data.get("foreign_office", False)
            charity.foreign_expenses = data.get("foreign_expenses", None)
            charity.grants_to_others = data.get("grants_to_others", 0)
            charity.domestic_misrep_flag = data.get("domestic_misrep_flag", False)
            charity.xml_name = xml_filename

            # Create and add officers to context
            for entry in officer_entries:
                officer = Officer(
                    first_name=entry["first_name"],
                    last_name=entry["last_name"],
                    full_name=entry["full_name"],
                    compensation=entry["amount"],
                    tax_year=charity.tax_year,
                    charity_id=charity.id
                )
                context.addObjectToDatabase(officer)

            # Parse Schedule L (Contractors) - part of main form
            self.parse_schedule_l(root, xml_filename, context, xpath_cache, charity=charity, form_type=charity.form_type, xpath_match_stats=xpath_match_stats)

            # Parse related entities (grants, political contributions)
            self.parse_related_entities(root, xml_filename, context, xpath_cache, charity=charity, form_type=charity.form_type, xpath_match_stats=xpath_match_stats)

            # Parse address information and add to context
            address = self.parse_address(root, xml_filename, context, xpath_cache, charity=charity, form_type=charity.form_type, xpath_match_stats=xpath_match_stats)
            if address:
                context.addObjectToDatabase(address)

            # Debug logging for address components
            if address:
                log_debug("DEBUG: Address parsed for EIN {0}: line1='{1}', line2='{2}', city='{3}', state='{4}', zip='{5}', canonical='{6}'",
                          address.ein, address.address_line1, address.address_line2, address.city, address.state, address.zip_code, address.canonical_address)
            else:
                log_debug("DEBUG: No address parsed for EIN {0} in file {1}",
                          charity.ein, xml_filename)

            log_debug("TRACE: parse_{0}() completed parsing for EIN: '{1}' in file {2}",
                      self.form_type.lower(), charity.ein, xml_filename)

        except Exception as e:
            log_error("Error parsing form {0} for EIN {1} in {2}: {3}",
                      self.form_type, charity.ein if charity else 'Unknown', xml_filename, str(e))
            raise  # Re-raise the exception after logging


    def set_form_specific_fields(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Set form-specific fields - to be implemented by subclasses"""
        raise NotImplementedError("Subclasses must implement set_form_specific_fields")

    @classmethod
    def create_parser(cls, form_type: str) -> Optional['BaseParser']:
        """Factory method to create appropriate parser based on form type"""
        if form_type == "990":
            from parse_990 import Parser990
            return Parser990()
        elif form_type == "990EZ":
            from parse_990ez import Parser990EZ
            return Parser990EZ()
        elif form_type == "990PF":
            from parse_990pf import Parser990PF
            return Parser990PF()
        elif form_type == "990T":
            from parse_990t import Parser990T
            return Parser990T()
        else:
            return None

    def parse_related_entities(
        self,
        root: etree._Element,
        xml_filename: str,
        context: Any,
        xpath_cache: Dict[str, Any],
        charity: Optional[Charity] = None,
        form_type: Optional[str] = None,
        xpath_match_stats: Optional[Dict[str, int]] = None
    ) -> None:
        """Parse grants and political contributions using optimized XPath unions"""
        # Parse Schedule I (Grants) - already optimized with XPath unions
        self.parse_schedule_i(root, xml_filename, context, xpath_cache, charity=charity, form_type=form_type, xpath_match_stats=xpath_match_stats)

        # Parse Schedule C (Political Contributions) - already optimized to skip if no Schedule C exists
        self.parse_schedule_c(root, xml_filename, context, xpath_cache, charity=charity, form_type=form_type, xpath_match_stats=xpath_match_stats)


def main() -> None:
    """Main function for testing - to be implemented by subclasses"""
    raise NotImplementedError("Subclasses must implement main function")


if __name__ == "__main__":
    main()