"""Phase 8: local, explainable safety assistant for the dashboard."""

from fastapi import APIRouter
from pydantic import BaseModel, Field

from app.iot.bridge import get_iot_bridge
from app.persistence.event_store import get_event_store

router = APIRouter(prefix="/assistant", tags=["Phase 8: Safety Assistant"])


class AssistantQuestion(BaseModel):
    question: str = Field(..., min_length=1, max_length=500)


@router.post("/ask")
def ask(question: AssistantQuestion):
    text = question.question.lower()
    bridge = get_iot_bridge()
    status = bridge.get_hardware_status()
    safety = status.get("safety", {})
    state = safety.get("safety_state", "UNKNOWN")
    locked = safety.get("door_locked", True)
    if any(word in text for word in ("why", "danger", "lock", "veto")):
        if state in ("CRITICAL", "FAIL_SAFE") or locked:
            answer = f"The door is locked because the current safety state is {state}. The deterministic door-zone controller has priority over touch requests and the ML advisor."
        else:
            answer = f"The current safety state is {state}, so no active veto is holding the door. A real unlock still requires a deliberate handle-touch event."
    elif any(word in text for word in ("mqtt", "wokwi", "esp32", "hardware")):
        answer = "The dashboard calls FastAPI over HTTP. FastAPI evaluates safety and sends structured MQTT commands. Wokwi acts as the ESP32, servo, LED, buzzer, and touch hardware."
    elif any(word in text for word in ("supabase", "database", "log", "record")):
        summary = get_event_store().summary()
        answer = f"The flight recorder has {summary.get('total_events', 0)} local events. SQLite is the immediate durable store; Supabase synchronizes them in the background when configured."
    elif any(word in text for word in ("machine", "model", "random", "risk")):
        answer = "Phase 7's Random Forest provides an advisory risk probability. It cannot override the deterministic door-zone lock decision."
    else:
        answer = f"Current system state: {state}; door {'locked' if locked else 'not locked'}. Ask about the veto, MQTT/Wokwi, Supabase logs, or the ML risk advisor."
    return {"question": question.question, "answer": answer, "safety_state": state, "door_locked": locked}
