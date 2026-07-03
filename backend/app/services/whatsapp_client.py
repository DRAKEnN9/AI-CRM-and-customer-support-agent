import httpx
import logging
from app.config import settings

logger = logging.getLogger(__name__)

class WhatsAppClient:
    def __init__(self):
        self.base_url = f"https://graph.facebook.com/v19.0/{settings.WHATSAPP_PHONE_NUMBER_ID}/messages"
        self.token = settings.WHATSAPP_TOKEN
        self.headers = {
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json",
        }

    async def send_text_message(self, to_phone: str, text: str):
        """Sends a plain text message to a specified phone number."""
        payload = {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": to_phone,
            "type": "text",
            "text": {"body": text},
        }
        return await self._post(payload)

    async def send_template_message(self, to_phone: str, template_name: str, language_code: str = "en_US", components: list = None):
        """Sends a pre-approved WhatsApp template message."""
        payload = {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": to_phone,
            "type": "template",
            "template": {
                "name": template_name,
                "language": {"code": language_code},
            },
        }
        if components:
            payload["template"]["components"] = components

        return await self._post(payload)

    async def mark_as_read(self, message_id: str):
        """Marks a message as read to trigger the read receipt."""
        # The endpoint for marking as read is different from the messages endpoint
        url = f"https://graph.facebook.com/v19.0/{message_id}/mark_read"
        payload = {
            "messaging_product": "whatsapp",
            "status": "read",
        }

        async with httpx.AsyncClient() as client:
            try:
                response = await client.post(url, headers=self.headers, json=payload)
                response.raise_for_status()
                return response.json()
            except httpx.HTTPError as e:
                logger.error(f"Error marking message {message_id} as read: {e}")
                return None

    async def _post(self, payload: dict):
        async with httpx.AsyncClient() as client:
            try:
                response = await client.post(self.base_url, headers=self.headers, json=payload)
                response.raise_for_status()
                return response.json()
            except httpx.HTTPError as e:
                logger.error(f"WhatsApp API Error: {e}")
                return None

whatsapp_client = WhatsAppClient()
