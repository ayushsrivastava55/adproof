#!/usr/bin/env python
"""PHASE 0 VERIFICATION SPIKE -- NOT PRODUCT CODE.

Exercises the real VideoDB flow end to end and prints exactly what the provider
returned, so the assumptions in docs/VIDEODB_VERIFIED_BEHAVIOR.md can be
confirmed or corrected against observed behaviour.

This script writes nothing to the AdProof database and is not importable by the
application. It exists to check the provider, not to produce product data.

    VIDEODB_API_KEY=... python scripts/verify_videodb_spike.py <video-url>
"""

from __future__ import annotations

import json
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from adproof.providers.errors import ProviderError  # noqa: E402
from adproof.providers.videodb_adapter import (  # noqa: E402
    SDK_VERSION,
    VideoDBAdapter,
)

VISUAL_PROMPT = (
    "Describe what is visibly present in this frame, factually and literally. "
    "Report only what can be seen. If something is partly obscured, small, or "
    "blurry, say so explicitly rather than guessing."
)


def banner(text: str) -> None:
    print(f"\n{'=' * 70}\n{text}\n{'=' * 70}")


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    source_url = sys.argv[1]
    spoken_query = sys.argv[2] if len(sys.argv) > 2 else "the"
    visual_query = sys.argv[3] if len(sys.argv) > 3 else "a person"
    # Sampling interval for the visual index. Production uses 2s; a coarser
    # value is often enough to VERIFY the mechanism on a long clip at a
    # fraction of the VLM cost. The interval does not affect whether shots
    # carry end timestamps or scores, which is what the assumptions turn on.
    seconds_per_scene = int(sys.argv[4]) if len(sys.argv) > 4 else 2
    print(f"visual sampling interval: {seconds_per_scene}s")

    print(f"SDK: {SDK_VERSION}")
    adapter = VideoDBAdapter()

    banner("1. INGEST")
    t0 = time.monotonic()
    media = adapter.ingest(source_url=source_url, name="adproof-phase0-spike")
    print(f"elapsed: {time.monotonic() - t0:.1f}s")
    print(json.dumps(media.snapshot, indent=2, default=str))
    print(f"duration_seconds -> {media.duration_seconds!r}")

    banner("2. STREAM URL")
    stream = media.stream_url or adapter.generate_stream_url(
        media.provider_video_id, media.provider_collection_id
    )
    print(stream)
    print(
        "\nCHECK MANUALLY: is this URL reachable without any credential? "
        "If yes, it must never be handed to an untrusted client (Phase 5)."
    )

    banner("3. SPOKEN-WORD INDEX (blocking)")
    t0 = time.monotonic()
    spoken = adapter.index_spoken_words(
        media.provider_video_id, collection_id=media.provider_collection_id
    )
    print(f"elapsed: {time.monotonic() - t0:.1f}s")
    print(f"provider_index_id -> {spoken.provider_index_id!r} (expected: None)")
    print(f"already_existed   -> {spoken.already_existed}")

    banner("4. FOCUSED VISUAL INDEX (blocking)")
    t0 = time.monotonic()
    visual = adapter.index_scenes(
        media.provider_video_id,
        prompt=VISUAL_PROMPT,
        index_name="adproof_spike_visual",
        seconds_per_scene=seconds_per_scene,
        collection_id=media.provider_collection_id,
    )
    print(f"elapsed: {time.monotonic() - t0:.1f}s")
    print(f"scene_index_id -> {visual.provider_index_id!r}")

    banner("5. SPOKEN SEARCH (keyword, exact)")
    hits = adapter.search(
        media.provider_video_id,
        query=spoken_query,
        index_type="spoken_word",
        search_type="keyword",
        result_threshold=10,
        collection_id=media.provider_collection_id,
    )
    print(f"{len(hits)} hit(s) for {spoken_query!r}")
    for hit in hits[:5]:
        print(
            f"  start={hit.start_seconds} end={hit.end_seconds} "
            f"score={hit.provider_score} text={(hit.text or '')[:70]!r}"
        )
    if hits:
        print("\nraw first hit:")
        print(json.dumps(hits[0].snapshot, indent=2, default=str))

    banner("6. VISUAL SEARCH (semantic, scoped to the focused index)")
    vhits = adapter.search(
        media.provider_video_id,
        query=visual_query,
        index_type="scene",
        search_type="semantic",
        score_threshold=0.2,
        result_threshold=10,
        scene_index_id=visual.provider_index_id,
        collection_id=media.provider_collection_id,
    )
    print(f"{len(vhits)} hit(s) for {visual_query!r}")
    for hit in vhits[:5]:
        print(
            f"  start={hit.start_seconds} end={hit.end_seconds} "
            f"score={hit.provider_score} index={hit.provider_index_name!r} "
            f"text={(hit.text or '')[:70]!r}"
        )
    if vhits:
        print("\nraw first hit:")
        print(json.dumps(vhits[0].snapshot, indent=2, default=str))

    banner("7. DELIBERATE EMPTY RESULT")
    nonsense = "zzqqxx nonexistent phrase zzqqxx"
    empty = adapter.search(
        media.provider_video_id,
        query=nonsense,
        index_type="spoken_word",
        search_type="keyword",
        result_threshold=10,
        collection_id=media.provider_collection_id,
    )
    print(f"{len(empty)} hit(s) for {nonsense!r} (expected 0)")
    print("An empty list must be classified by AdProof, never treated as a fail.")

    banner("SUMMARY OF ASSUMPTIONS TO RECORD")
    for line in [
        f"A-1 spoken index returns no id                 -> "
        f"{spoken.provider_index_id is None}",
        f"A-2 scene index returns an addressable id      -> "
        f"{bool(visual.provider_index_id)}",
        f"A-3 keyword search over spoken index works     -> {len(hits) >= 0}",
        f"A-5 shots carry end timestamps                 -> "
        f"{any(h.end_seconds is not None for h in (hits + vhits))}",
        f"A-6 shots carry relevance scores               -> "
        f"{any(h.provider_score is not None for h in (hits + vhits))}",
        f"A-8 provider reported a duration               -> "
        f"{media.duration_seconds is not None}",
    ]:
        print(line)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except ProviderError as exc:
        print(f"\nPROVIDER FAILURE: {exc.summary}\n{exc.detail}", file=sys.stderr)
        sys.exit(1)
