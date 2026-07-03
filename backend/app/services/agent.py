import logging
import json
from datetime import datetime, timedelta
from typing import TypedDict, List, Dict, Any, Tuple, Optional
from litellm import completion, embedding
from langgraph.graph import StateGraph, END
from app.config import settings
from app.services import tools
from app.services.whatsapp_client import whatsapp_client
from app.tasks.worker import send_appointment_reminder

logger = logging.getLogger(__name__)

class AgentState(TypedDict):
    messages: List[Tuple[str, str]]
    customer_phone: str
    customer_id: Optional[int]
    intent: str
    entities: Dict[str, Any]
    final_response: str
    needs_human: bool
    confirmation_pending: bool # New: track if we are waiting for confirmation

# --- Utility for LLM Calls ---

def call_llm(prompt: str, system_prompt: str = "You are a helpful assistant.", json_mode: bool = False) -> str:
    """Wrapper for LiteLLM completion calls."""
    response = completion(
        model=settings.LLM_MODEL,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": prompt},
        ],
        response_format={"type": "json_object"} if json_mode else None
    )
    return response.choices[0].message.content

# --- Graph Nodes ---

async def classify_intent(state: AgentState):
    """Classifies user intent and extracts entities."""
    last_message = state["messages"][-1][1]

    # If we are waiting for confirmation, treat "yes/no" as confirmation intent
    if state.get("confirmation_pending"):
        if any(word in last_message.lower() for word in ["yes", "confirm", "correct", "sure"]):
            return {"intent": "confirm_booking"}
        if any(word in last_message.lower() for word in ["no", "incorrect", "cancel"]):
            return {"intent": "cancel_booking_request"}

    system_prompt = (
        "You are an intent classifier for a hair salon WhatsApp bot. "
        "Classify the user's intent and extract entities into JSON format. "
        "Intents: 'faq', 'book_appointment', 'cancel_appointment', 'reschedule_appointment', "
        "'check_availability', 'greeting', 'escalate'. "
        "Entities to extract: 'service', 'staff_name', 'date', 'time'. "
        "Example output: {\"intent\": \"book_appointment\", \"entities\": {\"service\": \"haircut\", \"date\": \"2026-07-05\"}}"
    )

    prompt = f"User message: {last_message}"
    result = call_llm(prompt, system_prompt, json_mode=True)

    try:
        parsed = json.loads(result)
        return {
            "intent": parsed.get("intent", "faq"),
            "entities": parsed.get("entities", {}),
        }
    except Exception as e:
        logger.error(f"Failed to parse intent classification: {e}")
        return {"intent": "faq", "entities": {}}

async def faq_node(state: AgentState, config):
    """Handles FAQ queries via RAG."""
    db = config["configurable"].get("db")
    query = state["messages"][-1][1]

    emb_response = embedding(model=settings.EMBEDDING_MODEL, input=[query])
    query_vector = emb_response.data[0]["embedding"]

    docs = await tools.search_knowledge(db, query_vector)
    context = "\n".join(docs) if docs else ""

    system_prompt = (
        "You are a warm, welcoming, and professional hair salon concierge. "
        "Your goal is to help customers feel pampered even through text. "
        "Use a friendly, natural tone. Avoid sounding like a robot or mentioning 'knowledge bases'. "
        "If the context below provides the answer, weave it naturally into your response. "
        "If you truly can't find the answer, don't say 'I don't have that in my data', "
        "instead say something like 'That's a great question! I'm not 100% sure about that, "
        "but I can check with our manager and get back to you, or we can connect you right now.'"
    )

    prompt = f"Relevant Info: {context}\n\nUser: {query}"

    # If context is empty, let the LLM handle it with the personality prompt
    response = call_llm(prompt, system_prompt)

    return {"final_response": response}


async def booking_node(state: AgentState, config):
    """Handles appointment booking logic with a confirmation step."""
    db = config["configurable"].get("db")
    entities = state["entities"]
    service = entities.get("service")
    staff = entities.get("staff_name")
    date = entities.get("date")
    time = entities.get("time")

    missing = []
    if not service: missing.append("which service you're looking for")
    if not date: missing.append("the date")
    if not time: missing.append("the time")

    if missing:
        response = f"I'd love to get that booked for you! I just need a couple more details: {', '.join(missing)}. What works best for you?"
        return {"final_response": response}

    services = await tools.get_services(db)
    staff_list = await tools.get_staff(db)

    service_id = next((s["id"] for s in services if service.lower() in s["name"].lower()), None)
    staff_id = next((s["id"] for s in staff_list if staff and staff.lower() in s["name"].lower()), None)

    if not service_id:
        return {"final_response": f"Hmm, I couldn't find '{service}' on our menu. Could you double-check the name for me?"}

    if not staff_id:
        return {"final_response": "Any preference on who does your hair? Or I can just find the first available stylist for you!"}

    try:
        start_time = datetime.strptime(f"{date} {time}", "%Y-%m-%d %H:%M")
        service_info = next(s for s in services if s["id"] == service_id)
        duration = service_info["duration"]

        confirmation_msg = (
            f"Perfect! Just to make sure I've got this right: {service} with {staff} "
            f"on {date} at {time} (it'll take about {duration} min). Does that sound good?"
        )
        return {
            "final_response": confirmation_msg,
            "confirmation_pending": True
        }
    except Exception as e:
        return {"final_response": f"I'm having a little trouble with the date/time format. Could you try writing it as YYYY-MM-DD HH:MM? (e.g. 2026-07-05 10:00)"}

async def confirm_booking_node(state: AgentState, config):
    """Finalizes the booking after user says 'Yes'."""
    db = config["configurable"].get("db")
    entities = state["entities"]
    service = entities.get("service")
    staff = entities.get("staff_name")
    date = entities.get("date")
    time = entities.get("time")

    services = await tools.get_services(db)
    staff_list = await tools.get_staff(db)
    service_id = next((s["id"] for s in services if service.lower() in s["name"].lower()), None)
    staff_id = next((s["id"] for s in staff_list if staff and staff.lower() in s["name"].lower()), None)

    try:
        start_time = datetime.strptime(f"{date} {time}", "%Y-%m-%d %H:%M")
        appointment = await tools.book_appointment(db, state["customer_id"], staff_id, service_id, start_time)
        if appointment:
            # 1. Send Confirmation Template
            await whatsapp_client.send_template_message(
                state["customer_phone"],
                template_name="appointment_confirmed",
                components=[{"type": "body", "parameters": [
                    {"type": "text", "text": service},
                    {"type": "text", "text": f"{date} at {time}"}
                ]}]
            )

            # 2. Schedule Reminder (24h before)
            reminder_time = start_time - timedelta(hours=24)
            # Note: In a real system, we'd use a task scheduler like Celery Beat or a precise delay.
            # Here we'll call the task for demonstration.
            send_appointment_reminder.apply_async(
                args=[state["customer_phone"], f"{date} at {time}"],
                eta=reminder_time
            )

            return {
                "final_response": f"Perfect! Your {service} with {staff} is booked for {date} at {time}. See you then!",
                "confirmation_pending": False
            }
        else:
            return {"final_response": "Sorry, that slot was just taken. Could you pick another time?", "confirmation_pending": False}
    except Exception as e:
        return {"final_response": "An error occurred while finalizing your booking. Please try again.", "confirmation_pending": False}

async def cancel_node(state: AgentState, config):
    """Handles appointment cancellation."""
    db = config["configurable"].get("db")
    return {"final_response": "I've found your appointment. Are you sure you want to cancel it?"}

async def availability_node(state: AgentState, config):
    """Checks for available slots."""
    db = config["configurable"].get("db")
    entities = state["entities"]
    date = entities.get("date")
    if not date:
        return {"final_response": "I'd love to check that for you! Which date are you thinking of? (Please use YYYY-MM-DD)"}

    services = await tools.get_services(db)
    staff_list = await tools.get_staff(db)
    if not services or not staff_list:
        return {"final_response": "Our system is taking a quick nap... Could you try again in a few minutes?"}

    slots = await tools.get_availability(db, date, staff_list[0]["id"], services[0]["id"])
    if not slots:
        return {"final_response": f"Oh no! It looks like we're fully booked on {date}. Is there another day that works for you?"}

    slots_str = ", ".join(slots[:5])
    return {"final_response": f"I found some openings on {date}! How do these sound: {slots_str}? (I have more if those don't work!)"}

async def escalation_node(state: AgentState):
    """Handles handoff to human."""
    return {
        "needs_human": True,
        "final_response": "I'm so sorry I couldn't quite get that right. I've let my manager know, and someone from our team will jump in and help you out very shortly! 😊"
    }


async def response_generator(state: AgentState):
    """Final node to ensure the response is naturally human and WhatsApp-friendly."""
    response = state.get("final_response", "")
    if not response:
        return state

    # We use a quick LLM pass to "humanize" the response if it's not already.
    # This removes any remaining "AI-isms" like "Based on the context..."
    system_prompt = (
        "You are a social media manager for a high-end hair salon. "
        "Your job is to take a draft response and make it sound like a warm, friendly human "
        "sending a WhatsApp message. Use emojis sparingly, keep it concise, and a bit casual. "
        "NEVER mention that you are an AI or a bot. "
        "Example: 'I can check that for you' -> 'Let me check that for you! ✨'"
    )

    prompt = f"Draft response: {response}"
    humanized = call_llm(prompt, system_prompt)

    return {"final_response": humanized}

# --- Graph Construction ---

def build_salon_graph():
    workflow = StateGraph(AgentState)

    workflow.add_node("classify", classify_intent)
    workflow.add_node("faq", faq_node)
    workflow.add_node("book", booking_node)
    workflow.add_node("confirm_book", confirm_booking_node)
    workflow.add_node("cancel", cancel_node)
    workflow.add_node("availability", availability_node)
    workflow.add_node("escalate", escalation_node)
    workflow.add_node("respond", response_generator)

    workflow.set_entry_point("classify")

    def route_intent(state):
        intent = state["intent"]
        if intent == "confirm_booking": return "confirm_book"
        if intent == "cancel_booking_request": return "book" # Reset to book or faq
        if intent == "faq": return "faq"
        if intent == "book_appointment": return "book"
        if intent == "cancel_appointment": return "cancel"
        if intent == "check_availability": return "availability"
        if intent == "escalate": return "escalate"
        return "faq"

    workflow.add_conditional_edges("classify", route_intent)

    workflow.add_edge("faq", "respond")
    workflow.add_edge("book", "respond")
    workflow.add_edge("confirm_book", "respond")
    workflow.add_edge("cancel", "respond")
    workflow.add_edge("availability", "respond")
    workflow.add_edge("escalate", "respond")
    workflow.add_edge("respond", END)

    return workflow.compile()

salon_agent = build_salon_graph()
