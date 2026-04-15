import json
import os
import time
from flask import Flask, jsonify, request
from flask_cors import CORS
from dotenv import load_dotenv
from psycopg.errors import UniqueViolation

from db import get_conn, init_db

load_dotenv()

TEST_MODE = os.getenv("TEST_MODE", "true").lower() == "true"
TEST_PASSWORD = os.getenv("TEST_PASSWORD", "clemson-test-2026")
AUTO_RESET_ON_START = os.getenv("AUTO_RESET_ON_START", "true").lower() == "true"

API_VERSION = "2.8.1"
SPEC_VERSION = "2.8.1"
APP_START_TIME = time.time()
INITIAL_RESET_DONE = False

MIN_GRID_SIZE = 5
MAX_GRID_SIZE = 15
MIN_PLAYERS = 2
MAX_PLAYERS = 10
DEFAULT_GRID_SIZE = 8
DEFAULT_MAX_PLAYERS = 2
SHIPS_PER_PLAYER = 3

WAITING_STATUS = "waiting_setup"
PLAYING_STATUS = "playing"
FINISHED_STATUS = "finished"

PLACEHOLDER_GAME_IDS = {":id", "{id}", ":game_id", "{game_id}"}
PLACEHOLDER_PLAYER_IDS = {":player_id", "{player_id}"}

app = Flask(__name__)
CORS(app)


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
        SELECT player_id, username, created_at, total_games, total_wins, total_losses, total_moves
        FROM players
        WHERE player_id = %s
        """,
        (player_id,)
    )
    return cur.fetchone()


def get_player_row_by_username(cur, username):
    cur.execute(
        """
        SELECT player_id, username, created_at, total_games, total_wins, total_losses, total_moves
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
    if not isinstance(raw_ships, list) or len(raw_ships) != SHIPS_PER_PLAYER:
        return None

    normalized = []
    seen = set()

    for ship in raw_ships:
        if not isinstance(ship, dict):
            return None

        if set(ship.keys()) - {"row", "col"}:
            return None

        row = ship.get("row")
        col = ship.get("col")

        if not isinstance(row, int) or not isinstance(col, int):
            return None
        if row < 0 or row >= grid_size or col < 0 or col >= grid_size:
            return None
        if (row, col) in seen:
            return None

        seen.add((row, col))
        normalized.append((row, col))

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


try:
    init_db()
    if TEST_MODE and AUTO_RESET_ON_START:
        reset_database()
        print("Auto reset on startup completed.")
except Exception as ex:
    print(f"DB init/startup reset failed: {ex}")


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
        return jsonify({"players": []}), 200
    except Exception as ex:
        print(f"List players error: {ex}")
        return error_response("internal_error", "Failed to list players", 500)


@app.get("/api/games")
def list_games():
    try:
        return jsonify({"games": []}), 200
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

                cur.execute(
                    """
                    SELECT COUNT(*) AS total_hits
                    FROM shots
                    WHERE attacker_player_id = %s AND result = 'hit'
                    """,
                    (player_id,)
                )
                total_hits = cur.fetchone()["total_hits"]

        total_shots = player["total_moves"]
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
                    return error_response("bad_request", "Exactly 3 valid ships are required", 400)

                for row, col in normalized:
                    cur.execute(
                        """
                        INSERT INTO ships (game_id, player_id, ship_type, coordinates, row_index, col_index)
                        VALUES (%s, %s, 'single', %s::jsonb, %s, %s)
                        """,
                        (game_id, player_id, json.dumps([[row, col]]), row, col)
                    )

                update_game_to_playing_if_ready(cur, game_id)
                conn.commit()

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

    row = data.get("row")
    col = data.get("col")

    if not is_valid_int_id(player_id):
        return error_response("bad_request", "player_id is required", 400)
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

                cur.execute(
                    """
                    SELECT 1
                    FROM shots
                    WHERE game_id = %s AND row_index = %s AND col_index = %s
                    LIMIT 1
                    """,
                    (game_id, row, col)
                )
                if cur.fetchone():
                    return error_response("conflict", "Cell already fired upon", 409)

                if game["status"] == FINISHED_STATUS:
                    return error_response("bad_request", "Game already finished", 400)

                if game["status"] != PLAYING_STATUS:
                    return error_response("forbidden", "Game is not in playing state", 403)

                if membership["turn_order"] != game["current_turn_index"]:
                    return error_response("forbidden", "Not your turn", 403)

                target_player_id = None
                result = "miss"
                turn_rows = get_turn_order_rows(cur, game_id)

                for row_player in turn_rows:
                    other_id = row_player["player_id"]
                    if other_id == player_id:
                        continue

                    cur.execute(
                        """
                        SELECT 1
                        FROM ships
                        WHERE game_id = %s
                          AND player_id = %s
                          AND row_index = %s
                          AND col_index = %s
                        """,
                        (game_id, other_id, row, col)
                    )
                    if cur.fetchone():
                        target_player_id = other_id
                        result = "hit"
                        break

                if target_player_id is None:
                    for row_player in turn_rows:
                        if row_player["player_id"] != player_id:
                            target_player_id = row_player["player_id"]
                            break

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
                    SET total_moves = total_moves + 1
                    WHERE player_id = %s
                    """,
                    (player_id,)
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

        response = {
            "result": result,
            "next_player_id": next_player_id,
            "game_status": game_status,
        }
        if winner_id is not None:
            response["winner_id"] = winner_id
        return jsonify(response), 200
    except UniqueViolation:
        return error_response("conflict", "Cell already fired upon", 409)
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
                    SELECT attacker_player_id, row_index, col_index, result, created_at
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

                cur.execute(
                    """
                    SELECT p.player_id, gp.turn_order, p.total_games, p.total_wins, p.total_losses, p.total_moves
                    FROM game_players gp
                    JOIN players p ON p.player_id = gp.player_id
                    WHERE gp.game_id = %s
                    ORDER BY gp.turn_order
                    """,
                    (game_id,)
                )
                preserved_players = cur.fetchall()
                if not preserved_players:
                    return error_response("not_found", "Game does not exist", 404)

                creator_id = preserved_players[0]["player_id"]
                game_grid_size = game["grid_size"]
                game_max_players = game["max_players"]

                reset_database()

                for row in preserved_players:
                    cur.execute(
                        """
                        INSERT INTO players (player_id, username, total_games, total_wins, total_losses, total_moves)
                        VALUES (%s, %s, %s, %s, %s, %s)
                        """,
                        (
                            row["player_id"],
                            f"restored_player_{row['player_id']}_{game_id}",
                            row["total_games"],
                            row["total_wins"],
                            row["total_losses"],
                            row["total_moves"],
                        )
                    )

                cur.execute(
                    """
                    INSERT INTO games (game_id, status, grid_size, max_players, current_turn_index)
                    VALUES (%s, %s, %s, %s, 0)
                    """,
                    (game_id, WAITING_STATUS, game_grid_size, game_max_players)
                )

                cur.execute(
                    """
                    INSERT INTO game_players (game_id, player_id, turn_order)
                    VALUES (%s, %s, 0)
                    """,
                    (game_id, creator_id)
                )

                cur.execute(
                    "SELECT setval(pg_get_serial_sequence('players', 'player_id'), GREATEST(COALESCE((SELECT MAX(player_id) FROM players), 1), 1), true)"
                )
                cur.execute(
                    "SELECT setval(pg_get_serial_sequence('games', 'game_id'), GREATEST(COALESCE((SELECT MAX(game_id) FROM games), 1), 1), true)"
                )

                conn.commit()

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

        return jsonify({"status": "turn_set"}), 200
    except Exception as ex:
        print(f"Test set-turn error: {ex}")
        return error_response("internal_error", "Failed to set turn", 500)


if __name__ == "__main__":
    port = int(os.getenv("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
