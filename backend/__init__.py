"""Claim adjudication backend package."""
import os

# The knowledge layer multiplies many small matrices; multi-threaded BLAS
# thread start-up dominates at this size (measured ~100x slower), so pin BLAS
# to one thread before numpy is first imported. Operators can override.
for _var in ("OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS", "OMP_NUM_THREADS"):
    os.environ.setdefault(_var, "1")
