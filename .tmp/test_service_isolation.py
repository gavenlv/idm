"""test_service_isolation.py: 验证 upsert_table_asset 的服务隔离."""
import asyncio
import sys

from sqlalchemy.ext.asyncio import AsyncSession

from idm_api.db import get_engine
from idm_api.skills.utils import upsert_table_asset
from idm_kg.models.service import Service
from sqlalchemy import select

failed: list[str] = []

def check(c: bool, label: str) -> None:
    if c:
        print(f"  [OK] {label}")
    else:
        print(f"  [FAIL] {label}")
        failed.append(label)


async def main() -> None:
    engine = get_engine()
    async with AsyncSession(engine) as db:
        # 找 clickhouse-prod service
        svc = (await db.execute(select(Service).where(Service.name == "clickhouse-prod"))).scalar_one_or_none()
        if svc is None:
            print("  [SKIP] clickhouse-prod service not registered")
            return
        sid = str(svc.id)

        # 1) 合法 FQN: clickhouse-prod.x.y.z
        try:
            aid = await upsert_table_asset(
                db, fqn="clickhouse-prod.shop.default.iso_test_ok",
                name="iso_test_ok", service_id=sid, asset_type="table",
            )
            check(bool(aid), f"legit fqn accepted (id={aid})")
        except Exception as e:  # noqa: BLE001
            check(False, f"legit fqn rejected: {e}")

        # 2) 跨 service FQN: ch-shop_dw.x.y.z 但 service 是 clickhouse-prod
        try:
            await upsert_table_asset(
                db, fqn="ch-shop_dw.shop.default.iso_test_fail",
                name="iso_test_fail", service_id=sid, asset_type="table",
            )
            check(False, "cross-service fqn NOT rejected (bug)")
        except ValueError as ve:
            check("service isolation" in str(ve) or "must start" in str(ve), f"cross-service ValueError raised: {str(ve)[:80]}")

        # 3) service_id=None (跳过校验, 仍能写)
        try:
            aid = await upsert_table_asset(
                db, fqn="noop-svc.shop.default.iso_test_noop",
                name="iso_test_noop", service_id=None, asset_type="table",
            )
            check(bool(aid), f"service_id=None bypass (id={aid})")
        except Exception as e:  # noqa: BLE001
            check(False, f"service_id=None failed: {e}")

        # 4) 不存在的 service_id
        try:
            await upsert_table_asset(
                db, fqn="x.y.z.t", name="t", service_id="00000000-0000-0000-0000-000000000000",
            )
            check(False, "nonexistent service_id NOT rejected (bug)")
        except ValueError as ve:
            check("not found" in str(ve).lower(), f"nonexistent service ValueError: {str(ve)[:80]}")

    print()
    if failed:
        print(f"FAIL: {len(failed)} checks failed")
        sys.exit(1)
    print("PASS")


asyncio.run(main())
