import logging
from datetime import datetime, timedelta
from typing import List, Optional, Tuple
from sqlalchemy import select, and_
from sqlalchemy.ext.asyncio import AsyncSession
from app.models.salon import Appointment, WorkingHours, SalonService

logger = logging.getLogger(__name__)

class CalendarService:
    """Handles complex appointment scheduling logic."""

    @staticmethod
    async def check_slot_availability(
        db: AsyncSession,
        staff_id: int,
        service_id: int,
        start_time: datetime
    ) -> Tuple[bool, Optional[str]]:
        """
        Verifies if a specific slot is available.
        Returns (is_available, error_message).
        """
        # 1. Get service duration
        service = await db.get(SalonService, service_id)
        if not service:
            return False, "Service not found."

        duration = service.duration_minutes
        end_time = start_time + timedelta(minutes=duration)

        # 2. Check working hours for the staff on that day
        day_of_week = start_time.weekday()
        result = await db.execute(
            select(WorkingHours).where(
                and_(WorkingHours.staff_id == staff_id, WorkingHours.day_of_week == day_of_week)
            )
        )
        work_hours = result.scalar_one_or_none()

        if not work_hours:
            return False, "Staff member does not work on this day."

        # Check if requested time falls within working hours
        work_start = datetime.strptime(f"{start_time.date()} {work_hours.start_time}", "%Y-%m-%d %H:%M")
        work_end = datetime.strptime(f"{start_time.date()} {work_hours.end_time}", "%Y-%m-%d %H:%M")

        if start_time < work_start or end_time > work_end:
            return False, f"Requested time is outside working hours ({work_hours.start_time} - {work_hours.end_time})."

        # 3. Check for double-bookings (overlaps)
        result = await db.execute(
            select(Appointment).where(
                and_(
                    Appointment.staff_id == staff_id,
                    Appointment.status == "booked",
                    Appointment.start_time < end_time,
                    Appointment.end_time > start_time
                )
            )
        )
        overlap = result.scalar_one_or_none()
        if overlap:
            return False, "This time slot is already booked."

        return True, None

    @staticmethod
    async def get_available_slots(
        db: AsyncSession,
        date_str: str,
        staff_id: int,
        service_id: int
    ) -> List[str]:
        """Returns available 30-min intervals for a specific day."""
        service = await db.get(SalonService, service_id)
        if not service: return []
        duration = service.duration_minutes

        date_obj = datetime.strptime(date_str, "%Y-%m-%d")
        day_of_week = date_obj.weekday()

        result = await db.execute(
            select(WorkingHours).where(
                and_(WorkingHours.staff_id == staff_id, WorkingHours.day_of_week == day_of_week)
            )
        )
        work_hours = result.scalar_one_or_none()
        if not work_hours: return []

        slots = []
        current = datetime.strptime(f"{date_str} {work_hours.start_time}", "%Y-%m-%d %H:%M")
        end = datetime.strptime(f"{date_str} {work_hours.end_time}", "%Y-%m-%d %H:%M")

        while current + timedelta(minutes=duration) <= end:
            is_avail, _ = await CalendarService.check_slot_availability(db, staff_id, service_id, current)
            if is_avail:
                slots.append(current.strftime("%H:%M"))
            current += timedelta(minutes=30)

        return slots

# This instance is what tools.py tries to import
calendar_service = CalendarService()
