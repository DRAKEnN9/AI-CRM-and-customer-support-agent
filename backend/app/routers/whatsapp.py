import hmac
import hashlib
from fastapi import APIRouter, Request, Response, BackgroundTasks, HTTPException, Header, Query
from app.config import settings
from app.services.whatsapp_client import whatsapp_client
from app.services.agent import salon_agent
from app.middleware.conversation import conversation_manager
from app.database import async_session_maker
import logging

router = APIRouter(prefix="/webhook/whatsapp", tags=["WhatsApp"])
logger = logging.getLogger(__name__)

async def verify_whatsapp_signature(request: Request, x_hub_signature_256: str):
    """Verifies that the request came from Meta using the app secret."""
    if not x_hub_signature_256:
        return False

    body = await request.body()
    signature = f"sha256={hmac.new(settings.WHATSAPP_APP_SECRET.encode(), body, hashlib.sha256).hexdigest()}"
    return hmac.compare_digest(signature, x_hub_signature_256)

async def process_whatsapp_message(data: dict):
    """Background task to process the incoming WhatsApp message."""
    async with async_session_maker() as db:
        try:
            logger.info(f"Processing WhatsApp message: {data}")

            # Extract basic info
            entry = data.get("entry", [{}])[0]
            changes = entry.get("changes", [{}])[0]
            value = changes.get("value", {})
            messages = value.get("messages", [])

            if not messages:
                return

            message = messages[0]
            msg_id = message.get("id")
            from_phone = message.get("from")
            text = message.get("text", {}).get("body")

            logger.info(f"Received message from {from_phone}: {text}")

            # 1. Mark as read
            await whatsapp_client.mark_as_read(msg_id)

            # 2. Context & Customer Management
            customer_id = await conversation_manager.get_or_create_customer(db, from_phone)
            history = await conversation_manager.load_context(db, customer_id)

            # 3. Prepare Agent State
            state = {
                "messages": history + [("user", text)],
                "customer_phone": from_phone,
                "customer_id": customer_id,
                "intent": "",
                "entities": {},
                "final_response": "",
                "needs_human": False
            }

            # Check for cached state in Redis to resume if necessary
            cached_state = await conversation_manager.get_cached_state(customer_id)
            if cached_state:
                # Merge cached state if needed, but history is the primary source
                state.update({k: v for k, v in cached_state.items() if k not in ["messages"]})

            # 4. Run the Salon Agent
            # Note: we pass the db session into the agent's nodes via the graph state or a custom wrapper.
            # Since salon_agent is a compiled graph, we invoke it.
            # The current agent.py implementation assumes nodes receive 'db' as an argument.
            # LangGraph allows passing config/context. Here we use a simple approach by
            # patching the nodes or using a wrapper if needed.
            # For this MVP, we'll wrap the invocation.

            # Note: The agent nodes currently expect (state, db). We need to bridge this.
            # We'll update agent.py nodes to handle this if they aren't already.
            # For now, let's assume we can pass the session.

            # Actually, looking at agent.py, the nodes are defined as async def node(state, db).
            # The compiled graph.invoke(state) doesn't know about 'db'.
            # We should pass 'db' via the config.

            result = await salon_agent.ainvoke(state, config={"configurable": {"db": db}})

            final_response = result.get("final_response", "I'm sorry, I'm having trouble processing that.")
            intent = result.get("intent", "faq")
            needs_human = result.get("needs_human", False)

            # 5. Save Interaction & Update Cache
            await conversation_manager.save_interaction(
                db, customer_id, text, final_response, intent=intent
            )
            await conversation_manager.cache_state(customer_id, result)

            # 6. Send response to WhatsApp
            if needs_human:
                # Notify salon admin via WhatsApp
                admin_phone = settings.SALON_ADMIN_PHONE
                summary_prompt = f"Summarize the following conversation for a salon manager. Focus on the customer's need and why the AI couldn't handle it:\n\n{history} \nUser: {text}\nAI: {final_response}"

                # Use a simple LLM call for summary (can be optimized)
                from app.services.agent import call_llm
                summary = call_llm(summary_prompt, system_prompt="You are a professional administrative assistant. Summarize requests briefly.")

                admin_msg = (
                    f"🚨 *Human Handoff Requested*\n\n"
                    f"*Customer:* {from_phone}\n"
                    f"*Summary:* {summary}\n\n"
                    f"Please respond to the customer manually."
                )
                await whatsapp_client.send_text_message(admin_phone, admin_msg)
                logger.info(f"Handoff notification sent to admin {admin_phone}")

            await whatsapp_client.send_text_message(from_phone, final_response)

        except Exception as e:
            logger.error(f"Error processing WhatsApp message: {e}", exc_info=True)

@router.get("")
async def verify_webhook(
    request: Request,
    hub_mode: str = Query(None, alias="hub.mode"),
    hub_challenge: str = Query(None, alias="hub.challenge"),
    hub_verify_token: str = Query(None, alias="hub.verify_token")
):
    """Meta Webhook Verification Endpoint."""
    if hub_mode == "subscribe" and hub_verify_token == settings.WHATSAPP_VERIFY_TOKEN:
        return Response(content=hub_challenge, media_type="text/plain")

    raise HTTPException(status_code=403, detail="Verification failed")

@router.post("")
async def whatsapp_webhook(
    request: Request,
    background_tasks: BackgroundTasks,
    x_hub_signature_256: str = Header(None)
):
    """Main WhatsApp Webhook endpoint."""
    if not await verify_whatsapp_signature(request, x_hub_signature_256):
        logger.warning("Invalid WhatsApp signature received")
        raise HTTPException(status_code=403, detail="Invalid signature")

    data = await request.json()

    # Process in background to return 200 OK immediately to Meta
    background_tasks.add_task(process_whatsapp_message, data)

    return Response(status_code=200, content="EVENT_RECEIVED")
