import json
import os
import time
from flask import Flask, jsonify, request, send_from_directory
from flask_cors import CORS
from flask_socketio import SocketIO, join_room, leave_room
from dotenv import load_dotenv
from psycopg.errors import UniqueViolation

from db import get_conn, init_db

load_dotenv()

TEST_MODE = os.getenv("TEST_MODE", "true").lower() == "true"
TEST_PASSWORD = os.getenv("TEST_PASSWORD", "clemson-test-2026")
AUTO_RESET_ON_START = os.getenv("AUTO_RESET_ON_START", "true").lower() == "true"

API_VERSION = "2.9.0"
SPEC_VERSION = "2.9.0"
APP_START_TIME = time.time()
INITIAL_RESET_DONE = False

MIN_GRID_SIZE = 5
MAX_GRID_SIZE = 15
MIN_PLAYERS = 2
MAX_PLAYERS = 10
DEFAULT_GRID_SIZE = 8
DEFAULT_MAX_PLAYERS = 2
SHIPS_PER_PLAYER = 3
SHIP_BLUEPRINTS = [
    {"type": "patrol", "name": "Patrol Boat", "size": 2},
    {"type": "destroyer", "name": "Destroyer", "size": 3},
    {"type": "carrier", "name": "Carrier", "size": 5},
]
SHIP_SIZES = sorted([ship["size"] for ship in SHIP_BLUEPRINTS])
TOTAL_SHIP_CELLS = sum(SHIP_SIZES)

WAITING_STATUS = "waiting_setup"
PLAYING_STATUS = "playing"
FINISHED_STATUS = "finished"

PLACEHOLDER_GAME_IDS = {":id", "{id}", ":game_id", "{game_id}"}
PLACEHOLDER_PLAYER_IDS = {":player_id", "{player_id}"}

app = Flask(__name__)
CORS(app)
socketio = SocketIO(app, cors_allowed_origins="*", async_mode="eventlet")



def socket_room(game_id):
    return f"game_{game_id}"


def notify_game_update(game_id, reason="game_update"):
    try:
        socketio.emit("game_changed", {"game_id": game_id, "reason": reason, "timestamp": time.time()}, room=socket_room(game_id))
        socketio.emit("open_games_changed", {"reason": reason, "timestamp": time.time()})
    except Exception as ex:
        print(f"Socket emit failed: {ex}")


@socketio.on("watch_game")
def socket_watch_game(data):
    game_id = resolve_game_id((data or {}).get("game_id"))
    if is_valid_int_id(game_id):
        join_room(socket_room(game_id))
        return {"ok": True, "room": socket_room(game_id)}
    return {"ok": False, "error": "invalid_game_id"}


@socketio.on("leave_game")
def socket_leave_game(data):
    game_id = resolve_game_id((data or {}).get("game_id"))
    if is_valid_int_id(game_id):
        leave_room(socket_room(game_id))
        return {"ok": True}
    return {"ok": False, "error": "invalid_game_id"}

def error_response(error: str, message: str, status: int = 400):
    return jsonify({"error": error, "message": message}), status


def parse_json():
    return request.get_json(silent=True) or {}


def require_test_mode():
    supplied = request.headers.get("X-Test-Password") or request.headers.get("X-Test-Mode")
    if supplied != TEST_PASSWORD:
        return error_response("forbidden", "Invalid test password", 403)
    return None


def is_valid_int_id(value):
    return isinstance(value, int) and value > 0


def resolve_game_id(game_id):
    if isinstance(game_id, int):
        return game_id
    if isinstance(game_id, str):
        if game_id.isdigit():
            return int(game_id)
        if game_id in PLACEHOLDER_GAME_IDS:
            return 1
    return None


def resolve_player_id(player_id):
    if isinstance(player_id, int):
        return player_id
    if isinstance(player_id, str):
        if player_id.isdigit():
            return int(player_id)
        if player_id in PLACEHOLDER_PLAYER_IDS:
            return 1
    return None


def reset_database():
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("TRUNCATE TABLE shots, ships, game_players, games, players RESTART IDENTITY CASCADE")
            conn.commit()


def get_player_row(cur, player_id):
    cur.execute(
        """
        SELECT player_id, username, created_at, total_games, total_wins, total_losses, total_moves, total_hits
        FROM players
        WHERE player_id = %s
        """,
        (player_id,)
    )
    return cur.fetchone()


def get_player_row_by_username(cur, username):
    cur.execute(
        """
        SELECT player_id, username, created_at, total_games, total_wins, total_losses, total_moves, total_hits
        FROM players
        WHERE username = %s
        """,
        (username,)
    )
    return cur.fetchone()


def get_game_row(cur, game_id):
    cur.execute(
        """
        SELECT game_id, status, grid_size, max_players, current_turn_index, created_at
        FROM games
        WHERE game_id = %s
        """,
        (game_id,)
    )
    return cur.fetchone()


def count_players_in_game(cur, game_id):
    cur.execute(
        """
        SELECT COUNT(*) AS count
        FROM game_players
        WHERE game_id = %s
        """,
        (game_id,)
    )
    return cur.fetchone()["count"]


def get_turn_order_rows(cur, game_id):
    cur.execute(
        """
        SELECT gp.player_id, gp.turn_order, p.username
        FROM game_players gp
        JOIN players p ON p.player_id = gp.player_id
        WHERE gp.game_id = %s
        ORDER BY gp.turn_order
        """,
        (game_id,)
    )
    return cur.fetchall()


def player_in_game(cur, game_id, player_id):
    cur.execute(
        """
        SELECT turn_order
        FROM game_players
        WHERE game_id = %s AND player_id = %s
        """,
        (game_id, player_id)
    )
    return cur.fetchone()


def player_has_placed(cur, game_id, player_id):
    cur.execute(
        """
        SELECT COUNT(*) AS count
        FROM ships
        WHERE game_id = %s AND player_id = %s
        """,
        (game_id, player_id)
    )
    return cur.fetchone()["count"] >= SHIPS_PER_PLAYER


def any_ships_in_game(cur, game_id):
    cur.execute(
        """
        SELECT COUNT(*) AS count
        FROM ships
        WHERE game_id = %s
        """,
        (game_id,)
    )
    return cur.fetchone()["count"] > 0


def all_players_placed(cur, game_id):
    cur.execute(
        """
        SELECT player_id
        FROM game_players
        WHERE game_id = %s
        ORDER BY turn_order
        """,
        (game_id,)
    )
    players = cur.fetchall()

    if not players:
        return False

    for row in players:
        if not player_has_placed(cur, game_id, row["player_id"]):
            return False

    return True


def active_player_ids(cur, game_id):
    cur.execute(
        """
        SELECT player_id
        FROM game_players
        WHERE game_id = %s
        ORDER BY turn_order
        """,
        (game_id,)
    )
    return [row["player_id"] for row in cur.fetchall()]


def ships_remaining_for_player(cur, game_id, player_id):
    cur.execute(
        """
        SELECT COUNT(*) AS total_cells
        FROM ships
        WHERE game_id = %s AND player_id = %s
        """,
        (game_id, player_id)
    )
    total_cells = cur.fetchone()["total_cells"]

    cur.execute(
        """
        SELECT COUNT(*) AS hit_cells
        FROM shots
        WHERE game_id = %s AND target_player_id = %s AND result = 'hit'
        """,
        (game_id, player_id)
    )
    hit_cells = cur.fetchone()["hit_cells"]

    return max(total_cells - hit_cells, 0)


def surviving_players(cur, game_id):
    survivors = []
    for pid in active_player_ids(cur, game_id):
        if ships_remaining_for_player(cur, game_id, pid) > 0:
            survivors.append(pid)
    return survivors


def current_turn_player_id(cur, game_id):
    cur.execute(
        """
        SELECT gp.player_id
        FROM games g
        JOIN game_players gp
          ON gp.game_id = g.game_id
         AND gp.turn_order = g.current_turn_index
        WHERE g.game_id = %s
        """,
        (game_id,)
    )
    row = cur.fetchone()
    return row["player_id"] if row else None


def total_moves_for_game(cur, game_id):
    cur.execute(
        """
        SELECT COUNT(*) AS count
        FROM shots
        WHERE game_id = %s
        """,
        (game_id,)
    )
    return cur.fetchone()["count"]


def game_players_detail(cur, game_id):
    players = []
    for pid in active_player_ids(cur, game_id):
        players.append({
            "player_id": pid,
            "ships_remaining": ships_remaining_for_player(cur, game_id, pid)
        })
    return players


def update_game_to_playing_if_ready(cur, game_id):
    game = get_game_row(cur, game_id)
    if not game:
        return

    if game["status"] != WAITING_STATUS:
        return

    if count_players_in_game(cur, game_id) < game["max_players"]:
        return

    if not all_players_placed(cur, game_id):
        return

    cur.execute(
        """
        UPDATE games
        SET status = %s,
            current_turn_index = 0
        WHERE game_id = %s
        """,
        (PLAYING_STATUS, game_id)
    )


def normalize_ship_cells(raw_ships, grid_size):
    """Validate production ship placement.

    New format: one 2-cell ship, one 3-cell ship, and one 5-cell ship:
      [{"type": "patrol", "coordinates": [[0,0], [0,1]]}, ...]

    The older checkpoint format of three {row, col} single ships is still
    accepted for backwards compatibility with simple API tests.
    """
    if not isinstance(raw_ships, list):
        return None

    if len(raw_ships) == SHIPS_PER_PLAYER and all(isinstance(ship, dict) and "row" in ship and "col" in ship for ship in raw_ships):
        normalized = []
        seen = set()
        for index, ship in enumerate(raw_ships, start=1):
            row = ship.get("row")
            col = ship.get("col")
            if not isinstance(row, int) or not isinstance(col, int):
                return None
            if row < 0 or row >= grid_size or col < 0 or col >= grid_size:
                return None
            if (row, col) in seen:
                return None
            seen.add((row, col))
            normalized.append({"type": f"single_{index}", "coordinates": [(row, col)]})
        return normalized

    if len(raw_ships) != len(SHIP_BLUEPRINTS):
        return None

    normalized = []
    occupied = set()
    actual_sizes = []

    for ship in raw_ships:
        if not isinstance(ship, dict):
            return None

        ship_type = ship.get("type", "ship")
        coordinates = ship.get("coordinates")
        if not isinstance(ship_type, str) or not isinstance(coordinates, list):
            return None

        cleaned = []
        local_seen = set()
        for cell in coordinates:
            if not isinstance(cell, (list, tuple)) or len(cell) != 2:
                return None
            row, col = cell
            if not isinstance(row, int) or not isinstance(col, int):
                return None
            if row < 0 or row >= grid_size or col < 0 or col >= grid_size:
                return None
            if (row, col) in local_seen or (row, col) in occupied:
                return None
            cleaned.append((row, col))
            local_seen.add((row, col))

        size = len(cleaned)
        actual_sizes.append(size)
        rows = {row for row, _ in cleaned}
        cols = {col for _, col in cleaned}
        if len(rows) != 1 and len(cols) != 1:
            return None

        ordered = sorted(col for _, col in cleaned) if len(rows) == 1 else sorted(row for row, _ in cleaned)
        if ordered != list(range(ordered[0], ordered[0] + size)):
            return None

        for coord in cleaned:
            occupied.add(coord)

        normalized.append({"type": ship_type[:50], "coordinates": cleaned})

    if sorted(actual_sizes) != SHIP_SIZES:
        return None

    return normalized


def normalize_test_ships(raw_ships, grid_size):
    if not isinstance(raw_ships, list) or not raw_ships:
        return None

    normalized = []
    occupied = set()

    for ship in raw_ships:
        if not isinstance(ship, dict):
            return None

        ship_type = ship.get("type", "single")
        coordinates = ship.get("coordinates")

        if coordinates is None and "row" in ship and "col" in ship:
            coordinates = [[ship.get("row"), ship.get("col")]]

        if not isinstance(coordinates, list) or not coordinates:
            return None

        cleaned_coords = []
        for cell in coordinates:
            if (
                not isinstance(cell, list)
                or len(cell) != 2
                or not isinstance(cell[0], int)
                or not isinstance(cell[1], int)
            ):
                return None

            row, col = cell
            if row < 0 or row >= grid_size or col < 0 or col >= grid_size:
                return None
            if (row, col) in occupied:
                return None

            occupied.add((row, col))
            cleaned_coords.append((row, col))

        normalized.append({"type": ship_type, "coordinates": cleaned_coords})

    return normalized


def group_test_ships(cur, game_id, player_id):
    cur.execute(
        """
        SELECT ship_type, coordinates
        FROM ships
        WHERE game_id = %s AND player_id = %s
        ORDER BY ship_id
        """,
        (game_id, player_id)
    )
    rows = cur.fetchall()

    grouped = []
    seen = set()
    for row in rows:
        ship_type = row["ship_type"]
        coordinates = row["coordinates"]
        coord_key = tuple(tuple(cell) for cell in coordinates)
        key = (ship_type, coord_key)
        if key in seen:
            continue
        seen.add(key)
        grouped.append({"type": ship_type, "coordinates": coordinates})

    return grouped


def compute_sunk_for_player(cur, game_id, player_id):
    ships = group_test_ships(cur, game_id, player_id)

    cur.execute(
        """
        SELECT row_index, col_index
        FROM shots
        WHERE game_id = %s AND target_player_id = %s AND result = 'hit'
        """,
        (game_id, player_id)
    )
    hit_cells = {(row["row_index"], row["col_index"]) for row in cur.fetchall()}

    sunk = []
    for ship in ships:
        coords = ship["coordinates"]
        if all((cell[0], cell[1]) in hit_cells for cell in coords):
            sunk.append(ship)

    return sunk


def build_board_view(cur, game_id, player_id, grid_size):
    ship_cells = set()
    hit_cells = set()

    cur.execute(
        """
        SELECT row_index, col_index
        FROM ships
        WHERE game_id = %s AND player_id = %s
        """,
        (game_id, player_id)
    )
    for row in cur.fetchall():
        ship_cells.add((row["row_index"], row["col_index"]))

    cur.execute(
        """
        SELECT row_index, col_index
        FROM shots
        WHERE game_id = %s AND target_player_id = %s AND result = 'hit'
        """,
        (game_id, player_id)
    )
    for row in cur.fetchall():
        hit_cells.add((row["row_index"], row["col_index"]))

    board_rows = []
    for r in range(grid_size):
        rendered = []
        for c in range(grid_size):
            if (r, c) in hit_cells:
                rendered.append("X")
            elif (r, c) in ship_cells:
                rendered.append("O")
            else:
                rendered.append("~")
        board_rows.append(" ".join(rendered))

    return board_rows



def board_matrix_for_viewer(cur, game_id, owner_player_id, viewer_player_id, grid_size, reveal_all=False):
    ship_cells = set()
    cur.execute(
        """
        SELECT row_index, col_index
        FROM ships
        WHERE game_id = %s AND player_id = %s
        """,
        (game_id, owner_player_id)
    )
    for row in cur.fetchall():
        ship_cells.add((row["row_index"], row["col_index"]))

    targeted_by_any = {}
    cur.execute(
        """
        SELECT attacker_player_id, row_index, col_index, result
        FROM shots
        WHERE game_id = %s AND target_player_id = %s
        ORDER BY created_at, shot_id
        """,
        (game_id, owner_player_id)
    )
    for shot in cur.fetchall():
        targeted_by_any[(shot["row_index"], shot["col_index"])] = {
            "attacker_player_id": shot["attacker_player_id"],
            "result": shot["result"],
        }

    viewer_shots = {}
    cur.execute(
        """
        SELECT row_index, col_index, result
        FROM shots
        WHERE game_id = %s AND attacker_player_id = %s AND target_player_id = %s
        ORDER BY created_at, shot_id
        """,
        (game_id, viewer_player_id, owner_player_id)
    )
    for shot in cur.fetchall():
        viewer_shots[(shot["row_index"], shot["col_index"])] = shot["result"]

    grid = []
    for r in range(grid_size):
        row_cells = []
        for c in range(grid_size):
            cell = "empty"
            coord = (r, c)
            if owner_player_id == viewer_player_id:
                if coord in ship_cells and coord in targeted_by_any and targeted_by_any[coord]["result"] == "hit":
                    cell = "sunk"
                elif coord in ship_cells:
                    cell = "ship"
                elif coord in targeted_by_any and targeted_by_any[coord]["result"] == "miss":
                    cell = "miss"
            else:
                if coord in viewer_shots:
                    cell = "sunk" if viewer_shots[coord] == "hit" else "miss"
                elif reveal_all and coord in ship_cells:
                    cell = "ship"
            row_cells.append(cell)
        grid.append(row_cells)
    return grid


def winner_for_game(cur, game_id):
    survivors = surviving_players(cur, game_id)
    if len(survivors) == 1:
        return survivors[0]
    return None


def build_boards_payload(cur, game_id, viewer_player_id):
    game = get_game_row(cur, game_id)
    if not game:
        return None

    viewer_membership = player_in_game(cur, game_id, viewer_player_id)
    if not viewer_membership:
        return None

    turn_rows = get_turn_order_rows(cur, game_id)
    reveal_all = game["status"] == FINISHED_STATUS
    current_turn_id = current_turn_player_id(cur, game_id) if game["status"] == PLAYING_STATUS else None
    winner_id = winner_for_game(cur, game_id) if game["status"] == FINISHED_STATUS else None

    boards = []
    for row in turn_rows:
        owner_id = row["player_id"]
        boards.append({
            "player_id": owner_id,
            "username": row["username"],
            "turn_order": row["turn_order"],
            "is_viewer": owner_id == viewer_player_id,
            "is_current_turn": owner_id == current_turn_id,
            "placed": player_has_placed(cur, game_id, owner_id),
            "ships_remaining": ships_remaining_for_player(cur, game_id, owner_id),
            "eliminated": player_has_placed(cur, game_id, owner_id) and ships_remaining_for_player(cur, game_id, owner_id) == 0,
            "grid": board_matrix_for_viewer(cur, game_id, owner_id, viewer_player_id, game["grid_size"], reveal_all=reveal_all),
        })

    return {
        "game_id": game_id,
        "viewer_player_id": viewer_player_id,
        "grid_size": game["grid_size"],
        "max_players": game["max_players"],
        "status": game["status"],
        "current_turn_index": game["current_turn_index"],
        "current_turn_player_id": current_turn_id,
        "your_turn": current_turn_id == viewer_player_id if current_turn_id is not None else False,
        "winner_id": winner_id,
        "boards": boards,
    }


try:
    init_db()
    if TEST_MODE and AUTO_RESET_ON_START:
        reset_database()
        print("Auto reset on startup completed.")
except Exception as ex:
    print(f"DB init/startup reset failed: {ex}")

@app.get("/")
def serve_frontend():
    return send_from_directory("frontend", "index.html")

@app.before_request
def guard_test_routes_and_lazy_reset():
    global INITIAL_RESET_DONE
    if TEST_MODE and AUTO_RESET_ON_START and not INITIAL_RESET_DONE:
        try:
            reset_database()
            INITIAL_RESET_DONE = True
            print("Lazy auto reset before first request completed.")
        except Exception as ex:
            print(f"Lazy auto reset failed: {ex}")

    if request.path.startswith("/api/test/") or request.path.startswith("/test/"):
        test_check = require_test_mode()
        if test_check:
            return test_check


@app.get("/api/")
def api_metadata():
    return jsonify({
        "name": "Battleship API",
        "version": API_VERSION,
        "spec_version": SPEC_VERSION,
        "environment": "test" if TEST_MODE else "production",
        "test_mode": TEST_MODE,
    }), 200


@app.get("/api/version")
def version_info():
    return jsonify({"api_version": API_VERSION, "spec_version": SPEC_VERSION}), 200


@app.get("/api/health")
def health():
    return jsonify({
        "status": "ok",
        "uptime_seconds": int(time.time() - APP_START_TIME),
    }), 200


@app.get("/api/players")
def list_players():
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT player_id, username
                    FROM players
                    ORDER BY player_id ASC
                    """
                )
                rows = cur.fetchall()

        return jsonify({
            "players": [
                {
                    "player_id": row["player_id"],
                    "username": row["username"],
                }
                for row in rows
            ]
        }), 200
    except Exception as ex:
        print(f"List players error: {ex}")
        return error_response("internal_error", "Failed to list players", 500)


@app.get("/api/games")
def list_games():
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT
                        g.game_id,
                        g.status,
                        g.grid_size,
                        g.max_players,
                        g.current_turn_index,
                        g.created_at,
                        COUNT(gp.player_id) AS active_players
                    FROM games g
                    LEFT JOIN game_players gp ON gp.game_id = g.game_id
                    GROUP BY g.game_id, g.status, g.grid_size, g.max_players, g.current_turn_index, g.created_at
                    HAVING g.status = %s AND COUNT(gp.player_id) < g.max_players
                    ORDER BY g.created_at DESC, g.game_id DESC
                    """,
                    (WAITING_STATUS,)
                )
                games = cur.fetchall()

        return jsonify({
            "games": [
                {
                    "game_id": game["game_id"],
                    "status": game["status"],
                    "grid_size": game["grid_size"],
                    "max_players": game["max_players"],
                    "current_turn_index": game["current_turn_index"],
                    "active_players": game["active_players"],
                    "created_at": game["created_at"].isoformat().replace("+00:00", "Z") if game["created_at"] else None,
                }
                for game in games
            ]
        }), 200
    except Exception as ex:
        print(f"List games error: {ex}")
        return error_response("internal_error", "Failed to list games", 500)


@app.post("/api/reset")
def system_reset():
    try:
        reset_database()
        return jsonify({"status": "reset"}), 200
    except Exception as ex:
        print(f"System reset error: {ex}")
        return error_response("internal_error", "Failed to reset system", 500)



@app.post("/api/players/login")
def login_player():
    data = parse_json()
    username = data.get("username") or data.get("playerName")

    if username is None:
        return error_response("username required", "username required", 400)

    if not isinstance(username, str) or not username.strip():
        return error_response("username required", "username required", 400)

    username = username.strip()

    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                player = get_player_row_by_username(cur, username)
                if not player:
                    return error_response("not_found", "Player does not exist. Create the player first.", 404)

        return jsonify({
            "player_id": player["player_id"],
            "username": player["username"],
            "displayName": player["username"],
        }), 200
    except Exception as ex:
        print(f"Login player error: {ex}")
        return error_response("internal_error", "Failed to sign in player", 500)

@app.post("/api/players")
def create_player():
    data = parse_json()

    if "player_id" in data or "playerId" in data:
        return jsonify({
            "error": "Client may not supply player_id",
            "message": "Client may not supply player_id"
        }), 400

    username = data.get("username")
    if username is None:
        username = data.get("playerName")

    if username is None:
        return jsonify({
            "error": "Missing required field: username",
            "message": "Missing required field: username"
        }), 400

    if not isinstance(username, str) or not username.strip():
        return jsonify({
            "error": "username required",
            "message": "username required"
        }), 400

    username = username.strip()

    if len(username) > 30 or not username.replace("_", "a").isalnum():
        return jsonify({
            "error": "Username must be alphanumeric with underscores only",
            "message": "Username must be alphanumeric with underscores only"
        }), 400

    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                existing = get_player_row_by_username(cur, username)
                if existing:
                    return jsonify({
                        "error": "Username already taken",
                        "message": "Username already taken"
                    }), 409

                cur.execute(
                    """
                    INSERT INTO players (username)
                    VALUES (%s)
                    RETURNING player_id, username
                    """,
                    (username,)
                )
                player = cur.fetchone()
                conn.commit()

        return jsonify({
            "player_id": player["player_id"],
            "username": player["username"],
            "displayName": player["username"],
        }), 201
    except UniqueViolation:
        try:
            with get_conn() as conn:
                with conn.cursor() as cur:
                    existing = get_player_row_by_username(cur, username)
            if existing:
                return jsonify({
                    "error": "Username already taken",
                    "message": "Username already taken"
                }), 409
        except Exception as inner_ex:
            print(f"Duplicate username lookup error: {inner_ex}")

        return jsonify({
            "error": "Username already taken",
            "message": "Username already taken"
        }), 409
    except Exception as ex:
        print(f"Create player error: {ex}")
        return error_response("internal_error", "Failed to create player", 500)


@app.get("/api/players/<int:player_id>/stats")
@app.get("/api/players/<player_id>/stats")
def get_player_stats(player_id):
    player_id = resolve_player_id(player_id)
    if not is_valid_int_id(player_id):
        return error_response("not_found", "Player does not exist", 404)

    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                player = get_player_row(cur, player_id)
                if not player:
                    return error_response("not_found", "Player does not exist", 404)

        total_shots = player["total_moves"]
        total_hits = player["total_hits"]
        accuracy = round((total_hits / total_shots), 3) if total_shots > 0 else 0.0

        return jsonify({
            "games_played": player["total_games"],
            "games": player["total_games"],
            "wins": player["total_wins"],
            "losses": player["total_losses"],
            "shots": total_shots,
            "hits": total_hits,
            "total_shots": total_shots,
            "total_hits": total_hits,
            "accuracy": accuracy,
        }), 200
    except Exception as ex:
        print(f"Get player stats error: {ex}")
        return error_response("internal_error", "Failed to fetch player stats", 500)


@app.post("/api/games")
def create_game():
    data = parse_json()

    creator_id = data.get("creator_id")
    grid_size = data.get("grid_size")
    max_players = data.get("max_players")
    used_player_name_shortcut = creator_id is None and "player1" in data

    if creator_id is None and "player1" in data:
        player1_name = data.get("player1")
        if isinstance(player1_name, str) and player1_name.strip():
            try:
                with get_conn() as conn:
                    with conn.cursor() as cur:
                        existing = get_player_row_by_username(cur, player1_name.strip())
                        if existing:
                            creator_id = existing["player_id"]
                        else:
                            cur.execute(
                                """
                                INSERT INTO players (username)
                                VALUES (%s)
                                RETURNING player_id
                                """,
                                (player1_name.strip(),)
                            )
                            creator_id = cur.fetchone()["player_id"]
                            conn.commit()
            except Exception as ex:
                print(f"Auto-create player1 error: {ex}")

    if grid_size is None:
        grid_size = data.get("gridSize")
    if max_players is None:
        max_players = data.get("maxPlayers")

    if used_player_name_shortcut:
        if grid_size is None:
            grid_size = DEFAULT_GRID_SIZE
        if max_players is None:
            max_players = DEFAULT_MAX_PLAYERS

    if not is_valid_int_id(creator_id):
        return error_response("bad_request", "creator_id is required", 400)
    if grid_size is None or max_players is None:
        return error_response("bad_request", "missing required fields", 400)
    if not isinstance(grid_size, int) or not (MIN_GRID_SIZE <= grid_size <= MAX_GRID_SIZE):
        return error_response("bad_request", "grid_size must be between 5 and 15", 400)
    if not isinstance(max_players, int) or not (MIN_PLAYERS <= max_players <= MAX_PLAYERS):
        return error_response("bad_request", "max_players must be between 2 and 10", 400)

    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                creator = get_player_row(cur, creator_id)
                if not creator:
                    return error_response("bad_request", "Player does not exist", 400)

                cur.execute(
                    """
                    INSERT INTO games (status, grid_size, max_players, current_turn_index)
                    VALUES (%s, %s, %s, 0)
                    RETURNING game_id
                    """,
                    (WAITING_STATUS, grid_size, max_players)
                )
                game = cur.fetchone()

                cur.execute(
                    """
                    INSERT INTO game_players (game_id, player_id, turn_order)
                    VALUES (%s, %s, 0)
                    """,
                    (game["game_id"], creator_id)
                )
                conn.commit()

        notify_game_update(game["game_id"], "game_created")
        return jsonify({
            "game_id": game["game_id"],
            "grid_size": grid_size,
            "status": WAITING_STATUS,
        }), 201
    except Exception as ex:
        print(f"Create game error: {ex}")
        return error_response("internal_error", "Failed to create game", 500)


@app.get("/api/games/<int:game_id>")
@app.get("/api/games/<game_id>")
def get_game(game_id):
    game_id = resolve_game_id(game_id)
    if not is_valid_int_id(game_id):
        return error_response("not_found", "Game does not exist", 404)

    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                game = get_game_row(cur, game_id)
                if not game:
                    return error_response("not_found", "Game does not exist", 404)

                response = {
                    "game_id": game["game_id"],
                    "grid_size": game["grid_size"],
                    "max_players": game["max_players"],
                    "status": game["status"],
                    "players": game_players_detail(cur, game_id),
                    "current_turn_index": game["current_turn_index"],
                    "current_turn_player_id": current_turn_player_id(cur, game_id) if game["status"] == PLAYING_STATUS else None,
                    "active_players": count_players_in_game(cur, game_id),
                    "total_moves": total_moves_for_game(cur, game_id),
                }
        return jsonify(response), 200
    except Exception as ex:
        print(f"Get game error: {ex}")
        return error_response("internal_error", "Failed to fetch game", 500)


@app.post("/api/games/<int:game_id>/join")
@app.post("/api/games/<game_id>/join")
def join_game(game_id):
    game_id = resolve_game_id(game_id)
    if not is_valid_int_id(game_id):
        return error_response("not_found", "Game does not exist", 404)

    data = parse_json()
    player_id = data.get("player_id")
    if player_id is None:
        player_id = data.get("playerId")
    player_id = resolve_player_id(player_id)

    if not is_valid_int_id(player_id):
        return error_response("bad_request", "player_id is required", 400)

    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                game = get_game_row(cur, game_id)
                if not game:
                    return error_response("not_found", "Game does not exist", 404)

                player = get_player_row(cur, player_id)
                if not player:
                    return error_response("not_found", "Player does not exist", 404)

                existing = player_in_game(cur, game_id, player_id)
                if existing:
                    player_count = count_players_in_game(cur, game_id)
                    creator_setup_retry = (
                        existing["turn_order"] == 0
                        and player_count == 1
                        and game["status"] == WAITING_STATUS
                        and not any_ships_in_game(cur, game_id)
                    )
                    if creator_setup_retry:
                        return jsonify({
                            "status": "joined",
                            "game_id": game_id,
                            "player_id": player_id,
                        }), 200
                    return error_response("bad_request", "Player already joined this game", 400)

                if game["status"] != WAITING_STATUS:
                    return error_response("bad_request", "Game already started", 400)

                player_count = count_players_in_game(cur, game_id)
                if player_count >= game["max_players"]:
                    return error_response("bad_request", "Game is full", 400)

                cur.execute(
                    """
                    INSERT INTO game_players (game_id, player_id, turn_order)
                    VALUES (%s, %s, %s)
                    """,
                    (game_id, player_id, player_count)
                )
                update_game_to_playing_if_ready(cur, game_id)
                conn.commit()

        notify_game_update(game_id, "player_joined")
        return jsonify({
            "status": "joined",
            "game_id": game_id,
            "player_id": player_id,
        }), 200
    except UniqueViolation:
        return error_response("bad_request", "Player already joined this game", 400)
    except Exception as ex:
        print(f"Join game error: {ex}")
        return error_response("internal_error", "Failed to join game", 500)


@app.post("/api/games/<int:game_id>/place")
@app.post("/api/games/<game_id>/place")
def place_production_ships(game_id):
    game_id = resolve_game_id(game_id)
    if not is_valid_int_id(game_id):
        return error_response("bad_request", "game_id is required", 400)

    data = parse_json()

    player_id = data.get("player_id")
    if player_id is None:
        player_id = data.get("playerId")
    player_id = resolve_player_id(player_id)

    ships = data.get("ships")

    if not is_valid_int_id(player_id):
        return error_response("bad_request", "player_id is required", 400)

    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                game = get_game_row(cur, game_id)
                if not game:
                    return error_response("not_found", "Game does not exist", 404)

                if not get_player_row(cur, player_id):
                    return error_response("not_found", "Player does not exist", 404)

                membership = player_in_game(cur, game_id, player_id)
                if not membership:
                    return error_response("forbidden", "Player not in game", 403)

                if player_has_placed(cur, game_id, player_id):
                    return error_response("conflict", "Ships already placed", 409)

                if game["status"] != WAITING_STATUS:
                    return error_response("forbidden", "Not in setup phase", 403)

                normalized = normalize_ship_cells(ships, game["grid_size"])
                if normalized is None:
                    return error_response("bad_request", "Place one 2-cell ship, one 3-cell ship, and one 5-cell ship", 400)

                for ship in normalized:
                    ship_type = ship["type"]
                    coords = ship["coordinates"]
                    coords_json = json.dumps([[r, c] for r, c in coords])
                    for row, col in coords:
                        cur.execute(
                            """
                            INSERT INTO ships (game_id, player_id, ship_type, coordinates, row_index, col_index)
                            VALUES (%s, %s, %s, %s::jsonb, %s, %s)
                            """,
                            (game_id, player_id, ship_type, coords_json, row, col)
                        )

                update_game_to_playing_if_ready(cur, game_id)
                conn.commit()

        notify_game_update(game_id, "ships_placed")
        return jsonify({
            "status": "placed",
            "message": "ok",
            "game_id": game_id,
            "player_id": player_id,
        }), 200
    except UniqueViolation:
        return error_response("bad_request", "Invalid ship coordinates", 400)
    except Exception as ex:
        print(f"Place ships error: {ex}")
        return error_response("internal_error", "Failed to place ships", 500)


@app.post("/api/games/<int:game_id>/fire")
@app.post("/api/games/<game_id>/fire")
def fire(game_id):
    game_id = resolve_game_id(game_id)
    if not is_valid_int_id(game_id):
        return error_response("bad_request", "game_id is required", 400)

    data = parse_json()

    player_id = data.get("player_id")
    if player_id is None:
        player_id = data.get("playerId")
    player_id = resolve_player_id(player_id)

    target_player_id = data.get("target_player_id")
    if target_player_id is None:
        target_player_id = data.get("targetPlayerId")
    target_player_id = resolve_player_id(target_player_id)

    row = data.get("row")
    col = data.get("col")

    if not is_valid_int_id(player_id):
        return error_response("bad_request", "player_id is required", 400)
    if target_player_id is not None and not is_valid_int_id(target_player_id):
        return error_response("bad_request", "target_player_id must be a positive integer", 400)
    if not isinstance(row, int) or not isinstance(col, int):
        return error_response("bad_request", "row and col are required", 400)

    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                game = get_game_row(cur, game_id)
                if not game:
                    return error_response("not_found", "Game does not exist", 404)

                if not get_player_row(cur, player_id):
                    return error_response("not_found", "Player does not exist", 404)

                membership = player_in_game(cur, game_id, player_id)
                if not membership:
                    return error_response("forbidden", "Player not in game", 403)

                if row < 0 or row >= game["grid_size"] or col < 0 or col >= game["grid_size"]:
                    return error_response("bad_request", "Shot out of bounds", 400)

                if game["status"] == FINISHED_STATUS:
                    return error_response("bad_request", "Game already finished", 400)

                if game["status"] != PLAYING_STATUS:
                    return error_response("forbidden", "Game is not in playing state", 403)

                if membership["turn_order"] != game["current_turn_index"]:
                    return error_response("forbidden", "Not your turn", 403)

                result = "miss"
                turn_rows = get_turn_order_rows(cur, game_id)
                opponent_ids = [row_player["player_id"] for row_player in turn_rows if row_player["player_id"] != player_id]

                if not opponent_ids:
                    return error_response("bad_request", "No valid target player found", 400)

                if target_player_id is not None:
                    if target_player_id not in opponent_ids:
                        return error_response("bad_request", "Target player is not a valid opponent in this game", 400)
                else:
                    # Backwards-compatible fallback for older 2-player clients.
                    target_player_id = opponent_ids[0]

                # Now check if THIS attacker already fired at THIS target player's board cell.
                cur.execute(
                    """
                    SELECT 1
                    FROM shots
                    WHERE game_id = %s
                      AND attacker_player_id = %s
                      AND target_player_id = %s
                      AND row_index = %s
                      AND col_index = %s
                    LIMIT 1
                    """,
                    (game_id, player_id, target_player_id, row, col)
                )
                if cur.fetchone():
                    return error_response("conflict", "You already fired at that cell on this opponent's board", 409)

                # Determine hit or miss against the chosen target player's board only.
                cur.execute(
                    """
                    SELECT 1
                    FROM ships
                    WHERE game_id = %s
                      AND player_id = %s
                      AND row_index = %s
                      AND col_index = %s
                    LIMIT 1
                    """,
                    (game_id, target_player_id, row, col)
                )
                if cur.fetchone():
                    result = "hit"

                cur.execute(
                    """
                    INSERT INTO shots (game_id, attacker_player_id, target_player_id, row_index, col_index, result)
                    VALUES (%s, %s, %s, %s, %s, %s)
                    """,
                    (game_id, player_id, target_player_id, row, col, result)
                )

                cur.execute(
                    """
                    UPDATE players
                    SET total_moves = total_moves + 1,
                        total_hits = total_hits + CASE WHEN %s = 'hit' THEN 1 ELSE 0 END
                    WHERE player_id = %s
                    """,
                    (result, player_id)
                )

                survivors = surviving_players(cur, game_id)
                winner_id = None
                next_player_id = None
                game_status = PLAYING_STATUS

                if len(survivors) == 1:
                    winner_id = survivors[0]
                    game_status = FINISHED_STATUS
                    cur.execute(
                        """
                        UPDATE games
                        SET status = %s
                        WHERE game_id = %s
                        """,
                        (FINISHED_STATUS, game_id)
                    )

                    for pid in active_player_ids(cur, game_id):
                        cur.execute(
                            """
                            UPDATE players
                            SET total_games = total_games + 1,
                                total_wins = total_wins + CASE WHEN %s = %s THEN 1 ELSE 0 END,
                                total_losses = total_losses + CASE WHEN %s <> %s THEN 1 ELSE 0 END
                            WHERE player_id = %s
                            """,
                            (pid, winner_id, pid, winner_id, pid)
                        )
                else:
                    player_count = count_players_in_game(cur, game_id)
                    next_turn_index = (game["current_turn_index"] + 1) % player_count
                    cur.execute(
                        """
                        UPDATE games
                        SET current_turn_index = %s
                        WHERE game_id = %s
                        """,
                        (next_turn_index, game_id)
                    )

                    cur.execute(
                        """
                        SELECT player_id
                        FROM game_players
                        WHERE game_id = %s AND turn_order = %s
                        """,
                        (game_id, next_turn_index)
                    )
                    next_row = cur.fetchone()
                    next_player_id = next_row["player_id"] if next_row else None

                conn.commit()

        notify_game_update(game_id, "shot_fired")
        response = {
            "result": result,
            "next_player_id": next_player_id,
            "game_status": game_status,
            "target_player_id": target_player_id,
        }
        if winner_id is not None:
            response["winner_id"] = winner_id
        return jsonify(response), 200

    except UniqueViolation:
        return error_response("conflict", "You already fired at that cell on this opponent's board", 409)
    except Exception as ex:
        print(f"Fire error: {ex}")
        return error_response("internal_error", "Failed to fire", 500)


@app.post("/api/game/fire")
def fire_default_game():
    return fire(1)


@app.get("/api/games/<int:game_id>/moves")
@app.get("/api/games/<game_id>/moves")
def get_moves(game_id):
    game_id = resolve_game_id(game_id)
    if not is_valid_int_id(game_id):
        return error_response("not_found", "Game does not exist", 404)

    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                game = get_game_row(cur, game_id)
                if not game:
                    return error_response("not_found", "Game does not exist", 404)

                cur.execute(
                    """
                    SELECT attacker_player_id, target_player_id, row_index, col_index, result, created_at
                    FROM shots
                    WHERE game_id = %s
                    ORDER BY created_at, shot_id
                    """,
                    (game_id,)
                )
                shots = cur.fetchall()

        return jsonify({
            "game_id": game_id,
            "moves": [
                {
                    "move_number": index,
                    "player_id": shot["attacker_player_id"],
                    "target_player_id": shot["target_player_id"],
                    "row": shot["row_index"],
                    "col": shot["col_index"],
                    "result": shot["result"],
                    "timestamp": shot["created_at"].isoformat().replace("+00:00", "Z"),
                }
                for index, shot in enumerate(shots, start=1)
            ]
        }), 200
    except Exception as ex:
        print(f"Get moves error: {ex}")
        return error_response("internal_error", "Failed to fetch moves", 500)




@app.get("/api/leaderboard")
def leaderboard():
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT player_id, username, total_games, total_wins, total_losses, total_moves, total_hits
                    FROM players
                    ORDER BY total_wins DESC, total_hits DESC, total_moves ASC, player_id ASC
                    LIMIT 25
                    """
                )
                rows = cur.fetchall()

        players = []
        for row in rows:
            shots = row["total_moves"] or 0
            hits = row["total_hits"] or 0
            players.append({
                "player_id": row["player_id"],
                "username": row["username"],
                "games": row["total_games"],
                "wins": row["total_wins"],
                "losses": row["total_losses"],
                "shots": shots,
                "hits": hits,
                "accuracy": round((hits / shots), 3) if shots else 0.0,
            })
        return jsonify({"players": players}), 200
    except Exception as ex:
        print(f"Leaderboard error: {ex}")
        return error_response("internal_error", "Failed to fetch leaderboard", 500)


@app.get("/api/games/<int:game_id>/boards")
@app.get("/api/games/<game_id>/boards")
def get_game_boards(game_id):
    game_id = resolve_game_id(game_id)
    if not is_valid_int_id(game_id):
        return error_response("not_found", "Game does not exist", 404)

    viewer_player_id = request.args.get("viewer_player_id") or request.args.get("player_id")
    viewer_player_id = resolve_player_id(viewer_player_id)
    if not is_valid_int_id(viewer_player_id):
        return error_response("bad_request", "viewer_player_id is required", 400)

    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                game = get_game_row(cur, game_id)
                if not game:
                    return error_response("not_found", "Game does not exist", 404)
                if not get_player_row(cur, viewer_player_id):
                    return error_response("not_found", "Player does not exist", 404)
                if not player_in_game(cur, game_id, viewer_player_id):
                    return error_response("forbidden", "Player not in game", 403)

                payload = build_boards_payload(cur, game_id, viewer_player_id)
                return jsonify(payload), 200
    except Exception as ex:
        print(f"Get game boards error: {ex}")
        return error_response("internal_error", "Failed to fetch game boards", 500)


@app.post("/api/test/games/<int:game_id>/restart")
@app.post("/api/test/games/<game_id>/restart")
@app.post("/api/test/games/<int:game_id>/reset")
@app.post("/api/test/games/<game_id>/reset")
@app.post("/test/games/<int:game_id>/reset")
@app.post("/test/games/<game_id>/reset")
def test_restart(game_id):
    test_check = require_test_mode()
    if test_check:
        return test_check

    game_id = resolve_game_id(game_id)
    if not is_valid_int_id(game_id):
        return error_response("not_found", "Game does not exist", 404)

    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                game = get_game_row(cur, game_id)
                if not game:
                    return error_response("not_found", "Game does not exist", 404)

                cur.execute("DELETE FROM ships WHERE game_id = %s", (game_id,))
                cur.execute("DELETE FROM shots WHERE game_id = %s", (game_id,))
                cur.execute(
                    """
                    DELETE FROM game_players
                    WHERE game_id = %s AND turn_order <> 0
                    """,
                    (game_id,)
                )
                cur.execute(
                    """
                    UPDATE games
                    SET status = %s,
                        current_turn_index = 0
                    WHERE game_id = %s
                    """,
                    (WAITING_STATUS, game_id)
                )

                conn.commit()

        notify_game_update(game_id, "game_reset")
        return jsonify({"status": "reset", "game_id": game_id}), 200
    except Exception as ex:
        print(f"Test restart error: {ex}")
        return error_response("internal_error", "Failed to restart game", 500)


@app.post("/api/test/games/<int:game_id>/ships")
@app.post("/api/test/games/<game_id>/ships")
@app.post("/test/games/<int:game_id>/ships")
@app.post("/test/games/<game_id>/ships")
def test_place_ships(game_id):
    game_id = resolve_game_id(game_id)
    if not is_valid_int_id(game_id):
        return error_response("bad_request", "game_id is required", 400)

    data = parse_json()
    player_id = data.get("player_id")
    if player_id is None:
        player_id = data.get("playerId")
    player_id = resolve_player_id(player_id)

    raw_ships = data.get("ships")

    if not is_valid_int_id(player_id):
        return error_response("bad_request", "player_id is required", 400)

    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                game = get_game_row(cur, game_id)
                if not game:
                    return error_response("not_found", "Game does not exist", 404)

                if game["status"] != WAITING_STATUS:
                    return error_response("bad_request", "Ships can only be placed before game starts", 400)

                if not get_player_row(cur, player_id):
                    return error_response("not_found", "Player does not exist", 404)

                membership = player_in_game(cur, game_id, player_id)
                if not membership:
                    return error_response("forbidden", "Player not in game", 403)

                normalized = normalize_test_ships(raw_ships, game["grid_size"])
                if normalized is None:
                    return error_response("bad_request", "Invalid ship coordinates", 400)

                cur.execute(
                    """
                    DELETE FROM ships
                    WHERE game_id = %s AND player_id = %s
                    """,
                    (game_id, player_id)
                )

                for ship in normalized:
                    ship_type = ship["type"]
                    coords = ship["coordinates"]
                    coords_json = json.dumps([[r, c] for r, c in coords])
                    for r, c in coords:
                        cur.execute(
                            """
                            INSERT INTO ships (game_id, player_id, ship_type, coordinates, row_index, col_index)
                            VALUES (%s, %s, %s, %s::jsonb, %s, %s)
                            """,
                            (game_id, player_id, ship_type, coords_json, r, c)
                        )

                update_game_to_playing_if_ready(cur, game_id)
                conn.commit()

        notify_game_update(game_id, "ships_placed")
        return jsonify({
            "success": True,
            "status": "placed",
            "game_id": game_id,
            "player_id": player_id
        }), 200
    except UniqueViolation:
        return error_response("bad_request", "Duplicate ship cell", 400)
    except Exception as ex:
        print(f"Test place ships error: {ex}")
        return error_response("internal_error", "Failed to place test ships", 500)


@app.get("/api/test/games/<int:game_id>/board/<int:player_id>")
@app.get("/api/test/games/<game_id>/board/<player_id>")
@app.get("/api/test/games/<int:game_id>/board")
@app.get("/api/test/games/<game_id>/board")
@app.get("/test/games/<int:game_id>/board")
@app.get("/test/games/<game_id>/board")
def test_board(game_id, player_id=None):
    game_id = resolve_game_id(game_id)
    if not is_valid_int_id(game_id):
        return error_response("bad_request", "game_id is required", 400)

    if player_id is None:
        player_id = request.args.get("playerId") or request.args.get("player_id")

    player_id = resolve_player_id(player_id)
    if not is_valid_int_id(player_id):
        return error_response("bad_request", "player_id is required", 400)

    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                game = get_game_row(cur, game_id)
                if not game:
                    return error_response("not_found", "Game does not exist", 404)

                membership = player_in_game(cur, game_id, player_id)
                if not membership:
                    return error_response("forbidden", "Player not in game", 403)

                ships = group_test_ships(cur, game_id, player_id)

                cur.execute(
                    """
                    SELECT row_index, col_index
                    FROM shots
                    WHERE game_id = %s AND target_player_id = %s AND result = 'hit'
                    ORDER BY created_at
                    """,
                    (game_id, player_id)
                )
                hits = [[row["row_index"], row["col_index"]] for row in cur.fetchall()]

                cur.execute(
                    """
                    SELECT row_index, col_index
                    FROM shots
                    WHERE game_id = %s AND target_player_id = %s AND result = 'miss'
                    ORDER BY created_at
                    """,
                    (game_id, player_id)
                )
                misses = [[row["row_index"], row["col_index"]] for row in cur.fetchall()]

                sunk = compute_sunk_for_player(cur, game_id, player_id)
                board = build_board_view(cur, game_id, player_id, game["grid_size"])

        return jsonify({
            "game_id": game_id,
            "player_id": player_id,
            "board": board,
            "ships": ships,
            "hits": hits,
            "misses": misses,
            "sunk": sunk
        }), 200
    except Exception as ex:
        print(f"Test board error: {ex}")
        return error_response("internal_error", "Failed to fetch board", 500)


@app.post("/api/test/games/<int:game_id>/set-turn")
@app.post("/api/test/games/<game_id>/set-turn")
@app.post("/test/games/<int:game_id>/set-turn")
@app.post("/test/games/<game_id>/set-turn")
def test_set_turn(game_id):
    game_id = resolve_game_id(game_id)
    if not is_valid_int_id(game_id):
        return error_response("bad_request", "game_id is required", 400)

    data = parse_json()
    player_id = data.get("player_id")
    if player_id is None:
        player_id = data.get("playerId")
    player_id = resolve_player_id(player_id)

    if not is_valid_int_id(player_id):
        return error_response("bad_request", "player_id is required", 400)

    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                membership = player_in_game(cur, game_id, player_id)
                if not membership:
                    return error_response("forbidden", "Player not in game", 403)

                cur.execute(
                    """
                    UPDATE games
                    SET current_turn_index = %s
                    WHERE game_id = %s
                    """,
                    (membership["turn_order"], game_id)
                )
                conn.commit()

        notify_game_update(game_id, "turn_set")
        return jsonify({"status": "turn_set"}), 200
    except Exception as ex:
        print(f"Test set-turn error: {ex}")
        return error_response("internal_error", "Failed to set turn", 500)


if __name__ == "__main__":
    port = int(os.getenv("PORT", 5000))
    socketio.run(app, host="0.0.0.0", port=port)
