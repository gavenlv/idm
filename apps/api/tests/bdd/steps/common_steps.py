"""BDD shared step definitions (Given / When / Then).

Re-uses the `bdd_client` fixture from `tests/conftest.py` (sync wrapper
around httpx.AsyncClient + ASGI transport). pytest-bdd generates sync
test functions per scenario, so a sync client is required.
"""
from __future__ import annotations

import asyncio
import uuid as _uuid
from typing import Any

import pytest
from pytest_bdd import given, then, when


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _ctx(client) -> dict[str, Any]:
    """A tiny per-scenario store, attached to the client object.

    pytest-bdd's `context` fixture is convenient but is not always
    available across all steps; using the client as a key is simpler.
    """
    if not hasattr(client, "_bdd_store"):
        client._bdd_store = {}
    return client._bdd_store


def _run(coro):
    """Run an awaitable to completion, even if a loop is already running.

    Inside pytest-bdd, the test function is sync, so we can call
    `asyncio.run`. But if a test was run via `async def`, the loop is
    open and we fall back to `nest_asyncio`.
    """
    try:
        loop = asyncio.get_event_loop()
    except RuntimeError:
        loop = None
    if loop and loop.is_running():
        try:
            import nest_asyncio  # type: ignore
        except ImportError:  # pragma: no cover
            raise RuntimeError(
                "nest_asyncio is required when invoking sync BDD steps "
                "from inside an async test (e.g. `pytest --asyncio-mode=auto`)"
            )
        nest_asyncio.apply()
        return loop.run_until_complete(coro)
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# Given — arrange (seed KG with services / databases / schemas / assets)
# ---------------------------------------------------------------------------

@given("the IDM API is running")
def api_running(bdd_client):
    r = bdd_client.get("/health")
    assert r.status_code == 200, f"health failed: {r.status_code} {r.text}"


@given('a clickhouse service "shop" with database "shop" and schema "default"')
def seed_service_db_schema(bdd_client, app_with_db):
    from idm_api.db import _session_factory
    from idm_kg.models.database import Database
    from idm_kg.models.schema import Schema
    from idm_kg.models.service import Service

    sid, did, schid = _uuid.uuid4(), _uuid.uuid4(), _uuid.uuid4()

    async def _seed():
        async with _session_factory() as db:
            svc = Service(id=sid, name="shop", type="clickhouse", description="Shop CH")
            db.add(svc)
            await db.flush()
            d = Database(id=did, service_id=svc.id, name="shop", description="Shop DB")
            db.add(d)
            await db.flush()
            sch = Schema(id=schid, database_id=d.id, name="default")
            db.add(sch)
            await db.commit()
        return schid

    schid = _run(_seed())
    _ctx(bdd_client).update(
        service_id=str(sid), database_id=str(did), schema_id=str(schid),
    )


@given('a table asset "shop.default.orders_daily" with 5 columns')
def seed_table(bdd_client, app_with_db):
    from idm_api.db import _session_factory
    from idm_kg.models.column_asset import ColumnAsset
    from idm_kg.models.table_asset import TableAsset

    schid = _ctx(bdd_client)["schema_id"]
    tid = _uuid.uuid4()
    cols = [
        ("order_id", "UInt64", True),
        ("user_id", "UInt64", False),
        ("amount", "Float64", False),
        ("currency", "String", False),
        ("created_at", "DateTime", False),
    ]

    async def _seed():
        async with _session_factory() as db:
            t = TableAsset(
                id=tid, fqn="shop.default.orders_daily", name="orders_daily",
                schema_id=_uuid.UUID(schid), asset_type="table",
                tier="critical", status="active", description=None,
                column_count=5, row_count=10000,
            )
            db.add(t)
            await db.flush()
            for i, (n, dt, pk) in enumerate(cols):
                db.add(ColumnAsset(
                    id=_uuid.uuid4(), table_id=tid, name=n, ordinal=i,
                    data_type=dt, nullable=False, is_primary_key=pk,
                    is_partition_key=False, pii_class="none", pii_confidence=0,
                    sample_values=[], null_ratio=0,
                ))
            await db.commit()
        return tid

    tid = _run(_seed())
    _ctx(bdd_client)["table_id"] = str(tid)
    _ctx(bdd_client)["table_fqn"] = "shop.default.orders_daily"


@given('an owner "alice@example.com" verified for "shop.default.orders_daily"')
def seed_owner(bdd_client, app_with_db):
    from idm_api.db import _session_factory
    from idm_kg.models.owner import AssetOwner

    tid = _ctx(bdd_client)["table_id"]

    async def _seed():
        async with _session_factory() as db:
            db.add(AssetOwner(
                id=_uuid.uuid4(),
                table_id=_uuid.UUID(tid),
                table_fqn="shop.default.orders_daily",
                user_email="alice@example.com",
                user_name="Alice",
                team="data-eng",
                role="owner",
                source="git_blame",
                confidence=0.85,
                is_verified=True,
            ))
            await db.commit()

    _run(_seed())


@given('a glossary term "GMV" defined as "Gross Merchandise Volume"')
def seed_glossary(bdd_client, app_with_db):
    from idm_api.db import _session_factory
    from idm_kg.models.glossary import GlossaryTerm

    async def _seed():
        async with _session_factory() as db:
            db.add(GlossaryTerm(
                id=_uuid.uuid4(),
                term="GMV",
                definition="Gross Merchandise Volume",
                domain="sales",
                owner_team="bi",
                synonyms=["total_sales", "revenue"],
            ))
            await db.commit()

    _run(_seed())


# ---------------------------------------------------------------------------
# M2 — AI Governance: seed a second table / pending suggestions
# ---------------------------------------------------------------------------

@given('a table asset "shop.default.orders_summary" with 3 columns')
def seed_table_summary(bdd_client, app_with_db):
    from idm_api.db import _session_factory
    from idm_kg.models.column_asset import ColumnAsset
    from idm_kg.models.table_asset import TableAsset

    schid = _ctx(bdd_client)["schema_id"]
    tid = _uuid.uuid4()
    cols = [
        ("user_id", "UInt64", True),
        ("total", "Float64", False),
        ("last_order_at", "DateTime", False),
    ]

    async def _seed():
        async with _session_factory() as db:
            t = TableAsset(
                id=tid, fqn="shop.default.orders_summary", name="orders_summary",
                schema_id=_uuid.UUID(schid), asset_type="view",
                tier="normal", status="active", description="Aggregated by user",
                column_count=3, row_count=5000,
            )
            db.add(t)
            await db.flush()
            for i, (n, dt, pk) in enumerate(cols):
                db.add(ColumnAsset(
                    id=_uuid.uuid4(), table_id=tid, name=n, ordinal=i,
                    data_type=dt, nullable=False, is_primary_key=pk,
                    is_partition_key=False, pii_class="none", pii_confidence=0,
                    sample_values=[], null_ratio=0,
                ))
            await db.commit()
        return tid

    tid = _run(_seed())
    _ctx(bdd_client)["summary_table_id"] = str(tid)


@given('a lineage edge from "shop.default.orders_daily" to "shop.default.orders_summary"')
def seed_lineage_edge(bdd_client, app_with_db):
    from idm_api.db import _session_factory
    from idm_kg.models.table_lineage import TableLineage

    up_id = _ctx(bdd_client)["table_id"]
    dn_id = _ctx(bdd_client)["summary_table_id"]

    async def _seed():
        async with _session_factory() as db:
            # 幂等: 删了重建
            from sqlalchemy import select
            existing = (
                await db.execute(
                    select(TableLineage).where(
                        TableLineage.upstream_id == _uuid.UUID(up_id),
                        TableLineage.downstream_id == _uuid.UUID(dn_id),
                    )
                )
            ).scalars().all()
            for e in existing:
                await db.delete(e)
            await db.flush()
            db.add(
                TableLineage(
                    upstream_id=_uuid.UUID(up_id),
                    downstream_id=_uuid.UUID(dn_id),
                    transform_type="dbt_model",
                    job_id="bdd_seed",
                    confidence=1.0,
                    source="dbt_manifest",
                    extra={"seed": "bdd"},
                )
            )
            await db.commit()

    _run(_seed())


@given('a pending "glossary" suggestion for "shop.default.orders_daily" with term "GMV"')
def seed_glossary_suggestion(bdd_client, app_with_db):
    from idm_api.db import _session_factory
    from idm_kg.models.ai_suggestion import AISuggestion
    from idm_kg.models.glossary import GlossaryTerm
    from sqlalchemy import select

    tid = _ctx(bdd_client)["table_id"]

    async def _seed():
        async with _session_factory() as db:
            term = (
                await db.execute(select(GlossaryTerm).where(GlossaryTerm.name == "GMV"))
            ).scalar_one_or_none()
            assert term is not None, "GMV glossary term must be seeded first"
            sug = AISuggestion(
                suggestion_type="glossary",
                target_type="table",
                target_id=_uuid.UUID(tid),
                payload={
                    "term_id": str(term.id),
                    "term": "GMV",
                    "table_fqn": "shop.default.orders_daily",
                    "confidence": 0.85,
                },
                rationale="BDD test seed",
                confidence=0.85,
                model="bdd",
                skill="map_glossary",
                status="pending",
            )
            db.add(sug)
            await db.flush()
            await db.commit()
            return str(sug.id)

    sid = _run(_seed())
    _ctx(bdd_client)["latest_suggestion_id"] = sid


@given('a pending "owner" suggestion for "shop.default.orders_daily" with email "bob@example.com"')
def seed_owner_suggestion(bdd_client, app_with_db):
    from idm_api.db import _session_factory
    from idm_kg.models.ai_suggestion import AISuggestion

    tid = _ctx(bdd_client)["table_id"]

    async def _seed():
        async with _session_factory() as db:
            sug = AISuggestion(
                suggestion_type="owner",
                target_type="table",
                target_id=_uuid.UUID(tid),
                payload={
                    "user_email": "bob@example.com",
                    "user_name": "Bob",
                    "team": "data-shop",
                    "role": "owner",
                    "table_fqn": "shop.default.orders_daily",
                },
                rationale="BDD owner seed",
                confidence=0.78,
                model="bdd",
                skill="infer_table_owners",
                status="pending",
            )
            db.add(sug)
            await db.flush()
            await db.commit()
            return str(sug.id)

    sid = _run(_seed())
    _ctx(bdd_client)["latest_suggestion_id"] = sid


# ---------------------------------------------------------------------------
# When — act (HTTP calls)
# ---------------------------------------------------------------------------

@when("I list assets")
def list_assets(bdd_client):
    _ctx(bdd_client)["response"] = bdd_client.get("/api/v1/assets?limit=20")


@when('I list suggestions of type "insight"')
def list_insights(bdd_client):
    _ctx(bdd_client)["response"] = bdd_client.get(
        "/api/v1/suggestions?suggestion_type=insight&limit=20",
    )


@when('I list owners for service "shop"')
def list_owners(bdd_client):
    _ctx(bdd_client)["response"] = bdd_client.get("/api/v1/owners?service=shop")


@when("I list glossary terms")
def list_glossary(bdd_client):
    _ctx(bdd_client)["response"] = bdd_client.get("/api/v1/glossary?limit=20")


@when('I run skill "detect_anomalies" with apply=false')
def run_detect_anomalies(bdd_client):
    _ctx(bdd_client)["response"] = bdd_client.post("/api/v1/skills/run", json={
        "name": "detect_anomalies",
        "inputs": {"service": "shop", "apply": False, "skip_drift": True, "skip_null": True},
    })


@when('I run skill "map_glossary" with apply=false')
def run_map_glossary(bdd_client):
    _ctx(bdd_client)["response"] = bdd_client.post("/api/v1/skills/run", json={
        "name": "map_glossary",
        "inputs": {"service": "shop", "apply": False, "use_llm": False},
    })


@when('I run skill "profiler" with sample_rows=10')
def run_profiler(bdd_client):
    _ctx(bdd_client)["response"] = bdd_client.post("/api/v1/skills/run", json={
        "name": "profiler",
        "inputs": {"service": "shop", "sample_rows": 10, "apply": False},
    })


@when('I run skill "compose_insight" with days=7')
def run_compose_insight(bdd_client):
    _ctx(bdd_client)["response"] = bdd_client.post("/api/v1/skills/run", json={
        "name": "compose_insight",
        "inputs": {"service": "shop", "days": 7, "apply": True, "channel": "in_app"},
    })


@when('I run skill "nl2sql" with question "How many rows in orders_daily"')
def run_nl2sql(bdd_client):
    _ctx(bdd_client)["response"] = bdd_client.post("/api/v1/skills/run", json={
        "name": "nl2sql",
        "inputs": {
            "question": "How many rows in orders_daily",
            "service": "shop",
            "dry_run": True,
        },
    })


@when('I run extract_sql_lineage with downstream "shop.default.orders_summary"')
def run_extract_sql_lineage(bdd_client):
    _ctx(bdd_client)["response"] = bdd_client.post("/api/v1/skills/run", json={
        "name": "extract_sql_lineage",
        "inputs": {
            "sql": "INSERT INTO shop.default.orders_summary SELECT user_id, sum(amount) FROM shop.default.orders_daily GROUP BY user_id",
            "downstream_fqn": "shop.default.orders_summary",
            "service": "shop",
            "apply": False,
        },
    })


@when('I search globally for "orders"')
def search_orders(bdd_client):
    _ctx(bdd_client)["response"] = bdd_client.get("/api/v1/search?q=orders&limit=10")


@when("I get health of MCP sidecars")
def mcp_health(bdd_client):
    _ctx(bdd_client)["response"] = bdd_client.get("/api/v1/skills/mcp/health")


# ---------------------------------------------------------------------------
# M2 AI Governance — suggestion seed + approval
# ---------------------------------------------------------------------------

@when('I approve the latest suggestion')
def approve_latest(bdd_client):
    sid = _ctx(bdd_client).get("latest_suggestion_id")
    assert sid, "no suggestion seeded; missing 'latest_suggestion_id' in ctx"
    _ctx(bdd_client)["response"] = bdd_client.post(
        f"/api/v1/suggestions/{sid}/approve",
        json={"review_note": "BDD test approval"},
        headers={"X-IDM-Actor": "bdd@example.com", "X-IDM-Roles": "owner,admin"},
    )


# ---------------------------------------------------------------------------
# M3 Lineage / Impact
# ---------------------------------------------------------------------------

@when('I get lineage of "shop.default.orders_daily"')
def get_lineage(bdd_client):
    tid = _ctx(bdd_client)["table_id"]
    _ctx(bdd_client)["response"] = bdd_client.get(f"/api/v1/assets/{tid}/lineage?depth=3")


@when('I get impact for "shop.default.orders_daily" direction "downstream" depth 2')
def get_impact(bdd_client):
    tid = _ctx(bdd_client)["table_id"]
    _ctx(bdd_client)["response"] = bdd_client.get(
        f"/api/v1/impact/{tid}?direction=downstream&depth=2"
    )


# ---------------------------------------------------------------------------
# M4 Quality + ChatBI
# ---------------------------------------------------------------------------

@when("I get the quality dashboard")
def get_quality_dashboard(bdd_client):
    _ctx(bdd_client)["response"] = bdd_client.get("/api/v1/quality/dashboard")


@when('I create a freshness rule for "shop.default.orders_daily"')
def create_freshness_rule(bdd_client):
    tid = _ctx(bdd_client)["table_id"]
    _ctx(bdd_client)["response"] = bdd_client.post(
        "/api/v1/quality/rules",
        json={
            "table_id": tid,
            "name": "bdd_freshness_orders",
            "rule_type": "freshness",
            "severity": "warning",
            "definition": {"column": "created_at", "threshold_minutes": 60},
            "schedule": "0 * * * *",
            "description": "BDD test rule",
        },
    )


@when('I list quality rules for "shop.default.orders_daily"')
def list_quality_rules(bdd_client):
    tid = _ctx(bdd_client)["table_id"]
    _ctx(bdd_client)["response"] = bdd_client.get(f"/api/v1/quality/rules?table_id={tid}")


@when('I ask ChatBI "How many rows in orders_daily"')
def ask_chatbi(bdd_client):
    _ctx(bdd_client)["response"] = bdd_client.post(
        "/api/v1/chatbi",
        json={"question": "How many rows in orders_daily", "service": "shop", "dry_run": True},
    )


# ---------------------------------------------------------------------------
# M5 idm-self MCP
# ---------------------------------------------------------------------------

@when("I list idm-self tools")
def list_idm_self_tools(bdd_client):
    _ctx(bdd_client)["response"] = bdd_client.get("/api/v1/mcp/idm-self/tools")


@when('I call idm-self tool "idm.search_assets" with q "orders"')
def call_idm_self_search(bdd_client):
    _ctx(bdd_client)["response"] = bdd_client.post(
        "/api/v1/mcp/idm-self/call",
        json={"name": "idm.search_assets", "arguments": {"q": "orders", "limit": 5}},
    )


@when('I call idm-self tool "idm.list_skills"')
def call_idm_self_list_skills(bdd_client):
    _ctx(bdd_client)["response"] = bdd_client.post(
        "/api/v1/mcp/idm-self/call",
        json={"name": "idm.list_skills", "arguments": {}},
    )


# ---------------------------------------------------------------------------
# Then — assert
# ---------------------------------------------------------------------------

@then("the response status is 200")
def status_200(bdd_client):
    r = _ctx(bdd_client)["response"]
    assert r.status_code == 200, f"expected 200, got {r.status_code}: {r.text}"


@then("the response status is 2xx")
def status_2xx(bdd_client):
    r = _ctx(bdd_client)["response"]
    assert 200 <= r.status_code < 300, f"got {r.status_code}: {r.text}"


@then("the response body is valid JSON")
def body_json(bdd_client):
    r = _ctx(bdd_client)["response"]
    _ctx(bdd_client)["body"] = r.json()
    assert _ctx(bdd_client)["body"] is not None


@then("the response contains at least {n:d} asset")
def contains_assets(bdd_client, n):
    body = _ctx(bdd_client).get("body") or _ctx(bdd_client)["response"].json()
    items = body.get("items", body) if isinstance(body, dict) else body
    assert len(items) >= n, f"expected >= {n} assets, got {len(items)}: {body}"


@then('the response contains the table "shop.default.orders_daily"')
def contains_target_table(bdd_client):
    body = _ctx(bdd_client).get("body") or _ctx(bdd_client)["response"].json()
    items = body.get("items", [])
    fqns = [a.get("fqn") for a in items]
    assert "shop.default.orders_daily" in fqns, f"target not in: {fqns}"


@then('the response contains owner "alice@example.com"')
def contains_alice(bdd_client):
    body = _ctx(bdd_client).get("body") or _ctx(bdd_client)["response"].json()
    items = body.get("items", [])
    emails = [o.get("user_email") for o in items]
    assert "alice@example.com" in emails, f"alice not in: {emails}"


@then('the response contains glossary term "GMV"')
def contains_gmv(bdd_client):
    body = _ctx(bdd_client).get("body") or _ctx(bdd_client)["response"].json()
    items = body.get("items", [])
    terms = [t.get("term") for t in items]
    assert "GMV" in terms, f"GMV not in: {terms}"


@then("the detect_anomalies output reports at least one anomaly kind")
def detect_has_kind(bdd_client):
    body = _ctx(bdd_client).get("body") or _ctx(bdd_client)["response"].json()
    output = body.get("output", {})
    findings = output.get("items", [])
    kinds = {f.get("kind") for f in findings}
    assert len(kinds) >= 1, f"no anomaly kinds reported: {findings}"


@then("the MCP health reports clickhouse status")
def mcp_has_ch(bdd_client):
    body = _ctx(bdd_client).get("body") or _ctx(bdd_client)["response"].json()
    assert "clickhouse" in body, f"no clickhouse in mcp health: {body}"


@then("the search returns at least {n:d} hit")
def search_hits(bdd_client, n):
    body = _ctx(bdd_client).get("body") or _ctx(bdd_client)["response"].json()
    items = body.get("items", [])
    assert len(items) >= n, f"expected >= {n} search hits, got {len(items)}"


# ---------------------------------------------------------------------------
# M2 — AI Governance (Then)
# ---------------------------------------------------------------------------

@then('the suggestion of type "glossary" is created for "shop.default.orders_daily"')
def glossary_sug_created(bdd_client):
    body = _ctx(bdd_client).get("body") or _ctx(bdd_client)["response"].json()
    items = body.get("output", {}).get("items") or []
    fqns = [i.get("table_fqn") for i in items]
    terms = [i.get("term") for i in items]
    assert "shop.default.orders_daily" in fqns, f"target fqn not in: {fqns}"
    assert any(t for t in terms if t), f"no term inferred: {terms}"


@then('the table "shop.default.orders_daily" has glossary term "GMV" bound')
def glossary_bound(bdd_client, app_with_db):
    from idm_api.db import _session_factory
    from idm_kg.models.glossary import AssetTerm, GlossaryTerm
    from sqlalchemy import select
    tid = _ctx(bdd_client)["table_id"]

    async def _check():
        async with _session_factory() as db:
            term = (
                await db.execute(select(GlossaryTerm).where(GlossaryTerm.name == "GMV"))
            ).scalar_one_or_none()
            assert term is not None
            bind = (
                await db.execute(
                    select(AssetTerm).where(
                        AssetTerm.table_id == _uuid.UUID(tid),
                        AssetTerm.term_id == term.id,
                    )
                )
            ).scalar_one_or_none()
            assert bind is not None, f"GMV not bound to {tid}"

    _run(_check())


@then('the table "shop.default.orders_daily" has owner "bob@example.com" verified')
def owner_verified(bdd_client, app_with_db):
    from idm_api.db import _session_factory
    from idm_kg.models.owner import AssetOwner
    from sqlalchemy import select
    tid = _ctx(bdd_client)["table_id"]

    async def _check():
        async with _session_factory() as db:
            row = (
                await db.execute(
                    select(AssetOwner).where(
                        AssetOwner.table_id == _uuid.UUID(tid),
                        AssetOwner.user_email == "bob@example.com",
                    )
                )
            ).scalar_one_or_none()
            assert row is not None, f"bob owner not present"
            assert row.is_verified is True, f"bob owner not verified"

    _run(_check())


# ---------------------------------------------------------------------------
# M3 — Lineage / Impact (Then)
# ---------------------------------------------------------------------------

@then('the lineage response includes a downstream edge to "shop.default.orders_summary"')
def lineage_has_downstream(bdd_client):
    body = _ctx(bdd_client).get("body") or _ctx(bdd_client)["response"].json()
    down = body.get("downstream", [])
    fqns = {d.get("downstream_fqn") for d in down}
    assert "shop.default.orders_summary" in fqns, f"downstream fqns: {fqns}"


@then("the impact response includes downstream count >= {n:d}")
def impact_downstream_count(bdd_client, n):
    body = _ctx(bdd_client).get("body") or _ctx(bdd_client)["response"].json()
    c = body.get("downstream_count", 0)
    assert c >= n, f"downstream_count={c} < {n}"


@then('the impact response includes affected owner "alice@example.com"')
def impact_has_owner(bdd_client):
    body = _ctx(bdd_client).get("body") or _ctx(bdd_client)["response"].json()
    owners = body.get("affected_owners", [])
    assert "alice@example.com" in owners, f"owners: {owners}"


@then("the extract_sql_lineage output reports upstream tables")
def sql_lineage_has_upstream(bdd_client):
    body = _ctx(bdd_client).get("body") or _ctx(bdd_client)["response"].json()
    output = body.get("output", {})
    items = output.get("items") or []
    ups = [i.get("upstream_fqn") for i in items if i.get("upstream_fqn")]
    assert len(ups) >= 1, f"no upstream tables extracted: {items}"


# ---------------------------------------------------------------------------
# M4 — Quality / ChatBI (Then)
# ---------------------------------------------------------------------------

@then("the quality dashboard has \"{a}\" and \"{b}\" fields")
def dashboard_fields(bdd_client, a, b):
    body = _ctx(bdd_client).get("body") or _ctx(bdd_client)["response"].json()
    assert a in body, f"missing {a}: {list(body.keys())}"
    assert b in body, f"missing {b}: {list(body.keys())}"


@then("the rules list contains at least {n:d} rule")
def rules_count(bdd_client, n):
    body = _ctx(bdd_client).get("body") or _ctx(bdd_client)["response"].json()
    items = body.get("items", [])
    assert len(items) >= n, f"expected >= {n} rules, got {len(items)}"


@then("the response status is 201")
def status_201(bdd_client):
    r = _ctx(bdd_client)["response"]
    assert r.status_code == 201, f"expected 201, got {r.status_code}: {r.text}"


@then("the profiler output reports at least {n:d} profiled table")
def profiler_count(bdd_client, n):
    body = _ctx(bdd_client).get("body") or _ctx(bdd_client)["response"].json()
    items = (body.get("output") or {}).get("items") or []
    ok = [i for i in items if i.get("status") == "ok"]
    assert len(ok) >= n, f"only {len(ok)} profiled ok, expected >= {n}: {items}"


@then("the compose_insight output reports at least {n:d} finding")
def compose_count(bdd_client, n):
    body = _ctx(bdd_client).get("body") or _ctx(bdd_client)["response"].json()
    items = (body.get("output") or {}).get("items") or []
    assert len(items) >= n, f"only {len(items)} findings: {items}"


@then("the nl2sql output reports a non-empty sql field")
def nl2sql_has_sql(bdd_client):
    body = _ctx(bdd_client).get("body") or _ctx(bdd_client)["response"].json()
    out = body.get("output", {})
    summary = out.get("summary", {})
    sql = summary.get("sql") or ""
    assert sql.strip(), f"no sql in nl2sql summary: {summary}"


@then("the chatbi response includes a confidence field")
def chatbi_confidence(bdd_client):
    body = _ctx(bdd_client).get("body") or _ctx(bdd_client)["response"].json()
    assert "confidence" in body, f"missing confidence: {list(body.keys())}"


# ---------------------------------------------------------------------------
# M5 — idm-self MCP (Then)
# ---------------------------------------------------------------------------

@then('the idm-self tool list contains "{name}"')
def idm_self_has_tool(bdd_client, name):
    body = _ctx(bdd_client).get("body") or _ctx(bdd_client)["response"].json()
    tools = body.get("tools", []) or []
    names = {t.get("name") for t in tools}
    assert name in names, f"{name} not in tool list: {names}"


@then('the skills list contains "{name}"')
def skills_list_contains(bdd_client, name):
    body = _ctx(bdd_client).get("body") or _ctx(bdd_client)["response"].json()
    items = body.get("result") or body.get("items") or []
    names = {s.get("name") if isinstance(s, dict) else getattr(s, "name", None) for s in items}
    assert name in names, f"{name} not in skills: {names}"
