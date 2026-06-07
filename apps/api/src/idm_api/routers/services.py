"""/api/v1/services: Service CRUD (数据源注册)."""
from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from idm_api.db import get_db
from idm_api.schemas import ServiceCreate, ServiceRead
from idm_kg.models.service import Service

router = APIRouter()


@router.get("", response_model=list[ServiceRead], summary="List services")
async def list_services(
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
) -> list[Service]:
    """列出所有接入的数据源。"""
    stmt = select(Service).order_by(Service.name).limit(limit).offset(offset)
    result = await db.execute(stmt)
    return list(result.scalars().all())


@router.post("", response_model=ServiceRead, status_code=status.HTTP_201_CREATED, summary="Create service")
async def create_service(
    payload: ServiceCreate,
    db: AsyncSession = Depends(get_db),
) -> Service:
    """注册一个数据源。"""
    existing = await db.execute(select(Service).where(Service.name == payload.name))
    if existing.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Service '{payload.name}' already exists",
        )
    svc = Service(**payload.model_dump())
    db.add(svc)
    await db.flush()
    return svc


@router.get("/{service_id}", response_model=ServiceRead, summary="Get service")
async def get_service(service_id: UUID, db: AsyncSession = Depends(get_db)) -> Service:
    """获取单个数据源。"""
    svc = await db.get(Service, service_id)
    if svc is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Service not found")
    return svc
