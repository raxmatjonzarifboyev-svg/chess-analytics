

"""
Chess.com partiyalarini Stockfish bilan tahlil qiladi.
Endi vaqt (clock) ma'lumotini ham hisobga oladi: xatolar qachon -
vaqt yetishmaganda ko'proq bo'lyaptimi - shuni ko'rsatadi.
 
O'rnatish:
    pip install python-chess requests pandas
 
Ishga tushirish:
    python chess_analyzer.py
 
Natija:
    games.csv    - har bir partiya
    mistakes.csv - har bir xato yurish (endi time_left, time_spent, time_bucket bilan)
    moves.csv    - SIZNING har bir yurishingiz (xato bo'lsin-bo'lmasin) - vaqt tahlili shundan
    summary.txt  - statistika (buni LLM'ga berasiz)
"""
 
import re
import requests
import pandas as pd
import chess
import chess.pgn
import chess.engine
from io import StringIO
 
# ============ SOZLAMALAR ============
USERNAME = "ZarifboyevRahmatjon"
STOCKFISH_PATH = r"D:\programms\stockfish-windows-x86-64-universal\stockfish\stockfish-windows-x86-64-universal.exe"
MONTHS = 3
MAX_GAMES = 100
DEPTH = 12
# ====================================
 
HEADERS = {"User-Agent": f"chess-analyzer (username: {USERNAME})"}
CLAMP = 1000
DRAWS = {"agreed", "repetition", "stalemate", "insufficient", "50move", "timevsinsufficient"}
CLOCK_RE = re.compile(r"\[%clk\s*([\d:.]+)\]")
TIME_BUCKET_ORDER = ["<10s", "10-30s", "30-60s", "60-120s", "120s+", "noma'lum"]
 
 
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
    name = re.split(r"-\d", name)[0]
    return name.replace("-", " ")
 
 
def parse_time_control(tc):
    """'180' -> (180, 0)   '180+2' -> (180, 2)   noto'g'ri/mavjud bo'lmasa -> (None, 0)"""
    if not tc:
        return None, 0
    if "+" in tc:
        base, inc = tc.split("+", 1)
        try:
            return float(base), float(inc)
        except ValueError:
            return None, 0
    try:
        return float(tc), 0
    except ValueError:
        return None, 0
 
 
def parse_clock(comment):
    m = CLOCK_RE.search(comment or "")
    if not m:
        return None
    parts = [float(p) for p in m.group(1).split(":")]
    while len(parts) < 3:
        parts.insert(0, 0)
    h, mnt, s = parts
    return h * 3600 + mnt * 60 + s
 
 
def time_bucket(t):
    if t is None:
        return "noma'lum"
    if t < 10:
        return "<10s"
    if t < 30:
        return "10-30s"
    if t < 60:
        return "30-60s"
    if t < 120:
        return "60-120s"
    return "120s+"
 
 
def analyze_game(engine, game):
    pgn = chess.pgn.read_game(StringIO(game["pgn"]))
    color = "white" if game["white"]["username"].lower() == USERNAME.lower() else "black"
    my_color = chess.WHITE if color == "white" else chess.BLACK
 
    base_time, increment = parse_time_control(pgn.headers.get("TimeControl"))
 
    board = pgn.board()
    moves = list(pgn.mainline_moves())
 
    # Har bir yurishdan keyingi soat holatini o'qiymiz (agar chess.com yozgan bo'lsa)
    clocks_by_ply = []
    node = pgn
    for _ in moves:
        node = node.variations[0] if node.variations else None
        clocks_by_ply.append(parse_clock(node.comment) if node else None)
 
    def clock_before(i):
        return base_time if i < 2 else clocks_by_ply[i - 2]
 
    # Har bir pozitsiyani bir marta Stockfish bilan baholaymiz
    infos = [engine.analyse(board, chess.engine.Limit(depth=DEPTH))]
    boards = [board.copy()]
    for mv in moves:
        board.push(mv)
        boards.append(board.copy())
        infos.append(engine.analyse(board, chess.engine.Limit(depth=DEPTH)))
 
    def score_white(info):
        s = info["score"].white().score(mate_score=10000)
        return max(-CLAMP, min(CLAMP, s))
 
    mistakes, own_moves, losses = [], [], []
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
 
        t_after = clocks_by_ply[i]
        t_before = clock_before(i)
        if t_after is not None and t_before is not None:
            time_left = t_after
            time_spent = max(0.0, t_before - t_after + increment)
        else:
            time_left = None
            time_spent = None
        bucket = time_bucket(time_left)
 
        own_moves.append({
            "game_url": game["url"], "move_no": before.fullmove_number,
            "phase": get_phase(before, before.fullmove_number), "color": color,
            "time_class": game["time_class"], "time_control": pgn.headers.get("TimeControl", "?"),
            "loss": loss, "type": kind,
            "time_left": time_left, "time_spent": time_spent, "time_bucket": bucket,
        })
 
        if kind != "ok":
            best = infos[i].get("pv", [None])[0]
            mistakes.append({
                "game_url": game["url"], "move_no": before.fullmove_number,
                "phase": get_phase(before, before.fullmove_number), "color": color,
                "time_class": game["time_class"], "time_control": pgn.headers.get("TimeControl", "?"),
                "opening": opening_name(pgn),
                "played": before.san(mv), "best": before.san(best) if best else "",
                "loss": loss, "type": kind,
                "time_left": time_left, "time_spent": time_spent, "time_bucket": bucket,
                "fen_before": before.fen(),
            })
 
    game_row = {
        "url": game["url"], "color": color, "time_class": game["time_class"],
        "time_control": pgn.headers.get("TimeControl", "?"),
        "opening": opening_name(pgn), "result": result_score(game, color),
        "avg_loss": sum(losses) / len(losses) if losses else 0, "moves": len(losses),
    }
    return game_row, mistakes, own_moves
 
 
def build_summary(games_df, mistakes_df, moves_df):
    out = []
    out.append(f"Partiyalar soni: {len(games_df)}")
    out.append(f"Umumiy natija (1=yutuq, 0.5=durang): {games_df['result'].mean():.2f}")
    out.append(f"O'rtacha centipawn yo'qotish: {games_df['avg_loss'].mean():.1f}\n")
 
    out.append("== Rang bo'yicha ==")
    out.append(games_df.groupby("color").agg(
        partiya=("url", "count"), natija=("result", "mean"), yoqotish=("avg_loss", "mean")
    ).round(2).to_string())
 
    out.append("\n== Vaqt rejimi bo'yicha (blitz/rapid/bullet) ==")
    out.append(games_df.groupby("time_class").agg(
        partiya=("url", "count"), natija=("result", "mean"), yoqotish=("avg_loss", "mean")
    ).round(2).to_string())
 
    if "time_control" in games_df.columns:
        out.append("\n== Aniq format bo'yicha (masalan 180+2 = 3 daqiqa + 2 soniya) ==")
        out.append(games_df.groupby("time_control").agg(
            partiya=("url", "count"), natija=("result", "mean"), yoqotish=("avg_loss", "mean")
        ).round(2).sort_values("partiya", ascending=False).to_string())
 
    if not mistakes_df.empty:
        out.append("\n== Xatolar: bosqich va turi bo'yicha ==")
        out.append(pd.crosstab(mistakes_df["phase"], mistakes_df["type"]).to_string())
 
    out.append("\n== Debyutlar (kamida 3 partiya) ==")
    op = games_df.groupby(["color", "opening"]).agg(
        partiya=("url", "count"), natija=("result", "mean"), yoqotish=("avg_loss", "mean")
    ).round(2)
    op = op[op["partiya"] >= 3].sort_values("natija")
    out.append(op.to_string() if not op.empty else "Yetarli ma'lumot yo'q")
 
    # ---- VAQT TAHLILI ----
    if not moves_df.empty and moves_df["time_left"].notna().any():
        out.append("\n\n========== VAQT TAHLILI ==========")
        mv = moves_df.copy()
        mv["time_bucket"] = pd.Categorical(mv["time_bucket"], categories=TIME_BUCKET_ORDER, ordered=True)
 
        out.append("\n== Har bir vaqt oralig'ida nechta yurish qilingan va shundan necha foizi xato/blunder ==")
        agg = mv.groupby("time_bucket", observed=True).agg(
            yurishlar=("loss", "count"),
            xato_foiz=("type", lambda s: (s != "ok").mean() * 100),
            blunder_foiz=("type", lambda s: (s == "blunder").mean() * 100),
            ortacha_yoqotish=("loss", "mean"),
        ).round(1)
        out.append(agg.reindex(TIME_BUCKET_ORDER).dropna(how="all").to_string())
 
        known = mv[mv["time_left"].notna()]
        if not known.empty:
            low_time = known[known["time_left"] < 30]
            rest = known[known["time_left"] >= 30]
            out.append("\n== Xulosa: 30 soniyadan kam vaqt qolganda vs qolmaganda ==")
            out.append(f"  <30s   : {len(low_time):5d} yurish, blunder foizi {((low_time['type']=='blunder').mean()*100 if len(low_time) else 0):.1f}%, o'rtacha yo'qotish {low_time['loss'].mean() if len(low_time) else 0:.1f}")
            out.append(f"  >=30s  : {len(rest):5d} yurish, blunder foizi {((rest['type']=='blunder').mean()*100 if len(rest) else 0):.1f}%, o'rtacha yo'qotish {rest['loss'].mean() if len(rest) else 0:.1f}")
 
    return "\n".join(out)
 
 
def main():
    print("Partiyalar yuklanmoqda...")
    games = fetch_games()
    print(f"{len(games)} ta partiya topildi. Tahlil boshlandi (biroz vaqt oladi)...")
 
    game_rows, all_mistakes, all_own_moves = [], [], []
    with chess.engine.SimpleEngine.popen_uci(STOCKFISH_PATH) as engine:
        for i, g in enumerate(games, 1):
            try:
                row, mistakes, own_moves = analyze_game(engine, g)
                game_rows.append(row)
                all_mistakes.extend(mistakes)
                all_own_moves.extend(own_moves)
            except Exception as e:
                print(f"  ! partiya o'tkazib yuborildi ({g.get('url')}): {e}")
            print(f"  {i}/{len(games)}", end="\r")
 
    games_df = pd.DataFrame(game_rows)
    mistakes_df = pd.DataFrame(all_mistakes)
    moves_df = pd.DataFrame(all_own_moves)
 
    games_df.to_csv("games.csv", index=False)
    mistakes_df.to_csv("mistakes.csv", index=False)
    moves_df.to_csv("moves.csv", index=False)
 
    summary = build_summary(games_df, mistakes_df, moves_df)
    with open("summary.txt", "w", encoding="utf-8") as f:
        f.write(summary)
 
    print("\n\n" + summary)
    print("\nSaqlandi: games.csv, mistakes.csv, moves.csv, summary.txt")
 
 
if __name__ == "__main__":
    main()
 






