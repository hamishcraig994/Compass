"""Command line version.

  python3 cli.py --sample            try it with made-up data
  python3 cli.py                     use your Plex + TMDB (needs PLEX_TOKEN and TMDB_TOKEN)
  python3 cli.py --type tv --limit 5      (or --type movie, or --type new for new & trending)
  python3 cli.py --profile           also show what it thinks your taste is"""
import argparse
import sys

import config
import profile
import sources


def main(argv=None):
    parser = argparse.ArgumentParser(description="Recommend movies and TV shows you don't have yet.")
    parser.add_argument("--sample", action="store_true", help="use made-up sample data instead of Plex/TMDB")
    parser.add_argument("--type", choices=["movie", "tv", "new"], help="only movies, only TV shows, or only new & trending")
    parser.add_argument("--limit", type=int, default=10)
    parser.add_argument("--profile", action="store_true", help="show your taste profile")
    args = parser.parse_args(argv)

    if not args.sample and not config.live_configured():
        sys.exit("PLEX_TOKEN and TMDB_TOKEN aren't set (see README.md). Use --sample to try it without them.")
    try:
        result = sources.run(sample_mode=args.sample)
    except Exception as e:
        sys.exit(f"Couldn't get recommendations: {e}")

    for note in result["notes"]:
        print(f"note: {note}")
    print(f"Based on {result['watched_count']} watched titles.\n")
    if args.profile:
        for category, names in profile.summary(result["profile"]).items():
            print(f"  {category:9} {', '.join(names) or '-'}")
        print()
    items = [i for i in result["items"] if args.type is None
             or (i["new"] or i["trending"] if args.type == "new" else i["media_type"] == args.type)][:args.limit]
    for n, item in enumerate(items, 1):
        kind = "movie" if item["media_type"] == "movie" else "TV"
        badges = "".join(f" [{b}]" for b, on in (("NEW", item["new"]), ("TRENDING", item["trending"])) if on)
        print(f"{n:2}. {item['match']:2}%  {item['title']} ({item['year']}) [{kind}]{badges}")
        print(f"         {item['reason']}")
        if item["matches"]:
            print(f"         {' | '.join(item['matches'])}")
    if not items:
        print("No recommendations found.")


if __name__ == "__main__":
    main()
