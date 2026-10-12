"""Tests for the shared intel feed: every visitor gets the same, versioned, never-stale copy.
Covers the store (identity, dedup, versioning, canonical selection, cache invalidation, restore), persistence encoding,
and the REST + WebSocket endpoints. No network."""
import asyncio
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

from app.services.intel import feed_persistence
from app.services.intel.store import IntelStore, FEED_LIMIT


def iso(minutes_ago=0):
    return (datetime.utcnow() - timedelta(minutes=minutes_ago)).isoformat() + "Z"


def ev(i, source="USGS", lat=None, lon=None, minutes_ago=0, uid=None, dedup="spatial", severity="info"):
    lat = lat if lat is not None else (i % 80) * 1.0
    lon = lon if lon is not None else (i % 170) * 1.0 + 0.0
    e = {"id": f"{source}-{i}", "source": source, "tag": "T", "title": f"e{i}", "detail": "", "lat": lat, "lon": lon,
         "severity": severity, "ts": iso(minutes_ago), "meta": {}}
    if uid:
        e["uid"], e["dedup"] = uid, dedup
    return e


def run(coro):
    return asyncio.run(coro)


# ── identity & dedup ─────────────────────────────────────────────────────────
def test_same_uid_is_never_ingested_twice():
    s = IntelStore()
    assert run(s.ingest([ev(1, uid="USGS:a", dedup="uid")])) == 1
    assert run(s.ingest([ev(2, uid="USGS:a", dedup="uid", minutes_ago=5)])) == 0   # same real-world event, later poll


def test_identity_only_sources_keep_nearby_distinct_events():
    """Aftershocks inside one ~11 km cell are different earthquakes: only an exact uid match may drop them."""
    s = IntelStore()
    assert run(s.ingest([ev(1, lat=10.01, lon=20.01, uid="USGS:main", dedup="uid"), ev(2, lat=10.02, lon=20.02, uid="USGS:after", dedup="uid")])) == 2


def test_spatial_dedup_still_applies_to_noisy_sources():
    s = IntelStore()
    n = run(s.ingest([ev(1, "NASA FIRMS", lat=10.01, lon=20.01, uid="F:1"), ev(2, "NASA FIRMS", lat=10.02, lon=20.02, uid="F:2")]))
    assert n == 1


def test_events_without_uid_use_the_old_spatial_rule():
    s = IntelStore()
    assert run(s.ingest([ev(1, lat=5.01, lon=6.01), ev(2, lat=5.02, lon=6.02)])) == 1


# ── versioning & cache ───────────────────────────────────────────────────────
def test_version_changes_only_when_content_changes():
    s = IntelStore()
    f0 = s.feed_id
    run(s.ingest([ev(1, uid="U:1")]))
    f1 = s.feed_id
    assert f1 != f0
    run(s.ingest([ev(1, uid="U:1")]))              # all duplicates: nothing changed
    assert s.feed_id == f1


def test_cached_feed_is_reused_until_something_changes_and_never_hides_new_data():
    s = IntelStore()
    run(s.ingest([ev(1, uid="U:1")]))
    a, b = s.feed_response(), s.feed_response()
    assert a["body"] is b["body"]                  # one serialization serves every visitor
    run(s.ingest([ev(2, uid="U:2", lat=40.0, lon=50.0)]))
    c = s.feed_response()
    assert c["feed_id"] != a["feed_id"] and c["etag"] != a["etag"]
    assert {e["id"] for e in c["events"]} == {"USGS-1", "USGS-2"}   # the new event is in the very next response


def test_etag_includes_the_boot_id_so_a_restart_cannot_collide():
    assert IntelStore().feed_id != IntelStore().feed_id


# ── canonical selection ──────────────────────────────────────────────────────
def test_feed_is_identical_regardless_of_arrival_order():
    events = [ev(i, source=("USGS" if i % 3 else "GDELT"), lat=i * 0.5, lon=i * 0.7, minutes_ago=i, uid=f"U:{i}", dedup="uid") for i in range(1, 80)]
    a, b = IntelStore(), IntelStore()
    run(a.ingest(events))
    run(b.ingest(list(reversed(events))))
    assert [e["id"] for e in a.get_feed()] == [e["id"] for e in b.get_feed()]


def test_quiet_source_is_not_crowded_out_by_a_noisy_one():
    s = IntelStore()
    noisy = [ev(i, "NASA FIRMS", lat=(i % 80) * 1.0, lon=(i // 80) * 1.0 + 0.5, minutes_ago=1, uid=f"F:{i}", dedup="uid") for i in range(700)]
    quiet = [ev(1000 + i, "GDELT", lat=60 + i * 0.5, lon=100 + i * 0.5, minutes_ago=600 + i, uid=f"G:{i}", dedup="uid") for i in range(5)]
    run(s.ingest(noisy + quiet))
    feed = s.get_feed(limit=100)
    assert sum(1 for e in feed if e["source"] == "GDELT") == 5      # all 5 old GDELT events still present
    assert [e["ts"] for e in feed] == sorted((e["ts"] for e in feed), reverse=True)


def test_feed_respects_limit():
    s = IntelStore()
    run(s.ingest([ev(i, lat=(i % 80) * 1.0, lon=(i // 80) * 1.0, uid=f"U:{i}", dedup="uid") for i in range(300)]))
    assert len(s.get_feed(limit=100)) <= 100 + 40            # limit plus at most the per-source guarantee overlap
    assert len(s.get_feed(limit=FEED_LIMIT)) == 300


def test_stats_are_server_side_and_identical_for_everyone():
    s = IntelStore()
    run(s.ingest([ev(1, "USGS", severity="warn", uid="U:1", dedup="uid"), ev(2, "GDELT", lat=50, lon=60, uid="G:1")]))
    st = s.stats_snapshot()
    assert st["total"] == 2 and st["by_source"] == {"USGS": 1, "GDELT": 1} and st["by_severity"]["warn"] == 1


# ── expiry & restore ─────────────────────────────────────────────────────────
def test_purge_removes_expired_and_bumps_version():
    s = IntelStore()
    run(s.ingest([ev(1, uid="U:old", minutes_ago=60 * 30), ev(2, lat=40, lon=50, uid="U:new")]))
    before = s.feed_id
    run(s.purge_expired())
    assert s.feed_id != before and [e["id"] for e in s.get_feed()] == ["USGS-2"]


def test_restore_loads_without_broadcast_skips_expired_and_blocks_reingest():
    s = IntelStore()
    q = asyncio.Queue()
    s.subscribe(q)
    n = run(s.restore([ev(1, uid="U:1"), ev(2, lat=40, lon=50, uid="U:2"), ev(3, lat=70, lon=80, uid="U:3", minutes_ago=60 * 30)]))
    assert n == 2 and q.empty()                                 # nothing is "pushed" for restored history
    assert run(s.ingest([ev(9, uid="U:1")])) == 0               # the same real-world event can't re-enter after a restart
    assert {e["id"] for e in s.get_feed()} == {"USGS-1", "USGS-2"}


# ── persistence encoding / fake redis ────────────────────────────────────────
def test_persistence_roundtrip_and_failure_is_silent(monkeypatch):
    events = [ev(i, uid=f"U:{i}") for i in range(50)]
    assert feed_persistence.decode(feed_persistence.encode(events)) == events

    class FakeRedis:
        def __init__(self): self.d = {}
        def set(self, k, v, ex=None): self.d[k] = v
        def get(self, k): return self.d.get(k)
    r = FakeRedis()
    monkeypatch.setattr(feed_persistence, "_redis", lambda: r)
    assert feed_persistence.save(events) is True and feed_persistence.load() == events
    monkeypatch.setattr(feed_persistence, "_redis", lambda: None)               # no Redis configured
    assert feed_persistence.save(events) is False and feed_persistence.load() is None
    monkeypatch.setattr(feed_persistence, "_redis", lambda: (_ for _ in ()).throw(RuntimeError("down")))
    assert feed_persistence.save(events) is False and feed_persistence.load() is None   # never raises into the scheduler


def test_scheduler_restore_then_backfill_flow(monkeypatch):
    """What a cold start does: restore persisted events, then ingest the backfill; overlap is de-duplicated by uid."""
    s = IntelStore()
    persisted = [ev(1, uid="U:1"), ev(2, lat=40, lon=50, uid="U:2")]
    run(s.restore(persisted))
    backfill = [ev(3, uid="U:1", minutes_ago=3), ev(4, lat=40, lon=50, uid="U:2", minutes_ago=3), ev(5, lat=60, lon=70, uid="U:3")]
    assert run(s.ingest(backfill)) == 1 and len(s.get_feed()) == 3


# ── endpoints ────────────────────────────────────────────────────────────────
@pytest.fixture
def client(monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    import app.api.intel_endpoints as ep
    store = IntelStore()
    monkeypatch.setattr(ep, "intel_store", store)
    app = FastAPI()
    app.include_router(ep.router, prefix="/api/v1")
    return TestClient(app), store


def test_rest_feed_etag_304_and_refresh_on_new_data(client):
    c, store = client
    run(store.ingest([ev(1, uid="U:1")]))
    r = c.get("/api/v1/intel/feed")
    assert r.status_code == 200 and r.headers["cache-control"] == "no-cache" and r.json()["count"] == 1
    etag = r.headers["etag"]
    assert c.get("/api/v1/intel/feed", headers={"If-None-Match": etag}).status_code == 304   # unchanged: tiny response
    run(store.ingest([ev(2, lat=40, lon=50, uid="U:2")]))
    r2 = c.get("/api/v1/intel/feed", headers={"If-None-Match": etag})
    assert r2.status_code == 200 and r2.json()["count"] == 2 and r2.headers["etag"] != etag   # old copy is NOT reused


def test_two_visitors_get_the_identical_feed_over_rest_and_websocket(client):
    c, store = client
    run(store.ingest([ev(i, source=("USGS" if i % 2 else "GDELT"), lat=i * 0.9, lon=i * 1.3, uid=f"U:{i}", dedup="uid") for i in range(1, 40)]))
    rest_ids = [e["id"] for e in c.get("/api/v1/intel/feed").json()["events"]]
    with c.websocket_connect("/api/v1/intel/ws") as ws1, c.websocket_connect("/api/v1/intel/ws") as ws2:
        s1, s2 = ws1.receive_json(), ws2.receive_json()
    assert s1["type"] == "snapshot" and s1["feed_id"] == store.feed_id
    assert [e["id"] for e in s1["data"]] == [e["id"] for e in s2["data"]] == rest_ids
    assert s1["stats"] == s2["stats"] == store.stats_snapshot()


def test_websocket_live_event_carries_feed_id_and_stats(client):
    c, store = client
    run(store.ingest([ev(1, uid="U:1")]))
    with c.websocket_connect("/api/v1/intel/ws") as ws:
        ws.receive_json()                                           # snapshot
        run(store.ingest([ev(2, lat=40, lon=50, uid="U:2")]))
        msg = ws.receive_json()
    assert msg["type"] == "event" and msg["data"]["id"] == "USGS-2" and msg["feed_id"] == store.feed_id and msg["stats"]["total"] == 2


def test_overflowed_subscriber_is_detected_so_the_device_can_be_told_to_resync():
    s = IntelStore()
    q = asyncio.Queue(maxsize=2)
    s.subscribe(q)
    assert s.is_subscribed(q)
    run(s.ingest([ev(i, lat=i * 3.0, lon=i * 3.0, uid=f"U:{i}", dedup="uid") for i in range(1, 6)]))   # more than the queue holds
    assert not s.is_subscribed(q)           # dropped by the store: the websocket loop must notice and send "resync"
