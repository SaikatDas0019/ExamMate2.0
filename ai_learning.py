
"""
ExamMate AI Learning System
Phase 1 - Gemini AI Tutor Backend

This module is registered from app.py using:

register_ai_learning_routes(
    app=app,
    get_db_connection=get_db_connection,
    current_user=_current_user,
    request_data=_request_data
)
"""

import os
import json
import uuid
import logging
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import requests
from flask import request, jsonify


# ============================================================
# CONFIGURATION
# ============================================================

IST = ZoneInfo("Asia/Kolkata")

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "").strip()

DEFAULT_GEMINI_MODEL = os.getenv(
    "GEMINI_MODEL",
    "gemini-2.5-flash"
).strip()

GEMINI_API_URL = (
    "https://generativelanguage.googleapis.com/v1beta/models/"
)

AI_DAILY_LIMIT = int(os.getenv("AI_DAILY_LIMIT", "30"))
AI_MAX_INPUT_LENGTH = int(os.getenv("AI_MAX_INPUT_LENGTH", "8000"))
AI_MAX_OUTPUT_TOKENS = int(os.getenv("AI_MAX_OUTPUT_TOKENS", "1200"))

AI_REQUEST_TIMEOUT = 45

logger = logging.getLogger(__name__)


# ============================================================
# GENERAL HELPERS
# ============================================================

def _now():
    return datetime.now(IST).replace(tzinfo=None)


def _now_string():
    return _now().strftime("%Y-%m-%d %H:%M:%S")


def _today_start():
    return _now().replace(
        hour=0,
        minute=0,
        second=0,
        microsecond=0
    ).strftime("%Y-%m-%d %H:%M:%S")


def _new_id():
    return str(uuid.uuid4())


def _json_dump(value):
    return json.dumps(value, ensure_ascii=False)


def _json_load(value, default=None):
    if default is None:
        default = {}

    if not value:
        return default

    try:
        return json.loads(value)
    except (ValueError, TypeError):
        return default


def _row_to_dict(row):
    if row is None:
        return None

    try:
        return dict(row)
    except (TypeError, ValueError):
        return row


def _get_value(row, key, default=None):
    if row is None:
        return default

    try:
        value = row[key]
        return default if value is None else value
    except (KeyError, IndexError, TypeError):
        return default


def _clean_text(value, max_length=500):
    if not isinstance(value, str):
        return ""

    return value.strip()[:max_length]


def _get_email(user):
    if not isinstance(user, dict):
        return ""

    return _clean_text(
        user.get("email", ""),
        255
    ).lower()


def _error(message, status=400):
    return jsonify({
        "success": False,
        "message": message
    }), status


def _success(data=None, message="Success"):
    response = {
        "success": True,
        "message": message
    }

    if data is not None:
        response["data"] = data

    return jsonify(response)


# ============================================================
# DATABASE HELPERS
# ============================================================

class AIDatabase:

    def __init__(self, get_db_connection):
        self.get_db_connection = get_db_connection

    def connect(self):
        return self.get_db_connection()

    @staticmethod
    def placeholder(db_type):
        return "%s" if db_type == "postgres" else "?"

    def execute(self, cursor, db_type, query, params=()):
        """
        Use the correct placeholder style for SQLite/PostgreSQL.
        """

        if db_type == "postgres":
            query = query.replace("?", "%s")

        cursor.execute(query, params)

    def create_tables(self):
        conn, db_type = self.connect()
        cursor = conn.cursor()

        try:

            # --------------------------------------------
            # Learning Sessions
            # --------------------------------------------

            self.execute(cursor, db_type, """
                CREATE TABLE IF NOT EXISTS ai_learning_sessions (
                    session_id TEXT PRIMARY KEY,
                    user_email TEXT NOT NULL,
                    context_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
            """)

            # --------------------------------------------
            # Conversations
            # --------------------------------------------

            self.execute(cursor, db_type, """
                CREATE TABLE IF NOT EXISTS ai_conversations (
                    conversation_id TEXT PRIMARY KEY,
                    user_email TEXT NOT NULL,
                    session_id TEXT,
                    title TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
            """)

            # --------------------------------------------
            # Messages
            # --------------------------------------------

            self.execute(cursor, db_type, """
                CREATE TABLE IF NOT EXISTS ai_messages (
                    message_id TEXT PRIMARY KEY,
                    conversation_id TEXT NOT NULL,
                    user_email TEXT NOT NULL,
                    role TEXT NOT NULL,
                    content TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )
            """)

            # --------------------------------------------
            # Usage Logs
            # --------------------------------------------

            self.execute(cursor, db_type, """
                CREATE TABLE IF NOT EXISTS ai_usage_logs (
                    id TEXT PRIMARY KEY,
                    user_email TEXT NOT NULL,
                    conversation_id TEXT,
                    model TEXT,
                    input_characters INTEGER DEFAULT 0,
                    output_characters INTEGER DEFAULT 0,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )
            """)

            # --------------------------------------------
            # Indexes
            # --------------------------------------------

            self.execute(cursor, db_type, """
                CREATE INDEX IF NOT EXISTS idx_ai_sessions_user
                ON ai_learning_sessions(user_email)
            """)

            self.execute(cursor, db_type, """
                CREATE INDEX IF NOT EXISTS idx_ai_conversations_user
                ON ai_conversations(user_email, updated_at)
            """)

            self.execute(cursor, db_type, """
                CREATE INDEX IF NOT EXISTS idx_ai_messages_conversation
                ON ai_messages(conversation_id, created_at)
            """)

            self.execute(cursor, db_type, """
                CREATE INDEX IF NOT EXISTS idx_ai_usage_user_date
                ON ai_usage_logs(user_email, created_at)
            """)

            conn.commit()

        except Exception:
            conn.rollback()
            logger.exception("AI database initialization failed")
            raise

        finally:
            cursor.close()
            conn.close()

    def fetch_one(self, query, params=()):
        conn, db_type = self.connect()
        cursor = conn.cursor()

        try:
            self.execute(cursor, db_type, query, params)
            return _row_to_dict(cursor.fetchone())

        finally:
            cursor.close()
            conn.close()

    def fetch_all(self, query, params=()):
        conn, db_type = self.connect()
        cursor = conn.cursor()

        try:
            self.execute(cursor, db_type, query, params)
            return [
                _row_to_dict(row)
                for row in cursor.fetchall()
            ]

        finally:
            cursor.close()
            conn.close()

    def insert(self, query, params=()):
        conn, db_type = self.connect()
        cursor = conn.cursor()

        try:
            self.execute(cursor, db_type, query, params)
            conn.commit()

        except Exception:
            conn.rollback()
            raise

        finally:
            cursor.close()
            conn.close()

    def count_today(self, email):
        conn, db_type = self.connect()
        cursor = conn.cursor()

        try:
            self.execute(
                cursor,
                db_type,
                """
                SELECT COUNT(*) AS total
                FROM ai_usage_logs
                WHERE user_email = ?
                AND created_at >= ?
                AND status = ?
                """,
                (email, _today_start(), "success")
            )

            row = cursor.fetchone()

            return int(_get_value(row, "total", 0) or 0)

        finally:
            cursor.close()
            conn.close()


# ============================================================
# STUDENT CONTEXT VALIDATION
# ============================================================

ALLOWED_CONTEXT_FIELDS = {
    "class",
    "board",
    "subject",
    "chapter",
    "topic",
    "language",
    "learning_level",
    "learning_goal"
}


def _validate_context(context):

    if not isinstance(context, dict):
        return None

    cleaned = {}

    for key in ALLOWED_CONTEXT_FIELDS:

        value = context.get(key)

        if isinstance(value, str):
            cleaned[key] = value.strip()[:200]

    return cleaned


def _context_prompt(context):

    if not context:
        return "No additional learning context has been provided."

    labels = {
        "class": "Class",
        "board": "Board",
        "subject": "Subject",
        "chapter": "Chapter",
        "topic": "Topic",
        "language": "Preferred Language",
        "learning_level": "Learning Level",
        "learning_goal": "Learning Goal"
    }

    lines = []

    for key, label in labels.items():

        value = context.get(key)

        if value:
            lines.append(f"{label}: {value}")

    return "\n".join(lines)


# ============================================================
# GEMINI PROVIDER
# ============================================================

def _available_models():

    configured = os.getenv(
        "GEMINI_AVAILABLE_MODELS",
        DEFAULT_GEMINI_MODEL
    )

    models = [
        item.strip()
        for item in configured.split(",")
        if item.strip()
    ]

    if DEFAULT_GEMINI_MODEL not in models:
        models.insert(0, DEFAULT_GEMINI_MODEL)

    return models


def _call_gemini(
    message,
    context,
    history=None,
    selected_model=None
):

    if not GEMINI_API_KEY:
        raise RuntimeError(
            "Gemini API key is not configured on the server."
        )

    models = _available_models()

    model = (
        selected_model
        if selected_model in models
        else DEFAULT_GEMINI_MODEL
    )

    system_prompt = f"""
You are ExamMate AI Tutor.

Your purpose is to help students understand academic topics,
prepare for examinations and improve their learning.

STUDENT LEARNING CONTEXT:
{_context_prompt(context)}

TEACHING RULES:

1. Explain concepts in a simple and understandable way.
2. Adapt explanations to the student's class and learning level.
3. Use the student's preferred language whenever possible.
4. Break difficult concepts into smaller steps.
5. Give examples when useful.
6. Explain formulas and numerical problems step by step.
7. Do not invent facts, textbook references or sources.
8. If information is uncertain, clearly say so.
9. Encourage understanding instead of simply giving answers.
10. Keep responses age-appropriate and educational.
11. Do not ask for passwords, OTPs, phone numbers or private account details.
12. If the student asks an unrelated question, answer briefly and
    politely guide the conversation back to learning when appropriate.

IMPORTANT:
You are a tutor, not an examination authority.
Do not claim that a question will definitely appear in an exam.
"""

    contents = []

    # Keep only the latest messages to control token usage.
    for item in (history or [])[-10:]:

        role = item.get("role")

        if role not in ("user", "assistant"):
            continue

        content = _clean_text(
            item.get("content", ""),
            8000
        )

        if not content:
            continue

        gemini_role = (
            "model" if role == "assistant" else "user"
        )

        contents.append({
            "role": gemini_role,
            "parts": [
                {"text": content}
            ]
        })

    contents.append({
        "role": "user",
        "parts": [
            {"text": message}
        ]
    })

    url = (
        GEMINI_API_URL
        + model
        + ":generateContent"
    )

    payload = {
        "systemInstruction": {
            "parts": [
                {"text": system_prompt}
            ]
        },
        "contents": contents,
        "generationConfig": {
            "temperature": 0.5,
            "maxOutputTokens": AI_MAX_OUTPUT_TOKENS
        }
    }

    response = requests.post(
        url,
        params={"key": GEMINI_API_KEY},
        json=payload,
        timeout=AI_REQUEST_TIMEOUT
    )

    if response.status_code != 200:

        logger.error(
            "Gemini API error: status=%s body=%s",
            response.status_code,
            response.text[:1000]
        )

        if response.status_code == 429:
            raise RuntimeError(
                "AI service is temporarily busy. Please try again later."
            )

        if response.status_code in (401, 403):
            raise RuntimeError(
                "AI service configuration error. Please contact support."
            )

        raise RuntimeError(
            "AI service could not process your request."
        )

    data = response.json()

    candidates = data.get("candidates", [])

    if not candidates:
        raise RuntimeError(
            "AI did not return a response. Please try again."
        )

    parts = (
        candidates[0]
        .get("content", {})
        .get("parts", [])
    )

    answer = "\n".join(
        part.get("text", "")
        for part in parts
        if isinstance(part, dict) and part.get("text")
    ).strip()

    if not answer:
        raise RuntimeError(
            "AI returned an empty response."
        )

    return answer, model


# ============================================================
# ROUTE REGISTRATION
# ============================================================

def register_ai_learning_routes(
    app,
    get_db_connection,
    current_user,
    request_data
):

    db = AIDatabase(get_db_connection)

    # Create AI tables when the application starts.
    try:
        db.create_tables()
        app.logger.info(
            "ExamMate AI database initialized successfully."
        )

    except Exception:
        app.logger.exception(
            "Could not initialize ExamMate AI database."
        )

    # --------------------------------------------------------
    # AUTHENTICATION HELPER
    # --------------------------------------------------------

    def authenticated_user():

        user = current_user()

        if not isinstance(user, dict):
            return None, _error(
                "Please log in to use AI Learning.",
                401
            )

        email = _get_email(user)

        if not email or "@" not in email:
            return None, _error(
                "Invalid user session. Please log in again.",
                401
            )

        return {
            "email": email,
            "name": _clean_text(user.get("name", ""), 255),
            "role": _clean_text(user.get("role", ""), 50)
        }, None

    # --------------------------------------------------------
    # OWNERSHIP HELPERS
    # --------------------------------------------------------

    def get_session(session_id, email):

        return db.fetch_one(
            """
            SELECT *
            FROM ai_learning_sessions
            WHERE session_id = ?
            AND user_email = ?
            """,
            (session_id, email)
        )

    def get_conversation(conversation_id, email):

        return db.fetch_one(
            """
            SELECT *
            FROM ai_conversations
            WHERE conversation_id = ?
            AND user_email = ?
            """,
            (conversation_id, email)
        )

    # --------------------------------------------------------
    # USAGE LOGGER
    # --------------------------------------------------------

    def log_usage(
        email,
        conversation_id,
        model,
        input_chars,
        output_chars,
        status
    ):

        db.insert(
            """
            INSERT INTO ai_usage_logs (
                id,
                user_email,
                conversation_id,
                model,
                input_characters,
                output_characters,
                status,
                created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                _new_id(),
                email,
                conversation_id,
                model,
                input_chars,
                output_chars,
                status,
                _now_string()
            )
        )

    # ========================================================
    # API: AI CONFIGURATION
    # ========================================================

    @app.route("/api/ai/config", methods=["GET"])
    def ai_config():

        user, error = authenticated_user()

        if error:
            return error

        return _success({
            "provider": "gemini",
            "default_model": DEFAULT_GEMINI_MODEL,
            "available_models": _available_models(),
            "daily_limit": AI_DAILY_LIMIT,
            "max_input_length": AI_MAX_INPUT_LENGTH,
            "features": {
                "chat": True,
                "learning_context": True,
                "conversation_history": True,
                "study_materials": False,
                "quiz_generation": False
            }
        })

    # ========================================================
    # API: CREATE / LIST LEARNING SESSIONS
    # ========================================================

    @app.route("/api/ai/sessions", methods=["GET", "POST"])
    def ai_sessions():

        user, error = authenticated_user()

        if error:
            return error

        email = user["email"]

        if request.method == "GET":

            sessions = db.fetch_all(
                """
                SELECT session_id, context_json, created_at, updated_at
                FROM ai_learning_sessions
                WHERE user_email = ?
                ORDER BY updated_at DESC
                LIMIT 50
                """,
                (email,)
            )

            for item in sessions:
                item["context"] = _json_load(
                    item.pop("context_json", "{}")
                )

            return _success({
                "sessions": sessions
            })

        data = request_data()

        if not isinstance(data, dict):
            return _error("Invalid request data.")

        context = _validate_context(
            data.get("context", {})
        )

        if context is None:
            return _error("Invalid learning context.")

        session_id = _new_id()
        now = _now_string()

        db.insert(
            """
            INSERT INTO ai_learning_sessions (
                session_id,
                user_email,
                context_json,
                created_at,
                updated_at
            )
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                session_id,
                email,
                _json_dump(context),
                now,
                now
            )
        )

        return _success({
            "session_id": session_id,
            "context": context,
            "created_at": now
        }, "Learning session created.")

    # ========================================================
    # API: UPDATE LEARNING SESSION CONTEXT
    # ========================================================

    @app.route(
        "/api/ai/sessions/<session_id>",
        methods=["GET", "PUT"]
    )
    def ai_session_detail(session_id):

        user, error = authenticated_user()

        if error:
            return error

        email = user["email"]

        session_data = get_session(
            session_id,
            email
        )

        if not session_data:
            return _error(
                "Learning session not found.",
                404
            )

        if request.method == "GET":

            return _success({
                "session_id": session_id,
                "context": _json_load(
                    _get_value(session_data, "context_json")
                ),
                "created_at": _get_value(
                    session_data, "created_at"
                ),
                "updated_at": _get_value(
                    session_data, "updated_at"
                )
            })

        data = request_data()

        context = _validate_context(
            data.get("context", {})
        )

        if context is None:
            return _error("Invalid learning context.")

        db.insert(
            """
            UPDATE ai_learning_sessions
            SET context_json = ?,
                updated_at = ?
            WHERE session_id = ?
            AND user_email = ?
            """,
            (
                _json_dump(context),
                _now_string(),
                session_id,
                email
            )
        )

        return _success({
            "session_id": session_id,
            "context": context
        }, "Learning context updated.")

    # ========================================================
    # API: CREATE / LIST CONVERSATIONS
    # ========================================================

    @app.route("/api/ai/conversations", methods=["GET", "POST"])
    def ai_conversations():

        user, error = authenticated_user()

        if error:
            return error

        email = user["email"]

        if request.method == "GET":

            conversations = db.fetch_all(
                """
                SELECT conversation_id, session_id, title,
                       created_at, updated_at
                FROM ai_conversations
                WHERE user_email = ?
                ORDER BY updated_at DESC
                LIMIT 50
                """,
                (email,)
            )

            return _success({
                "conversations": conversations
            })

        data = request_data()

        session_id = _clean_text(
            data.get("session_id", ""),
            100
        )

        if session_id:

            learning_session = get_session(
                session_id,
                email
            )

            if not learning_session:
                return _error(
                    "Learning session not found.",
                    404
                )

        title = _clean_text(
            data.get("title", "New Learning Chat"),
            150
        ) or "New Learning Chat"

        conversation_id = _new_id()
        now = _now_string()

        db.insert(
            """
            INSERT INTO ai_conversations (
                conversation_id,
                user_email,
                session_id,
                title,
                created_at,
                updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                conversation_id,
                email,
                session_id or None,
                title,
                now,
                now
            )
        )

        return _success({
            "conversation_id": conversation_id,
            "session_id": session_id or None,
            "title": title,
            "created_at": now
        }, "Conversation created.")

    # ========================================================
    # API: GET CONVERSATION MESSAGES
    # ========================================================

    @app.route(
        "/api/ai/conversations/<conversation_id>/messages",
        methods=["GET"]
    )
    def ai_conversation_messages(conversation_id):

        user, error = authenticated_user()

        if error:
            return error

        email = user["email"]

        conversation = get_conversation(
            conversation_id,
            email
        )

        if not conversation:
            return _error(
                "Conversation not found.",
                404
            )

        messages = db.fetch_all(
            """
            SELECT message_id, role, content, created_at
            FROM ai_messages
            WHERE conversation_id = ?
            AND user_email = ?
            ORDER BY created_at ASC
            LIMIT 200
            """,
            (
                conversation_id,
                email
            )
        )

        return _success({
            "conversation_id": conversation_id,
            "messages": messages
        })

    # ========================================================
    # API: AI CHAT
    # ========================================================

    @app.route("/api/ai/chat", methods=["POST"])
    def ai_chat():

        user, error = authenticated_user()

        if error:
            return error

        email = user["email"]

        data = request_data()

        if not isinstance(data, dict):
            return _error("Invalid request data.")

        message = data.get("message")

        if not isinstance(message, str):
            return _error("Please enter a valid message.")

        message = message.strip()

        if not message:
            return _error("Message cannot be empty.")

        if len(message) > AI_MAX_INPUT_LENGTH:
            return _error(
                f"Message is too long. Maximum length is {AI_MAX_INPUT_LENGTH} characters."
            )

        # --------------------------------------------
        # Check daily usage limit
        # --------------------------------------------

        try:
            used_today = db.count_today(email)

        except Exception:
            logger.exception("Could not check AI usage.")
            return _error(
                "Could not verify AI usage. Please try again.",
                500
            )

        if used_today >= AI_DAILY_LIMIT:

            return jsonify({
                "success": False,
                "message": "You have reached your daily AI usage limit.",
                "usage": {
                    "used": used_today,
                    "limit": AI_DAILY_LIMIT,
                    "remaining": 0
                }
            }), 429

        # --------------------------------------------
        # Get or create conversation
        # --------------------------------------------

        conversation_id = _clean_text(
            data.get("conversation_id", ""),
            100
        )

        conversation = None

        if conversation_id:

            conversation = get_conversation(
                conversation_id,
                email
            )

            if not conversation:
                return _error(
                    "Conversation not found.",
                    404
                )

        else:

            session_id = _clean_text(
                data.get("session_id", ""),
                100
            )

            if session_id and not get_session(
                session_id,
                email
            ):
                return _error(
                    "Learning session not found.",
                    404
                )

            conversation_id = _new_id()
            now = _now_string()

            title = _clean_text(
                message,
                60
            ) or "New Learning Chat"

            db.insert(
                """
                INSERT INTO ai_conversations (
                    conversation_id,
                    user_email,
                    session_id,
                    title,
                    created_at,
                    updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    conversation_id,
                    email,
                    session_id or None,
                    title,
                    now,
                    now
                )
            )

            conversation = get_conversation(
                conversation_id,
                email
            )

        # --------------------------------------------
        # Resolve learning context
        # --------------------------------------------

        context = {}

        session_id = _get_value(
            conversation,
            "session_id"
        )

        if session_id:

            learning_session = get_session(
                session_id,
                email
            )

            if learning_session:

                context = _json_load(
                    _get_value(
                        learning_session,
                        "context_json"
                    )
                )

        # Allow context to be supplied on the first message.
        # Only accepted when the conversation has no messages.
        existing_messages = db.fetch_all(
            """
            SELECT role, content
            FROM ai_messages
            WHERE conversation_id = ?
            AND user_email = ?
            ORDER BY created_at ASC
            LIMIT 20
            """,
            (
                conversation_id,
                email
            )
        )

        if not existing_messages and isinstance(
            data.get("context"), dict
        ):

            supplied_context = _validate_context(
                data.get("context")
            )

            if supplied_context is not None:
                context = supplied_context

        # --------------------------------------------
        # Select configured model
        # --------------------------------------------

        requested_model = _clean_text(
            data.get("model", ""),
            100
        )

        if requested_model not in _available_models():
            requested_model = DEFAULT_GEMINI_MODEL

        # --------------------------------------------
        # Call Gemini
        # --------------------------------------------

        try:

            answer, used_model = _call_gemini(
                message=message,
                context=context,
                history=existing_messages,
                selected_model=requested_model
            )

        except RuntimeError as exc:

            try:
                log_usage(
                    email,
                    conversation_id,
                    requested_model,
                    len(message),
                    0,
                    "failed"
                )
            except Exception:
                logger.exception("Could not save failed usage log.")

            return _error(str(exc), 503)

        except requests.RequestException:

            logger.exception("Gemini connection failed.")

            try:
                log_usage(
                    email,
                    conversation_id,
                    requested_model,
                    len(message),
                    0,
                    "failed"
                )
            except Exception:
                logger.exception("Could not save failed usage log.")

            return _error(
                "Could not connect to the AI service. Please try again.",
                503
            )

        except Exception:

            logger.exception("Unexpected AI chat error.")

            return _error(
                "Something went wrong while generating the response.",
                500
            )

        # --------------------------------------------
        # Save messages
        # --------------------------------------------

        now = _now_string()

        try:

            conn, db_type = db.connect()
            cursor = conn.cursor()

            try:

                db.execute(
                    cursor,
                    db_type,
                    """
                    INSERT INTO ai_messages (
                        message_id,
                        conversation_id,
                        user_email,
                        role,
                        content,
                        created_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        _new_id(),
                        conversation_id,
                        email,
                        "user",
                        message,
                        now
                    )
                )

                db.execute(
                    cursor,
                    db_type,
                    """
                    INSERT INTO ai_messages (
                        message_id,
                        conversation_id,
                        user_email,
                        role,
                        content,
                        created_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        _new_id(),
                        conversation_id,
                        email,
                        "assistant",
                        answer,
                        now
                    )
                )

                db.execute(
                    cursor,
                    db_type,
                    """
                    UPDATE ai_conversations
                    SET updated_at = ?
                    WHERE conversation_id = ?
                    AND user_email = ?
                    """,
                    (
                        now,
                        conversation_id,
                        email
                    )
                )

                conn.commit()

            except Exception:
                conn.rollback()
                raise

            finally:
                cursor.close()
                conn.close()

            log_usage(
                email,
                conversation_id,
                used_model,
                len(message),
                len(answer),
                "success"
            )

        except Exception:

            logger.exception(
                "Could not save AI conversation."
            )

            return _error(
                "AI generated a response, but it could not be saved. Please try again.",
                500
            )

        # --------------------------------------------
        # Updated usage
        # --------------------------------------------

        try:
            total_used = db.count_today(email)
        except Exception:
            total_used = used_today + 1

        return _success({
            "conversation_id": conversation_id,
            "model": used_model,
            "reply": answer,
            "usage": {
                "used": total_used,
                "limit": AI_DAILY_LIMIT,
                "remaining": max(
                    0,
                    AI_DAILY_LIMIT - total_used
                )
            }
        }, "AI response generated.")

    # ========================================================
    # API: USAGE INFORMATION
    # ========================================================

    @app.route("/api/ai/usage", methods=["GET"])
    def ai_usage():

        user, error = authenticated_user()

        if error:
            return error

        email = user["email"]

        try:
            used = db.count_today(email)

        except Exception:
            logger.exception("Could not retrieve AI usage.")
            return _error(
                "Could not retrieve usage information.",
                500
            )

        return _success({
            "used": used,
            "limit": AI_DAILY_LIMIT,
            "remaining": max(
                0,
                AI_DAILY_LIMIT - used
            ),
            "resets_at": "00:00 IST"
        })

    app.logger.info(
        "ExamMate AI Learning routes registered successfully."
    )
