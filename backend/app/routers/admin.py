import json
import logging
from typing import Annotated
from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks, UploadFile, File, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.database import get_db
from app.services.knowledge import ingest_document
from app.models.salon import SalonService, Staff, WorkingHours, Appointment
from app.config import settings

router = APIRouter(prefix="/admin", tags=["Admin"])
logger = logging.getLogger(__name__)

# SIMPLE API KEY MIDDLEWARE PLACEHOLDER
async def verify_admin_key(request: Request):
    # In production, this would check a header like X-Admin-API-Key
    # For MVP, we just check if the key exists in config
    key = request.headers.get("X-Admin-API-Key")
    if key != settings.API_KEY:
        raise HTTPException(status_code=403, detail="Invalid Admin API Key")

@router.post("/seed")
async def seed_database(
    db: AsyncSession = Depends(get_db)
):
    """
    Populates the database with initial salon data.
    In a real app, this would load from a JSON file.
    """
    try:
        # 1. Seed Services
        services_data = [
            {"name": "Women's Haircut & Style", "duration_minutes": 60, "price": 60.0},
            {"name": "Men's Haircut", "duration_minutes": 30, "price": 30.0},
            {"name": "Full Color", "duration_minutes": 120, "price": 120.0},
            {"name": "Highlights", "duration_minutes": 180, "price": 150.0},
            {"name": "Beard Trim", "duration_minutes": 15, "price": 20.0},
        ]
        for s in services_data:
            db.add(SalonService(**s))

        # 2. Seed Staff
        staff_data = [
            {"name": "Sarah", "phone": "+1234567890"},
            {"name": "Mike", "phone": "+1234567891"},
            {"name": "Elena", "phone": "+1234567892"},
        ]
        for st in staff_data:
            db.add(Staff(**st))

        await db.flush() # Get IDs for staff

        # 3. Seed Working Hours (Mon-Sat, 9-6 approx)
        result = await db.execute(select(Staff))
        staff_members = result.scalars().all()
        for staff in staff_members:
            for day in range(6): # Mon-Sat
                db.add(WorkingHours(
                    staff_id=staff.id,
                    day_of_week=day,
                    start_time="09:00",
                    end_time="18:00"
                ))

        # 4. Seed Knowledge Base
        # We'll use the salon_info.txt we created earlier
        with open("salon_info.txt", "r", encoding="utf-8") as f:
            content = f.read()
        await ingest_document(db, content, source="seed_file")

        await db.commit()
        return {"status": "Database successfully seeded"}
    except Exception as e:
        await db.rollback()
        logger.error(f"Seeding failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/appointments")
async def list_appointments(db: AsyncSession = Depends(get_db)):
    """Returns a list of all appointments."""
    result = await db.execute(select(Appointment))
    appointments = result.scalars().all()
    return [
        {
            "id": a.id,
            "customer_id": a.customer_id,
            "staff_id": a.staff_id,
            "service_id": a.service_id,
            "start_time": a.start_time,
            "end_time": a.end_time,
            "status": a.status
        }
        for a in appointments
    ]

@router.post("/knowledge")
async def upload_knowledge(
    background_tasks: BackgroundTasks,
    text: str = None,
    file: UploadFile = File(None),
    db: AsyncSession = Depends(get_db)
):
    """Uploads knowledge base data via text or file."""
    content = ""
    source = "admin_upload"

    if text:
        content = text
    elif file:
        content = (await file.read()).decode("utf-8")
        source = file.filename

    if not content:
        raise HTTPException(status_code=400, detail="No content provided")

    background_tasks.add_task(ingest_document, db, content, source)
    return {"status": "Ingestion started in background", "source": source}
