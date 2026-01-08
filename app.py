from flask import Flask, render_template, request, redirect, url_for, session, jsonify
from flask_socketio import SocketIO, join_room, leave_room, emit
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import generate_password_hash, check_password_hash
import os
from datetime import datetime, timezone
import json

app = Flask(__name__)
app.config['SECRET_KEY'] = os.urandom(24)
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///chat.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
# Set permanent session lifetime to 30 days (in seconds)
app.config['PERMANENT_SESSION_LIFETIME'] = 2592000

db = SQLAlchemy(app)
socketio = SocketIO(app, cors_allowed_origins="*")

# Helper function to get current time in UTC
def get_utc_now():
    return datetime.now(timezone.utc)

# Database Models
class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password = db.Column(db.String(200), nullable=False)
    created_at = db.Column(db.DateTime, default=get_utc_now)
    
    def __repr__(self):
        return f'<User {self.username}>'

class Room(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(80), nullable=False)
    created_at = db.Column(db.DateTime, default=get_utc_now)
    created_by = db.Column(db.Integer, db.ForeignKey('user.id'))
    is_private = db.Column(db.Boolean, default=False)
    password = db.Column(db.String(200), nullable=True)
    
    def __repr__(self):
        return f'<Room {self.name}>'

class RoomMember(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'))
    room_id = db.Column(db.Integer, db.ForeignKey('room.id'))
    joined_at = db.Column(db.DateTime, default=get_utc_now)
    
    def __repr__(self):
        return f'<RoomMember User:{self.user_id} Room:{self.room_id}>'

class Message(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    content = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime, default=get_utc_now)
    room_id = db.Column(db.Integer, db.ForeignKey('room.id'))
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'))
    deleted = db.Column(db.Boolean, default=False)
    
    def __repr__(self):
        return f'<Message {self.id}>'

class BlockedUser(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'))
    blocked_user_id = db.Column(db.Integer, db.ForeignKey('user.id'))
    created_at = db.Column(db.DateTime, default=get_utc_now)
    
    def __repr__(self):
        return f'<BlockedUser {self.user_id} blocked {self.blocked_user_id}>'

# Format timestamp for client
def format_timestamp(dt):
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.strftime('%Y-%m-%d %H:%M:%S')

# Create database tables
with app.app_context():
    db.create_all()

# Routes
@app.route('/')
def index():
    if 'user_id' not in session:
        return redirect(url_for('login'))
    session.permanent = True  # Make session permanent
    return render_template('index.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        
        user = User.query.filter_by(username=username).first()
        
        if user and check_password_hash(user.password, password):
            session.permanent = True  # Make session permanent
            session['user_id'] = user.id
            session['username'] = user.username
            session['login_time'] = format_timestamp(get_utc_now())
            return redirect(url_for('index'))
        
        return render_template('login.html', error='Invalid credentials')
    
    return render_template('login.html')

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        username = request.form.get('username')
        email = request.form.get('email')
        password = request.form.get('password')
        
        # Check if username or email already exists
        if User.query.filter_by(username=username).first():
            return render_template('register.html', error='Username already exists')
        
        if User.query.filter_by(email=email).first():
            return render_template('register.html', error='Email already exists')
        
        # Create new user
        hashed_password = generate_password_hash(password)
        new_user = User(username=username, email=email, password=hashed_password)
        db.session.add(new_user)
        db.session.commit()
        
        return redirect(url_for('login'))
    
    return render_template('register.html')

@app.route('/logout')
def logout():
    session.pop('user_id', None)
    session.pop('username', None)
    session.pop('login_time', None)
    return redirect(url_for('login'))

@app.route('/create-room', methods=['POST'])
def create_room():
    if 'user_id' not in session:
        return redirect(url_for('login'))
    
    room_name = request.form.get('room_name')
    room_type = request.form.get('room_type', 'public')
    password = request.form.get('room_password', '')
    
    if not room_name:
        return redirect(url_for('index'))
    
    is_private = room_type == 'private'
    hashed_password = generate_password_hash(password) if is_private and password else None
    
    new_room = Room(
        name=room_name, 
        created_by=session['user_id'], 
        is_private=is_private,
        password=hashed_password
    )
    db.session.add(new_room)
    db.session.commit()
    
    # Add creator as room member
    new_member = RoomMember(user_id=session['user_id'], room_id=new_room.id)
    db.session.add(new_member)
    db.session.commit()
    
    return redirect(url_for('index'))

@app.route('/rooms')
def get_rooms():
    if 'user_id' not in session:
        return jsonify([])
    
    # Get all rooms
    rooms = Room.query.all()
    
    # Get rooms user is member of
    user_rooms = RoomMember.query.filter_by(user_id=session['user_id']).all()
    user_room_ids = [member.room_id for member in user_rooms]
    
    room_list = []
    for room in rooms:
        is_member = room.id in user_room_ids
        is_owner = room.created_by == session['user_id']
        room_list.append({
            'id': room.id,
            'name': room.name,
            'is_private': room.is_private,
            'is_member': is_member,
            'is_owner': is_owner,
            'created_at': format_timestamp(room.created_at)
        })
    
    return jsonify(room_list)

@app.route('/join-room', methods=['POST'])
def join_room_route():
    if 'user_id' not in session:
        return jsonify({'success': False, 'message': 'Not logged in'})
    
    data = request.json
    room_id = data.get('room_id')
    password = data.get('password', '')
    
    # Check if room exists
    room = Room.query.get(room_id)
    if not room:
        return jsonify({'success': False, 'message': 'Room not found'})
    
    # Check if already a member
    member = RoomMember.query.filter_by(user_id=session['user_id'], room_id=room_id).first()
    if member:
        return jsonify({'success': True, 'message': 'Already a member'})
    
    # Check password for private rooms
    if room.is_private:
        if not check_password_hash(room.password, password):
            return jsonify({'success': False, 'message': 'Invalid password'})
    
    # Add user to room
    new_member = RoomMember(user_id=session['user_id'], room_id=room_id)
    db.session.add(new_member)
    db.session.commit()
    
    return jsonify({'success': True, 'message': 'Successfully joined room'})

# Route for leaving a room
@app.route('/leave-room', methods=['POST'])
def leave_room_route():
    if 'user_id' not in session:
        return jsonify({'success': False, 'message': 'Not logged in'})
    
    data = request.json
    room_id = data.get('room_id')
    
    # Check if room exists
    room = Room.query.get(room_id)
    if not room:
        return jsonify({'success': False, 'message': 'Room not found'})
    
    # Can't leave a room you created (must delete it instead)
    if room.created_by == session['user_id']:
        return jsonify({'success': False, 'message': 'You cannot leave a room you created. Delete it instead.'})
    
    # Check if user is a member
    member = RoomMember.query.filter_by(user_id=session['user_id'], room_id=room_id).first()
    if not member:
        return jsonify({'success': False, 'message': 'You are not a member of this room'})
    
    # Remove user from room
    db.session.delete(member)
    db.session.commit()
    
    return jsonify({'success': True, 'message': 'Successfully left room'})

# Route for deleting a room
@app.route('/delete-room', methods=['POST'])
def delete_room():
    if 'user_id' not in session:
        return jsonify({'success': False, 'message': 'Not logged in'})
    
    data = request.json
    room_id = data.get('room_id')
    
    # Check if room exists
    room = Room.query.get(room_id)
    if not room:
        return jsonify({'success': False, 'message': 'Room not found'})
    
    # Only the creator can delete a room
    if room.created_by != session['user_id']:
        return jsonify({'success': False, 'message': 'Only the room creator can delete this room'})
    
    # Delete all messages in the room
    Message.query.filter_by(room_id=room_id).delete()
    
    # Delete all room members
    RoomMember.query.filter_by(room_id=room_id).delete()
    
    # Delete the room
    db.session.delete(room)
    db.session.commit()
    
    # Notify clients about room deletion through socket.io
    room_str = str(room_id)
    socketio.emit('room_deleted', {
        'room_id': room_id,
        'message': f"Room {room.name} has been deleted by the owner.",
        'timestamp': format_timestamp(get_utc_now())
    }, room=room_str)
    
    return jsonify({'success': True, 'message': 'Room successfully deleted'})

@app.route('/messages/<int:room_id>')
def get_messages(room_id):
    if 'user_id' not in session:
        return jsonify([])
    
    # For public rooms, we don't need to check membership
    room = Room.query.get(room_id)
    if not room:
        return jsonify([])
    
    # For private rooms, check if user is a member
    if room.is_private:
        member = RoomMember.query.filter_by(user_id=session['user_id'], room_id=room_id).first()
        if not member:
            return jsonify([])
    
    # Get blocked users
    blocked_users = BlockedUser.query.filter_by(user_id=session['user_id']).all()
    blocked_user_ids = [block.blocked_user_id for block in blocked_users]
    
    # Get messages not from blocked users and not deleted
    messages = Message.query.filter_by(room_id=room_id, deleted=False).filter(~Message.user_id.in_(blocked_user_ids)).all()
    
    message_list = []
    for message in messages:
        user = User.query.get(message.user_id)
        message_list.append({
            'id': message.id,
            'content': message.content,
            'user_id': message.user_id,
            'username': user.username,
            'created_at': format_timestamp(message.created_at),
            'is_own': message.user_id == session['user_id']
        })
    
    return jsonify(message_list)

@app.route('/check-room-access/<int:room_id>')
def check_room_access(room_id):
    if 'user_id' not in session:
        return jsonify({'has_access': False})
    
    room = Room.query.get(room_id)
    
    if not room:
        return jsonify({'has_access': False})
    
    # Check if user is a member of the room
    member = RoomMember.query.filter_by(user_id=session['user_id'], room_id=room_id).first()
    
    # Check if user is the room owner
    is_owner = room.created_by == session['user_id']
    
    # Public rooms are accessible to all
    if not room.is_private:
        return jsonify({
            'has_access': True,
            'is_private': False,
            'name': room.name,
            'is_owner': is_owner,
            'created_at': format_timestamp(room.created_at)
        })
    
    # For private rooms, check membership
    if room.is_private and member:
        return jsonify({
            'has_access': True,
            'is_private': True,
            'name': room.name,
            'is_owner': is_owner,
            'created_at': format_timestamp(room.created_at)
        })
    
    # If private and not a member
    return jsonify({
        'has_access': False,
        'is_private': True,
        'name': room.name,
        'is_owner': False,
        'created_at': format_timestamp(room.created_at)
    })

@app.route('/block-user', methods=['POST'])
def block_user():
    if 'user_id' not in session:
        return jsonify({'success': False})
    
    data = request.json
    user_to_block = data.get('user_id')
    
    if not user_to_block:
        return jsonify({'success': False})
    
    # Check if already blocked
    existing_block = BlockedUser.query.filter_by(user_id=session['user_id'], blocked_user_id=user_to_block).first()
    if existing_block:
        return jsonify({'success': True})
    
    # Create new block
    new_block = BlockedUser(user_id=session['user_id'], blocked_user_id=user_to_block)
    db.session.add(new_block)
    db.session.commit()
    
    return jsonify({'success': True})

@app.route('/delete-message', methods=['POST'])
def delete_message():
    if 'user_id' not in session:
        return jsonify({'success': False})
    
    data = request.json
    message_id = data.get('message_id')
    
    if not message_id:
        return jsonify({'success': False})
    
    message = Message.query.get(message_id)
    if not message or message.user_id != session['user_id']:
        return jsonify({'success': False})
    
    message.deleted = True
    db.session.commit()
    
    return jsonify({'success': True})

# Add a route to synchronize time
@app.route('/sync-time')
def sync_time():
    if 'user_id' not in session:
        return jsonify({'error': 'Not logged in'})
    
    server_time = get_utc_now()
    return jsonify({
        'server_time': format_timestamp(server_time),
        'timestamp': int(server_time.timestamp() * 1000)  # milliseconds timestamp
    })

# Socket Events
@socketio.on('connect')
def handle_connect():
    if 'user_id' not in session:
        return False
    
    print(f'Client connected: {request.sid}')

@socketio.on('join')
def handle_join(data):
    if 'user_id' not in session:
        return
    
    room_id = data['room']
    room = Room.query.get(room_id)
    
    if not room:
        return
    
    # For private rooms, check membership
    if room.is_private:
        member = RoomMember.query.filter_by(user_id=session['user_id'], room_id=room_id).first()
        if not member:
            return
    
    # Convert room_id to string for socket.io room
    room_str = str(room_id)
    join_room(room_str)
    
    current_time = get_utc_now()
    emit('status', {
        'msg': f"{session['username']} has joined the room.",
        'user_id': session['user_id'],
        'username': session['username'],
        'timestamp': format_timestamp(current_time)
    }, room=room_str)

@socketio.on('leave')
def handle_leave(data):
    if 'user_id' not in session:
        return
    
    room_id = data['room']
    room_str = str(room_id)
    leave_room(room_str)
    
    current_time = get_utc_now()
    emit('status', {
        'msg': f"{session['username']} has left the room.",
        'user_id': session['user_id'],
        'username': session['username'],
        'timestamp': format_timestamp(current_time)
    }, room=room_str)

@socketio.on('message')
def handle_message(data):
    if 'user_id' not in session:
        return
    
    room_id = data['room']
    content = data['message']
    
    room = Room.query.get(room_id)
    if not room:
        return
    
    # For private rooms, check membership
    if room.is_private:
        member = RoomMember.query.filter_by(user_id=session['user_id'], room_id=room_id).first()
        if not member:
            return
    
    # Save message to database
    new_message = Message(content=content, room_id=room_id, user_id=session['user_id'])
    db.session.add(new_message)
    db.session.commit()
    
    # Create message data for emitting
    message_data = {
        'id': new_message.id,
        'content': content,
        'user_id': session['user_id'],
        'username': session['username'],
        'created_at': format_timestamp(new_message.created_at)
    }
    
    # Convert room_id to string for socket.io room
    room_str = str(room_id)
    
    # Send to current user with is_own flag
    emit('message', {**message_data, 'is_own': True}, room=request.sid)
    
    # Send to all other users in the room with is_own flag set to False
    emit('message', {**message_data, 'is_own': False}, room=room_str, include_self=False)

# Optional JavaScript to be included in your client-side code
@app.route('/js/time-sync.js')
def time_sync_js():
    js_code = """
// Time synchronization client code
let serverTimeDiff = 0;

// Sync time with server on page load
function syncTimeWithServer() {
    fetch('/sync-time')
        .then(response => response.json())
        .then(data => {
            const clientTime = new Date().getTime();
            const serverTime = data.timestamp;
            serverTimeDiff = serverTime - clientTime;
            console.log(`Time synchronized. Difference: ${serverTimeDiff}ms`);
            
            // Update every 30 minutes
            setTimeout(syncTimeWithServer, 30 * 60 * 1000);
        })
        .catch(err => {
            console.error('Error syncing time:', err);
            // Retry after 1 minute on error
            setTimeout(syncTimeWithServer, 60 * 1000);
        });
}

// Get current server time
function getServerTime() {
    const clientTime = new Date().getTime();
    return new Date(clientTime + serverTimeDiff);
}

// Format time for display
function formatTime(date) {
    return date.toISOString().replace('T', ' ').substr(0, 19);
}

// Initialize time sync
document.addEventListener('DOMContentLoaded', syncTimeWithServer);
    """
    return js_code, 200, {'Content-Type': 'application/javascript'}

if __name__ == '__main__':
    print("Starting Flask server...")
    print("Server is running at http://127.0.0.1:5000")
    socketio.run(app, host='127.0.0.1', port=5000, debug=True)