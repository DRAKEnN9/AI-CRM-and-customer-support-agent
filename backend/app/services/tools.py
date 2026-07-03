import logging
from datetime import datetime, timedelta
from typing import List, Optional
from sqlalchemy import select, update, and_
from sqlalchemy.ext.asyncio import AsyncSession
from app.models.salon import SalonService, Staff, Appointment, WorkingHours
from app.services.knowledge import search_knowledge as r_search_knowledge
from app.services.calendar import calendar_service
from pgvector.sqlalchemy import Vector

logger = logging.getLogger(__name__)

async def get_services(db: AsyncSession) -> List[dict]:
    """Returns a list of active salon services."""
    result = await db.execute(select(SalonService).where(SalonService.active == True))
    services = result.scalars().all()
    return [{"id": s.id, "name": s.name, "price": s.price, "duration": s.duration_minutes} for s in services]

async def get_staff(db: AsyncSession) -> List[dict]:
    """Returns a list of active staff members."""
    result = await db.execute(select(Staff).where(Staff.active == True))
    staff_list = result.scalars().all()
    return [{"id": s.id, "name": s.name} for s in staff_list]

async def get_availability(db: AsyncSession, date_str: str, staff_id: int, service_id: int) -> List[str]:
    """Returns available slots via the calendar service."""
    return await calendar_service.get_available_slots(db, date_str, staff_id, service_id)

async def book_appointment(db: AsyncSession, customer_id: int, staff_id: int, service_id: int, start_time: datetime) -> Optional[Appointment]:
    """Creates a new appointment if slot is still available."""
    # Final check for double booking
    is_available, error = await calendar_service.check_slot_availability(db, staff_id, service_id, start_time)
    if not is_available:
        logger.error(f"Booking failed: {error}")
        return None

    service = await db.get(SalonService, service_id)
    end_time = start_time + timedelta(minutes=service.duration_minutes)

    appointment = Appointment(
        customer_id=customer_id,
        staff_id=staff_id,
        service_id=service_id,
        start_time=start_time,
        end_time=end_time,
        status="booked"
    )
    db.add(appointment)
    await db.commit()
    await db.refresh(appointment)
    return appointment

async def cancel_appointment(db: AsyncSession, appointment_id: int) -> bool:
    """Cancels an existing appointment."""
    appointment = await db.get(Appointment, appointment_id)
    if not appointment:
        return False

    appointment.status = "canceled"
    await db.commit()
    return True

async def search_knowledge(db: AsyncSession, query_embedding: List[float]) -> List[str]:
    """RAG retrieval using the knowledge service."""
    return await r_search_knowledge(db, query_embedding)
