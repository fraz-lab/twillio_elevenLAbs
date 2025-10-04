import os
import json
import base64
import audioop
import asyncio
import threading
import signal
import sys
import traceback
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor
import websockets  # pip install websockets

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request
from fastapi.responses import Response
from twilio.twiml.voice_response import VoiceResponse
from twilio.rest import Client
from dotenv import load_dotenv
import uvicorn

from elevenlabs import ElevenLabs  # For client, but we use raw WS now

# ==== Load environment ====
load_dotenv()

TWILIO_ACCOUNT_SID = os.getenv("TWILIO_ACCOUNT_SID")
TWILIO_AUTH_TOKEN = os.getenv("TWILIO_AUTH_TOKEN")
TWILIO_NUMBER = os.getenv("TWILIO_NUMBER")
NGROK_URL = os.getenv("NGROK_URL")
ELEVENLABS_API_KEY = os.getenv("ELEVENLABS_API_KEY")
AGENT_ID = os.getenv("AGENT_ID")

# Globals
app = FastAPI()
stream_sid_map = {}
current_el_ws = None  # ElevenLabs WebSocket
current_conversation_id = None
current_call_sid = None

# ================== TWILIO ENDPOINTS ==================

@app.post("/voice")
async def voice(request: Request):
    print("📞 [DEBUG] /voice HIT - Twilio fetching TwiML")
    resp = VoiceResponse()
    ngrok_ws = NGROK_URL.replace("https://", "").replace("http://", "")
    ws_url = f"wss://{ngrok_ws}/ws/agent"
    print(f"👉 [DEBUG] Generated WS URL: {ws_url}")  # Confirm path
    resp.connect().stream(url=ws_url)
    twiml = str(resp)
    print(f"📄 [DEBUG] TwiML: {twiml[:200]}...")  # Snippet
    return Response(twiml, media_type="application/xml")

@app.post("/callback")
async def callback(request: Request):
    form = await request.form()
    call_sid = form.get("CallSid")
    call_status = form.get("CallStatus")
    print(f"📡 Call {call_sid} status → {call_status}")
    return Response(status_code=200)

@app.get("/call/{phone_number}")
async def call_user(phone_number: str):
    global current_call_sid
    client = Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)
    print(f"📞 Placing call to {phone_number} ...")

    ngrok_ws = NGROK_URL.replace("https://", "").replace("http://", "")
    voice_url = f"https://{ngrok_ws}/voice"
    callback_url = f"https://{ngrok_ws}/callback"

    call = client.calls.create(
        to=phone_number,
        from_=TWILIO_NUMBER,
        url=voice_url,
        status_callback=callback_url,
        status_callback_event=["initiated", "ringing", "answered", "completed"],
        status_callback_method="POST"
    )
    current_call_sid = call.sid
    print(f"✅ Call initiated. SID: {call.sid}")
    return {"status": "calling", "sid": call.sid}

# ================== TWILIO <-> ELEVENLABS BRIDGE ==================

@app.websocket("/ws/agent")
async def agent_ws(websocket: WebSocket):
    print("🔗 [DEBUG] WS accept attempt")
    await websocket.accept()
    print("🔗 [DEBUG] Twilio WebSocket accepted - entering bridge")
    global current_el_ws
    try:
        current_el_ws = None  # Reset
        await bridge_twilio_eleven(websocket)
    except WebSocketDisconnect as e:
        print(f"🔻 [DEBUG] Clean WS disconnect: {e}")
    except asyncio.TimeoutError:
        print("⏰ [DEBUG] WS timeout - likely idle/ngrok drop")
    except Exception as e:
        print("💥 [DEBUG CRASH] WS handler fatal:", e)
        traceback.print_exc()
        raise  # Re-raise to see in uvicorn
    finally:
        if current_el_ws:
            await current_el_ws.close()
            print("🔒 [DEBUG] Closed ElevenLabs WS")

async def bridge_twilio_eleven(ws_twilio: WebSocket):
    """Bridge audio between Twilio WebSocket and ElevenLabs WebSocket."""
    global current_el_ws, current_conversation_id

    try:
        print("🟢 [DEBUG] Bridge loop starting - awaiting first msg")
        client = ElevenLabs(api_key=ELEVENLABS_API_KEY)
        print("🟢 [bridge] ElevenLabs client created (for potential signed URL)")

        # Assume public agent; if private, get signed URL:
        # signed_response = client.conversational_ai.get_signed_url(agent_id=AGENT_ID)
        # el_url = signed_response.signed_url
        el_url = f"wss://api.elevenlabs.io/v1/convai/conversation?agent_id={AGENT_ID}"
        print(f"🧠 [bridge] Connecting to EL WS: {el_url}")

        current_el_ws = await websockets.connect(el_url)
        print("✅ [bridge] EL WS connected")

        # Task to handle EL incoming messages
        async def handle_el_messages():
            try:
                async for el_msg in current_el_ws:
                    obj = json.loads(el_msg)
                    ev_type = obj.get("type")
                    print(f"📬 [EL] Event type: {ev_type}")
                    if ev_type == "audio":
                        audio_event = obj.get("audio_event", {})
                        audio_b64 = audio_event.get("audio_base_64")
                        if audio_b64:
                            audio_bytes = base64.b64decode(audio_b64)
                            print(f"🎤 [EL] Received audio chunk len: {len(audio_bytes)}")
                            # Assume EL sends PCM16 24kHz mono; convert to mu-law 8kHz for Twilio
                            try:
                                # Resample to 8kHz, mono, then to mu-law
                                pcm8 = audioop.ratecv(audio_bytes, 2, 1, 16000, 8000, None)[0]
                                mulaw = audioop.lin2ulaw(pcm8, 2)
                                payload = base64.b64encode(mulaw).decode("utf-8")
                                sid = stream_sid_map.get("sid")
                                if not sid:
                                    print("⚠️ [DEBUG] No SID - skipping send")
                                    continue
                                twilio_msg = {
                                    "event": "media",
                                    "streamSid": sid,
                                    "media": {"payload": payload}
                                }
                                await ws_twilio.send_text(json.dumps(twilio_msg))
                                print("📤 [bridge] Sent EL audio to Twilio")
                            except Exception as e:
                                print("❌ [bridge] Error converting EL audio:", e)
                    elif ev_type == "user_transcript":
                        transcript = obj.get("user_transcription_event", {}).get("user_transcript", "")
                        print(f"🧍 [EL] User transcript: {transcript}")
                    elif ev_type == "agent_response":
                        response = obj.get("agent_response_event", {}).get("agent_response", "")
                        print(f"🤖 [EL] Agent response: {response}")
                    elif ev_type == "ping":
                        print("📡 [EL] Ping received - send pong if needed")
                        # Optionally: await current_el_ws.send(json.dumps({"type": "pong", ...}))
                    else:
                        print(f"ℹ️ [EL] Unhandled: {obj}")
            except Exception as e:
                print("❌ [EL handler] Error:", e)

        el_task = asyncio.create_task(handle_el_messages())

        last_ping = datetime.now()
        # Now monitor Twilio → EL media
        while True:
            # Send ping to Twilio every 10s
            if (datetime.now() - last_ping).total_seconds() > 10:
                await ws_twilio.send_text(json.dumps({"event": "ping"}))
                print("📡 [DEBUG] Sent ping to Twilio")
                last_ping = datetime.now()

            msg = await ws_twilio.receive_text()
            print(f"🔁 [DEBUG] Msg received: {msg[:50]}...")

            try:
                obj = json.loads(msg)
            except Exception as e:
                print("⚠️ [bridge] JSON parse error:", e, "— msg:", msg)
                continue

            ev = obj.get("event")
            print("📬 [bridge] Twilio Event type:", ev)
            if ev == "connected":
                print("🔗 [bridge] Twilio 'connected' event arrived")
            elif ev == "start":
                start_info = obj.get("start", {})
                stream_sid = start_info.get("streamSid")
                print(f"🟡 [bridge] Stream started: sid={stream_sid}")
                stream_sid_map["sid"] = stream_sid
            elif ev == "media":
                media = obj.get("media", {})
                payload = media.get("payload")
                if payload is None:
                    print("⚠️ [bridge] media event has no payload")
                else:
                    audio_bytes = base64.b64decode(payload)
                    print(f"📤 [bridge] Decoded Twilio media bytes length: {len(audio_bytes)}")
                    try:
                        # Convert mu-law 8kHz to PCM16 16kHz (EL input format)
                        pcm16 = audioop.ulaw2lin(audio_bytes, 2)
                        pcm16 = audioop.ratecv(pcm16, 2, 1, 8000, 16000, None)[0]
                        print(f"🎯 [bridge] Converted to PCM16 16kHz length: {len(pcm16)}")
                        if len(pcm16) < 100:  # Safeguard
                            print("⚠️ [DEBUG] Short converted audio - skipping")
                            continue
                        # Send to EL as base64
                        audio_b64 = base64.b64encode(pcm16).decode("utf-8")
                        el_msg = {
                            "type": "user_audio_chunk",
                            "user_audio_chunk": audio_b64
                        }
                        await current_el_ws.send(json.dumps(el_msg))
                        print("📤 [bridge] Sent audio chunk to EL")
                    except Exception as e:
                        print("❌ [bridge] Error converting / sending to EL:", e)
            elif ev == "stop":
                print("🔴 [bridge] Received ‘stop’ event → breaking")
                break
            else:
                print("ℹ️ [bridge] Unhandled event type:", ev)

        el_task.cancel()
        print("🛑 [bridge] Twilio loop ended")

    except WebSocketDisconnect:
        print("🔻 [bridge] WebSocket disconnected")
    except Exception as e:
        print("❌ [bridge] Exception in bridge:", e)
        traceback.print_exc()
    finally:
        print("🛑 [bridge] Cleaning up")
        if current_el_ws:
            await current_el_ws.close()
        current_el_ws = None
        current_conversation_id = None
        print("🔚 [bridge] Bridge function exit")

# ================== TERMINATION WATCHER ==================

def start_keyboard_watcher():
    def _watch():
        from twilio.rest import Client as TwilioClient
        print("💡 Press 'q' + Enter at any time to end the call and exit.")
        while True:
            user_input = input().strip().lower()
            if user_input == "q":
                print("🛑 Termination requested by user")
                try:
                    global current_el_ws
                    if current_el_ws:
                        asyncio.run(current_el_ws.close())
                        print("🤖 ElevenLabs WS closed")
                    if current_call_sid:
                        tw_client = TwilioClient(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)
                        tw_client.calls(current_call_sid).update(status="completed")
                        print(f"☎️ Twilio call {current_call_sid} hung up")
                except Exception as e:
                    print("Error ending session:", e)
                os._exit(0)
    threading.Thread(target=_watch, daemon=True).start()

# ================== SERVER START ==================

def run_server():
    uvicorn.run(app, host="0.0.0.0", port=5000, log_level="debug")

if __name__ == "__main__":
    start_keyboard_watcher()

    def make_call():
        phone = input("📲 Enter the phone number to call (e.g. +15551234567): ").strip()
        if phone:
            os.system(f"curl http://localhost:5000/call/{phone}")

    threading.Thread(target=make_call, daemon=True).start()

    # run the server cleanly in main thread
    run_server()