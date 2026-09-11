#!/usr/bin/env python3
"""CLI utilities for Apollo company enrichment workflows."""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qsl, urlencode, unquote, urlsplit, urlunsplit
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo


APOLLO_API_BASE_URL = "https://api.apollo.io/api/v1"
META_GRAPH_API_BASE_URL = "https://graph.facebook.com"
MAX_PAGE_SIZE = 100
DEFAULT_TIMEZONE = "Europe/Madrid"
DEFAULT_LAST_UPDATED_FIELD = "enricher_last_updated_at"
DEFAULT_META_API_VERSION = "v25.0"
ENRICHER_META_ACTIVE_ADS_FIELD = "enricher_meta_active_ads"
ENRICHER_META_TOTAL_REACH_FIELD = "enricher_meta_total_reach_by_active_ads"
ENRICHER_META_PLATFORMS_FIELD = "enricher_meta_active_ad_platforms"


def load_dotenv(path: Path) -> None:
    if not path.exists():
        return

    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue

        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


def request_json(
    path: str,
    api_key: str,
    method: str = "GET",
    payload: dict | None = None,
) -> dict | list:
    data = None
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")

    request = Request(
        f"{APOLLO_API_BASE_URL}{path}",
        data=data,
        headers={
            "accept": "application/json",
            "content-type": "application/json",
            "x-api-key": api_key,
        },
        method=method,
    )

    try:
        with urlopen(request, timeout=30) as response:
            return json.loads(response.read().decode("utf-8"))
    except HTTPError as error:
        body = error.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Apollo API returned HTTP {error.code}: {body}") from error
    except URLError as error:
        raise RuntimeError(f"Could not reach Apollo API: {error.reason}") from error


def request_url_json(url: str) -> dict | list:
    request = Request(url, headers={"accept": "application/json"}, method="GET")

    try:
        with urlopen(request, timeout=30) as response:
            return json.loads(response.read().decode("utf-8"))
    except HTTPError as error:
        body = error.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Meta API returned HTTP {error.code}: {body}") from error
    except URLError as error:
        raise RuntimeError(f"Could not reach Meta API: {error.reason}") from error


def post_json(path: str, api_key: str, payload: dict) -> dict | list:
    return request_json(path, api_key, method="POST", payload=payload)


def patch_json(path: str, api_key: str, payload: dict) -> dict | list:
    return request_json(path, api_key, method="PATCH", payload=payload)


def list_records(response: dict | list) -> list[dict]:
    if isinstance(response, list):
        return [record for record in response if isinstance(record, dict)]

    for key in ("labels", "lists", "account_lists", "fields"):
        records = response.get(key)
        if isinstance(records, list):
            return [record for record in records if isinstance(record, dict)]
    return []


def resolve_list_id(api_key: str, list_ref: str) -> str:
    labels = list_records(request_json("/labels", api_key))

    for label in labels:
        if label.get("id") == list_ref:
            return list_ref

    matches = [
        label
        for label in labels
        if label.get("name") == list_ref and label.get("modality") in (None, "accounts")
    ]

    if len(matches) == 1:
        return str(matches[0]["id"])

    if len(matches) > 1:
        ids = ", ".join(str(match.get("id")) for match in matches)
        raise RuntimeError(f"Found multiple Apollo account lists named {list_ref!r}: {ids}")

    available = ", ".join(str(label.get("name")) for label in labels[:10] if label.get("name"))
    detail = f" Available lists include: {available}" if available else ""
    raise RuntimeError(f"Could not find Apollo account list named or identified by {list_ref!r}.{detail}")


def account_website(account: dict) -> str:
    website = (
        account.get("website_url")
        or account.get("website")
        or account.get("primary_domain")
        or account.get("domain")
    )
    return website or ""


def search_accounts(api_key: str, list_id: str, limit: int) -> list[dict]:
    accounts: list[dict] = []
    page = 1

    while len(accounts) < limit:
        per_page = min(MAX_PAGE_SIZE, limit - len(accounts))
        response = post_json(
            "/accounts/search",
            api_key,
            {
                "account_label_ids": [list_id],
                "page": page,
                "per_page": per_page,
            },
        )

        if not isinstance(response, dict):
            raise RuntimeError("Apollo returned an unexpected response for account search.")

        batch = response.get("accounts") or []
        if not batch:
            break

        accounts.extend(account for account in batch if isinstance(account, dict))

        if len(batch) < per_page:
            break

        page += 1

    return accounts[:limit]


def search_accounts_by_name(
    api_key: str,
    company_name: str,
    limit: int = 10,
    list_id: str | None = None,
) -> list[dict]:
    payload: dict = {
        "q_organization_name": company_name,
        "page": 1,
        "per_page": limit,
    }
    if list_id:
        payload["account_label_ids"] = [list_id]

    response = post_json("/accounts/search", api_key, payload)
    if not isinstance(response, dict):
        raise RuntimeError("Apollo returned an unexpected response for account search.")

    accounts = response.get("accounts") or []
    return [account for account in accounts if isinstance(account, dict)]


def find_account_by_name(
    api_key: str,
    company_name: str,
    list_ref: str | None = None,
) -> dict:
    list_id = resolve_list_id(api_key, list_ref) if list_ref else None
    accounts = search_accounts_by_name(api_key, company_name, list_id=list_id)
    if not accounts:
        scope = f" in list {list_ref!r}" if list_ref else ""
        raise RuntimeError(f"Could not find Apollo company named {company_name!r}{scope}.")

    exact_matches = [
        account
        for account in accounts
        if str(account.get("name", "")).casefold() == company_name.casefold()
    ]
    if len(exact_matches) == 1:
        return exact_matches[0]

    if len(accounts) == 1:
        return accounts[0]

    names = ", ".join(str(account.get("name")) for account in accounts[:5])
    raise RuntimeError(
        f"Found multiple companies matching {company_name!r}. "
        f"Refine the name or pass --list. Matches include: {names}"
    )


def field_candidates(field: dict) -> set[str]:
    candidates = set()
    for key in ("id", "label", "name", "api_name", "field_name"):
        value = field.get(key)
        if value:
            candidates.add(str(value))
            if key == "id" and "." in str(value):
                candidates.add(str(value).split(".", 1)[1])
    return candidates


def normalize_typed_custom_field_id(field_id: str) -> str:
    if "." in field_id:
        prefix, raw_id = field_id.split(".", 1)
        if prefix in ("account", "contact", "opportunity"):
            return raw_id
    return field_id


def resolve_custom_field_id(api_key: str, field_ref: str) -> str:
    fields = list_records(request_json("/fields?source=custom", api_key))

    for field in fields:
        field_id = str(field.get("id", ""))
        if field_id == field_ref or normalize_typed_custom_field_id(field_id) == field_ref:
            return normalize_typed_custom_field_id(field_id)

    matches = [
        field
        for field in fields
        if any(candidate.casefold() == field_ref.casefold() for candidate in field_candidates(field))
        and field.get("modality") in (None, "account", "accounts")
    ]

    if len(matches) == 1:
        return normalize_typed_custom_field_id(str(matches[0]["id"]))

    if len(matches) > 1:
        ids = ", ".join(str(match.get("id")) for match in matches)
        raise RuntimeError(f"Found multiple custom account fields matching {field_ref!r}: {ids}")

    available = ", ".join(
        str(field.get("label") or field.get("name") or field.get("id")) for field in fields[:10]
    )
    detail = f" Available custom fields include: {available}" if available else ""
    raise RuntimeError(f"Could not find custom account field {field_ref!r}.{detail}")


def current_time_value(timezone_name: str) -> str:
    try:
        timezone = ZoneInfo(timezone_name)
    except Exception as error:
        raise RuntimeError(f"Unknown timezone {timezone_name!r}.") from error

    return datetime.now(timezone).replace(microsecond=0).isoformat()


def update_account_custom_field(
    api_key: str,
    account_id: str,
    field_id: str,
    value: str,
) -> dict:
    response = patch_json(
        f"/accounts/{account_id}",
        api_key,
        {"typed_custom_fields": {field_id: value}},
    )
    if not isinstance(response, dict):
        raise RuntimeError("Apollo returned an unexpected response for account update.")

    account = response.get("account", response)
    if not isinstance(account, dict):
        raise RuntimeError("Apollo returned an unexpected account update response.")

    custom_field_errors = account.get("custom_field_errors") or {}
    if custom_field_errors:
        raise RuntimeError(f"Apollo rejected the custom field update: {custom_field_errors}")

    typed_custom_fields = account.get("typed_custom_fields") or {}
    if typed_custom_fields.get(field_id) is None:
        raise RuntimeError(
            "Apollo accepted the request but did not return the updated custom field. "
            "Check that the field is editable and is an account custom field."
        )

    return account


def update_account_custom_fields(
    api_key: str,
    account_id: str,
    field_values: dict[str, int | str],
) -> dict:
    response = patch_json(
        f"/accounts/{account_id}",
        api_key,
        {"typed_custom_fields": field_values},
    )
    if not isinstance(response, dict):
        raise RuntimeError("Apollo returned an unexpected response for account update.")

    account = response.get("account", response)
    if not isinstance(account, dict):
        raise RuntimeError("Apollo returned an unexpected account update response.")

    custom_field_errors = account.get("custom_field_errors") or {}
    if custom_field_errors:
        raise RuntimeError(f"Apollo rejected the custom field update: {custom_field_errors}")

    typed_custom_fields = account.get("typed_custom_fields") or {}
    missing = [
        field_id
        for field_id, value in field_values.items()
        if value != "" and typed_custom_fields.get(field_id) is None
    ]
    if missing:
        raise RuntimeError(
            "Apollo accepted the request but did not return these updated custom fields: "
            + ", ".join(missing)
        )

    return account


def search_meta_ads(
    access_token: str,
    search_terms: str,
    country: str,
    limit: int,
    api_version: str,
    active_only: bool = False,
) -> list[dict]:
    params = {
        "access_token": access_token,
        "search_terms": search_terms,
        "ad_reached_countries": json.dumps([country]),
        "ad_type": "ALL",
        "ad_active_status": "ACTIVE" if active_only else "ALL",
        "search_type": "KEYWORD_UNORDERED",
        "fields": ",".join(
            [
                "id",
                "page_id",
                "page_name",
                "ad_delivery_start_time",
                "ad_delivery_stop_time",
                "ad_snapshot_url",
            ]
        ),
        "limit": limit,
    }
    url = f"{META_GRAPH_API_BASE_URL}/{api_version}/ads_archive?{urlencode(params)}"
    response = request_url_json(url)

    if not isinstance(response, dict):
        raise RuntimeError("Meta returned an unexpected response for ad library search.")

    ads = response.get("data") or []
    return [ad for ad in ads if isinstance(ad, dict)]


def summarize_active_meta_ads(
    access_token: str,
    search_terms: str,
    country: str,
    api_version: str,
) -> dict:
    ads = count_meta_ads(
        access_token=access_token,
        search_terms=search_terms,
        country=country,
        api_version=api_version,
        active_only=True,
        fields="id,page_id,page_name,ad_delivery_start_time,ad_delivery_stop_time,eu_total_reach,publisher_platforms",
    )

    total_reach = 0
    ads_with_reach = 0
    platforms: set[str] = set()
    by_page: dict[str, int] = {}

    for ad in ads:
        page_key = f"{ad.get('page_name', '')} ({ad.get('page_id', '')})"
        by_page[page_key] = by_page.get(page_key, 0) + 1

        reach = ad.get("eu_total_reach")
        if isinstance(reach, int):
            ads_with_reach += 1
            total_reach += reach

        for platform in ad.get("publisher_platforms") or []:
            if platform:
                platforms.add(str(platform))

    return {
        "active_ads": len(ads),
        "total_reach": total_reach,
        "ads_with_reach": ads_with_reach,
        "platforms": sorted(platforms),
        "by_page": by_page,
    }


def facebook_page_ref(value: str) -> str:
    value = value.strip()
    if not value:
        raise RuntimeError("Facebook page URL or username cannot be empty.")

    if "://" not in value and "/" not in value:
        return value

    parsed = urlsplit(value if "://" in value else f"https://{value}")
    if "facebook.com" not in parsed.netloc.lower():
        raise RuntimeError(f"Expected a facebook.com URL, got {value!r}.")

    query = dict(parse_qsl(parsed.query, keep_blank_values=True))
    if parsed.path.rstrip("/") == "/profile.php" and query.get("id"):
        return query["id"]

    segments = [unquote(segment) for segment in parsed.path.split("/") if segment]
    if not segments:
        raise RuntimeError(f"Could not extract a page username or ID from {value!r}.")

    if segments[0] in {"pages", "pg"} and len(segments) >= 2:
        return segments[-1] if segments[-1].isdigit() else segments[1]

    return segments[0]


def get_meta_page(access_token: str, page_url_or_username: str, api_version: str) -> dict:
    page_ref = facebook_page_ref(page_url_or_username)
    params = {
        "access_token": access_token,
        "fields": "id,name,link,username",
    }
    url = f"{META_GRAPH_API_BASE_URL}/{api_version}/{page_ref}?{urlencode(params)}"
    response = request_url_json(url)
    if not isinstance(response, dict):
        raise RuntimeError("Meta returned an unexpected response for page lookup.")
    return response


def count_meta_ads(
    access_token: str,
    search_terms: str,
    country: str,
    api_version: str,
    active_only: bool = False,
    max_pages: int = 20,
    fields: str = "id,page_id,page_name,ad_delivery_start_time,ad_delivery_stop_time",
) -> list[dict]:
    params = {
        "access_token": access_token,
        "search_terms": search_terms,
        "ad_reached_countries": json.dumps([country]),
        "ad_type": "ALL",
        "ad_active_status": "ACTIVE" if active_only else "ALL",
        "search_type": "KEYWORD_UNORDERED",
        "fields": fields,
        "limit": 100,
    }
    url = f"{META_GRAPH_API_BASE_URL}/{api_version}/ads_archive?{urlencode(params)}"
    ads: list[dict] = []
    pages_seen = 0

    while url and pages_seen < max_pages:
        pages_seen += 1
        response = request_url_json(url)
        if not isinstance(response, dict):
            raise RuntimeError("Meta returned an unexpected response for ad library search.")

        ads.extend(ad for ad in response.get("data", []) if isinstance(ad, dict))
        url = (response.get("paging") or {}).get("next")

    return ads


def redact_url_access_token(url: str) -> str:
    if not url:
        return ""

    parts = urlsplit(url)
    query = urlencode(
        [
            (key, "[REDACTED]" if key == "access_token" else value)
            for key, value in parse_qsl(parts.query, keep_blank_values=True)
        ]
    )
    return urlunsplit((parts.scheme, parts.netloc, parts.path, query, parts.fragment))


def is_meta_ad_active(ad: dict) -> bool:
    return bool(ad.get("ad_delivery_start_time")) and not bool(ad.get("ad_delivery_stop_time"))


def print_companies(accounts: list[dict]) -> None:
    for index, account in enumerate(accounts, start=1):
        print(f"{index}. Name: {account.get('name', '')}")
        print(f"   Website: {account_website(account)}")


def add_common_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--env-file",
        default=".env",
        help="Path to the env file containing APOLLO_API_KEY. Defaults to .env.",
    )


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Apollo enrichment CLI.")
    add_common_args(parser)

    subparsers = parser.add_subparsers(dest="command", required=True)

    companies = subparsers.add_parser(
        "companies",
        help="Print company names and websites from an Apollo account list.",
    )
    companies.add_argument(
        "list",
        help="Apollo account list name or ID, also called the account label ID.",
    )
    companies.add_argument(
        "--limit",
        "-n",
        type=int,
        default=1,
        help="Number of companies to fetch. Defaults to 1.",
    )

    update_field = subparsers.add_parser(
        "update-company-field",
        help="Update a custom field on a saved Apollo company.",
    )
    update_field.add_argument(
        "company_name",
        help="Company/account name to update in Apollo.",
    )
    update_field.add_argument(
        "--field",
        default=DEFAULT_LAST_UPDATED_FIELD,
        help=f"Custom account field name or ID. Defaults to {DEFAULT_LAST_UPDATED_FIELD}.",
    )
    update_field.add_argument(
        "--value",
        help="Custom value to set. Defaults to the current timestamp.",
    )
    update_field.add_argument(
        "--timezone",
        default=DEFAULT_TIMEZONE,
        help=f"Timezone used for the default timestamp. Defaults to {DEFAULT_TIMEZONE}.",
    )
    update_field.add_argument(
        "--list",
        help="Optional Apollo account list name or ID to disambiguate the company search.",
    )
    update_field.add_argument(
        "--dry-run",
        action="store_true",
        help="Resolve the company and custom field, but do not update Apollo.",
    )

    enrich_meta = subparsers.add_parser(
        "enrich-meta-company",
        help="Fetch active Meta ads metrics and update enricher fields on an Apollo company.",
    )
    enrich_meta.add_argument(
        "company_name",
        help="Apollo company/account name to enrich.",
    )
    enrich_meta.add_argument(
        "--list",
        help="Optional Apollo account list name or ID to disambiguate the company search.",
    )
    enrich_meta.add_argument(
        "--meta-search-term",
        help="Optional Meta Ad Library search term. Defaults to the resolved Apollo company name.",
    )
    enrich_meta.add_argument(
        "--country",
        default="ES",
        help="Reached country code to search in Meta Ad Library. Defaults to ES.",
    )
    enrich_meta.add_argument(
        "--api-version",
        help=f"Meta Graph API version. Defaults to {DEFAULT_META_API_VERSION}.",
    )
    enrich_meta.add_argument(
        "--timezone",
        default=DEFAULT_TIMEZONE,
        help=f"Timezone used for enricher_last_updated_at. Defaults to {DEFAULT_TIMEZONE}.",
    )
    enrich_meta.add_argument(
        "--dry-run",
        action="store_true",
        help="Resolve and calculate enrichment values, but do not update Apollo.",
    )

    meta_page_id = subparsers.add_parser(
        "meta-page-id",
        help="Resolve a Facebook page URL or username to a Meta Page ID.",
    )
    meta_page_id.add_argument(
        "page",
        help="Facebook page URL or username, for example https://www.facebook.com/example-page/.",
    )
    meta_page_id.add_argument(
        "--api-version",
        help=f"Meta Graph API version. Defaults to {DEFAULT_META_API_VERSION}.",
    )

    meta_ads = subparsers.add_parser(
        "meta-ads",
        help="Search Meta Ad Library for ads matching a company name.",
    )
    meta_ads.add_argument(
        "company_name",
        help="Company name or search term to look up in Meta Ad Library.",
    )
    meta_ads.add_argument(
        "--country",
        default="ES",
        help="Reached country code to search. Defaults to ES.",
    )
    meta_ads.add_argument(
        "--limit",
        "-n",
        type=int,
        default=10,
        help="Maximum number of ads to fetch. Defaults to 10.",
    )
    meta_ads.add_argument(
        "--api-version",
        help=f"Meta Graph API version. Defaults to {DEFAULT_META_API_VERSION}.",
    )
    meta_ads.add_argument(
        "--active",
        action="store_true",
        help="Only return ads Meta marks as active.",
    )
    meta_ads.add_argument(
        "--count",
        action="store_true",
        help="Count matching ads instead of printing ad details.",
    )
    meta_ads.add_argument(
        "--sum-reach",
        action="store_true",
        help="Sum eu_total_reach across matching ads instead of printing ad details.",
    )

    return parser.parse_args(argv)


def run_companies(api_key: str, args: argparse.Namespace) -> int:
    if args.limit < 1:
        print("--limit must be greater than 0.", file=sys.stderr)
        return 1

    list_id = resolve_list_id(api_key, args.list)
    accounts = search_accounts(api_key, list_id, args.limit)

    if not accounts:
        print(f"No companies found in Apollo account list {args.list}.", file=sys.stderr)
        return 1

    print_companies(accounts)
    return 0


def run_update_company_field(api_key: str, args: argparse.Namespace) -> int:
    value = args.value or current_time_value(args.timezone)
    account = find_account_by_name(api_key, args.company_name, args.list)
    field_id = resolve_custom_field_id(api_key, args.field)
    if not args.dry_run:
        updated_account = update_account_custom_field(api_key, str(account["id"]), field_id, value)
        stored_value = updated_account.get("typed_custom_fields", {}).get(field_id)
    else:
        stored_value = None

    action = "Would update company" if args.dry_run else "Updated company"
    print(f"{action}: {account.get('name', args.company_name)}")
    print(f"Field: {args.field}")
    print(f"Value: {value}")
    if stored_value and stored_value != value:
        print(f"Stored value: {stored_value}")
    return 0


def run_meta_ads(access_token: str, args: argparse.Namespace) -> int:
    if args.limit < 1:
        print("--limit must be greater than 0.", file=sys.stderr)
        return 1

    api_version = args.api_version or os.environ.get("META_GRAPH_API_VERSION") or DEFAULT_META_API_VERSION

    if args.count or args.sum_reach:
        fields = "id,page_id,page_name,ad_delivery_start_time,ad_delivery_stop_time"
        if args.sum_reach:
            fields += ",eu_total_reach"

        ads = count_meta_ads(
            access_token=access_token,
            search_terms=args.company_name,
            country=args.country,
            api_version=api_version,
            active_only=args.active,
            fields=fields,
        )
        status = "active " if args.active else ""
        print(f"Found {len(ads)} {status}Meta ad(s) for {args.company_name!r} in {args.country}.")
        by_page: dict[str, int] = {}
        reach_by_page: dict[str, int] = {}
        total_reach = 0
        ads_with_reach = 0
        for ad in ads:
            page_key = f"{ad.get('page_name', '')} ({ad.get('page_id', '')})"
            by_page[page_key] = by_page.get(page_key, 0) + 1
            reach = ad.get("eu_total_reach")
            if isinstance(reach, int):
                ads_with_reach += 1
                total_reach += reach
                reach_by_page[page_key] = reach_by_page.get(page_key, 0) + reach
        for page_key, count in sorted(by_page.items(), key=lambda item: item[0].lower()):
            if args.sum_reach:
                print(f"- {page_key}: {count} ad(s), EU reach {reach_by_page.get(page_key, 0)}")
            else:
                print(f"- {page_key}: {count}")
        if args.sum_reach:
            print(f"Total EU reach: {total_reach}")
            print(f"Ads with eu_total_reach: {ads_with_reach}/{len(ads)}")
        return 0

    ads = search_meta_ads(
        access_token=access_token,
        search_terms=args.company_name,
        country=args.country,
        limit=args.limit,
        api_version=api_version,
        active_only=args.active,
    )

    if not ads:
        print(f"No Meta ads found for {args.company_name!r} in {args.country}.")
        return 0

    print(f"Found {len(ads)} Meta ad(s) for {args.company_name!r} in {args.country}.")
    for index, ad in enumerate(ads, start=1):
        print(f"{index}. Page: {ad.get('page_name', '')}")
        print(f"   Ad ID: {ad.get('id', '')}")
        print(f"   Active: {'yes' if is_meta_ad_active(ad) else 'no'}")
        print(f"   Started: {ad.get('ad_delivery_start_time', '')}")
        print(f"   Stopped: {ad.get('ad_delivery_stop_time', '')}")
        print(f"   Snapshot: {redact_url_access_token(ad.get('ad_snapshot_url', ''))}")
    return 0


def resolve_enricher_field_ids(api_key: str) -> dict[str, str]:
    field_names = [
        DEFAULT_LAST_UPDATED_FIELD,
        ENRICHER_META_ACTIVE_ADS_FIELD,
        ENRICHER_META_TOTAL_REACH_FIELD,
        ENRICHER_META_PLATFORMS_FIELD,
    ]
    return {field_name: resolve_custom_field_id(api_key, field_name) for field_name in field_names}


def run_enrich_meta_company(api_key: str, access_token: str, args: argparse.Namespace) -> int:
    account = find_account_by_name(api_key, args.company_name, args.list)
    search_term = args.meta_search_term or str(account.get("name") or args.company_name)
    api_version = args.api_version or os.environ.get("META_GRAPH_API_VERSION") or DEFAULT_META_API_VERSION

    summary = summarize_active_meta_ads(
        access_token=access_token,
        search_terms=search_term,
        country=args.country,
        api_version=api_version,
    )
    field_ids = resolve_enricher_field_ids(api_key)
    last_updated_at = current_time_value(args.timezone)
    platforms_value = "\n".join(summary["platforms"])

    field_values: dict[str, int | str] = {
        field_ids[ENRICHER_META_ACTIVE_ADS_FIELD]: summary["active_ads"],
        field_ids[ENRICHER_META_TOTAL_REACH_FIELD]: summary["total_reach"],
        field_ids[ENRICHER_META_PLATFORMS_FIELD]: platforms_value,
        field_ids[DEFAULT_LAST_UPDATED_FIELD]: last_updated_at,
    }

    if not args.dry_run:
        update_account_custom_fields(api_key, str(account["id"]), field_values)

    action = "Would update company" if args.dry_run else "Updated company"
    print(f"{action}: {account.get('name', args.company_name)}")
    print(f"Meta search term: {search_term}")
    print(f"Country: {args.country}")
    print(f"{ENRICHER_META_ACTIVE_ADS_FIELD}: {summary['active_ads']}")
    print(f"{ENRICHER_META_TOTAL_REACH_FIELD}: {summary['total_reach']}")
    print(f"Ads with eu_total_reach: {summary['ads_with_reach']}/{summary['active_ads']}")
    print(f"{ENRICHER_META_PLATFORMS_FIELD}:")
    if summary["platforms"]:
        for platform in summary["platforms"]:
            print(f"- {platform}")
    else:
        print("-")
    print(f"{DEFAULT_LAST_UPDATED_FIELD}: {last_updated_at}")

    if summary["by_page"]:
        print("Matched Meta pages:")
        for page_key, count in sorted(summary["by_page"].items(), key=lambda item: item[0].lower()):
            print(f"- {page_key}: {count} active ad(s)")

    return 0


def run_meta_page_id(access_token: str, args: argparse.Namespace) -> int:
    api_version = args.api_version or os.environ.get("META_GRAPH_API_VERSION") or DEFAULT_META_API_VERSION
    page = get_meta_page(access_token, args.page, api_version)

    print(f"Page ID: {page.get('id', '')}")
    print(f"Name: {page.get('name', '')}")
    print(f"Username: {page.get('username', '')}")
    print(f"Link: {page.get('link', '')}")
    return 0


def require_env(name: str, help_text: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(f"Missing {name}. {help_text}")
    return value


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    load_dotenv(Path(args.env_file))

    try:
        if args.command == "companies":
            api_key = require_env(
                "APOLLO_API_KEY",
                "Add it to .env or export it in your shell.",
            )
            return run_companies(api_key, args)
        if args.command == "update-company-field":
            api_key = require_env(
                "APOLLO_API_KEY",
                "Add it to .env or export it in your shell.",
            )
            return run_update_company_field(api_key, args)
        if args.command == "meta-ads":
            access_token = require_env(
                "META_ACCESS_TOKEN",
                "Add it to .env or export it in your shell.",
            )
            return run_meta_ads(access_token, args)
        if args.command == "meta-page-id":
            access_token = require_env(
                "META_ACCESS_TOKEN",
                "Add it to .env or export it in your shell.",
            )
            return run_meta_page_id(access_token, args)
        if args.command == "enrich-meta-company":
            api_key = require_env(
                "APOLLO_API_KEY",
                "Add it to .env or export it in your shell.",
            )
            access_token = require_env(
                "META_ACCESS_TOKEN",
                "Add it to .env or export it in your shell.",
            )
            return run_enrich_meta_company(api_key, access_token, args)
    except RuntimeError as error:
        print(error, file=sys.stderr)
        return 1

    print(f"Unknown command: {args.command}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
