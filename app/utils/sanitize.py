"""
Minimal whitelist HTML sanitizer for pitch email bodies.

Allows only the tags and attributes that the review editor can produce:
  <a href="...">  <br>  <strong>  <b>  <em>  <i>

Everything else is stripped (tags removed, text content kept).
javascript: hrefs are also stripped.
"""

import html
from html.parser import HTMLParser

_ALLOWED_TAGS = {'a', 'br', 'strong', 'b', 'em', 'i'}
_ALLOWED_ATTRS: dict[str, set[str]] = {'a': {'href'}}


class _Sanitizer(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=False)
        self._out: list[str] = []

    def handle_starttag(self, tag: str, attrs: list) -> None:
        if tag not in _ALLOWED_TAGS:
            return
        allowed = _ALLOWED_ATTRS.get(tag, set())
        safe_parts: list[str] = []
        for k, v in attrs:
            if k not in allowed or v is None:
                continue
            v = v.strip()
            if k == 'href' and v.lower().lstrip().startswith('javascript:'):
                continue
            safe_parts.append(f'{k}="{html.escape(v, quote=True)}"')
        attr_str = (' ' + ' '.join(safe_parts)) if safe_parts else ''
        if tag == 'br':
            self._out.append(f'<br>')
        else:
            self._out.append(f'<{tag}{attr_str}>')

    def handle_endtag(self, tag: str) -> None:
        if tag in _ALLOWED_TAGS and tag != 'br':
            self._out.append(f'</{tag}>')

    def handle_data(self, data: str) -> None:
        self._out.append(html.escape(data, quote=False))

    def handle_entityref(self, name: str) -> None:
        self._out.append(f'&{name};')

    def handle_charref(self, name: str) -> None:
        self._out.append(f'&#{name};')

    def get_output(self) -> str:
        return ''.join(self._out)


def sanitize_body_html(body: str) -> str:
    """Strip all HTML except the safe whitelist. Safe to call on empty strings."""
    if not body:
        return body
    p = _Sanitizer()
    p.feed(body)
    return p.get_output()
