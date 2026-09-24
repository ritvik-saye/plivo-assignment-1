# InspireWorks IVR Demo (Plivo Voice API)

A small Flask app that calls your phone, checks an OTP, then walks you through a two-level IVR menu built with Plivo XML.

```
Outbound call ──► OTP (4 digits, your birthdate DDMM)
                    │ wrong / no input → "incorrect, try again" (loops until correct)
                    ▼
                 Level 1: 1 English · 2 Spanish       (invalid → repeat)
                    ▼
                 Level 2 (in chosen language):         (invalid → repeat)
                    1 → play an MP3, then back to Level 2
                    2 → forward the call to a live associate
                        (if they don't answer → back to Level 2)
```

## How it works

- `POST /call` places the outbound call with the Plivo REST API. Its `answer_url` points at `/ivr/answer`.
- Every IVR step is its own endpoint that returns Plivo XML. `<GetInput inputType="dtmf">` collects the digits, and Plivo posts them to the next endpoint as `Digits`.
- **Stateless design:** the chosen language travels in the URL (`/ivr/menu?lang=es`), so there's no database or session to manage.
- **Invalid or missing input:** each prompt is followed by a `<Redirect>` back to itself, so silence repeats the prompt instead of hanging up. A wrong key goes back to the same menu with an "invalid option" message.
- **Security:** the OTP is never logged. Setting `VALIDATE_SIGNATURE=true` rejects webhook requests that aren't signed by Plivo (V3 signature).

| Endpoint | Purpose |
|---|---|
| `GET /` | Web page to trigger the call |
| `POST /call` | Starts the outbound call (`{"to": "+91..."}`) |
| `/ivr/answer` | OTP prompt |
| `/ivr/otp` | Checks the OTP |
| `/ivr/language`, `/ivr/language/choice` | Level 1 |
| `/ivr/menu`, `/ivr/menu/choice` | Level 2 |
| `/ivr/dial-status` | Handles the associate leg ending |
| `/ivr/hangup` | Logs call end (duration, cause) |

## Setup

**You need:** Python 3.9+, a Plivo account (Auth ID and Auth Token from the Plivo console), and [ngrok](https://ngrok.com/download) so Plivo can reach your laptop.

```bash
git clone <this-repo> && cd plivo-ivr
python -m venv venv
source venv/bin/activate          # Windows: venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env              # Windows: copy .env.example .env
```

Fill in `.env`:

| Variable | Meaning |
|---|---|
| `PLIVO_AUTH_ID` / `PLIVO_AUTH_TOKEN` | Plivo credentials |
| `PLIVO_NUMBER` | Plivo number the call comes from (`+`) |
| `ASSOCIATE_NUMBER` | Live associate number (`+`) |
| `TO_NUMBER` | Your phone number in E.164, e.g. `+919876543210` (prefills the page) |
| `OTP` | Your birthdate in DDMM, e.g. `1503` for 15 March |
| `BASE_URL` | Your public ngrok URL, no trailing slash |
| `AUDIO_URL` | Public MP3 played for option 1 (defaults to Plivo's sample) |

## Run

```bash
# terminal 1
ngrok http 5000                   # copy the https://....ngrok-free.app URL into BASE_URL in .env

# terminal 2
python app.py
```

Open http://localhost:5000, enter your number, and click **Call me**. Or from the command line:

```bash
curl -X POST http://localhost:5000/call -H "Content-Type: application/json" -d '{"to": "+919876543210"}'
```

## Test

```bash
pytest -q
```

The tests call each webhook with sample `Digits` and check the returned XML covers: wrong, empty, and correct OTP; both languages; invalid input at every level; audio playback; call forwarding; and an unanswered associate call. No real calls are made.

## Demo script (for the video)

1. Click **Call me** and answer the phone.
2. Enter a wrong OTP (e.g. `0000`). You'll hear "incorrect", then the prompt again.
3. Enter the correct OTP.
4. Press `9` at the language menu to show invalid handling, then press `2` for Spanish.
5. Press `1` to hear the audio. It returns to the menu afterwards.
6. Press `2` to be forwarded to the associate.

## Troubleshooting

- **Call rings but says "application error":** `BASE_URL` is wrong or ngrok isn't running. Check the ngrok dashboard at http://127.0.0.1:4040 for incoming requests.
- **Changed ngrok URL:** free ngrok URLs change on every restart. Update `BASE_URL` and restart the app.
- **Call never arrives:** check the number is in E.164 format, and look at the Plivo console's call logs for the reason.

## What I'd add next

- Limit OTP attempts (e.g. hang up after 5 wrong tries) to stop brute-forcing. The brief asks for unlimited retries, so it isn't enforced here.
- Generate a random OTP per call and send it by SMS, instead of a fixed code.
- Store call events for a simple dashboard.
