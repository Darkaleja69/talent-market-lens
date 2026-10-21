"""Minimal offline DOM adapter for Playwright-style page APIs (tests only).

Translates the small CSS subset used by ``src/irishjobs/serp.py`` and
``src/irishjobs/detail.py`` into XPath over lxml (already a project
dependency) so the parsers can be exercised against the sanitized HTML
fixtures without a browser or network access.
"""
from __future__ import annotations

import re

from lxml import html as lxml_html

_TOKEN = re.compile(
    r"^(?P<tag>[a-zA-Z][\w-]*)?"
    r"(?:\[(?P<attr>[\w-]+)"
    r"(?:(?P<op>\^=|\$=|\*=|~=|=)(?P<quote>['\"])(?P<value>.*?)(?P=quote))?"
    r"\])?$"
)


def css_to_xpath(selector: str, relative: bool = False) -> str:
    """Translate ``tag[attr op "value"]`` descendant selectors to XPath."""
    prefix = ".//" if relative else "//"
    alternatives = []
    for alt in selector.split(","):
        parts = alt.strip().split()
        if not parts:
            raise ValueError(f"empty selector: {selector!r}")
        steps = []
        for part in parts:
            m = _TOKEN.match(part)
            if not m:
                raise ValueError(f"selector not supported: {part!r}")
            tag = m.group("tag") or "*"
            attr = m.group("attr")
            if attr:
                op = m.group("op")
                if op is None:
                    steps.append(f"{tag}[@{attr}]")
                    continue
                value = m.group("value")
                lit = f"'{value}'"
                if op == "=":
                    pred = f"[@{attr}={lit}]"
                elif op == "*=":
                    pred = f"[contains(@{attr}, {lit})]"
                elif op == "^=":
                    pred = f"[starts-with(@{attr}, {lit})]"
                elif op == "$=":
                    pred = (f"[substring(@{attr}, string-length(@{attr}) - "
                            f"string-length({lit}) + 1) = {lit}]")
                elif op == "~=":
                    pred = (f"[contains(concat(' ', normalize-space(@{attr}), "
                            f"' '), concat(' ', {lit}, ' '))]")
                else:  # pragma: no cover - defensive
                    raise ValueError(f"operator not supported: {op!r}")
                steps.append(tag + pred)
            else:
                steps.append(tag)
        alternatives.append(prefix + "/".join(steps))
    return " | ".join(alternatives)


class FakeElement:
    """Playwright-like element wrapper over an lxml node."""

    def __init__(self, el):
        self._el = el

    def query_selector(self, selector: str):
        found = self._el.xpath(css_to_xpath(selector, relative=True))
        return FakeElement(found[0]) if found else None

    def query_selector_all(self, selector: str) -> list["FakeElement"]:
        return [FakeElement(e)
                for e in self._el.xpath(css_to_xpath(selector, relative=True))]

    def get_attribute(self, name: str):
        return self._el.get(name)

    def inner_text(self) -> str:
        return "\n".join(t.strip() for t in self._el.itertext() if t.strip())

    def text_content(self) -> str:
        return self._el.text_content()

    def evaluate(self, *args, **kwargs):
        return ""


class FakePage:
    """Playwright-like page over an HTML fixture string."""

    def __init__(self, html: str,
                 url: str = "https://www.irishjobs.ie/jobs/python-data?age=7"):
        self._doc = lxml_html.fromstring(html)
        self.url = url
        self.goto_calls: list[str] = []

    def query_selector(self, selector: str):
        found = self._doc.xpath(css_to_xpath(selector))
        return FakeElement(found[0]) if found else None

    def query_selector_all(self, selector: str) -> list[FakeElement]:
        return [FakeElement(e) for e in self._doc.xpath(css_to_xpath(selector))]

    def title(self) -> str:
        nodes = self._doc.xpath("//title")
        return (nodes[0].text or "").strip() if nodes else ""

    def content(self) -> str:
        return lxml_html.tostring(self._doc, encoding="unicode")

    def goto(self, url: str, **kwargs):
        self.goto_calls.append(url)
        self.url = url
        return None

    def wait_for_load_state(self, *args, **kwargs):
        return None

    def wait_for_selector(self, *args, **kwargs):
        return None

    def evaluate(self, *args, **kwargs):
        return ""
