# engine/evaluate.pyx
# Cython evaluation: material + PST loops typés
# Compile this with setup.py (module name engine.evaluate)

cimport cython
from libc.stdint cimport int32_t
import chess

# Typed piece values
cdef int PIECE_MG[7]
PIECE_MG[0] = 0
PIECE_MG[1] = 100
PIECE_MG[2] = 320
PIECE_MG[3] = 330
PIECE_MG[4] = 500
PIECE_MG[5] = 960
PIECE_MG[6] = 0

# We'll embed PST arrays as Python lists for simplicity but access them via typed ints
# (You can copy your full _PST_MG/_PST_EG tables here for full fidelity)
cdef int PST_MG_1[64]
cdef int PST_MG_2[64]
cdef int PST_MG_3[64]
cdef int PST_MG_4[64]
cdef int PST_MG_5[64]
cdef int PST_MG_6[64]

# Initialize PST to zeros (you can paste your real tables here)
for i in range(64):
    PST_MG_1[i] = 0
    PST_MG_2[i] = 0
    PST_MG_3[i] = 0
    PST_MG_4[i] = 0
    PST_MG_5[i] = 0
    PST_MG_6[i] = 0

@cython.boundscheck(False)
@cython.wraparound(False)
def evaluate(board):
    """
    evaluate(board) -> int
    Fast material + PST evaluation. Accepts chess.Board.
    """
    cdef int mg = 0
    cdef int eg = 0  # kept for compatibility if needed
    cdef int sq
    cdef object it

    # White pieces
    # Pawns
    it = board.pieces(1, True)
    for sq in it:
        mg += PIECE_MG[1] + PST_MG_1[sq]
    # Knights
    it = board.pieces(2, True)
    for sq in it:
        mg += PIECE_MG[2] + PST_MG_2[sq]
    # Bishops
    it = board.pieces(3, True)
    for sq in it:
        mg += PIECE_MG[3] + PST_MG_3[sq]
    # Rooks
    it = board.pieces(4, True)
    for sq in it:
        mg += PIECE_MG[4] + PST_MG_4[sq]
    # Queens
    it = board.pieces(5, True)
    for sq in it:
        mg += PIECE_MG[5] + PST_MG_5[sq]

    # Black pieces (mirror squares)
    it = board.pieces(1, False)
    for sq in it:
        mg -= PIECE_MG[1] + PST_MG_1[sq ^ 56]
    it = board.pieces(2, False)
    for sq in it:
        mg -= PIECE_MG[2] + PST_MG_2[sq ^ 56]
    it = board.pieces(3, False)
    for sq in it:
        mg -= PIECE_MG[3] + PST_MG_3[sq ^ 56]
    it = board.pieces(4, False)
    for sq in it:
        mg -= PIECE_MG[4] + PST_MG_4[sq ^ 56]
    it = board.pieces(5, False)
    for sq in it:
        mg -= PIECE_MG[5] + PST_MG_5[sq ^ 56]

    return mg
