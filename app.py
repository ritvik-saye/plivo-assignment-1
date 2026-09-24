
import logging
import os

import plivo
from dotenv import load_dotenv
from flask import Flask, Response, abort, jsonify, render_template, request, url_for
from plivo import plivoxml

load_dotenv()

# ---------------------------------------------------------------- config
AUTH_ID = os.getenv("PLIVO_AUTH_ID", "")
AUTH_TOKEN = os.getenv("PLIVO_AUTH_TOKEN", "")
PLIVO_NUMBER = os.getenv("PLIVO_NUMBER", "")
ASSOCIATE_NUMBER = os.getenv("ASSOCIATE_NUMBER", "")
DEFAULT_TO_NUMBER = os.getenv("TO_NUMBER", "")
BASE_URL = os.getenv("BASE_URL", "").rstrip("/")  # public URL, e.g. ngrok
OTP = os.getenv("OTP", "1503")  # birthdate in DDMM
AUDIO_URL = os.getenv("AUDIO_URL", "https://s3.amazonaws.com/plivocloud/Trumpet.mp3")
VALIDATE_SIGNATURE = os.getenv("VALIDATE_SIGNATURE", "false").lower() == "true"

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("ivr")

app = Flask(__name__)

# ---------------------------------------------------------------- prompts
# One place for all spoken text, so adding a language is just a new entry.
LANGS = {
    "en": {"tts": "en-US"},
    "es": {"tts": "es-ES"},
}
PROMPTS = {
    "otp": "Welcome to InspireWorks. Please enter your 4 digit O T P.",
    "otp_wrong": "The O T P you entered is incorrect. Please enter your 4 digit O T P again.",
    "otp_none": "We did not receive any input.",
    "otp_ok": "Thank you. You have been verified.",
    "lang": "For English, press 1. Para espanol, oprima 2.",
    "invalid": "Invalid option. Please try again.",
    "en": {
        "menu": "To hear a short message, press 1. To speak with a live associate, press 2.",
        "invalid": "Invalid option. Please try again.",
        "playing": "Here is your message.",
        "after_audio": "Returning to the main menu.",
        "connecting": "Connecting you to a live associate. Please hold.",
        "dial_failed": "Sorry, the associate is unavailable right now.",
    },
    "es": {
        "menu": "Para escuchar un mensaje corto, oprima 1. Para hablar con un asociado, oprima 2.",
        "invalid": "Opcion invalida. Por favor, intente de nuevo.",
        "playing": "Aqui esta su mensaje.",
        "after_audio": "Volviendo al menu principal.",
        "connecting": "Conectandolo con un asociado. Por favor espere.",
        "dial_failed": "Lo sentimos, el asociado no esta disponible en este momento.",
    },
}


# ---------------------------------------------------------------- helpers
def abs_url(endpoint, **params):
    """Absolute URL Plivo can reach (BASE_URL when behind ngrok)."""
    path = url_for(endpoint, **params)
    return f"{BASE_URL}{path}" if BASE_URL else url_for(endpoint, _external=True, **params)


def xml(response):
    return Response(response.to_string(), mimetype="application/xml")


def say(text, lang="en"):
    return plivoxml.SpeakElement(text, language=LANGS[lang]["tts"], voice="WOMAN")


def gather(action, num_digits, prompt, lang="en"):
    """DTMF-only GetInput with the prompt nested inside, so callers can barge in."""
    g = plivoxml.GetInputElement(
        action=action,
        method="POST",
        input_type="dtmf",
        num_digits=num_digits,
        digit_end_timeout=5,
        execution_timeout=15,
        redirect=True,
    )
    g.add(say(prompt, lang))
    return g


def digits():
    return (request.values.get("Digits") or "").strip()


def get_lang():
    lang = request.args.get("lang", "en")
    return lang if lang in LANGS else "en"


@app.before_request
def check_signature():
    """Optionally reject webhook requests that were not signed by Plivo."""
    if not VALIDATE_SIGNATURE or not request.path.startswith("/ivr/"):
        return
    ok = plivo.utils.validate_v3_signature(
        request.method,
        request.url if not BASE_URL else BASE_URL + request.full_path.rstrip("?"),
        request.headers.get("X-Plivo-Signature-V3-Nonce", ""),
        AUTH_TOKEN,
        request.headers.get("X-Plivo-Signature-V3", ""),
        request.form.to_dict() if request.method == "POST" else None,
    )
    if not ok:
        log.warning("Rejected unsigned request to %s", request.path)
        abort(403)


# ---------------------------------------------------------------- outbound call
@app.get("/")
def index():
    return render_template("index.html", default_to=DEFAULT_TO_NUMBER, from_number=PLIVO_NUMBER)


@app.post("/call")
def make_call():
    data = request.get_json(silent=True) or request.form
    to = (data.get("to") or DEFAULT_TO_NUMBER).replace(" ", "")
    if not to.startswith("+") or not to[1:].isdigit():
        return jsonify(error="Enter the number in E.164 format, e.g. +919876543210"), 400
    if not (AUTH_ID and AUTH_TOKEN and BASE_URL):
        return jsonify(error="Set PLIVO_AUTH_ID, PLIVO_AUTH_TOKEN and BASE_URL in .env"), 500
    try:
        client = plivo.RestClient(AUTH_ID, AUTH_TOKEN)
        resp = client.calls.create(
            from_=PLIVO_NUMBER,
            to_=to,
            answer_url=abs_url("answer"),
            answer_method="POST",
            hangup_url=abs_url("hangup"),
            hangup_method="POST",
        )
    except plivo.exceptions.PlivoRestError as e:
        log.exception("Call failed")
        return jsonify(error=str(e)), 502
    log.info("Call placed to %s: %s", to, resp)
    return jsonify(message="Calling " + to, request_uuid=getattr(resp, "request_uuid", None)), 201


# ---------------------------------------------------------------- IVR: OTP
@app.route("/ivr/answer", methods=["GET", "POST"])
def answer():
    log.info("Call answered: %s", request.values.get("CallUUID"))
    return otp_prompt(PROMPTS["otp"])


def otp_prompt(text):
    r = plivoxml.ResponseElement()
    r.add(gather(abs_url("verify_otp"), 4, text))
    # Runs only if the caller entered nothing: re-prompt instead of hanging up.
    r.add(say(PROMPTS["otp_none"]))
    r.add(plivoxml.RedirectElement(abs_url("answer")))
    return xml(r)


@app.route("/ivr/otp", methods=["GET", "POST"])
def verify_otp():
    entered = digits()
    if entered != OTP:
        log.info("Wrong OTP entered (%d digits)", len(entered))  # never log the OTP itself
        return otp_prompt(PROMPTS["otp_wrong"])
    log.info("OTP verified")
    r = plivoxml.ResponseElement()
    r.add(say(PROMPTS["otp_ok"]))
    r.add(plivoxml.RedirectElement(abs_url("language_menu")))
    return xml(r)


# ---------------------------------------------------------------- IVR: level 1
@app.route("/ivr/language", methods=["GET", "POST"])
def language_menu(prefix=""):
    r = plivoxml.ResponseElement()
    r.add(gather(abs_url("language_choice"), 1, prefix + PROMPTS["lang"]))
    r.add(plivoxml.RedirectElement(abs_url("language_menu")))
    return xml(r)


@app.route("/ivr/language/choice", methods=["GET", "POST"])
def language_choice():
    choice = {"1": "en", "2": "es"}.get(digits())
    if not choice:
        return language_menu(prefix=PROMPTS["invalid"] + " ")
    r = plivoxml.ResponseElement()
    r.add(plivoxml.RedirectElement(abs_url("action_menu", lang=choice)))
    return xml(r)


# ---------------------------------------------------------------- IVR: level 2
@app.route("/ivr/menu", methods=["GET", "POST"])
def action_menu(prefix=""):
    lang = get_lang()
    r = plivoxml.ResponseElement()
    r.add(gather(abs_url("action_choice", lang=lang), 1, prefix + PROMPTS[lang]["menu"], lang))
    r.add(plivoxml.RedirectElement(abs_url("action_menu", lang=lang)))
    return xml(r)


@app.route("/ivr/menu/choice", methods=["GET", "POST"])
def action_choice():
    lang = get_lang()
    p = PROMPTS[lang]
    choice = digits()
    r = plivoxml.ResponseElement()

    if choice == "1":
        r.add(say(p["playing"], lang))
        r.add(plivoxml.PlayElement(AUDIO_URL))
        r.add(say(p["after_audio"], lang))
        r.add(plivoxml.RedirectElement(abs_url("action_menu", lang=lang)))
    elif choice == "2":
        r.add(say(p["connecting"], lang))
        dial = plivoxml.DialElement(
            caller_id=PLIVO_NUMBER,
            timeout=30,
            action=abs_url("dial_status", lang=lang),
            method="POST",
            redirect=True,
        )
        dial.add(plivoxml.NumberElement(ASSOCIATE_NUMBER))
        r.add(dial)
    else:
        return action_menu(prefix=p["invalid"] + " ")
    return xml(r)


@app.route("/ivr/dial-status", methods=["GET", "POST"])
def dial_status():
    """Called when the associate leg ends. If it never connected, go back to the menu."""
    lang = get_lang()
    status = request.values.get("DialStatus", "")
    log.info("Dial finished: %s", status)
    r = plivoxml.ResponseElement()
    if status != "completed":
        r.add(say(PROMPTS[lang]["dial_failed"], lang))
        r.add(plivoxml.RedirectElement(abs_url("action_menu", lang=lang)))
    else:
        r.add(plivoxml.HangupElement())
    return xml(r)


@app.route("/ivr/hangup", methods=["GET", "POST"])
def hangup():
    log.info(
        "Call ended: uuid=%s duration=%s cause=%s",
        request.values.get("CallUUID"),
        request.values.get("Duration"),
        request.values.get("HangupCause"),
    )
    return "", 200


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "5000")), debug=True)
