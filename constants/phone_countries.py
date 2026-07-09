"""
Supported phone regions for user SMS numbers.

Use ISO 3166-1 alpha-2 region codes as returned by libphonenumber's
region_code_for_number(). Keep this list in sync with product support.
"""

_SUPPORTED_PHONE_REGION_ROWS = (
    ("US", "United States", "+1"), ("CA", "Canada", "+1"), ("PR", "Puerto Rico", "+1"),
    ("VG", "British Virgin Islands", "+1"), ("VI", "U.S. Virgin Islands", "+1"),
    ("IN", "India", "+91"), ("JP", "Japan", "+81"),
    ("AT", "Austria", "+43"), ("BE", "Belgium", "+32"), ("DK", "Denmark", "+45"),
    ("FR", "France", "+33"), ("DE", "Germany", "+49"), ("IS", "Iceland", "+354"),
    ("IE", "Ireland", "+353"), ("IM", "Isle of Man", "+44"), ("IT", "Italy", "+39"),
    ("NL", "Netherlands", "+31"), ("NO", "Norway", "+47"), ("PT", "Portugal", "+351"),
    ("ES", "Spain", "+34"), ("SE", "Sweden", "+46"), ("CH", "Switzerland", "+41"),
    ("UA", "Ukraine", "+380"), ("GB", "United Kingdom", "+44"),
    ("AR", "Argentina", "+54"), ("BR", "Brazil", "+55"), ("CL", "Chile", "+56"),
    ("EC", "Ecuador", "+593"), ("PE", "Peru", "+51"),
    ("AU", "Australia", "+61"), ("CC", "Cocos Islands", "+61"),
    ("CX", "Christmas Island", "+61"), ("NZ", "New Zealand", "+64"),
)

SUPPORTED_PHONE_REGIONS = tuple(
    {"region": region, "name": name, "dialCode": dial_code}
    for region, name, dial_code in _SUPPORTED_PHONE_REGION_ROWS
)
SUPPORTED_REGION_CODES = {country["region"] for country in SUPPORTED_PHONE_REGIONS}


def serialize_supported_phone_regions() -> list[dict[str, str]]:
    return [dict(country) for country in SUPPORTED_PHONE_REGIONS]
