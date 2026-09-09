"""M6 deterministic end-to-end evaluation corpus.

This package builds AIRCheck's frozen scenario corpus and drives each scenario
through the real M4 headless runtime with a deterministic typed semantic double.
Every stage is graded by deterministic comparison against typed ground truth that
is frozen *before* a scenario's first corpus execution. There is no LLM judge:
the stochastic model is measured, never trusted to grade itself.

The doctrine is unchanged: prose -> predicates -> facts -> authority -> evidence.
The corpus makes each failure attributable to the first divergent stage.
"""

from __future__ import annotations

CORPUS_VERSION = "m6_corpus_v1"

__all__ = ("CORPUS_VERSION",)
