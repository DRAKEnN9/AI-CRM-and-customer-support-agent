import json
import logging
from typing import List, Tuple, Optional
from sqlalchemy import select, insert
from sqlalchemy.ext.asyncio import AsyncSession
from redis import asyncio as aioredis

from app.config import settings
from app.models.salon import Customer, ConversationLog
from langchain_core.messages import HumanMessage, AIMessage, SystemMessage

logger = logging.getLogger(__name__)

class ConversationManager:
    def __init__(self):
        self.redis = aioredis.from_url(settings.REDIS_URL, decode_responses=True)

    async def get_or_create_customer(self, db: AsyncSession, phone: str) -> int:
        """Retrieves the customer ID by phone or creates a new customer."""
        result = await db.execute(
            select(Customer.id).where(Customer.whatsapp_phone == phone)
        )
        customer_id = result.scalar_one_or_none()

        if not customer_id:
            new_customer = Customer(whatsapp_phone=phone)
            db.add(new_customer)
            await db.commit()
            await db.refresh(new_customer)
            customer_id = new_customer.id
            logger.info(f"Created new customer for phone: {phone}")

        return customer_id

    async def load_context(self, db: AsyncSession, customer_id: int) -> List[Tuple[str, str]]:
        """
        Loads the last 10 messages from the DB and transforms them
        into a format the agent expects (role, content).
        """
        result = await db.execute(
            select(ConversationLog.role, ConversationLog.message_body)
            .where(ConversationLog.customer_id == customer_id)
            .order_by(ConversationLog.created_at.desc())
            .limit(10)
        )
        logs = result.all()

        # Reverse to la chronological order
        history = [(role, body) for role, body in reversed(logs)]
        return history

    async def save_interaction(self, db: AsyncSession, customer_id: int,
                                user_msg: str, ai_msg: str,
                                intent: Optional[str] = None,
                                metadata: Optional[dict] = None):
        """Saves both the user and AI messages to the database."""

        # User message
        user_log = ConversationLog(
            customer_id=customer_id,
            message_body=user_msg,
            role="user",
            intent=intent,
            metadata_json=metadata
        )

        # AI message
        ai_log = ConversationLog(
            customer_id=customer_id,
            message_body=ai_msg,
            role="assistant"
        )

        db.add(user_log)
        db.add(ai_log)
        await db.commit()

    async def cache_state(self, customer_id: int, state: dict):
        """Caches the agent state in Redis for 1 hour."""
        key = f"conv_state:{customer_id}"
        await self.redis.set(key, json.dumps(state), ex=3600)

    async def get_cached_state(self, customer_id: int) -> Optional[dict]:
        """Retrieves cached agent state from Redis."""
        key = f"conv_state:{customer_id}"
        data = await self.redis.get(key)
        return json.loads(data) if data else None

conversation_manager = ConversationManager()
