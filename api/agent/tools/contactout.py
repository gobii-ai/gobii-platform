"""Native ContactOut people and company sourcing tool."""

import logging
import re
from datetime import date
from typing import Any, Optional
from urllib.parse import urlparse

import requests
from django.conf import settings
from requests import Response
from requests.exceptions import RequestException, Timeout

from api.services.contactout_feature_flags import contactout_enabled_for_agent


logger = logging.getLogger(__name__)

CONTACTOUT_API_URL = "https://api.contactout.com"
CONTACTOUT_TOOL_NAME = "contactout"
CONTACTOUT_SYSTEM_SKILL_KEY = "contactout"

SEARCH_PEOPLE = "search_people"
COUNT_PEOPLE = "count_people"
ENRICH_LINKEDIN_PROFILE = "enrich_linkedin_profile"
SEARCH_COMPANIES = "search_companies"
ENRICH_COMPANY_DOMAINS = "enrich_company_domains"

CONTACTOUT_OPERATIONS = (
    SEARCH_PEOPLE,
    COUNT_PEOPLE,
    ENRICH_LINKEDIN_PROFILE,
    SEARCH_COMPANIES,
    ENRICH_COMPANY_DOMAINS,
)

_OPERATION_REQUESTS = {
    SEARCH_PEOPLE: ("POST", "/v1/people/search"),
    COUNT_PEOPLE: ("POST", "/v1/people/count"),
    ENRICH_LINKEDIN_PROFILE: ("GET", "/v1/linkedin/enrich"),
    SEARCH_COMPANIES: ("POST", "/v1/company/search"),
    ENRICH_COMPANY_DOMAINS: ("POST", "/v1/domain/enrich"),
}

_CONTACT_DATA_TYPES = {"personal_email", "work_email", "phone"}
_COMPANY_SIZE_RANGES = (
    "1_10",
    "11_50",
    "51_200",
    "201_500",
    "501_1000",
    "1001_5000",
    "5001_10000",
    "10001",
)
_YEAR_RANGE_PATTERN = re.compile(r"^(\d+)(?:_(\d+))?$")
_PEOPLE_FILTER_KEYS = {
    "page",
    "page_size",
    "name",
    "job_title",
    "exclude_job_titles",
    "current_titles_only",
    "include_related_job_titles",
    "match_experience",
    "job_function",
    "seniority",
    "skills",
    "languages",
    "education",
    "educations",
    "location",
    "location_radius",
    "current_work_location",
    "past_work_location",
    "company",
    "exclude_companies",
    "exclude_companies_filter",
    "company_filter",
    "current_company_only",
    "domain",
    "industry",
    "keyword",
    "company_size",
    "years_of_experience",
    "years_in_current_role",
    "recently_changed_jobs",
    "detailed_experience",
    "detailed_education",
    "output_fields",
}
_COUNT_IGNORED_FILTERS = {
    "page",
    "page_size",
    "detailed_experience",
    "detailed_education",
    "output_fields",
}
_COMPANY_FILTER_KEYS = {
    "page",
    "linkedin_url",
    "name",
    "domain",
    "size",
    "hq_only",
    "location",
    "industry",
    "technologies",
    "min_revenue",
    "max_revenue",
    "year_founded_from",
    "year_founded_to",
}
_PEOPLE_LIST_LIMITS = {
    "job_title": 50,
    "exclude_job_titles": 50,
    "job_function": 50,
    "seniority": 50,
    "skills": 50,
    "languages": 50,
    "education": 50,
    "educations": 50,
    "location": 50,
    "current_work_location": 50,
    "past_work_location": 50,
    "company": 50,
    "exclude_companies": 50,
    "domain": 50,
    "industry": 50,
    "company_size": 8,
    "years_of_experience": 20,
    "years_in_current_role": 20,
    "output_fields": 16,
}
_COMPANY_LIST_LIMITS = {
    "linkedin_url": 25,
    "name": 50,
    "domain": 50,
    "size": 50,
    "location": 50,
    "industry": 50,
    "technologies": 50,
}
_OUTPUT_FIELDS = (
    "li_vanity",
    "full_name",
    "title",
    "headline",
    "company",
    "company.name",
    "company.website",
    "company.headquarter",
    "company.domain",
    "company.size",
    "location",
    "industry",
    "experience",
    "education",
    "skills",
    "profile_picture_url",
)


def _array_schema(*, max_items: int = 50, item_schema: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    return {
        "type": "array",
        "items": item_schema or {"type": "string"},
        "minItems": 1,
        "maxItems": max_items,
    }


def _year_range_schema(*, subject: str) -> dict[str, Any]:
    return _array_schema(
        max_items=20,
        item_schema={
            "type": "string",
            "pattern": r"^\d+(?:_\d+)?$",
            "description": (
                f"An inclusive {subject} range in X_Y form, such as 5_30. "
                "ContactOut also accepts a single threshold such as 10."
            ),
        },
    )


def _people_filter_schema() -> dict[str, Any]:
    properties = {
        "page": {"type": "integer", "minimum": 1, "default": 1},
        "page_size": {"type": "integer", "minimum": 1, "maximum": 25, "default": 25},
        "name": {"type": "string"},
        "job_title": _array_schema(),
        "exclude_job_titles": _array_schema(),
        "current_titles_only": {"type": "boolean", "default": True},
        "include_related_job_titles": {"type": "boolean", "default": False},
        "match_experience": {
            "type": "string",
            "enum": ["current", "past", "both"],
            "description": (
                "Require job_title and company to match the same experience entry. "
                "Do not combine with current_titles_only or company_filter."
            ),
        },
        "job_function": _array_schema(),
        "seniority": _array_schema(),
        "skills": _array_schema(),
        "languages": _array_schema(
            item_schema={
                "type": "object",
                "properties": {
                    "language": {"type": "string"},
                    "proficiency": _array_schema(),
                },
                "required": ["language"],
                "additionalProperties": False,
            }
        ),
        "education": _array_schema(),
        "educations": _array_schema(
            item_schema={
                "type": "object",
                "properties": {
                    "school_name": {"type": "string"},
                    "field_of_study": {"type": "string"},
                    "location": {"type": "string"},
                },
                "minProperties": 1,
                "additionalProperties": False,
            }
        ),
        "location": _array_schema(),
        "location_radius": {
            "type": "integer",
            "minimum": 1,
            "maximum": 500,
            "description": "Radius in miles. Requires a city or area in location.",
        },
        "current_work_location": _array_schema(),
        "past_work_location": _array_schema(),
        "company": _array_schema(),
        "exclude_companies": _array_schema(),
        "exclude_companies_filter": {"type": "string", "enum": ["current", "past", "both"]},
        "company_filter": {"type": "string", "enum": ["current", "past", "past_only", "both"]},
        "current_company_only": {"type": "boolean", "default": True},
        "domain": _array_schema(),
        "industry": _array_schema(),
        "keyword": {"type": "string"},
        "company_size": _array_schema(
            max_items=8,
            item_schema={"type": "string", "enum": list(_COMPANY_SIZE_RANGES)},
        ),
        "years_of_experience": _year_range_schema(subject="years-of-experience"),
        "years_in_current_role": {
            **_year_range_schema(subject="years-in-current-role"),
            "description": "Cannot be combined with recently_changed_jobs=true.",
        },
        "recently_changed_jobs": {"type": "boolean", "default": False},
        "detailed_experience": {"type": "boolean", "default": False},
        "detailed_education": {"type": "boolean", "default": False},
        "output_fields": _array_schema(
            max_items=16,
            item_schema={"type": "string", "enum": list(_OUTPUT_FIELDS)},
        ),
    }
    return {"type": "object", "properties": properties, "additionalProperties": False}


def _company_filter_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "page": {"type": "integer", "minimum": 1, "default": 1},
            "linkedin_url": {
                **_array_schema(max_items=25),
                "description": (
                    "Company LinkedIn URLs, bare company slugs, or numeric company IDs. "
                    "Cannot be combined with other company search filters."
                ),
            },
            "name": _array_schema(),
            "domain": _array_schema(),
            "size": _array_schema(
                item_schema={"type": "string", "enum": list(_COMPANY_SIZE_RANGES)},
            ),
            "hq_only": {"type": "boolean"},
            "location": _array_schema(),
            "industry": _array_schema(),
            "technologies": _array_schema(),
            "min_revenue": {"type": "integer"},
            "max_revenue": {"type": "integer"},
            "year_founded_from": {"type": "integer", "minimum": 1985},
            "year_founded_to": {
                "type": "integer",
                "maximum": date.today().year,
                "description": "Maximum founding year. Requires year_founded_from.",
            },
        },
        "additionalProperties": False,
    }


def get_contactout_tool() -> dict[str, Any]:
    """Return the single native tool definition used by the pilot."""
    return {
        "type": "function",
        "function": {
            "name": CONTACTOUT_TOOL_NAME,
            "description": (
                "Search or count people, enrich a known LinkedIn person profile, search companies, or enrich "
                "company domains through ContactOut. Read-only. Contact emails and phones are hidden unless "
                "reveal_all_contact_info is explicitly true, which reveals every available contact category."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "operation": {"type": "string", "enum": list(CONTACTOUT_OPERATIONS)},
                    "people_filters": _people_filter_schema(),
                    "company_filters": _company_filter_schema(),
                    "linkedin_url": {
                        "type": "string",
                        "format": "uri",
                        "description": "Regular LinkedIn /in/ or /pub/ person profile URL.",
                    },
                    "reveal_all_contact_info": {
                        "type": "boolean",
                        "default": False,
                        "description": (
                            "Explicit authorization to reveal all available personal emails, work emails, and "
                            "phone numbers. ContactOut cannot reveal only a subset."
                        ),
                    },
                    "required_contact_data_types": {
                        "type": "array",
                        "items": {"type": "string", "enum": sorted(_CONTACT_DATA_TYPES)},
                        "minItems": 1,
                        "uniqueItems": True,
                        "description": (
                            "Availability filter only: require profiles to have at least one selected contact type. "
                            "This does not limit fields returned or billed when reveal_all_contact_info is true."
                        ),
                    },
                    "domains": {
                        "type": "array",
                        "items": {"type": "string"},
                        "minItems": 1,
                        "maxItems": 30,
                    },
                },
                "required": ["operation"],
                "additionalProperties": False,
            },
        },
    }


def _error(message: str, *, operation: str = "", retryable: bool = False, **details: Any) -> dict[str, Any]:
    result = {
        "status": "error",
        "provider": "contactout",
        "message": message,
        "retryable": retryable,
        **details,
    }
    if operation:
        result["operation"] = operation
    return result


def _is_nonempty(value: Any) -> bool:
    return value not in (None, False, "", [], {})


def _omit_empty_optional_filters(filters: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in filters.items()
        if value not in (None, "", [], {})
    }


def _validate_list_limits(values: dict[str, Any], limits: dict[str, int]) -> Optional[str]:
    for field, limit in limits.items():
        value = values.get(field)
        if value is None:
            continue
        if not isinstance(value, list):
            return f"{field} must be an array."
        if len(value) > limit:
            return f"{field} accepts at most {limit} values."
    return None


def _validate_year_ranges(filters: dict[str, Any]) -> Optional[str]:
    for field in ("years_of_experience", "years_in_current_role"):
        for value in filters.get(field, []):
            if not isinstance(value, str):
                return f"{field} values must be strings in X_Y form, such as 5_30."
            match = _YEAR_RANGE_PATTERN.fullmatch(value)
            if not match:
                return f"{field} values must use X_Y form, such as 5_30, or a single threshold."
            lower = int(match.group(1))
            upper = int(match.group(2)) if match.group(2) is not None else None
            if upper is not None and lower > upper:
                return f"{field} range minimum cannot exceed its maximum: {value}."
    return None


def _validate_educations(filters: dict[str, Any]) -> Optional[str]:
    allowed_fields = {"school_name", "field_of_study", "location"}
    for index, education in enumerate(filters.get("educations", [])):
        if not isinstance(education, dict):
            return f"educations[{index}] must be an object."
        unknown = sorted(set(education) - allowed_fields)
        if unknown:
            return f"Unsupported educations[{index}] fields: {', '.join(unknown)}."
        invalid_types = [field for field, value in education.items() if not isinstance(value, str)]
        if invalid_types:
            return f"educations[{index}] fields must be strings: {', '.join(invalid_types)}."
        if not any(value.strip() for value in education.values()):
            return f"educations[{index}] must include school_name, field_of_study, or location."
    return None


def _validate_company_sizes(values: dict[str, Any], field: str) -> Optional[str]:
    invalid = [value for value in values.get(field, []) if value not in _COMPANY_SIZE_RANGES]
    if invalid:
        return f"{field} contains unsupported company size ranges: {', '.join(map(str, invalid))}."
    return None


def _validate_bounded_integer(
    values: dict[str, Any],
    field: str,
    minimum: int,
    maximum: Optional[int] = None,
) -> Optional[str]:
    value = values.get(field)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        return f"{field} must be an integer."
    if value < minimum or (maximum is not None and value > maximum):
        upper = f" and {maximum}" if maximum is not None else ""
        return f"{field} must be between {minimum}{upper}." if upper else f"{field} must be at least {minimum}."
    return None


def _validate_people_filters(raw_filters: Any) -> tuple[Optional[dict[str, Any]], Optional[str]]:
    if raw_filters is None:
        return {}, None
    if not isinstance(raw_filters, dict):
        return None, "people_filters must be an object."
    unknown = sorted(set(raw_filters) - _PEOPLE_FILTER_KEYS)
    if unknown:
        return None, f"Unsupported people_filters: {', '.join(unknown)}."

    filters = _omit_empty_optional_filters(raw_filters)
    list_error = _validate_list_limits(filters, _PEOPLE_LIST_LIMITS)
    if list_error:
        return None, list_error
    range_error = _validate_year_ranges(filters)
    if range_error:
        return None, range_error
    education_error = _validate_educations(filters)
    if education_error:
        return None, education_error
    company_size_error = _validate_company_sizes(filters, "company_size")
    if company_size_error:
        return None, company_size_error
    for field, minimum, maximum in (("page", 1, None), ("page_size", 1, 25), ("location_radius", 1, 500)):
        integer_error = _validate_bounded_integer(filters, field, minimum, maximum)
        if integer_error:
            return None, integer_error

    enum_fields = {
        "match_experience": {"current", "past", "both"},
        "exclude_companies_filter": {"current", "past", "both"},
        "company_filter": {"current", "past", "past_only", "both"},
    }
    for field, choices in enum_fields.items():
        value = filters.get(field)
        if value is not None and value not in choices:
            return None, f"{field} must be one of: {', '.join(sorted(choices))}."

    if "match_experience" in filters:
        conflicting = [field for field in ("current_titles_only", "company_filter") if field in filters]
        if conflicting:
            return None, f"match_experience cannot be combined with {', '.join(conflicting)}."
    if "location_radius" in filters and not filters.get("location"):
        return None, "location_radius requires location."
    if filters.get("years_in_current_role") and filters.get("recently_changed_jobs") is True:
        return None, "years_in_current_role cannot be combined with recently_changed_jobs=true."
    return filters, None


def _validate_linkedin_url(value: Any) -> tuple[Optional[str], Optional[str]]:
    label = "LinkedIn profile URL"
    if not isinstance(value, str) or not value.strip():
        return None, f"A {label} is required."
    raw = value.strip()
    try:
        parsed = urlparse(raw)
        hostname = (parsed.hostname or "").lower()
    except ValueError:
        return None, f"{label.capitalize()} must be a fully formed linkedin.com URL."
    if parsed.scheme.lower() not in {"http", "https"} or not (
        hostname == "linkedin.com" or hostname.endswith(".linkedin.com")
    ):
        return None, f"{label.capitalize()} must be a fully formed linkedin.com URL."
    lowered = raw.lower()
    if "sales.linkedin.com" in lowered or "/sales/" in lowered or "/recruiter/" in lowered:
        return None, "Sales Navigator and Recruiter URLs are not supported."
    prefixes = ("/in/", "/pub/")
    if not any(parsed.path.lower().startswith(prefix) for prefix in prefixes):
        return None, f"{label.capitalize()} must use a regular /in/ or /pub/ path."
    if parsed.path.rstrip("/").lower() in {prefix.rstrip("/") for prefix in prefixes}:
        return None, f"{label.capitalize()} must identify a profile."
    return raw, None


_COMPANY_LINKEDIN_IDENTIFIER = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._-]*$")


def _validate_company_linkedin_identifier(value: Any) -> tuple[Optional[str], Optional[str]]:
    if not isinstance(value, str) or not value.strip():
        return None, "Company LinkedIn identifiers must be non-empty strings."
    raw = value.strip()
    if _COMPANY_LINKEDIN_IDENTIFIER.fullmatch(raw):
        return raw, None

    try:
        parsed = urlparse(raw if "://" in raw else f"https://{raw}")
        hostname = (parsed.hostname or "").lower()
    except ValueError:
        return None, f"Invalid company LinkedIn identifier: {raw}."
    if parsed.scheme.lower() not in {"http", "https"} or not (
        hostname == "linkedin.com" or hostname.endswith(".linkedin.com")
    ):
        return None, f"Invalid company LinkedIn identifier: {raw}."
    if not parsed.path.lower().startswith("/company/") or parsed.path.rstrip("/").lower() == "/company":
        return None, "Company LinkedIn URLs must identify a /company/ profile."
    return raw, None


def _validate_company_filters(raw_filters: Any) -> tuple[Optional[dict[str, Any]], Optional[str]]:
    if raw_filters is None:
        return {}, None
    if not isinstance(raw_filters, dict):
        return None, "company_filters must be an object."
    unknown = sorted(set(raw_filters) - _COMPANY_FILTER_KEYS)
    if unknown:
        return None, f"Unsupported company_filters: {', '.join(unknown)}."
    filters = _omit_empty_optional_filters(raw_filters)
    list_error = _validate_list_limits(filters, _COMPANY_LIST_LIMITS)
    if list_error:
        return None, list_error
    company_size_error = _validate_company_sizes(filters, "size")
    if company_size_error:
        return None, company_size_error
    page_error = _validate_bounded_integer(filters, "page", 1)
    if page_error:
        return None, page_error
    founded_error = _validate_bounded_integer(filters, "year_founded_from", 1985)
    if founded_error:
        return None, founded_error

    linkedin_urls = filters.get("linkedin_url") or []
    if linkedin_urls:
        other_filters = [
            key
            for key, value in filters.items()
            if key not in {"linkedin_url", "page"} and _is_nonempty(value)
        ]
        if other_filters:
            return None, "company_filters.linkedin_url cannot be combined with other company filters."
        normalized_urls = []
        for url in linkedin_urls:
            normalized_url, url_error = _validate_company_linkedin_identifier(url)
            if url_error:
                return None, url_error
            normalized_urls.append(normalized_url)
        filters["linkedin_url"] = normalized_urls

    for integer_field in ("min_revenue", "max_revenue", "year_founded_to"):
        value = filters.get(integer_field)
        if value is not None and (isinstance(value, bool) or not isinstance(value, int)):
            return None, f"{integer_field} must be an integer."
    year_founded_to = filters.get("year_founded_to")
    if year_founded_to is not None:
        if "year_founded_from" not in filters:
            return None, "year_founded_to requires year_founded_from."
        if year_founded_to > date.today().year:
            return None, f"year_founded_to cannot exceed {date.today().year}."
    for lower_field, upper_field in (
        ("min_revenue", "max_revenue"),
        ("year_founded_from", "year_founded_to"),
    ):
        lower = filters.get(lower_field)
        upper = filters.get(upper_field)
        if lower is not None and upper is not None and lower > upper:
            return None, f"{upper_field} must be greater than or equal to {lower_field}."
    return filters, None


_DOMAIN_LABEL = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")


def _normalize_domain(value: Any) -> tuple[Optional[str], Optional[str]]:
    if not isinstance(value, str) or not value.strip():
        return None, "domains must contain non-empty hostnames or URLs."
    raw = value.strip()
    try:
        parsed = urlparse(raw if "://" in raw else f"//{raw}")
        hostname = (parsed.hostname or "").lower().rstrip(".")
    except ValueError:
        return None, f"Invalid domain: {raw}."
    if hostname.startswith("www."):
        hostname = hostname[4:]
    labels = hostname.split(".")
    if len(labels) < 2 or any(not _DOMAIN_LABEL.fullmatch(label) for label in labels):
        return None, f"Invalid domain: {raw}."
    return hostname, None


def _normalize_domains(raw_domains: Any) -> tuple[Optional[list[str]], Optional[str]]:
    if not isinstance(raw_domains, list) or not raw_domains or len(raw_domains) > 30:
        return None, "domains must contain between 1 and 30 values."
    normalized = []
    seen = set()
    for value in raw_domains:
        domain, domain_error = _normalize_domain(value)
        if domain_error:
            return None, domain_error
        if domain not in seen:
            seen.add(domain)
            normalized.append(domain)
    if len(normalized) > 30:
        return None, "domains must contain between 1 and 30 unique values."
    return normalized, None


def _validate_contact_types(raw_types: Any) -> tuple[Optional[list[str]], Optional[str]]:
    if raw_types is None:
        return [], None
    if not isinstance(raw_types, list):
        return None, "required_contact_data_types must be an array."
    invalid = sorted({str(value) for value in raw_types} - _CONTACT_DATA_TYPES)
    if invalid:
        return None, f"Unsupported required_contact_data_types: {', '.join(invalid)}."
    return list(dict.fromkeys(raw_types)), None


def _request_headers() -> dict[str, str]:
    return {
        "token": settings.CONTACTOUT_API_TOKEN,
        "Accept": "application/json",
        "Content-Type": "application/json",
        "User-Agent": "gobii-platform/contactout-native",
    }


def _response_detail(response: Response, payload: Any = None) -> str:
    detail = ""
    if isinstance(payload, dict):
        detail = str(payload.get("message") or payload.get("error") or "").strip()
    if not detail and isinstance(response.text, str):
        detail = response.text.strip()
    if len(detail) > 500:
        return f"{detail[:500]}..."
    return detail


def _retry_after(response: Response) -> Optional[str]:
    headers = response.headers
    value = headers.get("Retry-After") if hasattr(headers, "get") else None
    return str(value) if value not in (None, "") else None


def _response_error(
    response: Response,
    operation: str,
    payload: Any = None,
    *,
    status_code: Optional[int] = None,
) -> dict[str, Any]:
    code = response.status_code if status_code is None else status_code
    detail = _response_detail(response, payload)
    message = f"ContactOut API returned HTTP {code}."
    if detail:
        message = f"{message} {detail}"
    extras = {"status_code": code}
    retry_after = _retry_after(response)
    if retry_after:
        extras["retry_after"] = retry_after
    return _error(
        message,
        operation=operation,
        retryable=code == 429 or code >= 500,
        **extras,
    )


def _request_contactout(
    operation: str,
    *,
    payload: Optional[dict[str, Any]] = None,
    query: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    method, path = _OPERATION_REQUESTS[operation]
    request = requests.post if method == "POST" else requests.get
    kwargs = {
        "headers": _request_headers(),
        "timeout": settings.CONTACTOUT_REQUEST_TIMEOUT_SECONDS,
    }
    if payload is not None:
        kwargs["json"] = payload
    if query is not None:
        kwargs["params"] = query

    try:
        response = request(f"{CONTACTOUT_API_URL}{path}", **kwargs)
    except Timeout:
        return _error("ContactOut API request timed out.", operation=operation, retryable=True)
    except RequestException as exc:
        logger.warning("ContactOut API request failed: %s", exc)
        return _error(
            "ContactOut API request failed before receiving a response.",
            operation=operation,
            retryable=True,
        )

    parsed = None
    try:
        parsed = response.json()
    except ValueError:
        if response.status_code < 400:
            return _error(
                "ContactOut API returned an invalid JSON response.",
                operation=operation,
                retryable=False,
            )

    if response.status_code >= 400:
        return _response_error(response, operation, parsed)

    body_status = parsed.get("status_code") if isinstance(parsed, dict) else None
    try:
        body_status = int(body_status) if body_status is not None else None
    except (TypeError, ValueError):
        body_status = None
    if body_status is not None and body_status >= 400:
        return _response_error(response, operation, parsed, status_code=body_status)
    return {
        "status": "success",
        "provider": "contactout",
        "operation": operation,
        "content": parsed,
    }


def execute_contactout(agent, params: dict[str, Any]) -> dict[str, Any]:
    """Validate and execute a ContactOut pilot operation."""
    if not contactout_enabled_for_agent(agent):
        return _error("ContactOut is not enabled for this agent.")
    if not settings.CONTACTOUT_API_TOKEN:
        return _error("ContactOut is not configured. Set CONTACTOUT_API_TOKEN before using this tool.")
    if not isinstance(params, dict):
        return _error("ContactOut parameters must be an object.")

    operation = params.get("operation")
    if operation not in CONTACTOUT_OPERATIONS:
        return _error(
            f"operation must be one of: {', '.join(CONTACTOUT_OPERATIONS)}.",
            operation=str(operation or ""),
        )

    allowed_parameters = {
        SEARCH_PEOPLE: {
            "operation",
            "people_filters",
            "reveal_all_contact_info",
            "required_contact_data_types",
        },
        COUNT_PEOPLE: {"operation", "people_filters"},
        ENRICH_LINKEDIN_PROFILE: {"operation", "linkedin_url", "reveal_all_contact_info"},
        SEARCH_COMPANIES: {"operation", "company_filters"},
        ENRICH_COMPANY_DOMAINS: {"operation", "domains"},
    }[operation]
    known_parameters = {
        "operation",
        "people_filters",
        "company_filters",
        "linkedin_url",
        "reveal_all_contact_info",
        "required_contact_data_types",
        "domains",
    }
    unknown = sorted(set(params) - known_parameters)
    if unknown:
        return _error(
            f"Unsupported ContactOut parameters: {', '.join(unknown)}.",
            operation=operation,
        )
    incompatible = sorted(
        key for key, value in params.items() if key not in allowed_parameters and _is_nonempty(value)
    )
    if incompatible:
        return _error(
            f"Parameters not supported by {operation}: {', '.join(incompatible)}.",
            operation=operation,
        )

    if operation in {SEARCH_PEOPLE, COUNT_PEOPLE}:
        filters, filter_error = _validate_people_filters(params.get("people_filters"))
        if filter_error:
            return _error(filter_error, operation=operation)
        if operation == COUNT_PEOPLE:
            payload = {key: value for key, value in filters.items() if key not in _COUNT_IGNORED_FILTERS}
            return _request_contactout(operation, payload=payload)

        reveal_all_contact_info = params.get("reveal_all_contact_info") is True
        contact_types, contact_error = _validate_contact_types(params.get("required_contact_data_types"))
        if contact_error:
            return _error(contact_error, operation=operation)
        filters["reveal_info"] = reveal_all_contact_info
        if contact_types:
            filters["data_types"] = contact_types
        return _request_contactout(operation, payload=filters)

    if operation == ENRICH_LINKEDIN_PROFILE:
        linkedin_url, linkedin_error = _validate_linkedin_url(params.get("linkedin_url"))
        if linkedin_error:
            return _error(linkedin_error, operation=operation)
        return _request_contactout(
            operation,
            query={
                "profile": linkedin_url,
                "profile_only": params.get("reveal_all_contact_info") is not True,
            },
        )

    if operation == SEARCH_COMPANIES:
        filters, filter_error = _validate_company_filters(params.get("company_filters"))
        if filter_error:
            return _error(filter_error, operation=operation)
        return _request_contactout(operation, payload=filters)

    domains, domain_error = _normalize_domains(params.get("domains"))
    if domain_error:
        return _error(domain_error, operation=operation)
    return _request_contactout(operation, payload={"domains": domains})
