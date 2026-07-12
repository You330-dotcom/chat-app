from flask import Flask, render_template, redirect, request
from flask_login import LoginManager, login_user, login_required, logout_user, current_user
from flask_sqlalchemy import SQLAlchemy
from flask_socketio import SocketIO, emit
from models import db, User, Message, MessageRead
from werkzeug.security import generate_password_hash, check_password_hash
import os

app = Flask(__name__, template_folder="instance/templates")
app.config["SECRET_KEY"] = "secret"

# 🔥 DB を絶対パスで指定（保存されない問題の完全解決）
app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///" + os.path.join(os.getcwd(), "app.db")

db.init_app(app)

socketio = SocketIO(app, cors_allowed_origins="*")

login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = "login"


@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))


@app.route("/")
def index():
    return redirect("/login")


@app.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect("/dashboard")

    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "").strip()

        if not username or not password:
            return render_template("login.html", error="入力してください")

        user = User.query.filter_by(username=username).first()
        if user and check_password_hash(user.password, password):
            login_user(user)
            return redirect("/dashboard")

        return render_template("login.html", error="ユーザー名またはパスワードが違います")

    return render_template("login.html")


@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        username = request.form["username"]
        password = generate_password_hash(request.form["password"])

        user = User(username=username, password=password)
        db.session.add(user)
        db.session.commit()

        return redirect("/login")

    return render_template("register.html")


@app.route("/dashboard")
@login_required
def dashboard():
    return render_template("dashboard.html")


@app.route("/chat")
@login_required
def chat():
    messages = Message.query.order_by(Message.timestamp).all()

    for msg in messages:
        if msg.user_id == current_user.id:
            continue

        already = MessageRead.query.filter_by(
            message_id=msg.id,
            user_id=current_user.id
        ).first()

        if not already:
            read = MessageRead(message_id=msg.id, user_id=current_user.id)
            db.session.add(read)
            db.session.commit()

    return render_template("chat.html", messages=messages)


# ---------------------------
# SocketIO（リアルタイム）
# ---------------------------

@socketio.on("send_message")
def handle_send_message(data):
    content = data["content"]
    username = data["username"]

    user = User.query.filter_by(username=username).first()
    if not user:
        return

    msg = Message(user_id=user.id, content=content)
    db.session.add(msg)
    db.session.commit()  # ← 保存されるようになった！

    emit("new_message", {
        "id": msg.id,
        "username": user.username,
        "content": msg.content,
        "time": msg.timestamp.strftime("%H:%M")
    }, broadcast=True)


@socketio.on("message_displayed")
def handle_message_displayed(data):
    msg_id = data["message_id"]
    username = data["username"]

    user = User.query.filter_by(username=username).first()
    if not user:
        return

    msg = Message.query.get(msg_id)
    if not msg or msg.user_id == user.id:
        return

    already = MessageRead.query.filter_by(
        message_id=msg_id,
        user_id=user.id
    ).first()

    if not already:
        read = MessageRead(message_id=msg_id, user_id=user.id)
        db.session.add(read)
        db.session.commit()

        emit("read_message", {
            "message_id": msg_id,
            "username": user.username
        }, broadcast=True)


@app.route("/logout")
def logout():
    logout_user()
    return redirect("/login")


if __name__ == "__main__":
    with app.app_context():
        db.create_all()
    socketio.run(app, host="0.0.0.0", port=5000)
