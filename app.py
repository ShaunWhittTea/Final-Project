import os
import json
from flask import Flask, jsonify, request
from flask_cors import CORS
from dotenv import load_dotenv
from psycopg.errors import UniqueViolation

from db import get_conn, init_db

load_dotenv()

TEST_PASSWORD = os.getenv("TEST_PASSWORD")
DEFAULT_GRID_SIZE = 10
MIN_GRID_SIZE = 5
MAX_GRID_SIZE = 15

app = Flask(__name__)
CORS(app)

try:
    init_db()
except Exception as ex:
    print(f"DB init failed: {ex}")


def error_response(message, status=400):
    return jsonify({"error": message}), status


def require_test_mode():
    header = request.headers.get("X-Test-Mode")
    if not TEST_PASSWORD or header != TEST_PASSWORD:
        return error_response("Forbidden.", 403)
    return None


def validate_grid_size(value):
    if not isinstance(value, int):
        return False
    return MIN_GRID_SIZE <= value <= MAX_GRID_SIZE


def validate_coordinates(coordinates, grid_size):
    if not isinstance(coordinates, list) or not coordinates:
        return False

    seen = set()

    for cell in coordinates:
        if (
            not isinstance(cell, list)
            or len(cell) != 2
            or not isinstance(cell[0], int)
            or not isinstance(cell[1], int)
        ):
            return False

        row_index, col_index = cell

        if row_index < 0 or row_index >= grid_size:
            return False
        if col_index < 0 or col_index >= grid_size:
            return False

        if (row_index, col_index) in seen:
            return False
        seen.add((row_index, col_index))

    return True


def compute_board_state(conn, game_id, player_id):
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT ship_type, coordinates
            FROM ships
            WHERE game_id = %s AND player_id = %s
            ORDER BY created_at
            """,
            (game_id, player_id)
        )
        ships = cur.fetchall()

        cur.execute(
            """
            SELECT row_index, col_index, result
            FROM shots
            WHERE game_id = %s AND target_player_id = %s
            ORDER BY created_at
            """,
            (game_id, player_id)
        )
        shots = cur.fetchall()

    ship_list = []
    hit_cells = set()
    hits = []
    misses = []

    for ship in ships:
        ship_list.append({
            "type": ship["ship_type"],
            "coordinates": ship["coordinates"]
        })

    for shot in shots:
        cell = [shot["row_index"], shot["col_index"]]
        if shot["result"] == "hit":
            hits.append(cell)
            hit_cells.add((shot["row_index"], shot["col_index"]))
        else:
            misses.append(cell)

    sunk = []
    for ship in ship_list:
        coords = ship["coordinates"]
        if all((cell[0], cell[1]) in hit_cells for cell in coords):
            sunk.append(ship)

    return {
        "ships": ship_list,
        "hits": hits,
        "misses": misses,
        "sunk": sunk
    }


@app.get("/api/health")
def health():
    return jsonify({"status": "ok"}), 200


@app.post("/players")
def create_player():
    data = request.get_json(silent=True) or {}

    username = data.get("username")
    if not username or not isinstance(username, str) or not username.strip():
        return error_response("username is required.", 400)

    username = username.strip()

    if "playerId" in data:
        return error_response("Client may not supply playerId.", 400)

    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO players (display_name)
                    VALUES (%s)
                    RETURNING player_id, display_name, created_at,
                              total_games, total_wins, total_losses, total_moves
                    """,
                    (username,)
                )
                player = cur.fetchone()
                conn.commit()

        return jsonify({
            "playerId": str(player["player_id"]),
            "username": player["display_name"],
            "displayName": player["display_name"],
            "createdAt": player["created_at"].isoformat(),
            "totalGames": player["total_games"],
            "totalWins": player["total_wins"],
            "totalLosses": player["total_losses"],
            "totalMoves": player["total_moves"]
        }), 201

    except UniqueViolation:
        return error_response("username already exists.", 400)
    except Exception as ex:
        print(f"Create player error: {ex}")
        return error_response("Failed to create player.", 500)


@app.get("/players/<player_id>")
def get_player(player_id):
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT player_id, display_name, created_at,
                           total_games, total_wins, total_losses, total_moves
                    FROM players
                    WHERE player_id = %s
                    """,
                    (player_id,)
                )
                player = cur.fetchone()

                if not player:
                    return error_response("Player not found.", 404)

        return jsonify({
            "playerId": str(player["player_id"]),
            "username": player["display_name"],
            "displayName": player["display_name"],
            "createdAt": player["created_at"].isoformat(),
            "totalGames": player["total_games"],
            "totalWins": player["total_wins"],
            "totalLosses": player["total_losses"],
            "totalMoves": player["total_moves"]
        }), 200

    except Exception as ex:
        print(f"Get player error: {ex}")
        return error_response("Failed to fetch player.", 500)


@app.post("/games")
def create_game():
    data = request.get_json(silent=True) or {}

    if "gameId" in data:
        return error_response("Client may not supply gameId.", 400)

    grid_size = data.get("gridSize", DEFAULT_GRID_SIZE)

    if not validate_grid_size(grid_size):
        return error_response(
            f"gridSize must be an integer between {MIN_GRID_SIZE} and {MAX_GRID_SIZE}.",
            400
        )

    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO games (status, grid_size)
                    VALUES ('waiting', %s)
                    RETURNING game_id, status, grid_size, created_at
                    """,
                    (grid_size,)
                )
                game = cur.fetchone()
                conn.commit()

        return jsonify({
            "gameId": str(game["game_id"]),
            "status": game["status"],
            "gridSize": game["grid_size"],
            "createdAt": game["created_at"].isoformat()
        }), 201

    except Exception as ex:
        print(f"Create game error: {ex}")
        return error_response("Failed to create game.", 500)


@app.post("/games/<game_id>/join")
def join_game(game_id):
    data = request.get_json(silent=True) or {}

    player_id = data.get("playerId")
    username = data.get("username") or data.get("playerName")

    if not player_id and not username:
        return error_response("playerId is required.", 400)

    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT game_id, status, grid_size
                    FROM games
                    WHERE game_id = %s
                    """,
                    (game_id,)
                )
                game = cur.fetchone()

                if not game:
                    return error_response("Game not found.", 404)

                player = None

                if player_id:
                    cur.execute(
                        """
                        SELECT player_id, display_name
                        FROM players
                        WHERE player_id = %s
                        """,
                        (player_id,)
                    )
                    player = cur.fetchone()

                    if not player:
                        return error_response("Player not found.", 404)
                else:
                    username = username.strip()
                    cur.execute(
                        """
                        SELECT player_id, display_name
                        FROM players
                        WHERE display_name = %s
                        """,
                        (username,)
                    )
                    player = cur.fetchone()

                    if not player:
                        cur.execute(
                            """
                            INSERT INTO players (display_name)
                            VALUES (%s)
                            RETURNING player_id, display_name
                            """,
                            (username,)
                        )
                        player = cur.fetchone()

                try:
                    cur.execute(
                        """
                        INSERT INTO game_players (game_id, player_id)
                        VALUES (%s, %s)
                        """,
                        (game_id, player["player_id"])
                    )
                except UniqueViolation:
                    return error_response("Player is already in this game.", 400)

                conn.commit()

        return jsonify({
            "gameId": str(game["game_id"]),
            "playerId": str(player["player_id"]),
            "username": player["display_name"],
            "playerName": player["display_name"],
            "status": game["status"],
            "gridSize": game["grid_size"]
        }), 201

    except Exception as ex:
        print(f"Join game error: {ex}")
        return error_response("Failed to join game.", 500)


@app.post("/test/games/<game_id>/ships")
def place_ships(game_id):
    test_check = require_test_mode()
    if test_check:
        return test_check

    data = request.get_json(silent=True) or {}
    player_id = data.get("playerId")
    ships = data.get("ships")

    if not player_id or not isinstance(player_id, str):
        return error_response("playerId is required.", 400)

    if not isinstance(ships, list) or not ships:
        return error_response("ships is required.", 400)

    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT game_id, status, grid_size
                    FROM games
                    WHERE game_id = %s
                    """,
                    (game_id,)
                )
                game = cur.fetchone()

                if not game:
                    return error_response("Game not found.", 404)

                if game["status"] != "waiting":
                    return error_response("Ships can only be placed before game starts.", 400)

                cur.execute(
                    """
                    SELECT 1
                    FROM game_players
                    WHERE game_id = %s AND player_id = %s
                    """,
                    (game_id, player_id)
                )
                membership = cur.fetchone()

                if not membership:
                    return error_response("Player is not in this game.", 403)

                occupied = set()
                validated_ships = []

                for ship in ships:
                    if not isinstance(ship, dict):
                        return error_response("Each ship must be an object.", 400)

                    ship_type = ship.get("type")
                    coordinates = ship.get("coordinates")

                    if not ship_type or not isinstance(ship_type, str):
                        return error_response("Each ship must have a valid type.", 400)

                    if not validate_coordinates(coordinates, game["grid_size"]):
                        return error_response("Invalid ship coordinates.", 400)

                    for cell in coordinates:
                        coord = (cell[0], cell[1])
                        if coord in occupied:
                            return error_response("Ship coordinates overlap.", 400)
                        occupied.add(coord)

                    validated_ships.append((ship_type, json.dumps(coordinates)))

                cur.execute(
                    """
                    DELETE FROM ships
                    WHERE game_id = %s AND player_id = %s
                    """,
                    (game_id, player_id)
                )

                for ship_type, coordinates_json in validated_ships:
                    cur.execute(
                        """
                        INSERT INTO ships (game_id, player_id, ship_type, coordinates)
                        VALUES (%s, %s, %s, %s::jsonb)
                        """,
                        (game_id, player_id, ship_type, coordinates_json)
                    )

                conn.commit()

        return jsonify({
            "success": True,
            "gameId": game_id,
            "playerId": player_id,
            "message": "Ships placed successfully."
        }), 200

    except Exception as ex:
        print(f"Test place ships error: {ex}")
        return error_response("Failed to place ships.", 500)


@app.get("/test/games/<game_id>/board")
def reveal_board(game_id):
    test_check = require_test_mode()
    if test_check:
        return test_check

    player_id = request.args.get("playerId")
    if not player_id:
        return error_response("playerId is required.", 400)

    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT game_id
                    FROM games
                    WHERE game_id = %s
                    """,
                    (game_id,)
                )
                game = cur.fetchone()

                if not game:
                    return error_response("Game not found.", 404)

                cur.execute(
                    """
                    SELECT 1
                    FROM game_players
                    WHERE game_id = %s AND player_id = %s
                    """,
                    (game_id, player_id)
                )
                membership = cur.fetchone()

                if not membership:
                    return error_response("Player is not in this game.", 403)

            board_state = compute_board_state(conn, game_id, player_id)

        return jsonify({
            "gameId": game_id,
            "playerId": player_id,
            **board_state
        }), 200

    except Exception as ex:
        print(f"Reveal board error: {ex}")
        return error_response("Failed to fetch board.", 500)


@app.post("/test/games/<game_id>/reset")
def reset_game(game_id):
    test_check = require_test_mode()
    if test_check:
        return test_check

    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT game_id
                    FROM games
                    WHERE game_id = %s
                    """,
                    (game_id,)
                )
                game = cur.fetchone()

                if not game:
                    return error_response("Game not found.", 404)

                cur.execute("DELETE FROM ships WHERE game_id = %s", (game_id,))
                cur.execute("DELETE FROM shots WHERE game_id = %s", (game_id,))

                cur.execute(
                    """
                    UPDATE games
                    SET status = 'waiting',
                        current_turn_player_id = NULL
                    WHERE game_id = %s
                    """,
                    (game_id,)
                )

                conn.commit()

        return jsonify({
            "success": True,
            "message": "Game reset successfully.",
            "gameId": game_id,
            "status": "waiting"
        }), 200

    except Exception as ex:
        print(f"Reset game error: {ex}")
        return error_response("Failed to reset game.", 500)


@app.post("/test/games/<game_id>/set-turn")
def set_turn(game_id):
    test_check = require_test_mode()
    if test_check:
        return test_check

    data = request.get_json(silent=True) or {}
    player_id = data.get("playerId")

    if not player_id:
        return error_response("playerId is required.", 400)

    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT 1
                    FROM game_players
                    WHERE game_id = %s AND player_id = %s
                    """,
                    (game_id, player_id)
                )
                membership = cur.fetchone()

                if not membership:
                    return error_response("Player is not in this game.", 403)

                cur.execute(
                    """
                    UPDATE games
                    SET current_turn_player_id = %s
                    WHERE game_id = %s
                    """,
                    (player_id, game_id)
                )

                conn.commit()

        return jsonify({
            "gameId": game_id,
            "currentTurnPlayerId": player_id
        }), 200

    except Exception as ex:
        print(f"Set turn error: {ex}")
        return error_response("Failed to set turn.", 500)


if __name__ == "__main__":
    port = int(os.getenv("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
