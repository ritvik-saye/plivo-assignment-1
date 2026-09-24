"""Tests for the IVR call flow. They check the XML each webhook returns, no real calls are made."""
import os

os.environ.update(OTP="1503", BASE_URL="https://example.ngrok.app", VALIDATE_SIGNATURE="false")

import pytest  # noqa: E402
from app import app  # noqa: E402


@pytest.fixture
def client():
    app.config["TESTING"] = True
    return app.test_client()


def post(client, path, digits=None):
    data = {"Digits": digits} if digits is not None else {}
    r = client.post(path, data=data)
    assert r.status_code == 200
    assert r.mimetype == "application/xml"
    return r.get_data(as_text=True)


def test_answer_asks_for_4_digit_otp(client):
    body = post(client, "/ivr/answer")
    assert 'numDigits="4"' in body
    assert "https://example.ngrok.app/ivr/otp" in body
    assert "<Redirect>https://example.ngrok.app/ivr/answer</Redirect>" in body  # no input -> re-prompt


def test_wrong_otp_reprompts(client):
    body = post(client, "/ivr/otp", "0000")
    assert "incorrect" in body
    assert "/ivr/otp" in body
    assert "/ivr/language" not in body


def test_empty_otp_reprompts(client):
    assert "incorrect" in post(client, "/ivr/otp", "")


def test_correct_otp_goes_to_language_menu(client):
    body = post(client, "/ivr/otp", "1503")
    assert "verified" in body
    assert "<Redirect>https://example.ngrok.app/ivr/language</Redirect>" in body


@pytest.mark.parametrize("digit,lang", [("1", "en"), ("2", "es")])
def test_language_choice(client, digit, lang):
    body = post(client, "/ivr/language/choice", digit)
    assert f"/ivr/menu?lang={lang}" in body


def test_invalid_language_repeats_prompt(client):
    body = post(client, "/ivr/language/choice", "9")
    assert "Invalid option" in body
    assert "/ivr/language/choice" in body


def test_spanish_menu_is_in_spanish(client):
    body = post(client, "/ivr/menu?lang=es")
    assert "oprima" in body
    assert 'language="es-ES"' in body


def test_option_1_plays_audio_then_returns_to_menu(client):
    body = post(client, "/ivr/menu/choice?lang=en", "1")
    assert "<Play>" in body
    assert "/ivr/menu?lang=en" in body


def test_option_2_dials_associate(client):
    body = post(client, "/ivr/menu/choice?lang=es", "2")
    assert "<Dial" in body and "<Number>" in body
    assert 'callerId="+918035454161"' in body
    assert "Conectandolo" in body


def test_invalid_menu_choice_repeats_in_same_language(client):
    body = post(client, "/ivr/menu/choice?lang=es", "7")
    assert "Opcion invalida" in body
    assert "/ivr/menu/choice?lang=es" in body


def test_unknown_lang_falls_back_to_english(client):
    assert "press 1" in post(client, "/ivr/menu?lang=fr")


def test_failed_dial_returns_to_menu(client):
    r = client.post("/ivr/dial-status?lang=en", data={"DialStatus": "no-answer"})
    assert "unavailable" in r.get_data(as_text=True)


def test_call_rejects_bad_number(client):
    r = client.post("/call", json={"to": "98765"})
    assert r.status_code == 400
