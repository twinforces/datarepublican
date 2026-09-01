# Class-Based Refactor Plan for IRS 990 Pipeline

## Current Issues with List-Based Approach

The current codebase extensively uses lists and dictionaries to pass around data, leading to:

1. **Index-based access**: Code like `row[0]`, `row[1]` is error-prone and hard to maintain
2. **Magic numbers**: Column positions are hardcoded throughout the codebase
3. **Type safety**: No validation of data types or required fields
4. **Code duplication**: Similar patterns repeated across parsing functions
5. **Maintenance burden**: Adding/changing fields requires updating multiple places

## Proposed Class-Based Design

### Core Data Classes

#### Charity Class
```python
@dataclass
class Charity:
    tax_year: int
    filer_ein: str
    filer_name: str
    receipt_amt: Optional[float]
    govt_amt: Optional[float]
    contrib_amt: Optional[float]
    org_type: str
    total_exp: Optional[float]
    prog_exp: Optional[float]
    travel_amt: Optional[float]
    conferences_amt: Optional[float]
    officer_comp: Optional[float]
    comp_pct: Optional[float]
    comp_ptile: Optional[float]
    travel_pct: Optional[float]
    travel_ptile: Optional[float]
    conferences_pct: Optional[float]
    conferences_ptile: Optional[float]
    grants_pct: Optional[float]
    grants_ptile: Optional[float]
    foreign_expenses_pct: Optional[float]
    foreign_expenses_ptile: Optional[float]
    grift_ratio: Optional[float]
    total_assets: Optional[float]
    form_type: str
    denominator: Optional[float]
    foreign_office: Optional[str]
    foreign_expenses: Optional[float]
    grants_to_others: Optional[float]
    domestic_misrep_flag: Optional[str]
    xml_name: str
    canonical_address: Optional[str]
    mailing_zip: Optional[str]
    colocator: Optional[str]

    @property
    def is_high_grift(self) -> bool:
        return self.grift_ratio and self.grift_ratio > 100

    @property
    def has_foreign_activities(self) -> bool:
        return bool(self.foreign_office or self.foreign_expenses)

    def to_row(self) -> List[Any]:
        """Convert to TSV row format for backward compatibility"""
        return [
            self.tax_year, self.filer_ein, self.filer_name, self.receipt_amt,
            self.govt_amt, self.contrib_amt, self.org_type, self.total_exp,
            self.prog_exp, self.travel_amt, self.conferences_amt, self.officer_comp,
            self.comp_pct, self.comp_ptile, self.travel_pct, self.travel_ptile,
            self.conferences_pct, self.conferences_ptile, self.grants_pct,
            self.grants_ptile, self.foreign_expenses_pct, self.foreign_expenses_ptile,
            self.grift_ratio, self.total_assets, self.form_type, self.denominator,
            self.foreign_office, self.foreign_expenses, self.grants_to_others,
            self.domestic_misrep_flag, self.xml_name, self.canonical_address,
            self.mailing_zip, self.colocator
        ]
```

#### Grant Class
```python
@dataclass
class Grant:
    filer_ein: str
    filer_name: str
    grant_ein: Optional[str]
    grant_amt: float
    tax_year: int
    filer_colocator: Optional[str]
    grantee_colocator: Optional[str]

    @property
    def is_domestic(self) -> bool:
        return bool(self.grant_ein and len(self.grant_ein) == 9)

    @property
    def is_foreign(self) -> bool:
        return not self.is_domestic
```

#### Address Class
```python
@dataclass
class Address:
    filer_ein: str
    filer_name: str
    canonical_address: str
    po_box: Optional[str]
    zip_code: Optional[str]
    address_dict: Dict[str, Any]

    @property
    def has_po_box(self) -> bool:
        return bool(self.po_box)

    @property
    def is_valid_zip(self) -> bool:
        return bool(self.zip_code and ZIP_REGEX.match(self.zip_code))
```

#### Contribution Class
```python
@dataclass
class Contribution:
    filer_ein: str
    filer_name: str
    recipient_ein: Optional[str]
    amount: float
    tax_year: int
```

### Parsing Interface

#### Abstract Base Parser
```python
from abc import ABC, abstractmethod
from typing import List, Tuple, Any

class CharityParser(ABC):
    @abstractmethod
    def parse_xml(self, root, xml_filename: str, xpath_cache: dict,
                  filer_ein: str, tax_year: int, form_type: str) -> Tuple[Charity, List[Officer], List[Contractor], List[Contribution]]:
        pass

    @abstractmethod
    def get_form_type(self) -> str:
        pass
```

#### Concrete Parsers
```python
class Form990Parser(CharityParser):
    def get_form_type(self) -> str:
        return "990"

    def parse_xml(self, root, xml_filename: str, xpath_cache: dict,
                  filer_ein: str, tax_year: int, form_type: str) -> Tuple[Charity, List[Officer], List[Contractor], List[Contribution]]:
        # Parse using XPATHS_990
        # Return Charity object instead of list
        pass

class Form990EZParser(CharityParser):
    def get_form_type(self) -> str:
        return "990EZ"
    # Similar implementation

class Form990PFParser(CharityParser):
    def get_form_type(self) -> str:
        return "990PF"
    # Similar implementation
```

## Implementation Plan

### Phase 1: Core Classes (Week 1)
1. Create `models.py` with dataclass definitions
2. Add type hints and validation
3. Implement `to_row()` methods for backward compatibility
4. Add basic property methods

### Phase 2: Parser Refactor (Week 2-3)
1. Create abstract `CharityParser` base class
2. Refactor `parse_990.py`, `parse_990ez.py`, `parse_990pf.py` to return objects
3. Update `extract_charities.py` to work with objects
4. Maintain backward compatibility with `to_row()` methods

### Phase 3: Grant/Address Processing (Week 4)
1. Update grant parsing in `parse_utils.py` to return `Grant` objects
2. Update address processing to return `Address` objects
3. Refactor `extract_grants.py` and `extract_addresses.py`

### Phase 4: Integration and Testing (Week 5)
1. Update pipeline functions to work with objects
2. Add comprehensive tests
3. Performance benchmarking
4. Documentation updates

## Benefits

### Type Safety
- Compile-time checking of field access
- IDE autocompletion and refactoring support
- Clear contracts between functions

### Maintainability
- Adding new fields requires updating class definition only
- Property methods encapsulate business logic
- Clear separation of concerns

### Code Reduction
- Eliminate index-based access patterns
- Reduce magic numbers and hardcoded positions
- Centralized field definitions

### Error Prevention
- Validation at object creation time
- Immutable dataclass prevents accidental modification
- Clear error messages for missing required fields

### Developer Experience
- Self-documenting code with named fields
- Easier debugging with structured data
- Better testability with mock objects

## Migration Strategy

### Backward Compatibility
- Keep existing TSV column order and naming
- Maintain `to_row()` methods for file output
- Gradual migration of internal processing

### Testing Approach
- Unit tests for each class and method
- Integration tests for parsing functions
- Performance regression tests
- End-to-end pipeline tests

### Rollback Plan
- Feature flags to switch between old/new approaches
- Database migration scripts if needed
- Clear documentation of breaking changes

## Success Metrics

1. **Code Quality**: Reduce lines of code by 20-30%
2. **Maintainability**: New field additions take <1 hour vs current 4+ hours
3. **Reliability**: Reduce index-related bugs by 90%
4. **Performance**: No degradation in processing speed
5. **Developer Velocity**: Faster onboarding and feature development