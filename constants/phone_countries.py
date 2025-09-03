"""
Supported phone regions for user SMS numbers.

Use ISO 3166-1 alpha-2 region codes as returned by libphonenumber's
region_code_for_number(). Keep this list in sync with product support.
"""

# North America
SUPPORTED_REGION_CODES = {
    # Canada, Puerto Rico, USA, Virgin Islands (British/U.S.)
    "CA", "PR", "US", "VG", "VI",

    # Asia
    "IN", "JP",

    # Europe
    "AT", "BE", "DK", "FR", "DE", "IS", "IE", "IM", "IT", "NL", "NO",
    "PT", "ES", "SE", "CH", "UA", "GB",

    # South America
    "AR", "BR", "CL", "EC", "PE",

    # Oceania (Australia, Cocos, Christmas), New Zealand
    "AU", "CC", "CX", "NZ",
}

