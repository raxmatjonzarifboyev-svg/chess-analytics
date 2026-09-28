import re
import requests
import pandas as pd
import chess
import chess.pgn
import chess.engine
from io import StringIO


 
# ============ SOZLAMALAR ============
USERNAME = "ZarifboyevRahmatjon"        # chess.com username
STOCKFISH_PATH = r"D:\programms\stockfish-windows-x86-64-universal\stockfish\stockfish-windows-x86-64-universal.exe"        # yoki to'liq yo'l: r"C:\stockfish\stockfish.exe"
MONTHS = 3                          # oxirgi nechta oy
MAX_GAMES = 100                  # eng ko'pi bilan nechta partiya
DEPTH = 12                          # 12-14 yetarli, kattasi sekinroq
# ====================================
 
HEADERS = {"User-Agent": f"chess-analyzer (username: {USERNAME})"}
CLAMP = 1000  # centipawn chegarasi (mat va katta ustunlik uchun)
 
DRAWS = {"agreed", "repetition", "stalemate", "insufficient", "50move", "timevsinsufficient"}
 
 
def fetch_games():
    url = f"https://api.chess.com/pub/player/{USERNAME}/games/archives"
    r = requests.get(url, headers=HEADERS)
    r.raise_for_status()
    archives = r.json()["archives"][-MONTHS:]
 
    games = []
    for a in reversed(archives):
        data = requests.get(a, headers=HEADERS).json()
        for g in reversed(data.get("games", [])):
            if g.get("pgn") and g.get("rules") == "chess":
                games.append(g)
    return games[:MAX_GAMES]
 
 
def result_score(game, color):
    res = game[color]["result"]
    if res == "win":
        return 1.0
    if res in DRAWS:
        return 0.5
    return 0.0
 
 
def get_phase(board, fullmove):
    # Endshpil: qirol va piyodalardan tashqari 6 tadan kam figura qolgan
    pieces = sum(
        len(board.pieces(pt, c))
        for pt in (chess.KNIGHT, chess.BISHOP, chess.ROOK, chess.QUEEN)
        for c in (chess.WHITE, chess.BLACK)
    )
    if fullmove <= 10:
        return "debyut"
    if pieces <= 6:
        return "endshpil"
    return "o'rta o'yin"
 
 
def classify(loss):
    if loss >= 300:
        return "blunder"
    if loss >= 100:
        return "mistake"
    if loss >= 50:
        return "inaccuracy"
    return "ok"
 
 
def opening_name(pgn_game):
    url = pgn_game.headers.get("ECOUrl", "")
    if "/openings/" not in url:
        return pgn_game.headers.get("ECO", "?")
    name = url.split("/openings/")[-1]
    name = re.split(r"-\d", name)[0]  # yurish raqamlarini kesib tashlaydi
    return name.replace("-", " ")
 
 
def analyze_game(engine, game):
    pgn = chess.pgn.read_game(StringIO(game["pgn"]))
    color = "white" if game["white"]["username"].lower() == USERNAME.lower() else "black"
    my_color = chess.WHITE if color == "white" else chess.BLACK
 
    board = pgn.board()
    moves = list(pgn.mainline_moves())
 
    # Har bir pozitsiyani bir marta baholaymiz
    infos = [engine.analyse(board, chess.engine.Limit(depth=DEPTH))]
    boards = [board.copy()]
    for mv in moves:
        board.push(mv)
        boards.append(board.copy())
        infos.append(engine.analyse(board, chess.engine.Limit(depth=DEPTH)))
 
    def score_white(info):
        s = info["score"].white().score(mate_score=10000)
        return max(-CLAMP, min(CLAMP, s))
 
    mistakes = []
    losses = []
    for i, mv in enumerate(moves):
        before = boards[i]
        if before.turn != my_color:
            continue
        s_before = score_white(infos[i])
        s_after = score_white(infos[i + 1])
        if my_color == chess.BLACK:
            s_before, s_after = -s_before, -s_after
        loss = max(0, s_before - s_after)
        losses.append(loss)
 
        kind = classify(loss)
        if kind != "ok":
            best = infos[i].get("pv", [None])[0]
            mistakes.append({
                "game_url": game["url"],
                "move_no": before.fullmove_number,
                "phase": get_phase(before, before.fullmove_number),
                "color": color,
                "time_class": game["time_class"],
                "opening": opening_name(pgn),
                "played": before.san(mv),
                "best": before.san(best) if best else "",
                "loss": loss,
                "type": kind,
                "fen_before": before.fen(),
            })
 
    game_row = {
        "url": game["url"],
        "color": color,
        "time_class": game["time_class"],
        "opening": opening_name(pgn),
        "result": result_score(game, color),
        "avg_loss": sum(losses) / len(losses) if losses else 0,
        "moves": len(losses),
    }
    return game_row, mistakes
 
 
def build_summary(games_df, mistakes_df):
    out = []
    out.append(f"Partiyalar soni: {len(games_df)}")
    out.append(f"Umumiy natija (1=yutuq, 0.5=durang): {games_df['result'].mean():.2f}")
    out.append(f"O'rtacha centipawn yo'qotish: {games_df['avg_loss'].mean():.1f}\n")
 
    out.append("== Rang bo'yicha ==")
    out.append(games_df.groupby("color").agg(
        partiya=("url", "count"), natija=("result", "mean"), yoqotish=("avg_loss", "mean")
    ).round(2).to_string())
 
    out.append("\n== Vaqt rejimi bo'yicha ==")
    out.append(games_df.groupby("time_class").agg(
        partiya=("url", "count"), natija=("result", "mean"), yoqotish=("avg_loss", "mean")
    ).round(2).to_string())
 
    if not mistakes_df.empty:
        out.append("\n== Xatolar: bosqich va turi bo'yicha ==")
        out.append(pd.crosstab(mistakes_df["phase"], mistakes_df["type"]).to_string())
 
        out.append("\n== Blunderlar: vaqt rejimi bo'yicha ==")
        b = mistakes_df[mistakes_df["type"] == "blunder"]
        out.append(b.groupby("time_class").size().to_string())
 
    out.append("\n== Debyutlar (kamida 3 partiya) ==")
    op = games_df.groupby(["color", "opening"]).agg(
        partiya=("url", "count"), natija=("result", "mean"), yoqotish=("avg_loss", "mean")
    ).round(2)
    op = op[op["partiya"] >= 3].sort_values("natija")
    out.append(op.to_string() if not op.empty else "Yetarli ma'lumot yo'q")
 
    return "\n".join(out)
 
 
def main():
    print("Partiyalar yuklanmoqda...")
    games = fetch_games()
    print(f"{len(games)} ta partiya topildi. Tahlil boshlandi (biroz vaqt oladi)...")
 
    game_rows, all_mistakes = [], []
    with chess.engine.SimpleEngine.popen_uci(STOCKFISH_PATH) as engine:
        for i, g in enumerate(games, 1):
            try:
                row, mistakes = analyze_game(engine, g)
                game_rows.append(row)
                all_mistakes.extend(mistakes)
            except Exception as e:
                print(f"  ! partiya o'tkazib yuborildi ({g.get('url')}): {e}")
            print(f"  {i}/{len(games)}", end="\r")
 
    games_df = pd.DataFrame(game_rows)
    mistakes_df = pd.DataFrame(all_mistakes)
    games_df.to_csv("games.csv", index=False)
    mistakes_df.to_csv("mistakes.csv", index=False)
 
    summary = build_summary(games_df, mistakes_df)
    with open("summary.txt", "w", encoding="utf-8") as f:
        f.write(summary)
 
    print("\n\n" + summary)
    print("\nSaqlandi: games.csv, mistakes.csv, summary.txt")
 
 
if __name__ == "__main__":
    main()