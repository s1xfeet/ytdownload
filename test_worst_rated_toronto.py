#!/usr/bin/env python3
"""Mock-API tests for worst_rated_toronto.py.

The fake lives at the urllib.request.urlopen seam rather than at
search_nearby, so these exercise the real request body, field mask and
response parsing - the parts that have never met the live API.
"""

import io
import json
import math
import os
import tempfile
import unittest
import urllib.error
import urllib.request
from unittest import mock

import worst_rated_toronto as m


class FakeResponse:
    def __init__(self, payload):
        self._body = json.dumps(payload).encode("utf-8")

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class World:
    """A synthetic city. Answers circle queries the way Nearby Search does."""

    def __init__(self, places):
        self.places = places
        self.requests = []
        self.total_returned = 0

    def urlopen(self, req, timeout=None):
        body = json.loads(req.data.decode("utf-8"))
        self.requests.append((req, body))
        circle = body["locationRestriction"]["circle"]
        clat = circle["center"]["latitude"]
        clng = circle["center"]["longitude"]
        radius = circle["radius"]
        hits = [
            p for p in self.places
            if m.haversine_m(clat, clng,
                             p["location"]["latitude"],
                             p["location"]["longitude"]) <= radius
        ]
        hits.sort(key=lambda p: m.haversine_m(
            clat, clng, p["location"]["latitude"], p["location"]["longitude"]))
        page = hits[:m.MAX_RESULTS_PER_CALL]
        self.total_returned += len(page)
        return FakeResponse({"places": page} if page else {})


def make_place(pid, lat, lng, rating=4.0, count=100,
               status="OPERATIONAL", primary="restaurant", name=None):
    return {
        "id": pid,
        "displayName": {"text": name or pid},
        "rating": rating,
        "userRatingCount": count,
        "businessStatus": status,
        "formattedAddress": "%s Fake St, Toronto" % pid,
        "location": {"latitude": lat, "longitude": lng},
        "primaryType": primary,
        "types": [primary, "food", "point_of_interest"],
        "googleMapsUri": "https://maps.google.com/?cid=%s" % pid,
    }


BBOX = (43.63, -79.42, 43.67, -79.36)


class GeometryTests(unittest.TestCase):
    def test_children_cover_parent(self):
        """The four children must cover every point of the parent rectangle.

        Inscribed child circles would leave uncovered corners. This is the
        property that keeps the quadtree from silently losing places.
        """
        parent = m.Cell(*BBOX)
        children = parent.split()
        for i in range(60):
            for j in range(60):
                lat = parent.south + (parent.north - parent.south) * i / 59.0
                lng = parent.west + (parent.east - parent.west) * j / 59.0
                covered = any(
                    m.haversine_m(lat, lng, *c.center) <= c.radius_m for c in children
                )
                self.assertTrue(covered, "gap at %.5f,%.5f" % (lat, lng))

    def test_split_preserves_area(self):
        parent = m.Cell(*BBOX)
        kids = parent.split()
        self.assertEqual(len(kids), 4)
        self.assertEqual(min(k.south for k in kids), parent.south)
        self.assertEqual(max(k.north for k in kids), parent.north)
        self.assertEqual(min(k.west for k in kids), parent.west)
        self.assertEqual(max(k.east for k in kids), parent.east)
        for k in kids:
            self.assertEqual(k.depth, parent.depth + 1)

    def test_radius_is_circumscribed(self):
        cell = m.Cell(*BBOX)
        clat, clng = cell.center
        corner = m.haversine_m(clat, clng, cell.south, cell.west)
        self.assertAlmostEqual(cell.radius_m, corner, delta=1.0)


class SweepTests(unittest.TestCase):
    def _sweep(self, places, max_requests=5000):
        world = World(places)
        store = {}
        with mock.patch.object(urllib.request, "urlopen", world.urlopen):
            stats = m.sweep(BBOX, ["restaurant"], "any", "fake-key",
                            max_requests, 30.0, 0, store)
        return world, store, stats

    def test_finds_every_place_in_a_dense_grid(self):
        """Full coverage: a 12x12 grid is 144 places, far past the 20 cap."""
        places = []
        for i in range(12):
            for j in range(12):
                lat = 43.635 + 0.0028 * i
                lng = -79.415 + 0.0042 * j
                places.append(make_place("p%d_%d" % (i, j), lat, lng))
        world, store, stats = self._sweep(places)
        self.assertEqual(len(store), 144, "quadtree lost places")
        self.assertGreater(stats.tiles_split, 0)

    def test_dedupes_by_place_id(self):
        """Overlapping child circles re-return the same places; the store
        must collapse them to one entry each."""
        places = []
        for i in range(8):
            for j in range(8):
                places.append(make_place("p%d_%d" % (i, j),
                                         43.640 + 0.0035 * i,
                                         -79.410 + 0.0055 * j))
        world, store, stats = self._sweep(places)
        self.assertEqual(len(store), 64)
        self.assertGreater(stats.tiles_searched, 1)
        self.assertGreater(world.total_returned, len(store),
                           "tiles never overlapped, so dedupe was not exercised")

    def test_empty_response_object_is_handled(self):
        """Places API returns {} not {"places": []} when nothing matches."""
        world, store, stats = self._sweep([])
        self.assertEqual(store, {})
        self.assertEqual(stats.tiles_searched, 1)

    def test_request_shape_and_field_mask(self):
        world, store, stats = self._sweep([make_place("a", 43.65, -79.39)])
        req, body = world.requests[0]
        self.assertEqual(req.get_header("X-goog-api-key"), "fake-key")
        self.assertIn("places.rating", req.get_header("X-goog-fieldmask"))
        self.assertIn("places.userratingcount".lower(),
                      req.get_header("X-goog-fieldmask").lower())
        self.assertEqual(body["maxResultCount"], 20)
        self.assertEqual(body["includedTypes"], ["restaurant"])
        self.assertNotIn("includedPrimaryTypes", body)
        self.assertLessEqual(body["locationRestriction"]["circle"]["radius"],
                             m.PLACES_MAX_RADIUS_M)

    def test_primary_type_mode_switches_the_field(self):
        world = World([make_place("a", 43.65, -79.39)])
        store = {}
        with mock.patch.object(urllib.request, "urlopen", world.urlopen):
            m.sweep(BBOX, ["restaurant"], "primary", "k", 5, 30.0, 0, store)
        _, body = world.requests[0]
        self.assertEqual(body["includedPrimaryTypes"], ["restaurant"])
        self.assertNotIn("includedTypes", body)

    def test_budget_cap_is_respected(self):
        places = [make_place("p%d" % i, 43.64 + 0.0001 * i, -79.40) for i in range(400)]
        world, store, stats = self._sweep(places, max_requests=7)
        self.assertEqual(stats.requests, 7)
        self.assertTrue(stats.budget_exhausted)

    def test_truncation_is_recorded_at_the_floor(self):
        """21+ places inside a 50m circle cannot be split apart; that must
        be reported rather than silently dropped."""
        places = [make_place("f%d" % i, 43.6500 + 0.000002 * i, -79.3900)
                  for i in range(25)]
        world, store, stats = self._sweep(places)
        self.assertTrue(stats.truncated, "food-court truncation went unreported")
        for t in stats.truncated:
            self.assertLessEqual(t["radius_m"], m.MIN_CELL_RADIUS_M)
            self.assertEqual(t["returned"], m.MAX_RESULTS_PER_CALL)
            self.assertIn("lat", t)
            self.assertIn("lng", t)
        # Overlapping neighbour tiles at the floor often recover the extras,
        # so a truncated tile signals risk of undercounting, not certain loss.
        self.assertEqual(len(store), 25)

    def test_tight_cluster_actually_loses_places(self):
        """The truncation warning is not theoretical. Pack 60 places into
        roughly a metre and no circle at the 50m floor can separate them."""
        places = [make_place("t%d" % i, 43.6500 + 0.0000001 * i, -79.3900)
                  for i in range(60)]
        world, store, stats = self._sweep(places)
        self.assertTrue(stats.truncated)
        self.assertLess(len(store), 60, "expected real loss inside the floor")


class RetryTests(unittest.TestCase):
    def _err(self, code):
        return urllib.error.HTTPError("u", code, "b", {}, io.BytesIO(b'{"error":"x"}'))

    def test_fatal_status_exits_without_retry(self):
        for code in (400, 401, 403):
            calls = []

            def boom(req, timeout=None):
                calls.append(1)
                raise self._err(code)

            with mock.patch.object(urllib.request, "urlopen", boom):
                with self.assertRaises(SystemExit):
                    m.search_nearby(m.Cell(*BBOX), ["restaurant"], "any",
                                    "k", 30.0, 4)
            self.assertEqual(len(calls), 1, "retried a fatal %d" % code)

    def test_retryable_status_recovers(self):
        state = {"n": 0}

        def flaky(req, timeout=None):
            state["n"] += 1
            if state["n"] < 3:
                raise self._err(503)
            return FakeResponse({"places": [make_place("ok", 43.65, -79.39)]})

        with mock.patch.object(urllib.request, "urlopen", flaky):
            with mock.patch("time.sleep"):
                out = m.search_nearby(m.Cell(*BBOX), ["restaurant"], "any",
                                      "k", 30.0, 4)
        self.assertEqual(len(out), 1)
        self.assertEqual(state["n"], 3)

    def test_429_is_retried(self):
        state = {"n": 0}

        def flaky(req, timeout=None):
            state["n"] += 1
            if state["n"] < 2:
                raise self._err(429)
            return FakeResponse({})

        with mock.patch.object(urllib.request, "urlopen", flaky):
            with mock.patch("time.sleep"):
                out = m.search_nearby(m.Cell(*BBOX), ["restaurant"], "any",
                                      "k", 30.0, 4)
        self.assertEqual(out, [])


class FilterTests(unittest.TestCase):
    def test_min_reviews_is_inclusive(self):
        """--min-reviews 50 keeps exactly 50. This is the >= vs > question."""
        self.assertIsNone(m.classify(make_place("a", 0, 0, 2.0, 50),
                                     50, None, False, set()))
        self.assertEqual(m.classify(make_place("b", 0, 0, 2.0, 49),
                                    50, None, False, set()), "below_min_reviews")

    def test_unrated_places_are_dropped(self):
        p = make_place("a", 0, 0)
        del p["rating"]
        self.assertEqual(m.classify(p, 50, None, False, set()), "no_rating")

    def test_closed_listings(self):
        p = make_place("a", 0, 0, 2.0, 100, status="CLOSED_PERMANENTLY")
        self.assertEqual(m.classify(p, 50, None, False, set()), "not_operational")
        self.assertIsNone(m.classify(p, 50, None, True, set()))

    def test_max_rating_cap(self):
        p = make_place("a", 0, 0, 3.5, 100)
        self.assertEqual(m.classify(p, 50, 3.0, False, set()), "above_max_rating")
        self.assertIsNone(m.classify(p, 50, 4.0, False, set()))

    def test_junk_primary_types_dropped(self):
        p = make_place("a", 0, 0, 2.0, 100, primary="gas_station")
        self.assertEqual(m.classify(p, 50, None, False, {"gas_station"}),
                         "excluded_primary_type")

    def test_sort_is_worst_first_with_review_count_tiebreak(self):
        places = [
            make_place("mid", 0, 0, 3.0, 100),
            make_place("bad_few", 0, 0, 2.1, 60),
            make_place("bad_many", 0, 0, 2.1, 900),
            make_place("good", 0, 0, 4.8, 100),
        ]
        order = [p["id"] for p in sorted(places, key=m.sort_key)]
        self.assertEqual(order, ["bad_many", "bad_few", "mid", "good"])


class CsvTests(unittest.TestCase):
    def test_csv_roundtrip(self):
        import csv as _csv
        places = [make_place("bad", 43.65, -79.39, 1.9, 220, name="Bad Diner"),
                  make_place("ok", 43.66, -79.38, 4.5, 300)]
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "out.csv")
            ranked = m.write_csv(path, places, None)
            with open(path, encoding="utf-8") as fh:
                rows = list(_csv.DictReader(fh))
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["name"], "Bad Diner")
        self.assertEqual(rows[0]["rank"], "1")
        self.assertEqual(rows[0]["rating"], "1.9")
        self.assertEqual(rows[0]["review_count"], "220")
        self.assertEqual(list(rows[0].keys()), m.CSV_COLUMNS)

    def test_top_limits_rows(self):
        places = [make_place("p%d" % i, 43.65, -79.39, 1.0 + i * 0.1, 100)
                  for i in range(10)]
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "out.csv")
            ranked = m.write_csv(path, places, 3)
        self.assertEqual(len(ranked), 3)


class CacheTests(unittest.TestCase):
    def test_cache_roundtrip_and_from_cache_needs_no_key(self):
        with tempfile.TemporaryDirectory() as d:
            cache_path = os.path.join(d, "cache.json")
            csv_path = os.path.join(d, "out.csv")
            doc = m.load_cache(cache_path)
            doc["places"]["bad"] = make_place("bad", 43.65, -79.39, 1.7, 300)
            doc["places"]["thin"] = make_place("thin", 43.65, -79.39, 1.2, 5)
            m.save_cache(cache_path, doc)

            # No GOOGLE_MAPS_API_KEY in the environment, no network patched:
            # --from-cache must still work.
            env = dict(os.environ)
            env.pop("GOOGLE_MAPS_API_KEY", None)
            with mock.patch.dict(os.environ, env, clear=True):
                rc = m.main(["--from-cache", "--cache", cache_path,
                             "--out", csv_path, "--min-reviews", "50"])
            self.assertEqual(rc, 0)
            with open(csv_path, encoding="utf-8") as fh:
                body = fh.read()
        self.assertIn("bad", body)
        self.assertNotIn("thin", body)  # 5 reviews is below the floor

    def test_sweep_without_key_fails_cleanly(self):
        env = dict(os.environ)
        env.pop("GOOGLE_MAPS_API_KEY", None)
        with tempfile.TemporaryDirectory() as d:
            with mock.patch.dict(os.environ, env, clear=True):
                rc = m.main(["--cache", os.path.join(d, "c.json"),
                             "--out", os.path.join(d, "o.csv")])
        self.assertEqual(rc, 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
