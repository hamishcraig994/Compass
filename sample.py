"""Made-up sample data, so the app can be run and tested without Plex or a TMDB account.

Titles are real, but the ids (1xxx movies, 2xxx shows) and the tags/links are hand-written approximations,
NOT real TMDB data. The 'viewer' likes cerebral sci-fi and dark crime dramas."""
from datetime import datetime, timedelta, timezone

# id: (type, title, year, genres, keywords, directors, cast, rating, votes, recommendations)
_CATALOGUE = {
    1001: ("movie", "Interstellar", 2014, ["Science Fiction", "Adventure", "Drama"],
           ["space travel", "black hole", "time dilation", "wormhole", "father daughter relationship"],
           ["Christopher Nolan"], ["Matthew McConaughey", "Anne Hathaway", "Jessica Chastain"], 8.4, 36000, [1019, 1006, 1002, 1005, 1009, 1008]),
    1002: ("movie", "Arrival", 2016, ["Science Fiction", "Drama", "Mystery"],
           ["alien contact", "linguistics", "time perception", "grief"],
           ["Denis Villeneuve"], ["Amy Adams", "Jeremy Renner", "Forest Whitaker"], 7.6, 19000, [1003, 1007, 1004, 1009, 1023, 1019]),
    1003: ("movie", "Blade Runner 2049", 2017, ["Science Fiction", "Drama", "Thriller"],
           ["android", "dystopia", "artificial intelligence", "neo-noir", "future"],
           ["Denis Villeneuve"], ["Ryan Gosling", "Harrison Ford", "Ana de Armas"], 7.5, 15000, [1005, 1004, 1002, 1007, 2017]),
    1004: ("movie", "Ex Machina", 2014, ["Science Fiction", "Drama", "Thriller"],
           ["artificial intelligence", "android", "isolation", "turing test"],
           ["Alex Garland"], ["Alicia Vikander", "Domhnall Gleeson", "Oscar Isaac"], 7.6, 13000, [1007, 2017, 1003, 1009, 1002]),
    1005: ("movie", "Dune", 2021, ["Science Fiction", "Adventure"],
           ["desert", "prophecy", "space opera", "feudalism", "future"],
           ["Denis Villeneuve"], ["Timothée Chalamet", "Rebecca Ferguson", "Oscar Isaac"], 7.8, 12000, [1003, 2010]),
    1006: ("movie", "The Martian", 2015, ["Science Fiction", "Drama", "Adventure"],
           ["space travel", "survival", "mars", "astronaut"],
           ["Ridley Scott"], ["Matt Damon", "Jessica Chastain"], 7.7, 20000, [1019, 1001, 1008, 1005]),
    1007: ("movie", "Annihilation", 2018, ["Science Fiction", "Horror", "Mystery"],
           ["alien contact", "mutation", "expedition", "isolation"],
           ["Alex Garland"], ["Natalie Portman", "Jennifer Jason Leigh", "Oscar Isaac"], 6.8, 9000, [1004, 1002, 2017]),
    1008: ("movie", "Edge of Tomorrow", 2014, ["Action", "Science Fiction"],
           ["time loop", "alien invasion", "war", "soldier"],
           ["Doug Liman"], ["Tom Cruise", "Emily Blunt"], 7.6, 15000, [1020, 1022, 1023]),
    1009: ("movie", "Moon", 2009, ["Science Fiction", "Drama", "Mystery"],
           ["space travel", "clone", "isolation", "artificial intelligence"],
           ["Duncan Jones"], ["Sam Rockwell", "Kevin Spacey"], 7.6, 5000, [1004, 1002]),
    1010: ("movie", "Se7en", 1995, ["Crime", "Mystery", "Thriller"],
           ["serial killer", "detective", "murder", "neo-noir"],
           ["David Fincher"], ["Brad Pitt", "Morgan Freeman", "Kevin Spacey"], 8.4, 24000, [1011, 1017, 1012, 2013, 1014]),
    1011: ("movie", "Zodiac", 2007, ["Crime", "Mystery", "Thriller"],
           ["serial killer", "detective", "journalist", "obsession"],
           ["David Fincher"], ["Jake Gyllenhaal", "Mark Ruffalo", "Robert Downey Jr."], 7.7, 11000, [1010, 2013, 1017, 1013, 1012]),
    1012: ("movie", "Prisoners", 2013, ["Crime", "Drama", "Thriller"],
           ["kidnapping", "detective", "missing child", "murder"],
           ["Denis Villeneuve"], ["Hugh Jackman", "Jake Gyllenhaal"], 8.1, 14000, [1018, 1010, 1017, 1013, 1011]),
    1013: ("movie", "Nightcrawler", 2014, ["Crime", "Drama", "Thriller"],
           ["journalist", "obsession", "neo-noir", "los angeles"],
           ["Dan Gilroy"], ["Jake Gyllenhaal", "Rene Russo"], 7.8, 13000, [1014, 1010, 1017, 1011]),
    1014: ("movie", "Heat", 1995, ["Crime", "Drama", "Thriller", "Action"],
           ["heist", "detective", "los angeles", "obsession"],
           ["Michael Mann"], ["Al Pacino", "Robert De Niro", "Val Kilmer"], 7.9, 9500, [1010, 1018]),
    1015: ("movie", "Superbad", 2007, ["Comedy"],
           ["high school", "friendship", "teenager", "party"],
           ["Greg Mottola"], ["Jonah Hill", "Michael Cera"], 7.2, 7500, [1022, 1021]),
    1017: ("movie", "Gone Girl", 2014, ["Mystery", "Thriller", "Drama"],
           ["missing person", "marriage", "detective", "media", "murder"],
           ["David Fincher"], ["Ben Affleck", "Rosamund Pike"], 8.1, 21000, [1010, 1011, 1012]),
    1018: ("movie", "Sicario", 2015, ["Crime", "Action", "Thriller"],
           ["drug cartel", "fbi", "mexico", "murder"],
           ["Denis Villeneuve"], ["Emily Blunt", "Benicio del Toro", "Josh Brolin"], 7.6, 10000, [1012, 1014]),
    1019: ("movie", "Gravity", 2013, ["Science Fiction", "Thriller", "Drama"],
           ["space travel", "survival", "astronaut", "isolation", "grief"],
           ["Alfonso Cuarón"], ["Sandra Bullock", "George Clooney"], 7.2, 14000, [1006, 1001]),
    1020: ("movie", "Looper", 2012, ["Action", "Science Fiction", "Thriller"],
           ["time travel", "assassin", "future", "dystopia"],
           ["Rian Johnson"], ["Joseph Gordon-Levitt", "Bruce Willis", "Emily Blunt"], 7.1, 11000, [1008, 1023]),
    1021: ("movie", "Knives Out", 2019, ["Comedy", "Crime", "Mystery"],
           ["detective", "murder", "family", "whodunit"],
           ["Rian Johnson"], ["Daniel Craig", "Ana de Armas", "Chris Evans"], 7.8, 13000, [1017]),
    1022: ("movie", "Palm Springs", 2020, ["Comedy", "Romance", "Science Fiction"],
           ["time loop", "wedding", "romance"],
           ["Max Barbakow"], ["Andy Samberg", "Cristin Milioti"], 7.4, 3500, [1008]),
    1023: ("movie", "Predestination", 2014, ["Science Fiction", "Thriller", "Mystery"],
           ["time travel", "paradox", "identity", "future"],
           ["Michael Spierig", "Peter Spierig"], ["Ethan Hawke", "Sarah Snook"], 7.3, 4500, [1002, 1020]),
    1099: ("movie", "Space Cheese Attack", 2019, ["Science Fiction", "Comedy"],
           ["space travel", "alien contact"], ["Nobody Famous"], [], 9.4, 30, [1002]),  # too few votes: must be filtered out
    2001: ("tv", "Severance", 2022, ["Drama", "Science Fiction", "Mystery"],
           ["conspiracy", "workplace", "memory", "dystopia", "corporation"],
           ["Dan Erickson"], ["Adam Scott", "Britt Lower", "John Turturro"], 8.4, 2500, [2011, 2017, 2002, 2003, 1004]),
    2002: ("tv", "Dark", 2017, ["Drama", "Science Fiction", "Mystery"],
           ["time travel", "small town", "conspiracy", "missing child", "family"],
           ["Baran bo Odar", "Jantje Friese"], ["Louis Hofmann", "Lisa Vicari"], 8.4, 5500, [2001, 2011, 2003, 2017]),
    2003: ("tv", "Black Mirror", 2011, ["Drama", "Science Fiction", "Mystery"],
           ["dystopia", "technology", "artificial intelligence", "anthology", "satire"],
           ["Charlie Brooker"], [], 8.3, 5000, [2011, 2017]),
    2004: ("tv", "Breaking Bad", 2008, ["Drama", "Crime"],
           ["drug trade", "chemistry teacher", "cancer", "cartel", "murder"],
           ["Vince Gilligan"], ["Bryan Cranston", "Aaron Paul"], 8.9, 13000, [2005, 2012, 2014, 2013]),
    2005: ("tv", "Better Call Saul", 2015, ["Crime", "Drama"],
           ["lawyer", "drug trade", "cartel", "prequel"],
           ["Vince Gilligan", "Peter Gould"], ["Bob Odenkirk", "Rhea Seehorn"], 8.6, 5500, [2004, 2014]),
    2006: ("tv", "The Bear", 2022, ["Drama", "Comedy"],
           ["restaurant", "family", "grief", "chef"],
           ["Christopher Storer"], ["Jeremy Allen White", "Ayo Edebiri"], 8.3, 1500, [2015]),
    2007: ("tv", "Succession", 2018, ["Drama"],
           ["family", "media", "wealth", "power struggle", "corporation"],
           ["Jesse Armstrong"], ["Brian Cox", "Jeremy Strong"], 8.4, 2500, [2001]),
    2008: ("tv", "Ted Lasso", 2020, ["Comedy", "Drama"],
           ["football", "coach", "friendship"],
           ["Bill Lawrence"], ["Jason Sudeikis", "Hannah Waddingham"], 8.4, 2000, [2006, 2015]),
    2009: ("tv", "Slow Horses", 2022, ["Drama", "Crime", "Comedy"],
           ["spy", "mi5", "conspiracy", "london", "espionage"],
           ["Will Smith"], ["Gary Oldman", "Jack Lowden"], 8.3, 1200, [2013, 2012]),
    2010: ("tv", "Foundation", 2021, ["Science Fiction", "Drama"],
           ["space opera", "empire", "future", "prophecy"],
           ["David S. Goyer"], ["Jared Harris", "Lee Pace"], 7.4, 1100, [2011]),
    2011: ("tv", "Silo", 2023, ["Science Fiction", "Drama", "Mystery"],
           ["dystopia", "conspiracy", "post-apocalyptic", "underground"],
           ["Graham Yost"], ["Rebecca Ferguson", "Tim Robbins"], 8.0, 1300, [2001, 2002, 2017]),
    2012: ("tv", "True Detective", 2014, ["Crime", "Drama", "Mystery"],
           ["serial killer", "detective", "murder", "anthology", "neo-noir"],
           ["Nic Pizzolatto"], ["Matthew McConaughey", "Woody Harrelson"], 8.3, 7000, [2013, 2014, 2004, 1010]),
    2013: ("tv", "Mindhunter", 2017, ["Crime", "Drama"],
           ["serial killer", "fbi", "detective", "psychology"],
           ["Joe Penhall"], ["Jonathan Groff", "Holt McCallany"], 8.2, 3500, [2012, 1010, 1011]),
    2014: ("tv", "Fargo", 2014, ["Crime", "Drama", "Comedy"],
           ["murder", "small town", "anthology", "dark comedy"],
           ["Noah Hawley"], ["Billy Bob Thornton", "Allison Tolman"], 8.3, 3800, [2004, 2012]),
    2015: ("tv", "The Great British Bake Off", 2010, ["Reality"],
           ["baking", "competition", "cooking"],
           [], ["Paul Hollywood"], 7.8, 900, []),
    2016: ("tv", "Andor", 2022, ["Science Fiction", "Drama", "Action"],
           ["rebellion", "spy", "space opera", "empire"],
           ["Tony Gilroy"], ["Diego Luna", "Stellan Skarsgård"], 8.2, 1800, [2010, 2011]),
    # Fictional recent/buzzy titles, to exercise the "New & trending" logic (see _RELEASED_DAYS_AGO / _TRENDING).
    1030: ("movie", "The Long Signal", 2026, ["Science Fiction", "Drama", "Mystery"],
           ["alien contact", "isolation", "expedition"],
           ["Ada Okafor"], ["Nia Sterling"], 7.4, 300, []),                  # new + trending, fits taste
    1032: ("movie", "Sunday Best", 2026, ["Comedy", "Romance"],
           ["wedding", "friendship"], ["Sam Reyes"], ["Lou Marsh"], 7.0, 400, []),   # new + trending, NOT your taste
    1034: ("movie", "Coming Soon", 2026, ["Science Fiction", "Thriller"],
           ["time travel"], ["Ada Okafor"], [], 0, 0, []),                   # trending but not released yet
    1035: ("movie", "Tiny New Thing", 2026, ["Science Fiction", "Mystery"],
           ["alien contact"], ["Nobody Famous"], [], 8.0, 10, []),           # new but only 10 votes
    2030: ("tv", "Harbour Lights", 2026, ["Crime", "Drama", "Mystery"],
           ["detective", "murder", "small town"],
           ["Priya Nair"], ["Tom Vasquez"], 7.9, 150, []),                   # new + trending, fits taste
    2031: ("tv", "Cooking Rivals", 2019, ["Reality"],
           ["cooking", "competition"], [], [], 7.0, 500, []),                # trending, NOT your taste
    2032: ("tv", "Orbit Nine", 2026, ["Science Fiction", "Drama"],
           ["space travel", "conspiracy"], ["Mei Tanaka"], ["Jo Lindqvist"], 7.6, 60, []),  # new, fits taste
    2017: ("tv", "Devs", 2020, ["Drama", "Science Fiction", "Mystery"],
           ["artificial intelligence", "conspiracy", "technology", "determinism", "quantum computing"],
           ["Alex Garland"], ["Sonoya Mizuno", "Nick Offerman"], 7.6, 1000, [2001, 1004]),
}

# (id, days ago last watched, your rating out of 10 or None, times watched, share of a show watched or None)
_WATCHED = [
    (1001, 30, 10, 2, None), (1002, 60, 9, 1, None), (1003, 200, 8, 1, None), (1004, 400, None, 1, None),
    (1006, 120, 7, 1, None), (1010, 20, 9, 1, None), (1011, 90, None, 1, None), (1012, 45, 9, 1, None),
    (1013, 300, 8, 1, None), (1015, 1500, 5, 1, None),
    (2001, 10, 10, 1, 1.0), (2002, 100, 9, 1, 1.0), (2004, 700, 10, 1, 1.0), (2005, 500, 8, 1, 1.0),
    (2012, 250, None, 1, 0.9), (2008, 40, None, 1, 0.1),
]
_EXTRA_IN_LIBRARY = [(("movie", 1005)), (("tv", 2003))]  # in Plex but not watched: shouldn't be suggested

# Release date = today minus this many days (negative = in the future). Everything else is released mid-year.
_RELEASED_DAYS_AGO = {1030: 60, 1032: 30, 1034: -30, 1035: 20, 2030: 45, 2032: 90}
_TRENDING = {"movie": [1014, 1030, 1032, 1034], "tv": [2030, 2031]}  # 1014 (Heat) is also linked to your favourites


def _details(tmdb_id, now):
    media_type, title, year, genres, keywords, directors, cast, rating, votes, recs = _CATALOGUE[tmdb_id]
    if tmdb_id in _RELEASED_DAYS_AGO:
        release_date = (now - timedelta(days=_RELEASED_DAYS_AGO[tmdb_id])).date().isoformat()
    else:
        release_date = f"{year}-06-15"
    return {
        "media_type": media_type, "tmdb_id": tmdb_id, "title": title, "year": year, "release_date": release_date,
        "overview": f"Sample data for {title}.", "poster_url": None, "url": None,
        "genres": genres, "keywords": keywords, "directors": directors, "cast": cast,
        "vote_average": rating, "vote_count": votes, "recommendations": recs,
        "backdrop_url": None, "poster_large_url": None, "runtime": None, "seasons": None, "certification": None,
    }


class SampleTmdb:
    """Same methods as tmdb.TmdbClient, backed by the catalogue above."""

    def __init__(self, now=None):
        self.now = now or datetime.now(timezone.utc)

    def details(self, media_type, tmdb_id, refresh=False):
        entry = _CATALOGUE.get(tmdb_id)
        if not entry or entry[0] != media_type:
            raise KeyError((media_type, tmdb_id))
        return _details(tmdb_id, self.now)

    def discover(self, media_type, genre_name):
        # Like the real thing, only well-voted titles (1000+ votes), best rated first.
        found = [i for i, e in _CATALOGUE.items() if e[0] == media_type and genre_name in e[3] and e[8] >= 1000]
        return sorted(found, key=lambda i: -_CATALOGUE[i][7])

    def trending(self, media_type):
        return list(_TRENDING[media_type])

    def new_releases(self, media_type, genre_name, since, until):
        found = [i for i, e in _CATALOGUE.items() if e[0] == media_type and genre_name in e[3]
                 and since <= _details(i, self.now)["release_date"] <= until and i in _RELEASED_DAYS_AGO]
        return found

    def search(self, media_type, title, year=None):
        matches = [i for i, e in _CATALOGUE.items() if e[0] == media_type and e[1].lower() == title.strip().lower()]
        if not matches:
            return None
        if year is None:
            return matches[0]
        close = [i for i in matches if abs(_CATALOGUE[i][2] - year) <= 1]
        return max(close, key=lambda i: _CATALOGUE[i][8]) if close else None


def load(now=None):
    """Returns (watched, library_keys) shaped like plex.PlexClient.load()."""
    now = now or datetime.now(timezone.utc)
    watched = []
    for tmdb_id, days_ago, rating, views, progress in _WATCHED:
        media_type, title, year = _CATALOGUE[tmdb_id][:3]
        watched.append({
            "media_type": media_type, "tmdb_id": tmdb_id, "title": title, "year": year,
            "last_viewed": (now - timedelta(days=days_ago)).isoformat(),
            "user_rating": rating, "view_count": views, "progress": progress,
        })
    library_keys = {(w["media_type"], w["tmdb_id"]) for w in watched} | set(_EXTRA_IN_LIBRARY)
    return watched, library_keys


def library_items(now=None):
    """The sample "Plex library" for the Library page: everything watched plus the owned-but-unwatched
    extras, as library item dicts (no poster, since there's no Plex to serve one)."""
    now = now or datetime.now(timezone.utc)
    items = []
    for n, (tmdb_id, days_ago, _, views, progress) in enumerate(_WATCHED):
        media_type, title, year = _CATALOGUE[tmdb_id][:3]
        items.append({"media_type": media_type, "tmdb_id": tmdb_id, "title": title, "year": year,
                      "added_at": (now - timedelta(days=days_ago + 30 + n)).isoformat(), "watched": views > 0,
                      "progress": progress, "poster_key": None,
                      "url": f"https://www.themoviedb.org/{media_type}/{tmdb_id}"})
    for n, (media_type, tmdb_id) in enumerate(_EXTRA_IN_LIBRARY):
        title, year = _CATALOGUE[tmdb_id][1:3]
        items.append({"media_type": media_type, "tmdb_id": tmdb_id, "title": title, "year": year,
                      "added_at": (now - timedelta(days=5 + n)).isoformat(), "watched": False, "progress": None,
                      "poster_key": None, "url": f"https://www.themoviedb.org/{media_type}/{tmdb_id}"})
    return items
