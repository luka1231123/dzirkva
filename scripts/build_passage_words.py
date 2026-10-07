"""Bounded word-index backfill over existing passages. No model, downloads, or vectors required."""

import argparse

from dzirkva.passages import build_word_index, connect


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=20_000, help="maximum old passages per run")
    parser.add_argument("--batch", type=int, default=1000)
    args = parser.parse_args()
    if args.limit < 1 or args.batch < 1:
        parser.error("limit and batch must be positive")
    with connect() as db:
        added, complete = build_word_index(db, min(args.batch, 5000), args.limit)
    print(f"passage word index: +{added:,}; backfill {'complete' if complete else 'pending'}", flush=True)


if __name__ == "__main__":
    main()
