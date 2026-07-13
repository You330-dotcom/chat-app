from flask import Flask, render_template, redirect, request
from flask_login import LoginManager, login_user, login_required, logout_user, current_user
from flask_socketio import SocketIO, emit, join_room
from werkzeug.security import generate_password_hash, check_password_hash
from models import db, User, Message, MessageRead, DMRoom, DMMessage, DMRead
import os

app = Flask(__name__, template_folder="instance/templates")
app.config["SECRET_KEY"] = "secret"

app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///" + os.path.join(os.getcwd(), "app.db")
db.init_app(app)

# ⭐ Windowsで必須（eventlet/geventを使わない）
socketio = SocketIO(app, cors_allowed_origins="*", async_mode="threading")

login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = "login"


@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))


# -------------------------
# 基本ルート
# -------------------------
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


@app.route("/logout")
def logout():
    logout_user()
    return redirect("/login")


# -------------------------
# 通常チャット
# -------------------------
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


# -------------------------
# DM一覧
# -------------------------
@app.route("/dm_list")
@login_required
def dm_list():
    users = User.query.all()
    return render_template("dm_list.html", users=users)


# -------------------------
# DMルーム
# -------------------------
@app.route("/dm/<int:user_id>")
@login_required
def dm(user_id):
    partner = User.query.get(user_id)

    room = DMRoom.query.filter(
        ((DMRoom.user1_id == current_user.id) & (DMRoom.user2_id == user_id)) |
        ((DMRoom.user1_id == user_id) & (DMRoom.user2_id == current_user.id))
    ).first()

    if not room:
        room = DMRoom(user1_id=current_user.id, user2_id=user_id)
        db.session.add(room)
        db.session.commit()

    messages = DMMessage.query.filter_by(room_id=room.id).order_by(DMMessage.timestamp).all()

    # ⭐ DM既読をつける
    for msg in messages:
        if msg.user_id == current_user.id:
            continue

        already = DMRead.query.filter_by(
            message_id=msg.id,
            user_id=current_user.id
        ).first()

        if not already:
            read = DMRead(message_id=msg.id, user_id=current_user.id)
            db.session.add(read)
            db.session.commit()

    return render_template("dm.html", partner=partner, room=room, messages=messages)


# -------------------------
# SocketIO（通常チャット）
# -------------------------
@socketio.on("send_message")
def handle_send_message(data):
    content = data["content"]
    username = data["username"]

    user = User.query.filter_by(username=username).first()

    msg = Message(user_id=user.id, content=content)
    db.session.add(msg)
    db.session.commit()

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

    msg = Message.query.get(msg_id)
    if msg.user_id == user.id:
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


# -------------------------
# SocketIO（DM）
# -------------------------
@socketio.on("join_dm")
def join_dm(data):
    join_room(data["room_id"])


@socketio.on("dm_send")
def dm_send(data):
    room_id = data["room_id"]
    username = data["username"]
    content = data["content"]

    user = User.query.filter_by(username=username).first()

    msg = DMMessage(room_id=room_id, user_id=user.id, content=content)
    db.session.add(msg)
    db.session.commit()

    emit("dm_new", {
        "id": msg.id,
        "username": username,
        "content": content,
        "time": msg.timestamp.strftime("%H:%M")
    }, to=room_id)


@socketio.on("dm_read")
def dm_read(data):
    msg_id = data["message_id"]
    username = data["username"]
    room_id = data["room_id"]

    user = User.query.filter_by(username=username).first()

    already = DMRead.query.filter_by(
        message_id=msg_id,
        user_id=user.id
    ).first()

    if not already:
        read = DMRead(message_id=msg_id, user_id=user.id)
        db.session.add(read)
        db.session.commit()

        emit("dm_read_update", {
            "message_id": msg_id,
            "username": username
        }, to=room_id)


# -------------------------
# 接続維持
# -------------------------
@socketio.on("ping_check")
def ping_check(data):
    pass


# -------------------------
# 起動
# -------------------------
if __name__ == "__main__":
    with app.app_context():
        db.create_all()

    socketio.run(app, host="0.0.0.0", port=5000, allow_unsafe_werkzeug=True)
