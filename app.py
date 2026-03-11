import json
import os
from flask import Flask, jsonify, request
from flask_cors import CORS
from dotenv import load_dotenv
from psycopg.errors import UniqueViolation

from db import get_conn, init_db

load_dotenv()

TEST_MODE = os.getenv("TEST_MODE", "true").lower() == "true"
TEST_PASSWORD = os.getenv("TEST_PASSWORD", "clemson-test-2026")

MIN_GRID_SIZE = 5
MAX_GRID_SIZE = 15
DEFAULT_GRID_SIZE = 8
DEFAULT_MAX_PLAYERS = 2
SHIPS_PER_PLAYER = 3

app = Flask(__name__)
CORS(app)

try:
    init_db()
except Exception as ex:
    print(f"DB init failed: {ex}")


def error_response(message, status=400):
    return jsonify({"error": message}), status


def parse_json():
    return request.get_json(silent=True) or {}


def require_test_mode():
    if not TEST_MODE:
        return error_response("Forbidden.", 403)

    supplied = request.headers.get("X-Test-Password") or request.headers.get("X-Test-Mode")
    if supplied != TEST_PASSWORD:
        return error_response("Forbidden.", 403)

    return None


def is_valid_int_id(value):
    return isinstance(value, int) and value > 0


def get_player_row(cur, player_id):
    cur.execute(
        """
        SELECT player_id, display_name, created_at, total_games, total_wins, total_losses, total_moves
        FROM players
        WHERE player_id = %s
        """,
        (player_id,)
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
        SELECT gp.player_id, gp.turn_order, p.display_name
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
    return cur.fetchone()["count"] == SHIPS_PER_PLAYER


def all_players_placed(cur, game_id):
    cur.execute(
        """
        SELECT player_id
        FROM game_players
        WHERE game_id = %s
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


def surviving_players(cur, game_id):
    players = active_player_ids(cur, game_id)
    survivors = []

    for pid in players:
        cur.execute(
            """
            SELECT COUNT(*) AS total_ships
            FROM ships
            WHERE game_id = %s AND player_id = %s
            """,
            (game_id, pid)
        )
        total_ships = cur.fetchone()["total_ships"]

        cur.execute(
            """
            SELECT COUNT(*) AS hits_taken
            FROM shots
            WHERE game_id = %s AND target_player_id = %s AND result = 'hit'
            """,
            (game_id, pid)
        )
        hits_taken = cur.fetchone()["hits_taken"]

        if total_ships == 0 or hits_taken < total_ships:
            survivors.append(pid)

    return survivors


def update_game_to_active_if_ready(cur, game_id):
    if all_players_placed(cur, game_id):
        cur.execute(
            """
            UPDATE games
            SET status = 'active',
                current_turn_index = 0
            WHERE game_id = %s
            """,
            (game_id,)
        )


def normalize_ship_cells(raw_ships, grid_size):
    if not isinstance(raw_ships, list) or len(raw_ships) != SHIPS_PER_PLAYER:
        return None

    normalized = []
    seen = set()

    for ship in raw_ships:
        if not isinstance(ship, dict):
            return None

        if "row" in ship and "col" in ship:
            row = ship.get("row")
            col = ship.get("col")
        else:
            coords = ship.get("coordinates")
            if (
                not isinstance(coords, list)
                or len(coords) != 1
                or not isinstance(coords[0], list)
                or len(coords[0]) != 2
            ):
                return None
            row = coords[0][0]
            col = coords[0][1]

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

        normalized.append({
            "type": ship_type,
            "coordinates": cleaned_coords
        })

    return normalized


@app.get("/api/health")
def health():
    return jsonify({"status": "ok"}), 200


@app.post("/api/reset")
def system_reset():
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM shots")
                cur.execute("DELETE FROM ships")
                cur.execute("DELETE FROM game_players")
                cur.execute("DELETE FROM games")
                cur.execute("DELETE FROM players")
                conn.commit()
        return jsonify({"status": "reset"}), 200
    except Exception as ex:
        print(f"System reset error: {ex}")
        return error_response("Failed to reset system.", 500)


@app.post("/api/players")
def create_player():
    data = parse_json()

    if "player_id" in data or "playerId" in data:
        return error_response("Client may not supply player_id.", 400)

    username = data.get("username") or data.get("playerName")
    if not isinstance(username, str) or not username.strip():
        return error_response("username is required.", 400)

    username = username.strip()

    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO players (display_name)
                    VALUES (%s)
                    RETURNING player_id
                    """,
                    (username,)
                )
                player = cur.fetchone()
                conn.commit()

        return jsonify({"player_id": player["player_id"]}), 201

    except UniqueViolation:
        return error_response("username already exists.", 400)
    except Exception as ex:
        print(f"Create player error: {ex}")
        return error_response("Failed to create player.", 500)


@app.get("/api/players/<int:player_id>/stats")
@app.get("/players/<int:player_id>")
def get_player_stats(player_id):
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                player = get_player_row(cur, player_id)
                if not player:
                    return error_response("Player not found.", 404)

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
        accuracy = (total_hits / total_shots) if total_shots > 0 else 0.0

        return jsonify({
            "games_played": player["total_games"],
            "wins": player["total_wins"],
            "losses": player["total_losses"],
            "total_shots": total_shots,
            "total_hits": total_hits,
            "accuracy": accuracy
        }), 200

    except Exception as ex:
        print(f"Get player stats error: {ex}")
        return error_response("Failed to fetch player stats.", 500)


@app.post("/api/games")
def create_game():
    data = parse_json()

    creator_id = data.get("creator_id")
    grid_size = data.get("grid_size", DEFAULT_GRID_SIZE)
    max_players = data.get("max_players", DEFAULT_MAX_PLAYERS)

    if not is_valid_int_id(creator_id):
        return error_response("creator_id is required.", 400)

    if not isinstance(grid_size, int) or grid_size < MIN_GRID_SIZE or grid_size > MAX_GRID_SIZE:
        return error_response("grid_size must be between 5 and 15.", 400)

    if not isinstance(max_players, int) or max_players < 1:
        return error_response("max_players must be at least 1.", 400)

    if max_players > grid_size:
        return error_response("max_players must be <= grid_size.", 400)

    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                creator = get_player_row(cur, creator_id)
                if not creator:
                    return error_response("Invalid player_id.", 403)

                cur.execute(
                    """
                    INSERT INTO games (status, grid_size, max_players, current_turn_index)
                    VALUES ('waiting', %s, %s, 0)
                    RETURNING game_id, grid_size, status, current_turn_index
                    """,
                    (grid_size, max_players)
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
            "grid_size": game["grid_size"],
            "status": game["status"],
            "current_turn_index": game["current_turn_index"],
            "active_players": 1
        }), 201

    except Exception as ex:
        print(f"Create game error: {ex}")
        return error_response("Failed to create game.", 500)


@app.post("/api/games/<int:game_id>/join")
def join_game(game_id):
    data = parse_json()
    player_id = data.get("player_id")

    if not is_valid_int_id(player_id):
        return error_response("player_id is required.", 400)

    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                game = get_game_row(cur, game_id)
                if not game:
                    return error_response("Game not found.", 404)

                player = get_player_row(cur, player_id)
                if not player:
                    return error_response("Invalid player_id.", 403)

                existing = player_in_game(cur, game_id, player_id)
                if existing:
                    return error_response("Player already joined this game.", 400)

                player_count = count_players_in_game(cur, game_id)
                if player_count >= game["max_players"]:
                    return error_response("Game is full.", 400)

                cur.execute(
                    """
                    INSERT INTO game_players (game_id, player_id, turn_order)
                    VALUES (%s, %s, %s)
                    """,
                    (game_id, player_id, player_count)
                )

                conn.commit()

        return jsonify({"status": "joined"}), 201

    except UniqueViolation:
        return error_response("Player already joined this game.", 400)
    except Exception as ex:
        print(f"Join game error: {ex}")
        return error_response("Failed to join game.", 500)


@app.get("/api/games/<int:game_id>")
def get_game(game_id):
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                game = get_game_row(cur, game_id)
                if not game:
                    return error_response("Game not found.", 404)

                active_players = count_players_in_game(cur, game_id)

        game_status = "finished" if game["status"] == "completed" else game["status"]

        return jsonify({
            "game_id": game["game_id"],
            "grid_size": game["grid_size"],
            "status": game_status,
            "current_turn_index": game["current_turn_index"],
            "active_players": active_players
        }), 200

    except Exception as ex:
        print(f"Get game error: {ex}")
        return error_response("Failed to fetch game.", 500)


@app.post("/api/games/<int:game_id>/place")
def place_production_ships(game_id):
    data = parse_json()
    player_id = data.get("player_id")
    ships = data.get("ships")

    if not is_valid_int_id(player_id):
        return error_response("player_id is required.", 400)

    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                game = get_game_row(cur, game_id)
                if not game:
                    return error_response("Game not found.", 404)

                if game["status"] != "waiting":
                    return error_response("Game is not accepting ship placement.", 403)

                if not get_player_row(cur, player_id):
                    return error_response("Invalid player_id.", 403)

                membership = player_in_game(cur, game_id, player_id)
                if not membership:
                    return error_response("Player not in game.", 403)

                if player_has_placed(cur, game_id, player_id):
                    return error_response("Player already placed ships.", 400)

                normalized = normalize_ship_cells(ships, game["grid_size"])
                if normalized is None:
                    return error_response("Exactly 3 valid single-cell ships are required.", 400)

                for row, col in normalized:
                    cur.execute(
                        """
                        INSERT INTO ships (game_id, player_id, ship_type, coordinates, row_index, col_index)
                        VALUES (%s, %s, 'single', %s::jsonb, %s, %s)
                        """,
                        (game_id, player_id, json.dumps([[row, col]]), row, col)
                    )

                update_game_to_active_if_ready(cur, game_id)
                conn.commit()

        return jsonify({"status": "placed"}), 200

    except UniqueViolation:
        return error_response("Overlapping or duplicate ship cell.", 400)
    except Exception as ex:
        print(f"Place ships error: {ex}")
        return error_response("Failed to place ships.", 500)


@app.post("/api/games/<int:game_id>/fire")
def fire(game_id):
    data = parse_json()
    player_id = data.get("player_id")
    row = data.get("row")
    col = data.get("col")

    if not is_valid_int_id(player_id):
        return error_response("player_id is required.", 400)

    if not isinstance(row, int) or not isinstance(col, int):
        return error_response("row and col are required.", 400)

    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                game = get_game_row(cur, game_id)
                if not game:
                    return error_response("Game not found.", 404)

                if game["status"] != "active":
                    return error_response("Game not active.", 403)

                if not get_player_row(cur, player_id):
                    return error_response("Invalid player_id.", 403)

                membership = player_in_game(cur, game_id, player_id)
                if not membership:
                    return error_response("Player not in game.", 403)

                if membership["turn_order"] != game["current_turn_index"]:
                    return error_response("Out of turn.", 403)

                if row < 0 or row >= game["grid_size"] or col < 0 or col >= game["grid_size"]:
                    return error_response("Shot out of bounds.", 400)

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
                    ship_here = cur.fetchone()

                    if ship_here:
                        target_player_id = other_id
                        result = "hit"
                        break

                if target_player_id is None:
                    for row_player in turn_rows:
                        other_id = row_player["player_id"]
                        if other_id != player_id:
                            target_player_id = other_id
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

                if len(survivors) == 1:
                    winner_id = survivors[0]

                    cur.execute(
                        """
                        UPDATE games
                        SET status = 'completed'
                        WHERE game_id = %s
                        """,
                        (game_id,)
                    )

                    for pid in active_player_ids(cur, game_id):
                        cur.execute(
                            """
                            UPDATE players
                            SET total_games = total_games + 1,
                                total_wins = total_wins + CASE WHEN player_id = %s THEN 1 ELSE 0 END,
                                total_losses = total_losses + CASE WHEN player_id <> %s THEN 1 ELSE 0 END
                            WHERE player_id = %s
                            """,
                            (winner_id, winner_id, pid)
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

                conn.commit()

        if winner_id:
            return jsonify({
                "result": result,
                "next_player_id": None,
                "game_status": "finished",
                "winner_id": winner_id
            }), 200

        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT player_id
                    FROM game_players
                    WHERE game_id = %s
                    ORDER BY turn_order
                    OFFSET (
                        SELECT current_turn_index FROM games WHERE game_id = %s
                    ) LIMIT 1
                    """,
                    (game_id, game_id)
                )
                next_row = cur.fetchone()

        return jsonify({
            "result": result,
            "next_player_id": next_row["player_id"] if next_row else None,
            "game_status": "active"
        }), 200

    except UniqueViolation:
        return error_response("Duplicate shot.", 400)
    except Exception as ex:
        print(f"Fire error: {ex}")
        return error_response("Failed to fire.", 500)


@app.get("/api/games/<int:game_id>/moves")
def get_moves(game_id):
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                game = get_game_row(cur, game_id)
                if not game:
                    return error_response("Game not found.", 404)

                cur.execute(
                    """
                    SELECT shot_id, attacker_player_id, target_player_id, row_index, col_index, result, created_at
                    FROM shots
                    WHERE game_id = %s
                    ORDER BY created_at
                    """,
                    (game_id,)
                )
                shots = cur.fetchall()

        return jsonify([
            {
                "shot_id": shot["shot_id"],
                "attacker_player_id": shot["attacker_player_id"],
                "target_player_id": shot["target_player_id"],
                "row": shot["row_index"],
                "col": shot["col_index"],
                "result": shot["result"],
                "created_at": shot["created_at"].isoformat()
            }
            for shot in shots
        ]), 200

    except Exception as ex:
        print(f"Get moves error: {ex}")
        return error_response("Failed to fetch moves.", 500)


@app.post("/api/test/games/<int:game_id>/restart")
@app.post("/api/test/games/<int:game_id>/reset")
def test_restart(game_id):
    test_check = require_test_mode()
    if test_check:
        return test_check

    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                game = get_game_row(cur, game_id)
                if not game:
                    return error_response("Game not found.", 404)

                cur.execute("DELETE FROM ships WHERE game_id = %s", (game_id,))
                cur.execute("DELETE FROM shots WHERE game_id = %s", (game_id,))
                cur.execute(
                    """
                    UPDATE games
                    SET status = 'waiting',
                        current_turn_index = 0
                    WHERE game_id = %s
                    """,
                    (game_id,)
                )
                conn.commit()

        return jsonify({"status": "restarted"}), 200

    except Exception as ex:
        print(f"Test restart error: {ex}")
        return error_response("Failed to restart game.", 500)


@app.post("/api/test/games/<int:game_id>/ships")
def test_place_ships(game_id):
    test_check = require_test_mode()
    if test_check:
        return test_check

    data = parse_json()
    player_id = data.get("player_id") or data.get("playerId")
    raw_ships = data.get("ships")

    if not is_valid_int_id(player_id):
        return error_response("player_id is required.", 400)

    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                game = get_game_row(cur, game_id)
                if not game:
                    return error_response("Game not found.", 404)

                if game["status"] != "waiting":
                    return error_response("Ships can only be placed before game starts.", 400)

                if not get_player_row(cur, player_id):
                    return error_response("Invalid player_id.", 403)

                membership = player_in_game(cur, game_id, player_id)
                if not membership:
                    return error_response("Player not in game.", 403)

                normalized = normalize_test_ships(raw_ships, game["grid_size"])
                if normalized is None:
                    return error_response("Invalid ship coordinates.", 400)

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

                    for row, col in coords:
                        cur.execute(
                            """
                            INSERT INTO ships (game_id, player_id, ship_type, coordinates, row_index, col_index)
                            VALUES (%s, %s, %s, %s::jsonb, %s, %s)
                            """,
                            (
                                game_id,
                                player_id,
                                ship_type,
                                json.dumps([[row, col]]),
                                row,
                                col
                            )
                        )

                conn.commit()

        return jsonify({"status": "placed"}), 200

    except UniqueViolation:
        return error_response("Duplicate ship cell.", 400)
    except Exception as ex:
        print(f"Test place ships error: {ex}")
        return error_response("Failed to place test ships.", 500)


@app.get("/api/test/games/<int:game_id>/board/<int:player_id>")
@app.get("/api/test/games/<int:game_id>/board")
def test_board(game_id, player_id=None):
    test_check = require_test_mode()
    if test_check:
        return test_check

    if player_id is None:
        player_id = request.args.get("playerId", type=int) or request.args.get("player_id", type=int)

    if not is_valid_int_id(player_id):
        return error_response("player_id is required.", 400)

    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                game = get_game_row(cur, game_id)
                if not game:
                    return error_response("Game not found.", 404)

                membership = player_in_game(cur, game_id, player_id)
                if not membership:
                    return error_response("Player not in game.", 403)

                cur.execute(
                    """
                    SELECT row_index, col_index
                    FROM ships
                    WHERE game_id = %s AND player_id = %s
                    ORDER BY row_index, col_index
                    """,
                    (game_id, player_id)
                )
                ships = [{"row": row["row_index"], "col": row["col_index"]} for row in cur.fetchall()]

                cur.execute(
                    """
                    SELECT row_index, col_index
                    FROM shots
                    WHERE game_id = %s AND target_player_id = %s AND result = 'hit'
                    ORDER BY created_at
                    """,
                    (game_id, player_id)
                )
                hits = [{"row": row["row_index"], "col": row["col_index"]} for row in cur.fetchall()]

                cur.execute(
                    """
                    SELECT row_index, col_index
                    FROM shots
                    WHERE game_id = %s AND target_player_id = %s AND result = 'miss'
                    ORDER BY created_at
                    """,
                    (game_id, player_id)
                )
                misses = [{"row": row["row_index"], "col": row["col_index"]} for row in cur.fetchall()]

        return jsonify({
            "player_id": player_id,
            "ships": ships,
            "hits": hits,
            "misses": misses
        }), 200

    except Exception as ex:
        print(f"Test board error: {ex}")
        return error_response("Failed to fetch board.", 500)


@app.post("/api/test/games/<int:game_id>/set-turn")
def test_set_turn(game_id):
    test_check = require_test_mode()
    if test_check:
        return test_check

    data = parse_json()
    player_id = data.get("player_id") or data.get("playerId")

    if not is_valid_int_id(player_id):
        return error_response("player_id is required.", 400)

    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                membership = player_in_game(cur, game_id, player_id)
                if not membership:
                    return error_response("Player not in game.", 403)

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
        return error_response("Failed to set turn.", 500)


if __name__ == "__main__":
    port = int(os.getenv("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
