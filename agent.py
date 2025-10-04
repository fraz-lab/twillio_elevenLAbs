import asyncio
import traceback
import os
from dotenv import load_dotenv
from elevenlabs.client import ElevenLabs
from elevenlabs.conversational_ai.conversation import Conversation
from elevenlabs.conversational_ai.default_audio_interface import DefaultAudioInterface
import aiohttp

from elevenlabs.client import ElevenLabs
import signal
import sys

load_dotenv()
# ====== 🔧 CONFIG ======
ELEVENLABS_API_KEY = os.getenv("ELEVENLABS_API_KEY")
AGENT_ID = os.getenv("AGENT_ID")
USE_SIGNED_URL = True
# Realtime API endpoint
REQUIRES_AUTH = True
USER_ID = ""
# ====== CALLBACK HANDLERS ======

def on_agent_audio(audio_bytes: bytes):
    """Called when the agent sends audio (PCM) bytes."""
    # You can play it or enqueue it for playback
    print("[Agent Audio] Received", len(audio_bytes), "bytes")

def on_agent_transcript(text: str):
    """Called when the agent sends a transcript (text) of its own speech."""
    print("[Agent Transcript] →", text)

def on_user_transcript(text: str):
    """Called when the user’s spoken input is transcribed."""
    print("[User Transcript] ←", text)

def on_error(exc: Exception):
    """Called when something goes wrong."""
    print("[Error]", exc)
    traceback.print_exc()

# ====== MAIN ======

async def main():
    # Create client
    client = ElevenLabs(api_key=ELEVENLABS_API_KEY)

    # Create conversation with default audio interface
    conv = Conversation(
        client=client,
        agent_id=AGENT_ID,
        requires_auth=REQUIRES_AUTH,
        audio_interface=DefaultAudioInterface(),
        callback_agent_response=lambda r: print("Agent:", r),
        callback_user_transcript=lambda t: print("User:", t),
        callback_agent_response_correction=lambda o, c: print("Agent corrected:", o, "->", c),
    )

    # 👇 return conv immediately so watcher can use it
    asyncio.get_event_loop().call_soon_threadsafe(lambda: start_exit_watcher(asyncio.get_event_loop(), conv))

    loop = asyncio.get_running_loop()
    start_exit_watcher(loop, conv)

    try:
        if USER_ID:
            conv.start_session(user_id=USER_ID)
        else:
            conv.start_session()

        await conv.wait_for_session_end()

    except asyncio.CancelledError:
        print("\n🛑 main(): Cancelled, ending session...")
        try:
            conv.end_session()
        except Exception as e:
            print("Error during conv.end_session():", e)
        raise
    except Exception as e:
        print("❌ Exception in main():", e)
        traceback.print_exc()
        try:
            conv.end_session()
        except Exception as e2:
            print("Error during cleanup:", e2)
def start_exit_watcher(loop, conv):
    import threading
    def _watch():
        print("💡 Press 'q' + Enter at any time to stop the agent.")
        while True:
            try:
                user_input = input().strip().lower()
                if user_input == "q":
                    print("🛑 Exit key pressed — ending session...")
                    try:
                        conv.end_session()
                    except Exception as e:
                        print("Error ending session in watcher:", e)
                    loop.call_soon_threadsafe(loop.stop)
                    break
            except EOFError:
                break
    t = threading.Thread(target=_watch, daemon=True)
    t.start()
def main_entry():
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    task = loop.create_task(main())  # watcher is started inside main now

    def _shutdown(signum, frame):
        print("\n🛑 SIGINT received — cancelling task...")
        task.cancel()

    signal.signal(signal.SIGINT, _shutdown)

    try:
        loop.run_until_complete(task)
    except asyncio.CancelledError:
        print("🌀 main task cancelled — exiting cleanly")
    except Exception as e:
        print("❌ Unhandled exception:", e)
        traceback.print_exc()
    finally:
        loop.close()
        print("✅ Event loop closed")
if __name__ == "__main__":
    main_entry()