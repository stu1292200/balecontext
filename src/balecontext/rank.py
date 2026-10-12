"""Symbol-first ranking: identifiers and class qualifiers outweigh body-text frequency."""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from pathlib import PurePosixPath

# Question fillers and instructions that agents put in queries ("cite the
# relevant source files and line numbers") but that describe no code.
STOP = {
    "a", "an", "and", "are", "as", "at", "be", "by", "do", "does", "did", "done", "for", "from", "how", "i", "in", "is",
    "it", "its", "of", "on", "or", "the", "this", "that", "these", "those", "to", "what", "when", "where", "which",
    "who", "why", "with", "without", "into", "can", "could", "should", "would", "will", "not", "any", "all", "some",
    "there", "here", "about", "then", "than", "so", "if", "out", "up", "me", "my", "we", "our", "you", "your",
    "work", "works", "working", "happen", "happens", "happening", "explain", "describe", "trace", "show", "tell",
    "cite", "citing", "relevant", "source", "sources", "file", "files", "line", "lines", "number", "numbers",
    "modify", "please", "code", "codebase", "repository", "repo", "project", "detail", "details", "step", "steps",
    "also", "called", "get", "gets", "used", "using", "via", "way", "ways", "need", "needs", "decide", "decides",
}
TEST_WORDS = {"test", "tests", "testing", "spec", "specs"}
# "the next request", "the same session": after a determiner these only describe the noun.
GENERIC_ADJECTIVES = {"next", "previous", "first", "last", "new", "old", "same", "other", "current", "following", "original", "final"}
DETERMINERS = {"the", "a", "an", "this", "that", "each", "every", "its", "their", "our", "your", "my"}
DOC_WORDS = {"doc", "docs", "documentation", "guide", "readme"}
CODE_EXTENSIONS = (".py", ".pyi", ".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx")
DOC_EXTENSIONS = (".md", ".rst", ".txt", ".adoc", ".mdx")
TOKEN = re.compile(r"[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*(?:\(\))?")


def stem(word: str) -> str:
    """Tiny suffix stripper applied to both query and index tokens."""
    word = word.lower()
    if len(word) > 6 and word.endswith("ations"):
        return word[:-6]
    if len(word) > 5 and word.endswith("ation"):
        return word[:-5]
    if len(word) > 5 and word.endswith("ing"):
        return word[:-3]
    if len(word) > 4 and word.endswith("ed") and not word.endswith("eed"):
        return word[:-2]
    if len(word) > 4 and word.endswith("ies"):
        return word[:-3] + "y"
    if len(word) > 3 and word.endswith("es") and word[:-2].endswith(("s", "x", "z", "ch", "sh")):
        return word[:-2]
    if len(word) > 3 and word.endswith("s") and not word.endswith(("ss", "us", "is")):
        return word[:-1]
    return word


def split_identifier(text: str) -> list[str]:
    """`URLResolver._clean_fields` -> url, resolver, clean, fields (lowercase, unstemmed)."""
    parts: list[str] = []
    for chunk in re.split(r"[^A-Za-z0-9]+", text):
        parts.extend(re.findall(r"[A-Z]+(?=[A-Z][a-z])|[A-Z]?[a-z]+|[A-Z]+|[0-9]+", chunk))
    return [part.lower() for part in parts if part]


def path_class(path: str) -> str:
    pure = PurePosixPath(path.lower())
    parts = set(pure.parts[:-1])
    name = pure.name
    if parts & {"tests", "test", "testing", "__tests__", "spec", "specs"} or name.startswith("test_") or re.search(r"(_test|_tests|\.test|\.spec)\.[a-z]+$", name):
        return "test"
    if parts & {"examples", "example", "samples", "sample"}:
        return "example"
    if name.endswith(DOC_EXTENSIONS) or parts & {"docs", "doc"}:
        return "doc"
    if name.endswith(CODE_EXTENSIONS):
        return "code"
    return "other"


@dataclass
class Identifier:
    text: str
    leaf: str
    qualifiers: list[str]
    is_class: bool
    called: bool = False


@dataclass
class Query:
    identifiers: list[Identifier] = field(default_factory=list)
    words: list[str] = field(default_factory=list)
    wants_tests: bool = False
    wants_docs: bool = False
    wants_types: bool = False
    prose_words: set[str] = field(default_factory=set)

    def describe(self) -> str:
        names = ", ".join(i.text for i in self.identifiers) or "none"
        return f"identifiers: {names}; words: {', '.join(self.words) or 'none'}"


def _is_identifier(token: str, called: bool) -> bool:
    if called or "." in token or "_" in token.strip("_") or token.startswith("_"):
        return True
    if re.search(r"[a-z][A-Z]", token):
        return True
    # URLResolver-style acronym prefixes, but not plain acronyms such as POST or HTTP.
    return bool(re.match(r"[A-Z]{2,}[a-z]", token))


def parse(query: str) -> Query:
    result = Query()
    seen_words: set[str] = set()
    seen_identifiers: set[str] = set()
    lowered = {w.lower() for w in re.findall(r"[A-Za-z]+", query)}
    result.wants_tests = bool(lowered & TEST_WORDS)
    result.wants_docs = bool(lowered & DOC_WORDS)
    result.wants_types = bool(lowered & {"type", "types", "interface", "interfaces", "typing"})
    tokens = [match.group(0) for match in TOKEN.finditer(query)]
    for position, token in enumerate(tokens):
        previous = tokens[position - 1].lower() if position else ""
        following = tokens[position + 1].lower() if position + 1 < len(tokens) else ""
        called = token.endswith("()")
        token = token.removesuffix("()").strip(".")
        if not token:
            continue
        if token.lower().endswith(CODE_EXTENSIONS + DOC_EXTENSIONS) and "." in token:
            # A file name: keep its stem as a path word.
            words = [token.rsplit(".", 1)[0]]
        elif _is_identifier(token, called):
            if token not in seen_identifiers:
                seen_identifiers.add(token)
                segments = token.split(".")
                leaf = segments[-1]
                result.identifiers.append(Identifier(token, leaf, [s for s in segments[:-1] if s], leaf[:1].isupper() and not leaf.isupper(), called))
            words = split_identifier(token)
        else:
            words = split_identifier(token)
            if previous in DETERMINERS and token.lower() in GENERIC_ADJECTIVES:
                continue
            # "the next request", "the Session": after a determiner a plain word is a noun or an
            # adjective, not a function's name, unless the question says so ("the compose function").
            named = previous not in DETERMINERS or following in {"function", "method", "helper"}
            if len(words) == 1 and len(words[0]) > 2 and words[0] not in STOP and named:
                result.prose_words.add(stem(words[0]))
        for word in words:
            if len(word) <= 2 or word in STOP:
                continue
            stemmed = stem(word)
            if stemmed not in seen_words:
                seen_words.add(stemmed)
                result.words.append(stemmed)
    return result


@dataclass
class Entry:
    id: str
    path: str
    name: str
    kind: str
    start: int
    end: int
    leaf: str
    owner: list[str]
    name_tokens: set[str]
    leaf_tokens: set[str]
    path_tokens: set[str]
    file_stem: str
    category: str


class SymbolTable:
    """In-memory symbol index; rebuilt only when the index generation changes."""

    def __init__(self, rows):
        self.entries: list[Entry] = []
        self.by_leaf: dict[str, list[int]] = {}
        self.by_token: dict[str, list[int]] = {}
        self.by_path_token: dict[str, list[int]] = {}
        for row in rows:
            if row["kind"] in {"file", "chunk"}:
                continue
            segments = row["name"].split(".")
            pure = PurePosixPath(row["path"])
            entry = Entry(
                row["id"], row["path"], row["name"], row["kind"], row["start"], row["end"], segments[-1].lower().lstrip("#"),
                [segment.lower() for segment in segments[:-1]],
                {stem(t) for t in split_identifier(row["name"])},
                {stem(t) for t in split_identifier(segments[-1])},
                {stem(t) for part in pure.parts[:-1] for t in split_identifier(part)} | {stem(t) for t in split_identifier(pure.stem)},
                stem(pure.stem.lower()), path_class(row["path"]),
            )
            index = len(self.entries)
            self.entries.append(entry)
            self.by_leaf.setdefault(entry.leaf, []).append(index)
            for token in entry.name_tokens:
                self.by_token.setdefault(token, []).append(index)
            for token in entry.path_tokens:
                self.by_path_token.setdefault(token, []).append(index)
        self.total = max(1, len(self.entries))

    def idf(self, postings: list[int] | None) -> float:
        return math.log(1 + self.total / (1 + len(postings or ())))


def score(table: SymbolTable, query: Query, body_scores: dict[str, float] | None = None) -> list[tuple[float, Entry, set[str]]]:
    """Return (score, entry, matched identifier texts) sorted best first."""
    body_scores = body_scores or {}
    class_names = {i.leaf.lower() for i in query.identifiers if i.is_class}
    query_words = set(query.words)
    candidates: set[int] = set()
    for identifier in query.identifiers:
        candidates.update(table.by_leaf.get(identifier.leaf.lower(), ()))
    for word in query.words:
        candidates.update(table.by_token.get(word, ()))
    by_id = {}
    if body_scores:
        by_id = {entry.id: index for index, entry in enumerate(table.entries)}
        candidates.update(by_id[i] for i in body_scores if i in by_id)
    word_idf = {word: table.idf(table.by_token.get(word, []) + table.by_path_token.get(word, [])) for word in query.words}
    total_terms = max(1, len(query.words))
    # Agents list identifiers most-important first.
    order_bonus = {identifier.text: 1 + 0.15 * (len(query.identifiers) - position) / len(query.identifiers) for position, identifier in enumerate(query.identifiers)}
    results: list[tuple[float, Entry, set[str]]] = []
    for index in candidates:
        entry = table.entries[index]
        value = 0.0
        matched: set[str] = set()
        owner_tokens = {stem(t) for segment in entry.owner for t in split_identifier(segment)}
        for identifier in query.identifiers:
            leaf = identifier.leaf.lower()
            full = identifier.text.lower()
            if identifier.called and entry.kind in {"type", "interface"}:
                continue  # `next()` is a call; a type named Next cannot be called.
            if entry.name.lower() == full or (identifier.qualifiers and entry.name.lower().endswith("." + full)):
                value += 6000 * table.idf(table.by_leaf.get(leaf))
                matched.add(identifier.text)
            elif entry.leaf == leaf:
                bonus = 1000 * table.idf(table.by_leaf.get(leaf))
                # Another identifier in the query names this symbol's class: `BaseForm full_clean`.
                if class_names & set(entry.owner):
                    bonus += 2500
                elif entry.owner and len(split_identifier(entry.owner[-1])) == 1 and stem(entry.owner[-1]) in query_words:
                    # A plain word names the class: "Hono's Context ... c.json", "Hono ... onError".
                    bonus += 1200
                for qualifier in identifier.qualifiers:
                    q = stem(qualifier)
                    if qualifier.lower() in entry.owner:
                        bonus += 1500
                    elif qualifier[:1].isupper() and q in owner_tokens:
                        bonus += 300
                    if qualifier[:1].islower() and not entry.owner:
                        # `requests.get`: a lowercase qualifier is usually a module, so prefer module-level functions.
                        bonus += 500
                    if q == entry.file_stem:
                        bonus += 300
                    elif q in entry.path_tokens:
                        bonus += 100
                value += bonus * order_bonus[identifier.text]
                matched.add(identifier.text)
        # Members of a class the query names ("How does CsrfViewMiddleware check ...").
        if class_names & set(entry.owner):
            value += 400
        # The symbol's own name counts more than its class's name: a question about
        # "URL resolver" should not rank every dunder method of a class equally.
        leaf_hits = [w for w in query.words if w in entry.leaf_tokens]
        owner_hits = [w for w in query.words if w not in entry.leaf_tokens and w in entry.name_tokens]
        path_hits = [w for w in query.words if w not in entry.name_tokens and w in entry.path_tokens]
        partial = [w for w in query.words if w not in entry.name_tokens and len(w) >= 5 and any(t.startswith(w[:-1]) or w.startswith(t) for t in entry.leaf_tokens if len(t) >= 4)]
        coverage = (len(leaf_hits) + len(owner_hits) + 0.5 * len(partial)) / total_terms
        value += sum(60 * word_idf[w] for w in leaf_hits) * (0.5 + coverage)
        if entry.kind not in {"class", "type", "interface"} and len(entry.leaf_tokens) == 1 and stem(entry.leaf) in query.prose_words and len(table.by_leaf.get(entry.leaf, ())) <= 2:
            # A plain word that is a function's whole, distinctive name: "How does compose run middleware?"
            value += 150 * table.idf(table.by_leaf.get(entry.leaf))
        value += sum(30 * word_idf[w] for w in owner_hits) * (0.5 + coverage)
        value += sum(30 * word_idf[w] for w in partial) * (0.5 + coverage)
        value += sum(12 * word_idf[w] for w in path_hits)
        value += body_scores.get(entry.id, 0.0)
        if value <= 0:
            continue
        multiplier = {"code": 1.0, "test": 0.2, "doc": 0.25, "example": 0.5, "other": 0.6}[entry.category]
        if entry.kind in {"type", "interface"} and not query.wants_types:
            # Type declarations describe shapes, not behaviour.
            multiplier *= 0.2
        if entry.category == "test" and query.wants_tests:
            multiplier = 1.0
        if entry.category == "doc" and query.wants_docs:
            multiplier = 1.0
        results.append((value * multiplier, entry, matched))
    results.sort(key=lambda item: (-item[0], len(item[1].name), item[1].path, item[1].start))
    # Subsystem prior: favour the directory where the strongest code matches concentrate.
    totals: dict[str, float] = {}
    for value, entry, _ in results[:20]:
        if entry.category == "code":
            parent = str(PurePosixPath(entry.path).parent)
            totals[parent] = totals.get(parent, 0.0) + value
    if totals:
        best = max(totals, key=totals.get)
        results = [(value * (1.3 if str(PurePosixPath(entry.path).parent) == best else 1.0), entry, matched) for value, entry, matched in results]
        results.sort(key=lambda item: (-item[0], len(item[1].name), item[1].path, item[1].start))
    return results


def pick_seeds(query: Query, ranked: list[tuple[float, Entry, set[str]]], limit: int = 3) -> list[tuple[float, Entry, set[str]]]:
    """Best match per identifier, then a few strong extras; same-named symbols share a slot."""
    # Tests and docs are evidence only when the question asks for them, even when one is named
    # exactly like an identifier in the question (a test called `no_proxy`).
    asked = {"code", "example", "other"} | ({"test"} if query.wants_tests else set()) | ({"doc"} if query.wants_docs else set())
    ranked = [item for item in ranked if item[1].category in asked] or ranked
    seeds: list[tuple[float, Entry, set[str]]] = []
    used_ids: set[str] = set()
    for identifier in query.identifiers:
        best = next((item for item in ranked if identifier.text in item[2] and item[1].category == "code" and item[1].id not in used_ids), None)
        best = best or next((item for item in ranked if identifier.text in item[2] and item[1].id not in used_ids), None)
        if best:
            seeds.append(best)
            used_ids.add(best[1].id)
    if not ranked:
        return seeds
    top = ranked[0][0]
    leaves = {item[1].leaf for item in seeds}
    extras = 0
    for item in ranked:
        if extras >= limit or item[0] < top * 0.2:
            break
        if item[1].id in used_ids or item[1].leaf in leaves:
            continue
        seeds.append(item)
        used_ids.add(item[1].id)
        leaves.add(item[1].leaf)
        extras += 1
    # A class answers little by itself: add its best-matching members as seeds too,
    # unless the query already named some of them.
    words = set(query.words)

    def related_word(token: str) -> bool:
        return token in words or any(len(token) >= 4 and len(w) >= 5 and (w.startswith(token) or token.startswith(w[:-1])) for w in words)

    fallback: set[str] = set()
    for value, entry, matched in list(seeds):
        if entry.kind != "class" or any(other.name.startswith(entry.name + ".") for _, other, _ in seeds):
            continue
        members = [item for item in ranked if item[1].path == entry.path and item[1].name.startswith(entry.name + ".") and item[1].id not in used_ids and any(related_word(t) for t in item[1].leaf_tokens)]
        if not members:
            # Calling or constructing the class is its behaviour.
            members = [item for item in ranked if item[1].path == entry.path and item[1].name in (entry.name + ".__call__", entry.name + ".__init__") and item[1].id not in used_ids]
            members.sort(key=lambda item: not item[1].name.endswith("__call__"))
            fallback.update(item[1].id for item in members[:1])
        # The action the question names first leads: "add a route and build its matcher".
        position = {w: i for i, w in enumerate(query.words)}

        def first_word(item) -> int:
            hits = [position[w] for w in query.words for t in item[1].leaf_tokens if t == w or (len(t) >= 4 and len(w) >= 5 and (w.startswith(t) or t.startswith(w[:-1])))]
            return min(hits, default=len(position))

        members = sorted(members[:3], key=first_word)
        for member in members:
            seeds.append((value * 0.99, member[1], member[2]))
            used_ids.add(member[1].id)
    seeds.sort(key=lambda item: -item[0])
    # A class whose members are already seeds adds only its header: put it last.
    classes = [item for item in seeds if item[1].kind == "class" and any(other[1].name.startswith(item[1].name + ".") for other in seeds)]
    seeds = [item for item in seeds if item not in classes] + classes
    # Functions explain behaviour better than class headers: lead with a strong function.
    functions = [item for item in seeds if item[1].kind != "class" and item[1].id not in fallback]
    if seeds and seeds[0][1].kind == "class" and functions and functions[0][0] >= 0.3 * seeds[0][0]:
        seeds.remove(functions[0])
        seeds.insert(0, functions[0])
    return seeds[:12]
