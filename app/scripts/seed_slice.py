#!/usr/bin/env python
"""Create one campaign and one submission against a running API.

This seeds INPUT (a campaign, rules, a media URL). It creates no results, no
evidence, and no timestamps: everything downstream comes from real processing
by the worker.

    python scripts/seed_slice.py <video-url> [--phrase AYUSH20]
                                             [--concept "PulseBar package"]
                                             [--seconds 6]
"""

from __future__ import annotations

import argparse
import json
import urllib.request
import uuid

DEFAULT_API = "http://127.0.0.1:8000"


def post(api: str, path: str, payload: dict) -> dict:
    request = urllib.request.Request(
        f"{api}{path}",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request) as response:
        return json.loads(response.read())


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("video_url")
    parser.add_argument("--api", default=DEFAULT_API)
    parser.add_argument("--phrase", default="AYUSH20")
    parser.add_argument("--concept", default="PulseBar protein bar package")
    parser.add_argument("--seconds", type=float, default=6.0)
    parser.add_argument("--creator", default="creator-001")
    parser.add_argument("--confirmed-by", default="campaign.manager@example.com")
    args = parser.parse_args()

    campaign = post(
        args.api,
        "/api/campaigns",
        {
            "campaign_name": "PulseBar launch",
            "brief_text": (
                f"The creator must clearly state the discount code {args.phrase}. "
                f"The {args.concept} must be clearly visible for at least "
                f"{args.seconds:g} seconds."
            ),
            "confirmed_by": args.confirmed_by,
            "rules": [
                {
                    "rule_type": "required_spoken_phrase",
                    "requirement_text": (
                        f'Creator must state the discount code "{args.phrase}"'
                    ),
                    "source_brief_excerpt": (
                        f"must clearly state the discount code {args.phrase}"
                    ),
                    "phrase": args.phrase,
                    "min_occurrences": 1,
                    # Exact keyword lookup over a completed transcript is the
                    # one case where absence may fail, and only then.
                    "absence_policy": "fail_when_coverage_complete",
                },
                {
                    "rule_type": "min_visual_duration",
                    "requirement_text": (
                        f"{args.concept} visible for at least {args.seconds:g} seconds"
                    ),
                    "source_brief_excerpt": (
                        f"must be clearly visible for at least {args.seconds:g} seconds"
                    ),
                    "visual_concept": args.concept,
                    "min_duration_seconds": args.seconds,
                    "score_threshold": 0.2,
                    # A semantic visual miss is never strong enough to fail.
                    "absence_policy": "uncertain",
                },
            ],
        },
    )
    print(f"campaign      {campaign['campaign_id']}")

    submission = post(
        args.api,
        "/api/submissions",
        {
            "campaign_id": campaign["campaign_id"],
            "creator_reference": args.creator,
            "submitted_by": args.confirmed_by,
            "idempotency_key": f"seed-{uuid.uuid4()}",
            "source_url": args.video_url,
        },
    )
    print(f"submission    {submission['submission_id']}")
    print("\nStart the worker to process it:")
    print("  VIDEODB_API_KEY=... python -m adproof.orchestrator.worker")
    print(f"\nThen open {args.api}/ and select the submission.")


if __name__ == "__main__":
    main()
