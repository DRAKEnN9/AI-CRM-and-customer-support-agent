from celery import Celery
from app.config import settings
import logging

logger = logging.getLogger(__name__)
celery_app = Celery('tasks', broker=settings.CELERY_BROKER_URL, backend=settings.CELERY_RESULT_BACKEND)

@celery_app.task(name="send_appointment_reminder")
def send_appointment_reminder(customer_phone: str, appointment_details: str):
    """
    Schedules a reminder message via WhatsApp.
    Since Celery runs in a separate process, we use a synchronous-friendly
    way to call the WhatsApp client or a separate script.
    """
    import asyncio
    from app.services.whatsapp_client import whatsapp_client

    async def _send():
        try:
            msg = f"Reminder: You have a salon appointment on {appointment_details}. See you soon!"
            await whatsapp_client.send_text_message(customer_phone, msg)
            logger.info(f"Reminder sent to {customer_phone}")
        except Exception as e:
            logger.error(f"Failed to send reminder to {customer_phone}: {e}")

    asyncio.run(_send())
