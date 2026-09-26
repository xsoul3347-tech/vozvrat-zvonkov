import os
import uuid
from datetime import datetime, timezone
from flask import Flask, request, jsonify, Response, render_template

app = Flask(__name__)

# ------------------------------------------------------------
# DEMO DATABASE
# Для первого теста храним звонки в памяти.
# Позже заменим на PostgreSQL.
# ------------------------------------------------------------
calls = {}


def now():
    return datetime.now(timezone.utc).isoformat()


def create_call(phone: str):
    call_id = str(uuid.uuid4())
    calls[call_id] = {
        "id": call_id,
        "phone": phone,
        "created_at": now(),
        "status": "missed",
        "callback_status": "pending",
        "reason": None,
        "details": None,
        "transcript": [],
        "finished_at": None,
    }
    return calls[call_id]


def xml_response(xml: str):
    return Response(xml, mimetype="application/xml")


# ------------------------------------------------------------
# 1. WEBHOOK ОТ ТЕЛЕФОННОГО ПРОВАЙДЕРА
#
# Провайдер сообщает нашему серверу:
# "На номер клиента был пропущен звонок".
#
# В реальной интеграции сюда подставляется формат
# конкретного провайдера (Binotel / UniTalk / Ringostat / etc.).
# ------------------------------------------------------------
@app.post("/webhook/missed-call")
def missed_call():
    data = request.get_json(silent=True) or request.form
    phone = (data.get("phone") or data.get("from") or "").strip()

    if not phone:
        return jsonify({"ok": False, "error": "phone is required"}), 400

    call = create_call(phone)

    # Здесь будет вызов API телефонного провайдера:
    # start_callback(phone, call["id"])
    #
    # Пока только создаём задачу.
    call["callback_status"] = "ready"

    return jsonify({
        "ok": True,
        "call_id": call["id"],
        "message": "Пропущенный звонок принят. Готов к автоматическому перезвону."
    })


# ------------------------------------------------------------
# 2. ЗАПУСК ОБРАТНОГО ЗВОНКА
#
# Пока это demo endpoint.
# Реальный provider API будет вызываться здесь.
# ------------------------------------------------------------
@app.post("/api/callback/<call_id>")
def start_callback(call_id):
    call = calls.get(call_id)

    if not call:
        return jsonify({"ok": False, "error": "call not found"}), 404

    call["callback_status"] = "calling"
    call["status"] = "callback_started"

    # В реальной версии:
    # provider.calls.create(
    #     to=call["phone"],
    #     callback_url=f"{BASE_URL}/voice/intro?call_id={call_id}"
    # )

    return jsonify({
        "ok": True,
        "call_id": call_id,
        "phone": call["phone"],
        "status": "calling",
        "demo": True,
        "message": "В demo-режиме звонок не совершается. Подключите API провайдера."
    })


# ------------------------------------------------------------
# 3. ГОЛОСОВОЕ ПРИВЕТСТВИЕ
#
# Этот endpoint можно подключить как voice webhook.
# XML совместим с провайдерами, поддерживающими TwiML-подобный
# сценарий. Конкретный формат адаптируем под выбранного провайдера.
# ------------------------------------------------------------
@app.post("/voice/intro")
@app.get("/voice/intro")
def voice_intro():
    call_id = request.args.get("call_id", "")
    action = f"/voice/reason?call_id={call_id}"

    xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<Response>
  <Say language="ru-RU" voice="alice">
    Здравствуйте! Вы звонили нам, но мы не смогли ответить.
  </Say>
  <Pause length="1"/>
  <Say language="ru-RU" voice="alice">
    Подскажите, пожалуйста, по какому вопросу вы обращались?
  </Say>
  <Gather input="speech" language="ru-RU" speechTimeout="auto"
          action="{action}" method="POST">
  </Gather>
  <Say language="ru-RU" voice="alice">
    Я не услышал ответ. Пожалуйста, ожидайте, вам перезвонят.
  </Say>
</Response>"""

    return xml_response(xml)


# ------------------------------------------------------------
# 4. ПЕРВЫЙ ОТВЕТ КЛИЕНТА
# ------------------------------------------------------------
@app.post("/voice/reason")
def voice_reason():
    call_id = request.args.get("call_id", "")
    call = calls.get(call_id)

    speech = (
        request.form.get("SpeechResult")
        or request.form.get("speech")
        or request.json.get("speech") if request.is_json else None
    )

    if call:
        call["reason"] = speech or "Ответ не распознан"
        call["transcript"].append({
            "speaker": "client",
            "text": speech or "Ответ не распознан",
            "time": now()
        })
        call["status"] = "collecting_details"

    action = f"/voice/details?call_id={call_id}"

    xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<Response>
  <Say language="ru-RU" voice="alice">
    Спасибо. Что именно вы хотели узнать или сделать?
  </Say>
  <Gather input="speech" language="ru-RU" speechTimeout="auto"
          action="{action}" method="POST">
  </Gather>
  <Say language="ru-RU" voice="alice">
    Спасибо. Я записал информацию. Пожалуйста, ожидайте, вам перезвонят.
  </Say>
  <Hangup/>
</Response>"""

    return xml_response(xml)


# ------------------------------------------------------------
# 5. УТОЧНЕНИЕ
# ------------------------------------------------------------
@app.post("/voice/details")
def voice_details():
    call_id = request.args.get("call_id", "")
    call = calls.get(call_id)

    speech = (
        request.form.get("SpeechResult")
        or request.form.get("speech")
        or (request.get_json(silent=True) or {}).get("speech")
    )

    if call:
        call["details"] = speech or "Дополнительная информация не распознана"
        call["transcript"].append({
            "speaker": "client",
            "text": speech or "Дополнительная информация не распознана",
            "time": now()
        })
        call["status"] = "waiting_for_owner"
        call["callback_status"] = "finished"
        call["finished_at"] = now()

    xml = """<?xml version="1.0" encoding="UTF-8"?>
<Response>
  <Say language="ru-RU" voice="alice">
    Спасибо. Я записал информацию.
    Пожалуйста, ожидайте — вам перезвонят.
  </Say>
  <Hangup/>
</Response>"""

    return xml_response(xml)


# ------------------------------------------------------------
# 6. ДАННЫЕ ДЛЯ ЛИЧНОГО КАБИНЕТА
# ------------------------------------------------------------
@app.get("/api/calls")
def get_calls():
    result = sorted(
        calls.values(),
        key=lambda item: item["created_at"],
        reverse=True
    )
    return jsonify(result)


@app.get("/api/calls/<call_id>")
def get_call(call_id):
    call = calls.get(call_id)

    if not call:
        return jsonify({"ok": False, "error": "call not found"}), 404

    return jsonify(call)


@app.post("/api/calls/<call_id>/done")
def mark_done(call_id):
    call = calls.get(call_id)

    if not call:
        return jsonify({"ok": False, "error": "call not found"}), 404

    call["status"] = "done"
    return jsonify({"ok": True, "call": call})



# ------------------------------------------------------------
# WEB-СТРАНИЦЫ
# ------------------------------------------------------------
@app.get("/")
def home():
    return render_template("vozvrat-zvonkov.html")

@app.get("/registration")
def registration():
    return render_template("registration.html")

@app.get("/connect-number")
def connect_number():
    return render_template("connect-number.html")

@app.get("/sms-confirm")
def sms_confirm():
    return render_template("sms-confirm.html")

@app.get("/auto-reply")
def auto_reply():
    return render_template("auto-reply.html")

@app.get("/done")
def done():
    return render_template("done.html")

@app.get("/demo")
def demo():
    return render_template("demo.html")

# ------------------------------------------------------------
# HEALTH CHECK — удобно для Render
# ------------------------------------------------------------
@app.get("/health")
def health():
    return jsonify({
        "ok": True,
        "service": "vozvrat-zvonkov-auto-reply",
        "time": now()
    })


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "5000"))
    app.run(host="0.0.0.0", port=port, debug=True)
