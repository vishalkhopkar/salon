import json
import logging
import os
import random
import re
import time
from datetime import datetime

import requests
from bs4 import BeautifulSoup
from geopy.extra.rate_limiter import RateLimiter
from geopy.geocoders import Nominatim

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("find_salon")
# basicConfig() is a no-op (level included) when a handler already exists on the root
# logger - which Lambda's runtime pre-attaches before this module loads. Setting the
# level directly on this logger works regardless of what the root ends up at.
logger.setLevel(logging.INFO)

LISTING_URL = "https://coupons-2save.com/greatclips"
REQUEST_TIMEOUT = 20
DELAY_RANGE = (0.5, 1.5)

# When SALONS_BUCKET_NAME is set (i.e. running as the Lambda), results are persisted to S3
# and /tmp is used as Lambda's only writable local path. Otherwise this behaves exactly like
# the original local script, reading/writing salons.json next to this file.
S3_BUCKET = os.environ.get("SALONS_BUCKET_NAME")
S3_KEY = os.environ.get("SALONS_OBJECT_KEY", "salons.json")
OUTPUT_JSON = (
    os.path.join("/tmp", "salons.json")
    if S3_BUCKET
    else os.path.join(os.path.dirname(os.path.abspath(__file__)), "salons.json")
)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    )
}

TIER_PRICE_RE = re.compile(r"\$(\d+)\.(\d+)")
OFFER_URL_RE = re.compile(r"https://offers\.greatclips\.com/[A-Za-z0-9]+")
ADDRESS_TERMINATORS = ("Not valid", "Limit one", "No copies", "Taxes may apply", "Expires")
STREET_CITY_STATE_RE = re.compile(r"at\s+(\d.*?)\s+in\s+(.+?),\s*([A-Z]{2})\s*$")
EXPIRY_DATE_RE = re.compile(r"(?:Offer\s+)?expires\s+(\d{1,2}/\d{1,2}/\d{4})", re.IGNORECASE)

session = requests.Session()
session.headers.update(HEADERS)

geolocator = Nominatim(user_agent="find_salon_script (personal coupon scraper)")
geocode = RateLimiter(geolocator.geocode, min_delay_seconds=1, max_retries=1, error_wait_seconds=2)
geocode_cache = {}


def fetch(url):
    response = session.get(url, timeout=REQUEST_TIMEOUT)
    response.raise_for_status()
    return response.text


def find_tier_entries(listing_html):
    soup = BeautifulSoup(listing_html, "lxml")
    entries = []
    seen_urls = set()
    for div in soup.find_all("div", class_="greatclips-discount"):
        if "expired" in div.get("class", []):
            continue
        match = TIER_PRICE_RE.search(div.get_text())
        if not match:
            continue
        dollars, cents = match.groups()
        tier_url = f"https://coupons-2save.com/greatclips/${dollars}-{cents}"
        if tier_url in seen_urls:
            continue
        seen_urls.add(tier_url)
        entries.append((tier_url, f"{dollars}.{cents}"))
    return entries


def find_offer_urls(tier_html):
    return sorted(set(OFFER_URL_RE.findall(tier_html)))


def is_offer_active(offer_url):
    try:
        response = session.post(f"{offer_url}/redemption_status", timeout=REQUEST_TIMEOUT)
    except requests.RequestException:
        return False
    if response.status_code != 200:
        return False
    try:
        return response.json().get("status_text") == "ok"
    except ValueError:
        return False


def clean_address(address):
    address = re.sub(r"\s+", " ", address).strip()
    # template leaves a dangling "at in ," when a multi-location offer has no single street
    # address; matched narrowly so it doesn't eat a trailing "IN" (Indiana) state code
    address = re.sub(r"\s+at\s+in\s*,?\s*$", "", address, flags=re.IGNORECASE)
    return address.strip(" ,")


def simplify_address(address):
    address = re.sub(r"^only\s+", "", address, flags=re.IGNORECASE)
    address = re.sub(r"^at\s+", "", address, flags=re.IGNORECASE)
    address = re.sub(r"^participating\s+", "", address, flags=re.IGNORECASE)
    address = re.sub(r"^Great Clips\s+", "", address, flags=re.IGNORECASE)
    address = re.sub(r"\s+Great Clips salons$", "", address, flags=re.IGNORECASE)
    return address.strip()


def geocode_query(query):
    cache_key = repr(query)
    if cache_key in geocode_cache:
        return geocode_cache[cache_key]
    try:
        location = geocode(query, timeout=15)
    except Exception as exc:
        logger.warning("Geocode error for %r: %s", query, exc)
        location = None
    result = (round(location.latitude, 6), round(location.longitude, 6)) if location else (None, None)
    geocode_cache[cache_key] = result
    return result


def geocode_specific_address(display_address):
    match = STREET_CITY_STATE_RE.search(display_address)
    if match:
        street, city, state = match.groups()
        lat, lon = geocode_query({"street": street, "city": city, "state": state, "country": "USA"})
        if lat is not None:
            return lat, lon
        lat, lon = geocode_query({"city": city, "state": state, "country": "USA"})
        if lat is not None:
            return lat, lon
    return geocode_query(f"{display_address}, USA")


def geocode_generic_address(display_address):
    region = re.sub(r"\s+area$", "", display_address, flags=re.IGNORECASE).strip()
    region = re.split(r"\s*[,&]\s*", region)[0].strip()

    lat, lon = geocode_query(f"{region}, USA")
    if lat is not None:
        return lat, lon

    if "-" in region:
        first_part = region.split("-", 1)[0].strip()
        lat, lon = geocode_query(f"{first_part}, USA")
        if lat is not None:
            return lat, lon

    return None, None


def extract_terms_text(offer_html):
    soup = BeautifulSoup(offer_html, "lxml")
    terms = soup.find(id="terms_and_conditions")
    return terms.get_text(" ", strip=True) if terms else soup.get_text(" ", strip=True)


def extract_address(text):
    for terminator in ADDRESS_TERMINATORS:
        match = re.search(rf"Valid\s+(.*?)\.\s*{re.escape(terminator)}", text, re.IGNORECASE)
        if match:
            return clean_address(match.group(1))

    match = re.search(r"Valid\s+([^.]*)\.", text, re.IGNORECASE)
    return clean_address(match.group(1)) if match else None


def extract_expiry_date(text):
    match = EXPIRY_DATE_RE.search(text)
    return match.group(1) if match else None


def is_still_valid(expiry_date_str):
    if not expiry_date_str:
        return False
    try:
        expiry = datetime.strptime(expiry_date_str, "%m/%d/%Y").date()
    except ValueError:
        return False
    return expiry >= datetime.now().date()


def write_json(results):
    with open(OUTPUT_JSON, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2)

    if S3_BUCKET:
        import boto3

        boto3.client("s3").upload_file(
            OUTPUT_JSON, S3_BUCKET, S3_KEY,
            ExtraArgs={"ContentType": "application/json"},
        )


def load_existing_results():
    if S3_BUCKET:
        return _load_existing_results_from_s3()
    return _load_existing_results_from_disk()


def _load_existing_results_from_disk():
    logger.info("Reading existing results file: %s", OUTPUT_JSON)
    if not os.path.exists(OUTPUT_JSON):
        logger.info("No existing results file found, starting with an empty cache")
        return {}
    try:
        with open(OUTPUT_JSON, encoding="utf-8") as f:
            existing = json.load(f)
        by_stub = {doc["stub"]: doc for doc in existing}
    except (json.JSONDecodeError, OSError, KeyError) as exc:
        logger.warning("Could not load existing %s: %s", OUTPUT_JSON, exc)
        return {}
    logger.info("Loaded %d cached offers from %s", len(by_stub), OUTPUT_JSON)
    return by_stub


def _load_existing_results_from_s3():
    import boto3
    from botocore.exceptions import ClientError

    logger.info("Reading existing results object: s3://%s/%s", S3_BUCKET, S3_KEY)
    s3 = boto3.client("s3")
    try:
        response = s3.get_object(Bucket=S3_BUCKET, Key=S3_KEY)
        existing = json.loads(response["Body"].read())
        by_stub = {doc["stub"]: doc for doc in existing}
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") in ("NoSuchKey", "404"):
            logger.info("No existing object at s3://%s/%s, starting with an empty cache", S3_BUCKET, S3_KEY)
        else:
            logger.warning("Could not load existing s3://%s/%s: %s", S3_BUCKET, S3_KEY, exc)
        return {}
    except (json.JSONDecodeError, KeyError) as exc:
        logger.warning("Could not parse existing s3://%s/%s: %s", S3_BUCKET, S3_KEY, exc)
        return {}
    logger.info("Loaded %d cached offers from s3://%s/%s", len(by_stub), S3_BUCKET, S3_KEY)
    return by_stub


def main():
    logger.info("Starting find_salon scraper")
    existing_by_stub = load_existing_results()

    logger.info("Fetching listing page: %s", LISTING_URL)
    tier_entries = find_tier_entries(fetch(LISTING_URL))
    logger.info("Found %d active price-tier pages", len(tier_entries))

    seen_offers = set()
    results = []

    for tier_url, dollar_value in tier_entries:
        time.sleep(random.uniform(*DELAY_RANGE))
        logger.info("Opening tier page: %s (%s)", tier_url, dollar_value)
        try:
            offer_urls = find_offer_urls(fetch(tier_url))
        except requests.RequestException as exc:
            logger.error("Failed to load tier page %s: %s", tier_url, exc)
            continue
        logger.info("Found %d offer URLs on %s", len(offer_urls), tier_url)

        for offer_url in offer_urls:
            if offer_url in seen_offers:
                continue
            seen_offers.add(offer_url)
            offer_code = offer_url.rsplit("/", 1)[-1]

            cached = existing_by_stub.get(offer_code)
            if cached and is_still_valid(cached.get("expiry_date")):
                logger.info(
                    "Offer %s cached and not yet expired (expires %s) - skipping page fetch",
                    offer_code, cached.get("expiry_date"),
                )
                results.append(cached)
                write_json(results)
                continue

            logger.info("Offer %s not cached or expired - opening offer page", offer_code)
            time.sleep(random.uniform(*DELAY_RANGE))

            if not is_offer_active(offer_url):
                logger.info("Offer %s is inactive - skipping", offer_code)
                continue

            try:
                offer_html = fetch(offer_url)
            except requests.RequestException as exc:
                logger.error("Failed to load offer page %s: %s", offer_url, exc)
                continue

            terms_text = extract_terms_text(offer_html)
            address = extract_address(terms_text)
            if not address:
                logger.warning("No address found on %s - skipping", offer_url)
                continue
            expiry_date = extract_expiry_date(terms_text)

            is_generic = "participating" in address.lower()
            display_address = simplify_address(address)

            if is_generic:
                lat, lon = geocode_generic_address(display_address)
            else:
                lat, lon = geocode_specific_address(display_address)

            is_generic_str = "true" if is_generic else "false"
            results.append({
                "stub": offer_code,
                "value": float(dollar_value),
                "address": display_address,
                "is_generic": is_generic_str,
                "lat": lat,
                "long": lon,
                "expiry_date": expiry_date,
            })
            write_json(results)
            logger.info(
                "Offer %s -> %s [value=%s, generic=%s, lat/long=%s/%s, expires=%s]",
                offer_code, display_address, dollar_value, is_generic_str, lat, lon, expiry_date,
            )

    logger.info("Completed. Wrote %d records to %s", len(results), OUTPUT_JSON)


def lambda_handler(event, context):
    main()
    return {"statusCode": 200, "body": json.dumps({"message": "find_salon completed"})}


if __name__ == "__main__":
    main()
