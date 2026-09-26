"""Command line version.

  python3 cli.py --sample            try it with made-up data
  python3 cli.py                     use your Plex + TMDB (needs PLEX_TOKEN and TMDB_TOKEN)
  python3 cli.py --type tv --limit 5      (or --type movie, or --type new for new & trending)
  python3 cli.py --profile           also show what it thinks your taste is

To use Tautulli instead of Plex for watch history, set HISTORY_SOURCE=tautulli plus
TAUTULLI_URL and TAUTULLI_API_KEY (see .env.example), then check it against your real
server before trusting it:
  python3 cli.py --tautulli-probe    print raw Tautulli data, unprocessed"""
import argparse
import json
import sys

import config
import profile
import sources
import tautulli


def main(argv=None):
    parser = argparse.ArgumentParser(description="Recommend movies and TV shows you don't have yet.")
    parser.add_argument("--sample", action="store_true", help="use made-up sample data instead of Plex/TMDB")
    parser.add_argument("--type", choices=["movie", "tv", "new"], help="only movies, only TV shows, or only new & trending")
    parser.add_argument("--limit", type=int, default=10)
    parser.add_argument("--profile", action="store_true", help="show your taste profile")
    parser.add_argument("--tautulli-probe", action="store_true",
                        help="print raw Tautulli history/metadata (for checking field names against your server)")
    args = parser.parse_args(argv)

    if args.tautulli_probe:
        if not config.tautulli_configured():
            sys.exit("TAUTULLI_URL and TAUTULLI_API_KEY aren't set (see .env.example).")
        client = tautulli.TautulliClient(config.TAUTULLI_URL, config.TAUTULLI_API_KEY, config.TAUTULLI_USER)
        try:
            print(json.dumps(client.raw_probe(), indent=2, default=str))
        except Exception as e:
            sys.exit(f"Couldn't reach Tautulli: {e}")
        return

    if not args.sample and not config.live_configured():
        sys.exit("Watch-history source and TMDB_TOKEN aren't set (see README.md). Use --sample to try it without them.")
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
