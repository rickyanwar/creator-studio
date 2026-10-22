"""Real team, maker, sponsor, person and circuit names that must not appear in Mode 8 prompts or copy.

Matching is case-sensitive on whole words, so ordinary words stay usable ("a carbon shell",
"in March", "glowing arrows"). Ambiguous words are left out on purpose, or listed only in a
longer unambiguous form ("Joan Mir", "Ross Brawn").
"""
import re

MAKER_TEAM_SPONSOR_NAMES = (
    # F1 teams, constructors and engine makers, current and historic
    "Alpine", "Aston Martin", "Audi", "Cadillac", "Ferrari", "Scuderia", "Haas", "McLaren", "Mercedes", "AMG",
    "Racing Bulls", "Red Bull", "Williams", "Sauber", "Lotus", "Brabham", "Tyrrell", "Benetton", "Renault",
    "Toyota", "Honda", "Chaparral", "Ligier", "Minardi", "Toro Rosso", "AlphaTauri", "Force India",
    "Racing Point", "Brawn GP", "Jordan Grand Prix", "Jaguar", "BMW", "Porsche", "Lamborghini", "Maserati",
    "Cosworth", "Ford", "Peugeot",
    # MotoGP manufacturers and teams
    "Ducati", "Yamaha", "KTM", "Aprilia", "Suzuki", "Kawasaki", "MV Agusta", "Gresini", "VR46", "Pramac",
    "Trackhouse", "Tech3", "LCR",
    # Sponsors and suppliers
    "Petronas", "Oracle", "Aramco", "Monster Energy", "Repsol", "Pirelli", "Michelin", "Bridgestone", "Dunlop",
    "Brembo", "Öhlins", "Ohlins", "Akrapovič", "Akrapovic", "Alpinestars", "Dainese", "Rolex", "TAG Heuer",
    "Tag Heuer", "DHL", "Heineken", "Lenovo", "Santander", "MoneyGram", "BWT", "Castrol", "Marlboro",
    "Mobil 1", "Liqui Moly", "Qatar Airways", "Crypto.com", "Hugo Boss",
)

PERSON_NAMES = (
    # Drivers, riders and team figures
    "Verstappen", "Hamilton", "Leclerc", "Norris", "Piastri", "Russell", "Antonelli", "Alonso", "Stroll",
    "Gasly", "Colapinto", "Albon", "Sainz", "Ocon", "Bearman", "Hulkenberg", "Hülkenberg", "Bortoleto",
    "Lawson", "Hadjar", "Lindblad", "Perez", "Pérez", "Bottas", "Tsunoda", "Ricciardo", "Senna", "Schumacher",
    "Prost", "Lauda", "Vettel", "Raikkonen", "Räikkönen", "Rosberg", "Massa", "Webber", "Fangio", "Mansell",
    "Piquet", "Villeneuve", "Hakkinen", "Häkkinen", "Jim Clark", "Jackie Stewart", "James Hunt", "Damon Hill",
    "Graham Hill", "Stirling Moss", "Jenson Button", "Newey", "Colin Chapman", "Ross Brawn", "Horner", "Wolff",
    "Marquez", "Márquez", "Bagnaia", "Bezzecchi", "Acosta", "Quartararo", "Binder", "Rossi", "Lorenzo",
    "Stoner", "Pedrosa", "Bastianini", "Morbidelli", "Vinales", "Viñales", "Zarco", "Oliveira", "Ogura",
    "Aldeguer", "Di Giannantonio", "Jorge Martin", "Jorge Martín", "Joan Mir", "Rins", "Jack Miller",
    "Espargaro", "Espargaró", "Dovizioso", "Crutchlow", "Nakagami", "Marini",
)

CIRCUIT_NAMES = (
    # Circuits and named corners
    "Monza", "Spa-Francorchamps", "Silverstone", "Monaco", "Monte Carlo", "Suzuka", "Interlagos", "Imola",
    "Zandvoort", "Baku", "Jeddah", "Sakhir", "Albert Park", "Mugello", "Assen", "Jerez", "Misano", "Sepang",
    "Mandalika", "Phillip Island", "Le Mans", "Sachsenring", "Losail", "Lusail", "COTA", "Circuit of the Americas",
    "Hungaroring", "Catalunya", "Montmeló", "Red Bull Ring", "Spielberg", "Yas Marina", "Marina Bay",
    "Hockenheim", "Nürburgring", "Nurburgring", "Portimão", "Portimao", "Motegi", "Buriram", "Eau Rouge",
    "Raidillon", "Parabolica", "Maggotts", "Becketts", "Degner", "Rascasse", "Tamburello",
)

REAL_NAMES = MAKER_TEAM_SPONSOR_NAMES + PERSON_NAMES + CIRCUIT_NAMES


def _pattern(names: tuple[str, ...]) -> re.Pattern:
    return re.compile(
        r"(?<![\w-])(" + "|".join(re.escape(name) for name in sorted(names, key=len, reverse=True)) + r")(?![\w-])"
    )


_PATTERN = _pattern(REAL_NAMES)
_PERSON_PATTERN = _pattern(PERSON_NAMES)


def find_real_names(text: str) -> list[str]:
    """Distinct real names found in text, in order of first appearance."""
    found: list[str] = []
    for match in _PATTERN.finditer(text or ""):
        if match.group(1) not in found:
            found.append(match.group(1))
    return found


def find_person_names(text: str) -> list[str]:
    """Distinct person names found in text, in order of first appearance."""
    found: list[str] = []
    for match in _PERSON_PATTERN.finditer(text or ""):
        if match.group(1) not in found:
            found.append(match.group(1))
    return found
