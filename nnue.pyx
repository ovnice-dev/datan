# engine/nnue.pyx
# cython: language_level=3
"""
NNUE wrapper minimal en Cython (corrigé).
Stocke les numpy arrays comme objets, puis crée des np.ndarray typés
avant de construire des memoryviews locaux pour les calculs.
"""

from time import perf_counter
cimport cython
import numpy as np
cimport numpy as np

ctypedef np.float32_t FLOAT32_t

cdef class NNUE:
    # stocke les arrays comme objets Python (numpy.ndarray)
    cdef public object npz_path
    cdef bint loaded
    cdef public object w1_obj
    cdef public object b1_obj
    cdef public object w2_obj
    cdef public object b2_obj
    cdef double last_eval_time_s
    cdef long n_evals

    def __cinit__(self, npz_path):
        self.npz_path = npz_path
        self.loaded = False
        self.w1_obj = None
        self.b1_obj = None
        self.w2_obj = None
        self.b2_obj = None
        self.last_eval_time_s = 0.0
        self.n_evals = 0
        try:
            self._load_npz(npz_path)
            self.loaded = True
        except Exception:
            self.loaded = False

    cpdef void _load_npz(self, object npz_path):
        cdef object data = np.load(npz_path, allow_pickle=False)
        # charge et force float32 + contiguous
        self.w1_obj = np.ascontiguousarray(np.asarray(data["w1"], dtype=np.float32))
        self.b1_obj = np.ascontiguousarray(np.asarray(data["b1"], dtype=np.float32))
        self.w2_obj = np.ascontiguousarray(np.asarray(data["w2"], dtype=np.float32))
        self.b2_obj = np.ascontiguousarray(np.asarray(data["b2"], dtype=np.float32))

    cpdef dict get_runtime_stats(self):
        if self.n_evals == 0:
            nps = 0
        else:
            nps = <int>(self.n_evals / max(self.last_eval_time_s, 1e-6))
        return {"nps": nps, "ponder": False, "book_move": None, "nnue": bool(self.loaded)}

    cpdef str simple_test(self):
        if not self.loaded:
            return "NNUE not loaded"
        try:
            shape0 = int(self.w1_obj.shape[0])
            shape1 = int(self.w1_obj.shape[1])
            return f"NNUE OK ({shape0}x{shape1})"
        except Exception:
            return "NNUE loaded (shape unknown)"

    cpdef float evaluate_fen(self, str fen):
        """
        Placeholder d'évaluation NNUE.
        On convertit d'abord les objets numpy en np.ndarray typés locaux,
        puis on crée des memoryviews pour les boucles C.
        """
        if not self.loaded:
            raise RuntimeError("NNUE not loaded")

        cdef double t0 = perf_counter()
        import chess
        board = chess.Board(fen)

        # build minimal feature vector (counts of 12 piece types)
        cdef int feat_len = int(np.asarray(self.w1_obj).shape[0])
        feats = np.zeros(feat_len, dtype=np.float32)

        # counts 12
        counts = np.zeros(12, dtype=np.float32)
        for sq, piece in board.piece_map().items():
            idx = (0 if piece.color else 6) + (piece.piece_type - 1)
            if 0 <= idx < 12:
                counts[idx] += 1.0
        n = counts.shape[0] if counts.shape[0] < feat_len else feat_len
        feats[:n] = counts[:n]

        # --- IMPORTANT : convertir object -> np.ndarray typé localement ---
        # on crée d'abord des np.ndarray typés, puis on obtient des memoryviews
        cdef np.ndarray[FLOAT32_t, ndim=1] feats_arr = np.asarray(feats, dtype=np.float32)
        cdef np.ndarray[FLOAT32_t, ndim=2] w1_arr = np.asarray(self.w1_obj, dtype=np.float32)
        cdef np.ndarray[FLOAT32_t, ndim=1] b1_arr = np.asarray(self.b1_obj, dtype=np.float32)
        cdef np.ndarray[FLOAT32_t, ndim=2] w2_arr = np.asarray(self.w2_obj, dtype=np.float32)
        cdef np.ndarray[FLOAT32_t, ndim=1] b2_arr = np.asarray(self.b2_obj, dtype=np.float32)

        # maintenant on peut créer des memoryviews à partir des np.ndarray typés
        cdef FLOAT32_t[:] feats_view = feats_arr
        cdef FLOAT32_t[:, :] w1_view = w1_arr
        cdef FLOAT32_t[:] b1_view = b1_arr
        cdef FLOAT32_t[:, :] w2_view = w2_arr
        cdef FLOAT32_t[:] b2_view = b2_arr

        # forward: hidden = feats @ w1 + b1  (boucles C)
        cdef int hdim = w1_view.shape[1]
        cdef np.ndarray[FLOAT32_t, ndim=1] hidden = np.empty(hdim, dtype=np.float32)
        cdef int i, j
        cdef double acc

        for j in range(hdim):
            acc = 0.0
            for i in range(feats_view.shape[0]):
                acc += feats_view[i] * w1_view[i, j]
            acc += b1_view[j]
            if acc < 0.0:
                acc = 0.0
            hidden[j] = <FLOAT32_t> acc

        # hidden @ w2 + b2 -> sum to scalar
        cdef int odim = w2_view.shape[1] if w2_view.ndim == 2 else 1
        cdef double out_sum = 0.0
        for j in range(odim):
            acc = 0.0
            for i in range(hdim):
                acc += hidden[i] * w2_view[i, j]
            acc += b2_view[j]
            out_sum += acc

        self.n_evals += 1
        self.last_eval_time_s = perf_counter() - t0
        return float(out_sum)

    cpdef void shutdown(self):
        self.loaded = False
        self.w1_obj = None
        self.w2_obj = None
        self.b1_obj = None
        self.b2_obj = None
