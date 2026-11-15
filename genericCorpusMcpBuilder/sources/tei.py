"""Reading TEI XML editions (www.tei-c.org): the citation structure a file declares (CTS cRefPattern), its cited
divisions or lines, their text without notes and apparatus, sections refined by milestones (Stephanus 327 -> 327a),
and the header's title, author and language. Used by the Perseus and the generic TEI adapters."""
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path

TEI = "{http://www.tei-c.org/ns/1.0}"
XML_LANG = "{http://www.w3.org/XML/1998/namespace}lang"
SKIPPED = {TEI + tag for tag in ("bibl", "head", "figure", "fw", "rdg", "sic", "gap", "speaker", "teiHeader")}
NOTE = TEI + "note"
MILESTONE = TEI + "milestone"
STEP = re.compile(r"(//|/)([^/\[\]]+)((?:\[[^\]]*\])*)")
PREDICATE = re.compile(r"@([\w:.-]+)\s*=\s*['\"]([^'\"]*)['\"]")
WHITESPACE = re.compile(r"\s+")


def clean(text: str) -> str:
    return WHITESPACE.sub(" ", text).strip()


@dataclass
class TeiText:
    path: Path
    level_names: list[str]
    segments: list[tuple[list[str], str, str]]                 # (labels, text, notes) in reading order
    split_schemes: dict[str, int] = field(default_factory=dict)  # milestone schemes that refined sections, e.g. Stephanus
    title: str = ""
    author: str = ""
    language: str = ""                                        # as written in the file (xml:lang)
    license: str = ""                                         # the header's availability/licence statement


def qualified(tag: str) -> str:
    prefix, _, local = tag.rpartition(":")
    return "*" if local == "*" else (TEI + local if prefix in ("tei", "") else local)


def attribute(name: str) -> str:
    return XML_LANG if name == "xml:lang" else name


def citation_scheme(root: ET.Element) -> tuple[list[str], dict[int, list[tuple]]] | None:
    """(level names, XPath steps by depth) from the cRefPatterns."""
    patterns = [(p.get("n") or "", p.get("replacementPattern") or "") for p in root.iter(TEI + "cRefPattern")]
    patterns = [(name, pattern) for name, pattern in patterns if pattern.startswith("#xpath(")]
    if not patterns:
        return None
    depth_of = lambda pattern: len(set(re.findall(r"\$\d+", pattern)))
    names = {depth_of(pattern): name for name, pattern in patterns}
    steps = {depth_of(pattern): [(axis, qualified(tag), [(attribute(a), v) for a, v in PREDICATE.findall(predicates)])
                                 for axis, tag, predicates in STEP.findall(pattern[len("#xpath("):-1])]
             for _, pattern in patterns}
    return [names.get(k, f"level {k}") for k in range(1, max(steps) + 1)], steps


def matches(element: ET.Element, tag: str, conditions: list, labels: list[str]) -> list[str] | None:
    if tag != "*" and element.tag != tag:
        return None
    for name, value in conditions:
        found = element.get(name)
        if value.startswith("$"):
            if found is None or not found.strip():
                return None
            labels = labels + [found.strip()]
        elif found != value:
            return None
    return labels


def cited_elements(root: ET.Element, steps: list[tuple]):
    """(labels, element) for every element the XPath steps select, in document order."""
    (_, tag, conditions), rest = steps[0], steps[1:]
    labels = matches(root, tag, conditions, [])
    current = [(root, labels)] if labels is not None else []
    for axis, tag, conditions in rest:
        following = []
        for node, labels in current:
            pool = (e for e in node.iter() if e is not node) if axis == "//" else iter(node)
            for element in pool:
                found = matches(element, tag, conditions, labels)
                if found is not None:
                    following.append((element, found))
        current = following
    return [(labels, element) for element, labels in current]


# Text

def events(element: ET.Element):
    """("text", str), ("note", str) and ("milestone", unit, n, resp) in document order, without the element's tail."""
    if element.text:
        yield ("text", element.text)
    for child in element:
        if child.tag == NOTE:
            yield ("note", clean("".join(child.itertext())))
        elif child.tag == MILESTONE:
            yield ("milestone", child.get("unit"), (child.get("n") or "").strip(), child.get("resp"))
        elif child.tag not in SKIPPED and isinstance(child.tag, str):
            yield from events(child)
        if child.tail:
            yield ("text", child.tail)


def refines(label: str, milestone: str) -> bool:
    """327a refines 327 (a Stephanus section of page 327)."""
    return milestone != label and milestone.startswith(label) and milestone[len(label):].isalpha()


def segments_of(labels: list[str], element: ET.Element, schemes: dict) -> list[tuple[list[str], str, str]]:
    """The element's text as one segment, or several where milestones refine its label."""
    pieces = [[labels[-1], [], []]]
    for event in events(element):
        if event[0] == "milestone":
            _, unit, n, resp = event
            if refines(labels[-1], n):
                schemes.setdefault(resp or unit or "page", 0)
                schemes[resp or unit or "page"] += 1
                pieces.append([n, [], []])
        elif event[0] == "note":
            pieces[-1][2].append(event[1])
        else:
            pieces[-1][1].append(event[1])
    if len(pieces) > 1:                       # text before the first refining milestone belongs to the first section
        head = pieces.pop(0)
        pieces[0][1][:0], pieces[0][2][:0] = head[1], head[2]
    return [(labels[:-1] + [label], clean("".join(text)), "\n".join(n for n in notes if n))
            for label, text, notes in pieces if clean("".join(text))]


def milestone_segments(labels: list[str], element: ET.Element, unit: str) -> list[tuple[list[str], str, str]]:
    """Text between milestones of the unit, each labelled by its milestone; text before the first goes with it."""
    pieces = [[None, [], []]]
    for event in events(element):
        if event[0] == "milestone":
            if (event[1] or "").lower() == unit and event[2]:
                pieces.append([event[2], [], []])
        elif event[0] == "note":
            pieces[-1][2].append(event[1])
        else:
            pieces[-1][1].append(event[1])
    if len(pieces) > 1:
        head = pieces.pop(0)
        pieces[0][1][:0], pieces[0][2][:0] = head[1], head[2]
    return [(labels + [label], clean("".join(text)), "\n".join(n for n in notes if n))
            for label, text, notes in pieces if label and clean("".join(text))]


def header_value(root: ET.Element, path: str) -> str:
    element = root.find(path)
    return clean("".join(element.itertext())) if element is not None else ""


def text_language(root: ET.Element) -> str:
    for path in (f"{TEI}text/{TEI}body/{TEI}div", f"{TEI}text/{TEI}body", f"{TEI}text"):
        element = root.find(path)
        if element is not None and element.get(XML_LANG):
            return element.get(XML_LANG)
    language = root.find(f".//{TEI}langUsage/{TEI}language")
    return (language.get("ident") or "") if language is not None else (root.get(XML_LANG) or "")


def read_tei(path: Path, report=print) -> TeiText | None:
    """A TEI file's cited segments, or None (with a report) if it cannot be read or declares no citation pattern."""
    try:
        root = ET.parse(path).getroot()
    except ET.ParseError as error:
        report(f"skipped unreadable {path.name}: {error}")
        return None
    scheme = citation_scheme(root)
    if not scheme:
        report(f"skipped {path.name}: no CTS citation pattern (refsDecl/cRefPattern)")
        return None
    level_names, steps = scheme
    split_schemes, segments = {}, []
    for labels, element in cited_elements(root, steps[max(steps)]):
        segments += segments_of(labels, element, split_schemes)
    if not segments and max(steps) - 1 in steps:
        # Some files declare nested divisions they do not have: the deepest level is milestones in the one above.
        unit = level_names[-1].lower()
        for labels, element in cited_elements(root, steps[max(steps) - 1]):
            segments += milestone_segments(labels, element, unit)
        if segments:
            report(f"{path.name}: {unit} read from milestones (its citation pattern matches nothing)")
    stmt = f"{TEI}teiHeader/{TEI}fileDesc/{TEI}titleStmt/"
    licence = root.find(f".//{TEI}availability/{TEI}licence")
    license = " ".join(filter(None, [header_value(root, f".//{TEI}availability/{TEI}licence"),
                                     licence.get("target") if licence is not None else ""]))
    return TeiText(path, level_names, segments, split_schemes, title=header_value(root, stmt + f"{TEI}title"),
                   author=header_value(root, stmt + f"{TEI}author"), language=text_language(root),
                   license=license or header_value(root, f".//{TEI}availability"))
