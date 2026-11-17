"""SkillName: when two skill names mean the same skill ("UAT", "UAT Testing" and "User Acceptance Testing")."""

from collections.abc import Iterable

# Small words a name's initials skip: "Know Your Customer" -> KYC, "Quality of Service" -> QS
LINK_WORDS = {"and", "&", "of", "the", "for", "in", "to", "on", "a", "an"}
MIN_ABBREVIATION, MAX_ABBREVIATION = 2, 6  # letters in an abbreviation ("AI" to "GDPRMS"); longer is a word


class SkillName:
    """Compares skill names from one set of names (e.g. a search's jobs and the resume). Case and spacing never
    matter, and an abbreviation is the same skill as the one longer name in the set whose initials it spells:
    "UAT" is "User Acceptance Testing", and "UAT Testing" is "UAT" too. Only an abbreviation written in capitals
    counts, here or by another name in the set (the language "Go" is never "Google Operations"), and initials that two names share ("CI": Continuous
    Integration, Competitive Intelligence) are left alone, since they can't say which one is meant. Industry-
    neutral: no list of skills, only how abbreviations are made."""

    def __init__(self, names: Iterable[str]) -> None:
        full_names: dict[str, set[str]] = {}
        self.__written_in_capitals: set[str] = set()  # abbreviations some name writes in capitals ("UAT Testing")
        for name in names:
            tokens = name.replace("-", " ").split()
            if tokens and self.__is_abbreviation(tokens[0]):
                self.__written_in_capitals.add(tokens[0].lower())
            words = self.__words(name)
            initials = "".join(word[0] for word in words if word not in LINK_WORDS)
            if len(words) >= 2 and MIN_ABBREVIATION <= len(initials) <= MAX_ABBREVIATION:
                full_names.setdefault(initials, set()).add(" ".join(words))
        # Only initials that spell exactly one name: "ci" from two different names stands for neither
        self.__full_name = {initials: next(iter(found)) for initials, found in full_names.items() if len(found) == 1}

    def key(self, name: str) -> str:
        """The form two names share when they are the same skill: "UAT", " uat testing " and
        "User Acceptance Testing" -> "user acceptance testing" (when the set holds that full name)."""
        tokens = name.replace("-", " ").split()
        words = [token.lower() for token in tokens]
        # An abbreviation: written in capitals here, or typed in lower case ("uat") where another name in the set
        # writes it in capitals ("UAT"); "Go" is never one, as no name writes "GO"
        abbreviation = bool(words) and (self.__is_abbreviation(tokens[0]) or words[0] in self.__written_in_capitals)
        if abbreviation and words[0] in self.__full_name:
            full = self.__full_name[words[0]]
            # "UAT" alone, or "UAT Testing" (the abbreviation then the full name's last word again)
            if len(words) == 1 or (len(words) == 2 and words[1] == full.split()[-1]):
                return full
        return " ".join(words)

    def same(self, first: str, second: str) -> bool:
        """Whether two names are the same skill."""
        return self.key(first) == self.key(second)

    @staticmethod
    def __words(name: str) -> list[str]:
        """The name's words in lower case; a hyphen splits words ("Anti-Money Laundering" -> anti money laundering)."""
        return name.lower().replace("-", " ").split()

    @staticmethod
    def __is_abbreviation(token: str) -> bool:
        """Written as one: all capitals, letters only, 2-6 of them ("UAT", "KYC"; not "Go", not "Excel")."""
        return token.isalpha() and token.isupper() and MIN_ABBREVIATION <= len(token) <= MAX_ABBREVIATION
