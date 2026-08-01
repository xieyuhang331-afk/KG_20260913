from __future__ import annotations

import asyncio
import importlib.metadata
import inspect
import multiprocessing
import statistics
import time
import tracemalloc
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from uuid_utils.compat import uuid7


RFC_9562_V7_VECTOR = uuid.UUID("017f22e2-79b0-7cc3-98c4-dc0c0c07398f")
RFC_9562_VECTOR_UNIX_MS = 1_645_557_742_000


def _generate_batch(size: int, timestamp: int | None = None, nanos: int | None = None) -> list[int]:
    return [uuid7(timestamp, nanos).int for _ in range(size)]


def _assert_uuid7(values: list[int], expected_size: int) -> None:
    assert len(values) == expected_size
    assert len(set(values)) == expected_size
    for value in values[:10_000]:
        generated = uuid.UUID(int=value)
        assert generated.version == 7
        assert generated.variant == uuid.RFC_4122


def test_compat_boundary_returns_standard_library_uuid():
    generated = uuid7()

    assert type(generated) is uuid.UUID
    assert generated.version == 7
    assert generated.variant == uuid.RFC_4122


def test_rfc_9562_vector_and_controlled_timestamp_layout():
    assert RFC_9562_V7_VECTOR.version == 7
    assert RFC_9562_V7_VECTOR.variant == uuid.RFC_4122
    assert RFC_9562_V7_VECTOR.int >> 80 == RFC_9562_VECTOR_UNIX_MS

    generated = uuid7(timestamp=1_645_557_742, nanos=0)

    assert generated.int >> 80 == RFC_9562_VECTOR_UNIX_MS
    assert generated.version == RFC_9562_V7_VECTOR.version
    assert generated.variant == RFC_9562_V7_VECTOR.variant


def test_canonical_string_bytes_and_integer_round_trip():
    generated = uuid7()

    assert str(uuid.UUID(str(generated))) == str(generated)
    assert uuid.UUID(bytes=generated.bytes) == generated
    assert uuid.UUID(int=generated.int) == generated
    assert str(generated) == str(generated).lower()
    assert len(str(generated)) == 36


def test_single_process_one_million_are_unique():
    started = time.perf_counter()
    values = _generate_batch(1_000_000)
    duration = time.perf_counter() - started

    _assert_uuid7(values, 1_000_000)
    print(
        "single_process_count=1000000 "
        f"single_process_seconds={duration:.6f} "
        f"single_process_per_second={1_000_000 / duration:.2f}"
    )


def test_same_millisecond_one_hundred_thousand_are_unique():
    values = _generate_batch(100_000, timestamp=1_645_557_742, nanos=0)

    _assert_uuid7(values, 100_000)
    assert {value >> 80 for value in values} == {RFC_9562_VECTOR_UNIX_MS}


def test_multithreaded_one_million_are_unique():
    with ThreadPoolExecutor(max_workers=8) as executor:
        batches = list(executor.map(_generate_batch, [125_000] * 8))

    values = [value for batch in batches for value in batch]
    _assert_uuid7(values, 1_000_000)


def test_spawn_and_fork_processes_one_million_each_are_unique():
    methods = ["spawn"]
    if "fork" in multiprocessing.get_all_start_methods():
        methods.append("fork")

    for method in methods:
        context = multiprocessing.get_context(method)
        with context.Pool(processes=4) as pool:
            batches = pool.map(_generate_batch, [250_000] * 4)
        values = [value for batch in batches for value in batch]
        _assert_uuid7(values, 1_000_000)


def test_async_one_hundred_thousand_invocations_are_unique():
    async def generate_one() -> int:
        return uuid7().int

    async def generate_all() -> list[int]:
        return await asyncio.gather(*(generate_one() for _ in range(100_000)))

    values = asyncio.run(generate_all())
    _assert_uuid7(values, 100_000)


def test_clock_rollback_and_recovery_do_not_duplicate():
    values = []
    for timestamp in (1_700_000_000, 1_699_999_999, 1_700_000_001):
        values.extend(_generate_batch(100_000, timestamp=timestamp, nanos=0))

    _assert_uuid7(values, 300_000)


def test_random_region_has_no_obvious_fixed_bit_bias():
    values = _generate_batch(100_000, timestamp=1_645_557_742, nanos=0)
    random_mask = (1 << 62) - 1
    one_bits = sum((value & random_mask).bit_count() for value in values)
    ratio = one_bits / (len(values) * 62)

    assert 0.48 <= ratio <= 0.52


def test_latency_cpu_and_memory_benchmark():
    samples = 50_000
    latencies = []
    tracemalloc.start()
    cpu_started = time.process_time()
    wall_started = time.perf_counter()
    for _ in range(samples):
        started = time.perf_counter_ns()
        uuid7()
        latencies.append(time.perf_counter_ns() - started)
    wall_duration = time.perf_counter() - wall_started
    cpu_duration = time.process_time() - cpu_started
    _, peak_memory = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    quantiles = statistics.quantiles(latencies, n=100)

    print(
        f"benchmark_count={samples} "
        f"latency_p50_ns={statistics.median(latencies):.0f} "
        f"latency_p95_ns={quantiles[94]:.0f} "
        f"latency_p99_ns={quantiles[98]:.0f} "
        f"cpu_seconds={cpu_duration:.6f} "
        f"wall_seconds={wall_duration:.6f} "
        f"peak_memory_bytes={peak_memory}"
    )


def test_generator_signature_exposes_only_time_inputs():
    assert list(inspect.signature(uuid7).parameters) == ["timestamp", "nanos"]


def test_distribution_metadata_and_sbom_are_present():
    distribution = importlib.metadata.distribution("uuid-utils")
    metadata = distribution.metadata
    sbom = Path(distribution.locate_file("uuid_utils-0.17.0.dist-info/sboms/uuid-utils.cyclonedx.json"))

    assert distribution.version == "0.17.0"
    assert metadata["License-Expression"] == "BSD-3-Clause"
    assert distribution.requires is None
    assert sbom.is_file()
    assert '"bomFormat": "CycloneDX"' in sbom.read_text(encoding="utf-8")
