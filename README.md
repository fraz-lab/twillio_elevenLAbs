# 🎙️ Real-Time Voice Agent with Twilio + ElevenLabs + FastAPI

This project implements a **real-time AI voice agent** that listens, speaks, and interacts with users over the phone or live audio stream — powered by **Twilio**, **FastAPI**, and **ElevenLabs Realtime API**.

It’s designed to demonstrate how AI voice interfaces can replace or assist human agents in customer support, bookings, or automated information systems.

---

## 🚀 Features

- 🎧 Real-time voice streaming via **Twilio Media Streams**
- 🧠 AI-driven voice agent using **ElevenLabs Realtime API**
- ⚡ Built with **FastAPI** for quick and efficient communication
- 📞 Receives and responds to calls in natural conversation
- 🔄 Async bi-directional audio streaming
- 🗣️ Converts AI text responses into lifelike speech

---

## 🧩 Components Overview

### 1. `app_fast.py` (FastAPI Server)
Handles:
- Incoming WebSocket and Twilio stream connections  
- Routes real-time audio data between Twilio and the AI agent  
- Runs the local server that communicates with ElevenLabs Realtime API

### 2. `agent.py` (AI Agent)
Handles:
- Connecting to ElevenLabs’ Realtime API  
- Sending and receiving transcribed text + generated audio  
- Bridging communication between user voice input and AI responses

---

## 🛠️ Requirements

Before running the project, make sure you have:
- Python **3.10+**
- A **Twilio account**
- An **ElevenLabs API key** (Realtime model enabled)
- Internet connection

---

## 📦 Installation and Setup (All Steps Together)

1. **Clone this repository**
   ```bash
   git clone https://github.com/yourusername/voice-agent.git
   cd voice-agent
