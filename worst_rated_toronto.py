#!/usr/bin/env python3
"""
worst_rated_toronto.py - find the worst-reviewed restaurants in Toronto.

Sweeps a lat/lng bounding box with the Google Places API (New)
places:searchNearby endpoint, deduplicates by place id, filters on review
count and business status, and writes a CSV sorted worst-first.

Nearby Search returns at most 20 results per call and offers no worst-first
ordering, so the box is tiled with a quadtree: any cell that comes back with a
full page of 20 is split into four and searched again. That is what produces
real coverage in dense areas instead of just the 20 most prominent places.

Every raw sweep is cached to toronto_places_cache.json. --from-cache re-filters
that cache with zero API spend.

Requires GOOGLE_MAPS_API_KEY in the environment. Stdlib only.
"""

import argparse
import collections
import csv
import json
import math
import os
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone

ENDPOINT = "https://places.googleapis.com/v1/places:searchNearby"

# Nearby Search (New) hard caps.
MAX_RESULTS_PER_CALL = 20
PLACES_MAX_RADIUS_M = 50000.0

# Stop splitting once a cell is this small. A cell that still returns a full
# page at this radius is recorded as truncated rather than silently accepted;
# see --truncation-report.
MIN_CELL_RADIUS_M = 50.0

EARTH_RADIUS_M = 6371008.8

# rating and userRatingCount put every request in the Enterprise field tier.
# Billing is per request, not per field.
FIELD_MASK = ",".join(
    "places." + f
    for f in (
        "id",
        "displayName",
        "rating",
        "userRatingCount",
        "businessStatus",
        "formattedAddress",
        "location",
        "primaryType",
        "types",
        "googleMapsUri",
    )
)

# Toronto, roughly Etobicoke to Scarborough and lake to Steeles.
DEFAULT_BBOX = (43.58, -79.64, 43.86, -79.12)

FATAL_STATUS = {400, 401, 403}
RETRY_STATUS = {429, 500, 502, 503, 504}

# Primary types that carry "restaurant" in their type array but are not
# restaurants. Applied at filter time so it also works with --from-cache.
DEFAULT_DROP_PRIMARY_TYPES = (
    "gas_station",
    "convenience_store",
    "supermarket",
    "grocery_store",
    "lodging",
    "hotel",
    "shopping_mall",
    "department_store",
    "stadium",
    "airport",
    "hospital",
    "school",
    "university",
)


def _utcnow():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# --------------------------------------------------------------------------
# geometry
# --------------------------------------------------------------------------


def haversine_m(lat1, lng1, lat2, lng2):
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = p2 - p1
    dl = math.radians(lng2 - lng1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * EARTH_RADIUS_M * math.atan2(math.sqrt(a), math.sqrt(1 - a))


class Cell:
    """A lat/lng rectangle plus the circle that circumscribes it.

    The search circle is sized to the farthest corner, so a cell's four
    children always cover the parent completely. Circles do not tile; using an
    inscribed circle instead would leave uncovered corners and silently lose
    places.
    """

    __slots__ = ("south", "west", "north", "east", "depth")

    def __init__(self, south, west, north, east, depth=0):
        self.south, self.west, self.north, self.east = south, west, north, east
        self.depth = depth

    @property
    def center(self):
        return ((self.south + self.north) / 2.0, (self.west + self.east) / 2.0)

    @property
    def radius_m(self):
        clat, clng = self.center
        return max(
            haversine_m(clat, clng, lat, lng)
            for lat in (self.south, self.north)
            for lng in (self.west, self.east)
        )

    def split(self):
        mlat = (self.south + self.north) / 2.0
        mlng = (self.west + self.east) / 2.0
        d = self.depth + 1
        return [
            Cell(self.south, self.west, mlat, mlng, d),
            Cell(self.south, mlng, mlat, self.east, d),
            Cell(mlat, self.west, self.north, mlng, d),
            Cell(mlat, mlng, self.north, self.east, d),
        ]


# --------------------------------------------------------------------------
# API
# --------------------------------------------------------------------------


class FatalAPIError(SystemExit):
    pass


def search_nearby(cell, types, type_mode, api_key, timeout, max_retries):
    """One searchNearby call. Returns the list of raw place dicts."""
    clat, clng = cell.center
    body = {
        "maxResultCount": MAX_RESULTS_PER_CALL,
        "rankPreference": "DISTANCE",
        "locationRestriction": {
            "circle": {
                "center": {"latitude": clat, "longitude": clng},
                "radius": min(cell.radius_m, PLACES_MAX_RADIUS_M),
            }
        },
    }
    body["includedPrimaryTypes" if type_mode == "primary" else "includedTypes"] = list(types)

    payload = json.dumps(body).encode("utf-8")
    delay = 1.0
    for attempt in range(max_retries + 1):
        req = urllib.request.Request(ENDPOINT, data=payload, method="POST")
        req.add_header("Content-Type", "application/json")
        req.add_header("X-Goog-Api-Key", api_key)
        req.add_header("X-Goog-FieldMask", FIELD_MASK)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                doc = json.loads(resp.read().decode("utf-8"))
            # An empty result is {} rather than {"places": []}.
            return doc.get("places", [])
        except urllib.error.HTTPError as exc:
            raw = exc.read().decode("utf-8", "replace")
            if exc.code in FATAL_STATUS:
                sys.stderr.write(
                    "\nFATAL: HTTP %d from Places API. Not retrying.\n%s\n"
                    "Check the API key, that Places API (New) is enabled for the\n"
                    "project, and that the field mask above is valid.\n" % (exc.code, raw[:2000])
                )
                raise FatalAPIError(2)
            if exc.code in RETRY_STATUS and attempt < max_retries:
                sys.stderr.write("  HTTP %d, retrying in %.0fs\n" % (exc.code, delay))
                time.sleep(delay)
                delay *= 2
                continue
            sys.stderr.write("\nHTTP %d from Places API:\n%s\n" % (exc.code, raw[:2000]))
            raise
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            if attempt < max_retries:
                sys.stderr.write("  %s, retrying in %.0fs\n" % (type(exc).__name__, delay))
                time.sleep(delay)
                delay *= 2
                continue
            raise


# --------------------------------------------------------------------------
# sweep
# --------------------------------------------------------------------------


class SweepStats:
    def __init__(self):
        self.requests = 0
        self.tiles_searched = 0
        self.tiles_split = 0
        self.truncated = []
        self.budget_exhausted = False


def sweep(bbox, types, type_mode, api_key, max_requests, timeout, max_retries, store):
    """Quadtree sweep. Mutates `store` (place id -> raw place). Returns stats."""
    stats = SweepStats()
    # Breadth-first on purpose. The request budget is the binding constraint on
    # a city-sized box, and BFS degrades gracefully: cutting it short leaves
    # complete coverage at a coarser resolution. Depth-first would instead
    # resolve one corner perfectly and never visit the rest of the city, which
    # biases the result list by neighbourhood.
    queue = collections.deque([Cell(*bbox)])

    while queue:
        if stats.requests >= max_requests:
            stats.budget_exhausted = True
            sys.stderr.write(
                "\nRequest budget of %d reached with %d cells still queued.\n"
                % (max_requests, len(queue))
            )
            break

        cell = queue.popleft()
        radius = cell.radius_m

        # Circle radius is capped by the API; split oversized cells unsearched.
        if radius > PLACES_MAX_RADIUS_M:
            queue.extend(cell.split())
            continue

        places = search_nearby(cell, types, type_mode, api_key, timeout, max_retries)
        stats.requests += 1
        stats.tiles_searched += 1

        new = 0
        for p in places:
            pid = p.get("id")
            if not pid:
                continue
            if pid not in store:
                new += 1
            store[pid] = p

        full_page = len(places) >= MAX_RESULTS_PER_CALL
        if full_page:
            if radius > MIN_CELL_RADIUS_M:
                queue.extend(cell.split())
                stats.tiles_split += 1
            else:
                # Cannot split further; this tile is silently truncated.
                clat, clng = cell.center
                stats.truncated.append(
                    {
                        "lat": round(clat, 6),
                        "lng": round(clng, 6),
                        "radius_m": round(radius, 1),
                        "returned": len(places),
                        "depth": cell.depth,
                        "ts": _utcnow(),
                    }
                )

        if stats.tiles_searched % 25 == 0 or new:
            sys.stderr.write(
                "\r  requests=%d tiles=%d queued=%d unique=%d truncated=%d"
                % (stats.requests, stats.tiles_searched, len(queue), len(store), len(stats.truncated))
            )
            sys.stderr.flush()

    sys.stderr.write("\n")
    return stats


# --------------------------------------------------------------------------
# cache
# --------------------------------------------------------------------------


def load_cache(path):
    if not os.path.exists(path):
        return {"meta": {"schema": 1, "created_utc": _utcnow(), "sweeps": []},
                "places": {}, "truncated_tiles": []}
    with open(path, "r", encoding="utf-8") as fh:
        doc = json.load(fh)
    doc.setdefault("meta", {}).setdefault("sweeps", [])
    doc.setdefault("places", {})
    doc.setdefault("truncated_tiles", [])
    return doc


def save_cache(path, doc):
    doc["meta"]["updated_utc"] = _utcnow()
    doc["meta"]["field_mask"] = FIELD_MASK
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(doc, fh, indent=1, sort_keys=True)
    os.replace(tmp, path)


# --------------------------------------------------------------------------
# filter and output
# --------------------------------------------------------------------------


def classify(place, min_reviews, max_rating, include_closed, drop_primary):
    """Return None if the place passes, else a short reason string."""
    rating = place.get("rating")
    count = place.get("userRatingCount")
    if rating is None or count is None:
        return "no_rating"
    if count < min_reviews:  # --min-reviews N keeps N and above
        return "below_min_reviews"
    if max_rating is not None and rating > max_rating:
        return "above_max_rating"
    status = place.get("businessStatus") or "OPERATIONAL"
    if not include_closed and status != "OPERATIONAL":
        return "not_operational"
    primary = place.get("primaryType")
    if primary and primary in drop_primary:
        return "excluded_primary_type"
    return None


def sort_key(place):
    # Worst first. Among equal ratings the place with more reviews is the more
    # defensible entry, so it ranks higher.
    return (place.get("rating"), -place.get("userRatingCount", 0))


CSV_COLUMNS = [
    "rank", "name", "rating", "review_count", "primary_type", "business_status",
    "address", "latitude", "longitude", "place_id", "maps_uri", "types",
]


def row_for(place, rank):
    loc = place.get("location") or {}
    return {
        "rank": rank,
        "name": (place.get("displayName") or {}).get("text", ""),
        "rating": place.get("rating"),
        "review_count": place.get("userRatingCount"),
        "primary_type": place.get("primaryType", ""),
        "business_status": place.get("businessStatus", ""),
        "address": place.get("formattedAddress", ""),
        "latitude": loc.get("latitude", ""),
        "longitude": loc.get("longitude", ""),
        "place_id": place.get("id", ""),
        "maps_uri": place.get("googleMapsUri", ""),
        "types": "|".join(place.get("types") or []),
    }


def write_csv(path, places, top):
    ranked = sorted(places, key=sort_key)
    if top:
        ranked = ranked[:top]
    with open(path, "w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=CSV_COLUMNS)
        writer.writeheader()
        for i, place in enumerate(ranked, 1):
            writer.writerow(row_for(place, i))
    return ranked


# --------------------------------------------------------------------------
# cli
# --------------------------------------------------------------------------


def parse_bbox(text):
    parts = [float(x) for x in text.split(",")]
    if len(parts) != 4:
        raise argparse.ArgumentTypeError("bbox needs 4 values: south,west,north,east")
    south, west, north, east = parts
    if south >= north:
        raise argparse.ArgumentTypeError("south must be less than north")
    if west >= east:
        raise argparse.ArgumentTypeError("west must be less than east")
    return (south, west, north, east)


def build_parser():
    p = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--min-reviews", type=int, default=50,
                   help="keep places with AT LEAST this many reviews, i.e. count >= N (default: 50)")
    p.add_argument("--max-rating", type=float, default=None,
                   help="keep only places rated at or below this (default: no cap)")
    p.add_argument("--bbox", type=parse_bbox, default=DEFAULT_BBOX,
                   help="south,west,north,east (default: all of Toronto)")
    p.add_argument("--types", default="restaurant",
                   help="comma-separated Places types (default: restaurant)")
    p.add_argument("--type-mode", choices=("any", "primary"), default="any",
                   help="'any' matches includedTypes and catches cuisine-specific "
                        "restaurant types; 'primary' matches includedPrimaryTypes and "
                        "is tighter but loses recall (default: any)")
    p.add_argument("--max-requests", type=int, default=800,
                   help="hard cap on billed API calls (default: 800)")
    p.add_argument("--out", default="worst_rated_toronto.csv", help="CSV output path")
    p.add_argument("--cache", default="toronto_places_cache.json", help="raw sweep cache path")
    p.add_argument("--from-cache", action="store_true",
                   help="re-filter the cache without making any API calls")
    p.add_argument("--include-closed", action="store_true",
                   help="keep temporarily and permanently closed listings")
    p.add_argument("--top", type=int, default=None, help="write only the worst N rows")
    p.add_argument("--drop-primary-types", default=",".join(DEFAULT_DROP_PRIMARY_TYPES),
                   help="primary types to exclude as not-really-restaurants")
    p.add_argument("--truncation-report", default=None,
                   help="write truncated tiles (full page at minimum radius) to this JSON path")
    p.add_argument("--timeout", type=float, default=30.0, help="per-request timeout in seconds")
    p.add_argument("--max-retries", type=int, default=4, help="retries on 429 and 5xx")
    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    cache = load_cache(args.cache)
    store = cache["places"]

    if args.from_cache:
        if not store:
            sys.stderr.write("Cache %s is empty. Run a sweep first.\n" % args.cache)
            return 1
        sys.stderr.write("Re-filtering %d cached places, zero API spend.\n" % len(store))
        stats = None
    else:
        api_key = os.environ.get("GOOGLE_MAPS_API_KEY")
        if not api_key:
            sys.stderr.write(
                "GOOGLE_MAPS_API_KEY is not set.\n"
                "Export it, or use --from-cache to work from an existing sweep.\n"
            )
            return 1
        south, west, north, east = args.bbox
        types = [t.strip() for t in args.types.split(",") if t.strip()]
        sys.stderr.write(
            "Sweeping %.4f,%.4f to %.4f,%.4f\n"
            "  types=%s mode=%s budget=%d requests (billed, Enterprise field tier)\n"
            % (south, west, north, east, ",".join(types), args.type_mode, args.max_requests)
        )
        before = len(store)
        stats = sweep(args.bbox, types, args.type_mode, api_key,
                      args.max_requests, args.timeout, args.max_retries, store)
        cache["truncated_tiles"].extend(stats.truncated)
        cache["meta"]["sweeps"].append({
            "ts": _utcnow(),
            "bbox": list(args.bbox),
            "types": types,
            "type_mode": args.type_mode,
            "requests_used": stats.requests,
            "tiles_searched": stats.tiles_searched,
            "tiles_split": stats.tiles_split,
            "tiles_truncated": len(stats.truncated),
            "budget_exhausted": stats.budget_exhausted,
            "places_before": before,
            "places_after": len(store),
        })
        save_cache(args.cache, cache)
        sys.stderr.write("Cache written to %s\n" % args.cache)

    drop_primary = {t.strip() for t in args.drop_primary_types.split(",") if t.strip()}
    kept, reasons = [], {}
    for place in store.values():
        why = classify(place, args.min_reviews, args.max_rating,
                       args.include_closed, drop_primary)
        if why is None:
            kept.append(place)
        else:
            reasons[why] = reasons.get(why, 0) + 1

    ranked = write_csv(args.out, kept, args.top)

    if args.truncation_report:
        with open(args.truncation_report, "w", encoding="utf-8") as fh:
            json.dump(cache["truncated_tiles"], fh, indent=1)

    sys.stderr.write("\n--- summary ---\n")
    if stats is not None:
        sys.stderr.write(
            "requests billed   %d\n"
            "tiles searched    %d (%d split)\n"
            "tiles truncated   %d  <- full page of %d at the %.0fm floor\n"
            % (stats.requests, stats.tiles_searched, stats.tiles_split,
               len(stats.truncated), MAX_RESULTS_PER_CALL, MIN_CELL_RADIUS_M)
        )
        if stats.budget_exhausted:
            sys.stderr.write("WARNING: budget exhausted, coverage is incomplete.\n")
        if stats.truncated:
            sys.stderr.write(
                "WARNING: %d tiles hit the floor with a full page. Those areas are\n"
                "         undercounted. Lower MIN_CELL_RADIUS_M or fall back to\n"
                "         Text Search (pageToken, up to 60 results) for them.\n"
                % len(stats.truncated)
            )
    sys.stderr.write("unique places     %d\n" % len(store))
    for why in sorted(reasons):
        sys.stderr.write("  dropped %-22s %d\n" % (why, reasons[why]))
    sys.stderr.write("kept after filter %d\n" % len(kept))
    sys.stderr.write("rows written      %d -> %s\n" % (len(ranked), args.out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
