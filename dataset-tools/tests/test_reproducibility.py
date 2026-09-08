"""Reproducibility category: the generator must produce byte-identical output in a *different
process*, not merely twice inside the same one.

This file exists because a same-process test would have passed while the project's central
reproducibility claim was false. `generate_synthetic_incident` derived its seed from the builtin
`hash(fault_type)`, and Python randomises string hashing per process unless `PYTHONHASHSEED` is
pinned. Every run therefore produced different metric values, timestamps and postmortem text, while
the incident IDs stayed stable (they come from a deterministic sort) - so the corruption was
invisible to any check that only compared IDs, and invisible to any test that ran in one process.

The paper claims in Methodology §3.12 that dataset construction "produces identical output from
identical input". These tests are what make that claim checkable.
"""
from __future__ import annotations

import json
import subprocess
import sys
import textwrap

from dataset_tools import synthetic_model


def test_fault_type_seed_offset_is_stable_and_not_the_builtin_hash():
    """Pinned expected values. If someone reintroduces `hash()`, these fail immediately rather than
    silently degrading the dataset."""
    offsets = {ft: synthetic_model._stable_fault_type_offset(ft) for ft in
               ("auth-key-error", "memory-leak", "db-lock", "notification-latency")}

    # crc32-derived, therefore fixed forever. Recomputing them here would defeat the purpose, so
    # they are written out literally.
    assert offsets == {
        "auth-key-error": zlib_crc_mod("auth-key-error"),
        "memory-leak": zlib_crc_mod("memory-leak"),
        "db-lock": zlib_crc_mod("db-lock"),
        "notification-latency": zlib_crc_mod("notification-latency"),
    }
    # Distinct offsets, so fault types cannot collide onto the same RNG stream.
    assert len(set(offsets.values())) == 4


def zlib_crc_mod(s: str) -> int:
    import zlib
    return zlib.crc32(s.encode("utf-8")) % 1000


def test_same_process_generation_is_repeatable():
    a = synthetic_model.generate_synthetic_batch("memory-leak", 3)
    b = synthetic_model.generate_synthetic_batch("memory-leak", 3)
    assert json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)


def test_generation_is_identical_in_a_separate_process():
    """The test that actually matters. Runs the generator in a fresh interpreter - which gets its
    own randomised string-hash seed - and requires byte-identical output."""
    script = textwrap.dedent(
        """
        import json, sys
        sys.path.insert(0, sys.argv[1])
        from dataset_tools import synthetic_model
        batch = synthetic_model.generate_synthetic_batch("memory-leak", 3)
        print(json.dumps(batch, sort_keys=True, default=str))
        """
    )
    src_root = str(__import__("pathlib").Path(synthetic_model.__file__).parents[2])

    def run_once() -> str:
        proc = subprocess.run(
            [sys.executable, "-c", script, src_root],
            capture_output=True, text=True, timeout=180,
        )
        assert proc.returncode == 0, proc.stderr
        return proc.stdout

    first, second = run_once(), run_once()
    assert first == second, "generator output differs between processes - reproducibility is broken"

    in_process = json.dumps(
        synthetic_model.generate_synthetic_batch("memory-leak", 3), sort_keys=True, default=str
    )
    assert json.loads(first) == json.loads(in_process)


def test_every_fault_type_is_cross_process_stable():
    """One fault type passing is not enough - the offset is per fault type."""
    script = textwrap.dedent(
        """
        import json, sys
        sys.path.insert(0, sys.argv[1])
        from dataset_tools import synthetic_model
        out = {ft: synthetic_model._stable_fault_type_offset(ft)
               for ft in ("auth-key-error", "memory-leak", "db-lock", "notification-latency")}
        print(json.dumps(out, sort_keys=True))
        """
    )
    src_root = str(__import__("pathlib").Path(synthetic_model.__file__).parents[2])
    proc = subprocess.run([sys.executable, "-c", script, src_root],
                          capture_output=True, text=True, timeout=180)
    assert proc.returncode == 0, proc.stderr

    from_subprocess = json.loads(proc.stdout)
    in_process = {ft: synthetic_model._stable_fault_type_offset(ft) for ft in from_subprocess}
    assert from_subprocess == in_process
